"""End-to-end: import → extract → jobs → procedures → descriptions → review → export."""

from __future__ import annotations

import openpyxl
import pytest

from tests.conftest import wait_idle


@pytest.fixture(scope="module")
def processed(client, fixtures_dir):  # noqa: ANN001, ANN201
    p = client.post("/api/projects", json={"name": "MV Test Vessel", "vessel_name": "MV Test", "vessel_imo": "1234567"}).json()
    paths = [str(fixtures_dir[k]) for k in ("main_engine", "burner", "scanned", "corrupt", "encrypted")]
    res = client.post(f"/api/projects/{p['id']}/documents/import", json={"paths": paths}).json()["results"]
    client.post(f"/api/projects/{p['id']}/processing/start")
    status = wait_idle(client, p["id"])
    jobs = client.get(f"/api/projects/{p['id']}/jobs").json()["items"]
    return {"project": p, "import": res, "status": status, "jobs": {j["title"]: j for j in jobs}, "all_jobs": jobs}


def _job(processed, fragment: str) -> dict:  # noqa: ANN001
    hits = [j for t, j in processed["jobs"].items() if fragment.lower() in t.lower()]
    assert hits, f"no job containing {fragment!r}; have {list(processed['jobs'])}"
    return hits[0]


def test_import_handles_bad_files(processed) -> None:  # noqa: ANN001
    by_name = {r["filename"]: r for r in processed["import"]}
    assert by_name["main_engine_manual.pdf"]["status"] == "imported"
    assert by_name["corrupt.pdf"]["status"] in ("error", "rejected")
    assert by_name["encrypted.pdf"]["status"] == "error"
    assert "password" in by_name["encrypted.pdf"]["message"].lower()


def test_every_page_has_a_terminal_state(client, processed) -> None:  # noqa: ANN001
    pages = processed["status"]["pages"]
    assert pages.get("pending", 0) == 0 and pages.get("processing", 0) == 0
    assert pages["total"] == pages["done"]


def test_scanned_page_was_ocrd(client, processed) -> None:  # noqa: ANN001
    docs = client.get(f"/api/projects/{processed['project']['id']}/documents").json()
    scanned = next(d for d in docs if d["filename"] == "purifier_scanned.pdf")
    assert scanned["content_type"] == "scanned"
    assert scanned["stats"]["ocr_pages"] == 2
    bowl = client.get(f"/api/jobs/{_job(processed, 'separator bowl')['id']}").json()
    assert bowl["matches"][0]["heading"].lower().startswith("separator bowl")  # procedure read from an OCR'd page
    assert "WARNING" in bowl["long"]["content"]


def test_jobs_and_frequencies(processed) -> None:  # noqa: ANN001
    fv = _job(processed, "Fuel valve – overhaul")
    assert fv["frequency_type"] == "operating_hours" and fv["frequency_value"] == 3000
    assert fv["frequency_original"] == "3,000 operating hours"
    xh = _job(processed, "bearing clearance")
    assert xh["component"] == "Crosshead bearing" and xh["frequency_value"] == 8000
    # No schedule row is lost: a row without an interval or without a procedure is kept and flagged.
    exhaust = _job(processed, "exhaust valve")
    assert exhaust["frequency_type"] == "unspecified" and exhaust["extraction_status"] == "needs_review"
    liner = _job(processed, "liner wear")
    assert liner["procedure_status"] == "not_found" and liner["description_status"] == "not_available"
    # A related but different procedure (separator bowl, not its seal rings) is never offered as the match.
    assert _job(processed, "seal rings")["procedure_status"] == "not_found"
    monthly = _job(processed, "General cleaning")
    assert monthly["frequency_type"] == "calendar" and monthly["frequency_unit"] == "month"
    matrix = _job(processed, "turbocharger air filter")
    assert matrix["frequency_value"] == 500


def test_spare_parts_are_not_jobs(processed) -> None:  # noqa: ANN001
    titles = " ".join(processed["jobs"]).lower()
    for part in ("valve body", "spindle guide", "o-ring 45x3", "nozzle"):
        assert part not in titles


def test_procedure_far_from_schedule_is_verified(client, processed) -> None:  # noqa: ANN001
    fv = client.get(f"/api/jobs/{_job(processed, 'Fuel valve – overhaul')['id']}").json()
    assert fv["procedure_status"] == "verified"
    top = fv["matches"][0]
    assert top["selected"] and top["heading"].startswith("909-1.2")
    assert top["end_page"] == top["start_page"] + 1, "multi-page procedure must span the continuation page"
    assert top["start_page"] - fv["schedule_page_no"] > 30


def test_boundary_includes_figure_page(client, processed) -> None:  # noqa: ANN001
    xh = client.get(f"/api/jobs/{_job(processed, 'bearing clearance')['id']}").json()
    m = xh["matches"][0]
    assert m["heading"].startswith("912-2.1")
    assert m["end_page"] - m["start_page"] == 2  # procedure page, figure page, continuation page
    assert m["evidence"]["figure_pages"]
    assert "0.30 – 0.45 mm" in xh["long"]["content"] or "0.30 - 0.45 mm" in xh["long"]["content"]


def test_long_description_is_faithful(client, processed) -> None:  # noqa: ANN001
    fv = client.get(f"/api/jobs/{_job(processed, 'Fuel valve – overhaul')['id']}").json()
    long = fv["long"]["content"]
    for must in ("WARNING", "180 Nm", "120 Nm", "0.015 mm", "0.62 mm", "909-T1", "CAUTION"):
        assert must in long, must
    assert "909-1.3 Fuel Valve - Pressure Testing" not in long, "next procedure must not be included"
    steps = [b for b in fv["long"]["blocks"] if b["type"] == "step"]
    assert [b.get("number") for b in steps][:10] == [str(i) for i in range(1, 11)]
    assert {b["page"] for b in steps} == {fv["matches"][0]["start_page"], fv["matches"][0]["end_page"]}
    assert "Page " not in long.split("\n")[0], "running headers/footers removed"


def test_short_description_is_grounded(client, processed) -> None:  # noqa: ANN001
    fv = client.get(f"/api/jobs/{_job(processed, 'Fuel valve – overhaul')['id']}").json()
    short = fv["short"]
    assert short["method"] == "extractive"
    assert not short["validation"]["issues"]
    assert "WARNING" in short["content"] or "shut off" in short["content"].lower()
    lines = short["content"].splitlines()
    assert 5 <= len(lines) <= 25  # point-wise: key steps + essentials, not the full procedure
    assert len(short["content"]) < len(fv["long"]["content"])
    assert any("180 Nm" in ln for ln in lines) and any("0.015 mm" in ln for ln in lines)  # essential values kept


def test_cross_manual_procedure(client, processed) -> None:  # noqa: ANN001
    tc = client.get(f"/api/jobs/{_job(processed, 'turbocharger air filter')['id']}").json()
    assert tc["matches"], "candidate expected"
    top = tc["matches"][0]
    assert top["document_name"] == "main_engine_manual.pdf"
    assert "Turbocharger Air Filter" in top["heading"]


def test_review_and_export(client, processed, tmp_path) -> None:  # noqa: ANN001
    pid = processed["project"]["id"]
    fv = _job(processed, "Fuel valve – overhaul")
    edited = client.patch(f"/api/jobs/{fv['id']}", json={"changes": {"title": "Fuel valve overhaul"}}).json()
    assert edited["title"] == "Fuel valve overhaul" and edited["machine"]["title"].startswith("Fuel valve –")
    assert "title" in edited["edited_fields"]
    approved = client.post(f"/api/jobs/{fv['id']}/review", json={"action": "approve"}).json()
    assert approved["review_status"] == "approved"

    # A job whose procedure is unconfirmed cannot be approved without choosing a match.
    pending = [j for j in processed["all_jobs"] if j["procedure_status"] == "needs_confirmation"]
    if pending:
        r = client.post(f"/api/jobs/{pending[0]['id']}/review", json={"action": "approve"})
        assert r.status_code == 409

    preview = client.post(f"/api/projects/{pid}/export/preview", json={"scope": "approved"}).json()
    assert preview["total_rows"] == 1
    out = tmp_path / "checklist.xlsx"
    res = client.post(f"/api/projects/{pid}/export", json={"scope": "all", "file_path": str(out), "template_id": "builtin-review"}).json()
    assert out.exists() and res["rows"] >= 10
    wb = openpyxl.load_workbook(out, read_only=True)
    assert {"Maintenance checklist", "Long descriptions", "Short descriptions", "Unresolved", "Export info"} <= set(wb.sheetnames)
    rows = list(wb["Maintenance checklist"].iter_rows(values_only=True))
    header = rows[0]
    data = {r[header.index("Job title")]: r for r in rows[1:]}
    row = data["Fuel valve overhaul"]
    assert row[header.index("Procedure start page")] is not None
    # Unconfirmed procedures never appear as sources in the export.
    for r in rows[1:]:
        if r[header.index("Procedure match status")] == "needs_confirmation":
            assert r[header.index("Procedure start page")] is None
    hist = client.get(f"/api/projects/{pid}/exports").json()
    assert hist and hist[0]["job_count"] == res["rows"]


def test_reviewed_jobs_survive_reextraction(client, processed) -> None:  # noqa: ANN001
    pid = processed["project"]["id"]
    docs = client.get(f"/api/projects/{pid}/documents").json()
    me = next(d for d in docs if d["filename"] == "main_engine_manual.pdf")
    client.post(f"/api/documents/{me['id']}/pages/4/reprocess")
    client.post(f"/api/projects/{pid}/processing/start")
    wait_idle(client, pid)
    jobs = client.get(f"/api/projects/{pid}/jobs").json()["items"]
    fv = next(j for j in jobs if j["title"] == "Fuel valve overhaul")
    assert fv["review_status"] == "approved"


def test_search_labels_provenance(client, processed) -> None:  # noqa: ANN001
    pid = processed["project"]["id"]
    res = client.get(f"/api/projects/{pid}/search", params={"q": "fuel valve"}).json()
    assert res["jobs"] and res["pages"]
    assert all(p["provenance"] == "source_text" for p in res["pages"])
    kinds = {d["provenance"] for d in res["descriptions"]}
    assert kinds <= {"extracted_source_text", "extractive_summary", "ai_generated_summary", "reviewer_edited"}


def test_auth_required(client) -> None:  # noqa: ANN001
    from fastapi.testclient import TestClient

    bare = TestClient(client.app)
    assert bare.get("/api/projects").status_code == 401
    assert bare.get("/api/health").status_code == 200


def test_evaluation_harness_on_fixtures(processed) -> None:  # noqa: ANN001
    """Regression guard: measured metrics on the synthetic fixtures (not a claim about real manuals)."""
    import json
    from pathlib import Path

    from mmi.evaluation import evaluate

    truth = json.loads((Path(__file__).parent / "fixtures" / "ground_truth.json").read_text(encoding="utf-8"))
    r = evaluate(processed["project"]["id"], truth)
    assert r["job_recall"] == 1.0 and r["job_precision"] == 1.0
    assert r["frequency_accuracy"] == 1.0, [d for d in r["details"] if d.get("frequency_ok") is False]
    assert r["procedure_recall_at_1"] == 1.0 and r["boundary_exact_accuracy"] == 1.0
    assert r["auto_verified_precision"] == 1.0
    assert r["long_description_probe_recall"] == 1.0


def test_document_coded_manual(client, fixtures_dir) -> None:  # noqa: ANN001
    """Schedule matrix with bare-hour headers, continuation page, superscript comments, Doc-ID linking."""
    from tests.conftest import wait_idle

    p = client.post("/api/projects", json={"name": "Doc-coded"}).json()
    client.post(f"/api/projects/{p['id']}/documents/import", json={"paths": [str(fixtures_dir["doc_coded"])]})
    client.post(f"/api/projects/{p['id']}/processing/start")
    wait_idle(client, p["id"])
    jobs = {j["extra_fields"].get("source_task_id"): j for j in client.get(f"/api/projects/{p['id']}/jobs").json()["items"]}

    chk = jobs["20010"]
    assert chk["title"] == "Relief Valve - Checking"  # superscript "24" not glued to the title
    assert chk["component"] == "Relief Valve"
    assert chk["frequency_type"] == "running_hours" and chk["frequency_value"] == 8000  # bare "8000" header + intro wording
    assert any("500 and 1000 hours" in c for c in chk["extra_fields"]["schedule_comments"])  # comment legend resolved

    ovh = jobs["20020"]
    assert ovh["frequency_type"] == "condition"  # "B" = based on observations

    assert "20040" in jobs, "rows on the schedule's continuation page must be extracted"
    na = jobs.get("20030")
    assert na is not None, "a task with Doc ID N/A is still a job"
    assert na["procedure_status"] == "not_found" and na["description_status"] == "not_available"

    d_chk = client.get(f"/api/jobs/{chk['id']}").json()
    m = d_chk["matches"][0]
    assert d_chk["procedure_status"] == "verified" and m["selected"]
    assert (m["start_page"], m["end_page"]) == (4, 4), "checking task uses the Checking section only"
    assert "check the valve for leaks" in d_chk["long"]["content"] and "45 Nm" not in d_chk["long"]["content"]
    assert "ACME Engines" not in d_chk["long"]["content"] and "Work Card" not in d_chk["long"]["content"]

    d_ovh = client.get(f"/api/jobs/{ovh['id']}").json()
    m = d_ovh["matches"][0]
    assert (m["start_page"], m["end_page"]) == (5, 5) and "45 Nm" in d_ovh["long"]["content"]
    assert "Remove screws\nRemove the screws" in d_ovh["long"]["content"]  # two-column step: title then text, nothing inserted


def test_descriptions_contain_only_manual_text(client, processed) -> None:  # noqa: ANN001
    """Strict: no description text that is not printed in the manual."""
    import re

    def compact(t: str) -> str:
        return re.sub(r"[\s\-­]+", "", t)

    page_cache: dict[tuple[str, int], str] = {}
    checked = 0
    for row in processed["all_jobs"]:
        job = client.get(f"/api/jobs/{row['id']}").json()
        long, short = job["long"], job["short"]
        if not long or not long["content"]:
            continue
        m = next((m for m in job["matches"] if m["id"] == long["procedure_match_id"]), None)
        assert m is not None
        source = ""
        for no in range(m["start_page"], m["end_page"] + 1):
            key = (m["document_id"], no)
            if key not in page_cache:
                page_cache[key] = client.get(f"/api/documents/{m['document_id']}/pages/{no}/text").json()["text"]
            source += page_cache[key]
        src = compact(source)
        for word in long["content"].split():
            assert compact(word) in src, f"{job['code']}: long description word {word!r} is not in the manual"
        norm_long = " ".join(long["content"].split())
        for line in (short["content"] if short else "").splitlines():
            assert " ".join(line.split()) in norm_long, f"{job['code']}: short line not copied from the long description: {line!r}"
        assert "[Figure" not in long["content"] and "Steps:" not in (short or {}).get("content", "")
        checked += 1
    assert checked >= 5


def test_pms_template_exports(client, processed, tmp_path) -> None:  # noqa: ANN001
    import openpyxl

    pid = processed["project"]["id"]
    for kind in ("long", "short"):
        out = tmp_path / f"ocr_{kind}.xlsx"
        r = client.post(f"/api/projects/{pid}/export/pms", json={"template": "ocr", "description": kind, "file_path": str(out)})
        assert r.status_code == 200, r.text
        wb = openpyxl.load_workbook(out)
        assert wb.sheetnames == ["OCR Template"]
        rows = list(wb["OCR Template"].iter_rows(values_only=True))
        assert list(rows[0]) == ["Work ID", "job Title", "Work Code", "Work Code 2", "Period In hours", "Calender Periods",
                                 "Calender unit of measurement", "Critical", "Job Description", "Class item"]  # fmt: skip
        by_title = {row[1]: row for row in rows[1:]}
        assert [row[0] for row in rows[1:]] == list(range(1, len(rows)))
        fv = next(v for t, v in by_title.items() if "overhaul" in t.lower() and "fuel" in t.lower())
        assert fv[4] == 3000 and fv[3] == "40-41" and fv[8] and fv[9] == "MainEngineFuelValve"
        sa = next(v for t, v in by_title.items() if "starting air" in t.lower())
        assert sa[4] == 0 and sa[5] == 6 and sa[6] == "Months"

        out = tmp_path / f"cap_{kind}.xlsx"
        r = client.post(f"/api/projects/{pid}/export/pms", json={"template": "capital", "description": kind, "file_path": str(out)})
        assert r.status_code == 200, r.text
        wb = openpyxl.load_workbook(out)
        assert wb.sheetnames == ["works", "tasks", "Documents"]
        works = list(wb["works"].iter_rows(values_only=True))
        assert list(works[0]) == ["MasterComponentId", "Equipment", "WorkID", "JobTitle", "Period In Hours", "Calendar Period",
                                  "Calendar Unit of Measure", "Based On Observation"]  # fmt: skip
        titles = {w[3] for w in works[1:]}
        assert {"GENERAL OVERHAUL", "GENERAL CHECKING"} <= titles
        assert any(w[6] == "M" and w[5] == 6 and w[7] == "No" for w in works[1:])
        tasks = list(wb["tasks"].iter_rows(values_only=True))
        assert list(tasks[0]) == ["WorkID", "JOB Description"] and len(tasks) > 1
        ids = {w[2] for w in works[1:]}
        assert {t[0] for t in tasks[1:]} <= ids
        documents = list(wb["Documents"].iter_rows(values_only=True))
        assert list(documents[0]) == ["WorkID", "Equipment", "Attachment Path", "Manual Name", "Page No"]
        assert any(d[3] == "main_engine_manual" and d[4] == "40-41" for d in documents[1:])
