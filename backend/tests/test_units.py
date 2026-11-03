from __future__ import annotations

import sqlite3

import pytest

from mmi.ai.tasks import quote_in_source
from mmi.descriptions.short import extractive_short, validate_short
from mmi.extraction.frequency import parse_frequency


@pytest.mark.parametrize(
    "text,ftype,value,unit",
    [
        ("Every 24 hours", "hours_unqualified", 24, "hour"),
        ("Every 7 days", "calendar", 7, "day"),
        ("Every month", "calendar", 1, "month"),
        ("Every 3 months", "calendar", 3, "month"),
        ("Every 3,000 operating hours", "operating_hours", 3000, "hour"),
        ("Every 8,000 engine hours", "engine_hours", 8000, "hour"),
        ("8.000 running hours", "running_hours", 8000, "hour"),
        ("At every dry docking", "event", None, ""),
        ("Before starting the engine", "event", None, ""),
        ("When differential pressure exceeds 0.8 bar", "condition", None, ""),
        ("As required", "condition", None, ""),
        ("every 2000 starts", "cycle", 2000, "cycle"),
        ("half-yearly", "calendar", 6, "month"),
        ("", "unspecified", None, ""),
        ("see remarks", "unspecified", None, ""),
    ],
)
def test_frequency_parsing(text, ftype, value, unit) -> None:  # noqa: ANN001
    f = parse_frequency(text)
    assert f.type == ftype
    assert f.value == value
    assert f.unit == unit
    assert f.original == text.strip()


def test_hours_never_assumed_operating_without_context() -> None:
    assert parse_frequency("3000 h").type == "hours_unqualified"
    assert parse_frequency("3000 h", context="Intervals in operating hours").type == "operating_hours"


def test_compact_cell_codes() -> None:
    assert parse_frequency("3M", cell=True).label() == "3 months"
    assert parse_frequency("1Y", cell=True).label() == "1 year"
    w = parse_frequency("W", cell=True)
    assert w.unit == "week" and w.uncertain  # single letters are flagged


def test_whichever_first_keeps_alternatives() -> None:
    f = parse_frequency("8000 h or 2 years, whichever comes first")
    assert f.value == 8000 and f.alternatives and f.alternatives[0]["unit"] == "year"
    assert not f.uncertain


def test_short_validation_rejects_invented_values() -> None:
    long = "WARNING: Isolate supply.\n1. Remove cover.\n2. Tighten to 180 Nm."
    blocks = [{"type": "safety", "level": "WARNING", "text": "WARNING: Isolate supply."}]
    # Lines copied word for word from the long description pass.
    assert validate_short("WARNING: Isolate supply.\n2. Tighten to 180 Nm.", long, blocks)["issues"] == []
    # Paraphrased or invented text is rejected, as are invented values and dropped safety notices.
    issues = validate_short("Tighten to 200 Nm.", long, blocks)["issues"]
    assert any("200" in i for i in issues) and any("safety" in i for i in issues)
    assert any("word for word" in i for i in issues)
    assert validate_short("Isolate the supply first.", long, blocks)["issues"]


def test_extractive_short_keeps_safety_first() -> None:
    blocks = [
        {"type": "safety", "level": "DANGER", "text": "DANGER: Block the starting air.", "page": 1},
        {"type": "step", "number": "1", "text": "1. Open the doors.", "page": 1},
        {"type": "step", "number": "2", "text": "2. Measure clearance: 0.30 mm.", "page": 1, "values": ["0.30 mm"]},
        {"type": "step", "number": "3", "text": "3. Close the doors and test.", "page": 1},
    ]
    s = extractive_short("\n".join(b["text"] for b in blocks), blocks)
    assert s.content.splitlines()[0].startswith("DANGER") and "0.30 mm" in s.content and s.ok


def test_quote_grounding() -> None:
    src = "Fuel valve | Overhaul | 3,000 operating hours"
    assert quote_in_source("Fuel valve  Overhaul", src)
    assert not quote_in_source("Exhaust valve overhaul", src)


def test_newer_schema_is_refused_without_modification(tmp_path) -> None:  # noqa: ANN001
    from mmi.db import session as db

    path = tmp_path / "x.sqlite3"
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE schema_version (version INTEGER)")
    con.execute("INSERT INTO schema_version VALUES (999)")
    con.commit()
    con.close()
    with pytest.raises(RuntimeError, match="newer"):
        db._migrate(db.create_engine(f"sqlite:///{path.as_posix()}"), True, path, tmp_path)
    con = sqlite3.connect(path)
    assert con.execute("SELECT version FROM schema_version").fetchone()[0] == 999
    con.close()


def test_upgrade_backs_up_database(tmp_path, monkeypatch) -> None:  # noqa: ANN001
    from mmi.db import session as db

    path = tmp_path / "y.sqlite3"
    db._migrate(db.create_engine(f"sqlite:///{path.as_posix()}"), False, path, tmp_path / "b")
    calls = []
    monkeypatch.setattr(db, "SCHEMA_VERSION", db.SCHEMA_VERSION + 1)
    monkeypatch.setitem(db.MIGRATIONS, db.SCHEMA_VERSION, lambda conn: calls.append(1))
    db._migrate(db.create_engine(f"sqlite:///{path.as_posix()}"), True, path, tmp_path / "b")
    assert calls == [1]
    assert list((tmp_path / "b").glob("mmi-pre-v*.sqlite3"))


def test_recovery_requeues_interrupted_work(client) -> None:  # noqa: ANN001
    """Simulate a crash: tasks left 'running' and pages left 'processing' return to the queue."""
    from sqlalchemy import select

    from mmi.db.models import Document, Page, ProcessingTask, Project
    from mmi.db.session import session_scope
    from mmi.processing.engine import Engine

    with session_scope() as s:
        p = Project(name="crash", processing_state="paused")
        s.add(p)
        s.flush()
        d = Document(project_id=p.id, filename="x.pdf", original_path="x", stored_path="x", sha256="0" * 64, page_count=1)
        s.add(d)
        s.flush()
        s.add(Page(document_id=d.id, page_no=1, status="processing"))
        s.add(ProcessingTask(project_id=p.id, document_id=d.id, kind="extract_pages", dedupe_key="crash", status="running", payload={"page_nos": [1]}))
        pid = p.id
    Engine().recover()
    with session_scope() as s:
        t = s.scalar(select(ProcessingTask).where(ProcessingTask.dedupe_key == "crash"))
        page = s.scalar(select(Page).join(Document).where(Document.project_id == pid))
        assert t.status == "pending" and page.status == "pending"


def test_delete_project_removes_everything(client, fixtures_dir) -> None:  # noqa: ANN001
    from pathlib import Path

    p = client.post("/api/projects", json={"name": "to delete"}).json()
    res = client.post(f"/api/projects/{p['id']}/documents/import", json={"paths": [str(fixtures_dir["burner"])]}).json()
    doc = client.get(f"/api/documents/{res['results'][0]['document_id']}").json()
    copy = Path(client.get(f"/api/projects/{p['id']}/documents").json()[0]["original_path"])
    r = client.delete(f"/api/projects/{p['id']}")
    assert r.status_code == 200 and r.json()["documents"] == 1
    assert client.get(f"/api/projects/{p['id']}").status_code == 404
    assert client.get(f"/api/documents/{doc['id']}").status_code == 404
    assert copy.exists(), "the user's original PDF must never be deleted"


def test_ai_may_only_delete_words() -> None:
    from mmi.descriptions.short import condense_points, deletion_only

    src = "Do not clean the nozzle orifices with metal objects, tighten to 180 Nm."
    assert deletion_only("Do not clean nozzle orifices with metal objects, tighten to 180 Nm.", src)
    assert not deletion_only("Do not clean the nozzle holes with metal objects, tighten to 180 Nm.", src)  # reworded
    assert not deletion_only("Do clean the nozzle orifices with metal objects, tighten to 180 Nm.", src)  # negation removed
    assert not deletion_only("Do not clean the nozzle orifices, tighten to Nm.", src)  # number removed
    assert not deletion_only("Do not clean the nozzle orifices, tighten to 180.", src)  # unit removed

    class FakeModel:
        def chat_json(self, system, user, schema):  # noqa: ANN001, ANN201, ARG002
            return {"points": [
                {"sources": [0, 1], "text": "1. Mount a tackle above the relief valve."},  # merge + deletions: ok
                {"sources": [2], "text": "Remove all screws quickly."},  # adds words: rejected
            ]}  # point 3 skipped by the model

    points = ["1. Mount a tackle", "Mount one or two tackles in suitable positions above the relief valve.",
              "Remove the screws securing the relief valve.", "Refit the valve and tighten to 90 Nm."]  # fmt: skip
    out, rejected = condense_points(FakeModel(), points)
    assert rejected == 1
    assert out == ["1. Mount a tackle above the relief valve.", points[2], points[3]]


def test_interval_hyphenated_across_line_break() -> None:
    f = parse_frequency("After ar- rival in port.")
    assert f.type == "event" and f.event == "voyage"


def test_procedure_must_name_the_whole_component() -> None:
    from mmi.retrieval.discovery import Candidate, JobQuery, ProcedureFinder

    def names(component: str, **features: float) -> bool:
        q = JobQuery("j", f"{component} - Checking", component, "", "inspect", "", "d", 1)
        return ProcedureFinder._names_job(q, Candidate("d", 5, {}, features={"action_match": 1.0, **features}))

    # Heading inside another component's work card ("Turning gear switch" in the Cylinder Liner card).
    assert not names("Turning Gear Switch", component_in_heading=1.0, has_doc_title=1.0, doc_title_in_component=0.0)
    # Work card for a more specific object ("Hydraulic Tools" for "All tools").
    assert not names("All tools", has_doc_title=1.0, component_in_doc_title=1.0, doc_title_in_component=0.5)
    # Reference documents (descriptions, data sheets) are never the procedure.
    assert not names("Chain Drive", component_in_heading=1.0, doc_type="reference")
    assert names("Working Platforms", has_doc_title=1.0, component_in_doc_title=1.0, doc_title_in_component=1.0)
    # Parent document whose text names the sub-component.
    assert names("Fuel oil water content", has_doc_title=1.0, component_in_doc_title=0.5, doc_title_in_component=1.0, text_match=1.0)
