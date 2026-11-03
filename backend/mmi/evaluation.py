"""Evaluation harness: compare a processed project against manually verified ground truth.

    python -m mmi.evaluation --data-dir <dir> --project <project_id> --truth truth.json [--out report.json]

Ground truth format (one entry per real maintenance job; build it by hand from the manuals):

    {"jobs": [{
        "title": "Fuel valve overhaul",            # required
        "document": "main_engine_manual.pdf",      # schedule manual (filename)
        "schedule_page": 4,
        "frequency_type": "operating_hours",       # optional
        "frequency_value": 3000,                   # optional (null when not specified)
        "procedure": {"document": "main_engine_manual.pdf", "start_page": 40, "end_page": 41},  # or null
        "long_description_contains": ["180 Nm", "WARNING"]   # optional fidelity probes
    }]}

Metrics: job detection precision/recall, frequency accuracy, procedure retrieval
recall@1 / recall@5, exact boundary accuracy, long-description probe recall,
short-description validation pass rate, and page throughput. Only measured
values are reported; metrics without ground truth are reported as null.
"""

from __future__ import annotations

import argparse
import json
import sys
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from sqlalchemy import func, select


def _norm(s: str) -> str:
    return " ".join("".join(c.lower() if c.isalnum() else " " for c in s).split())


def _sim(a: str, b: str) -> float:
    return SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _job_sim(job, title: str) -> float:  # noqa: ANN001
    """Best match over title-only and component+title forms (titles may already include the component)."""
    return max(_sim(job.title, title), _sim(f"{job.component} {job.title}", title), _sim(f"{job.component} {job.original_title}", title))


def evaluate(project_id: str, truth: dict[str, Any], threshold: float = 0.72) -> dict[str, Any]:
    from .db.models import Document, Job, JobDescription, Page, ProcedureMatch, ProcessingRun
    from .db.session import session_scope

    with session_scope() as s:
        docs = {d.id: d.filename for d in s.scalars(select(Document).where(Document.project_id == project_id))}
        jobs = s.scalars(select(Job).where(Job.project_id == project_id)).all()
        matches: dict[str, list[ProcedureMatch]] = {}
        for m in s.scalars(select(ProcedureMatch).join(Job).where(Job.project_id == project_id).order_by(ProcedureMatch.rank)):
            matches.setdefault(m.job_id, []).append(m)
        longs = {d.job_id: d for d in s.scalars(select(JobDescription).join(Job).where(Job.project_id == project_id, JobDescription.kind == "long", JobDescription.is_current.is_(True)))}
        shorts = {d.job_id: d for d in s.scalars(select(JobDescription).join(Job).where(Job.project_id == project_id, JobDescription.kind == "short", JobDescription.is_current.is_(True)))}
        pages = s.scalar(select(func.count()).select_from(Page).where(Page.document_id.in_(list(docs)))) or 0
        runs = s.scalars(select(ProcessingRun).where(ProcessingRun.project_id == project_id, ProcessingRun.finished_at.is_not(None))).all()

        # Machine output is evaluated (not reviewer corrections) so the numbers describe the pipeline.
        extracted = list(jobs)
        used: set[str] = set()
        rows = []
        for t in truth["jobs"]:
            cands = [j for j in extracted if j.id not in used and (not t.get("document") or docs.get(j.document_id) == t["document"])]
            best = max(cands, key=lambda j: _job_sim(j, t["title"]), default=None)
            score = _job_sim(best, t["title"]) if best else 0.0
            if best is None or score < threshold:
                rows.append({"truth": t["title"], "found": False})
                continue
            used.add(best.id)
            r: dict[str, Any] = {"truth": t["title"], "found": True, "job": best.title, "similarity": round(score, 3)}
            if "frequency_type" in t:
                r["frequency_ok"] = best.frequency_type == t["frequency_type"] and best.frequency_value == t.get("frequency_value")
            proc = t.get("procedure")
            if proc is not None:
                ms = [m for m in matches.get(best.id, []) if m.status != "rejected"]

                def hit(m: ProcedureMatch) -> bool:
                    return docs.get(m.document_id) == proc["document"] and m.start_page <= proc["start_page"] <= m.end_page

                r["proc_at1"] = bool(ms) and hit(ms[0])
                r["proc_at5"] = any(hit(m) for m in ms[:5])
                r["boundary_exact"] = bool(ms) and hit(ms[0]) and ms[0].start_page == proc["start_page"] and ms[0].end_page == proc["end_page"]
                r["auto_verified"] = best.procedure_status == "verified"
                r["auto_verified_correct"] = r["auto_verified"] and r["proc_at1"]
            probes = t.get("long_description_contains") or []
            if probes:
                text = longs[best.id].content if best.id in longs else ""
                r["probe_recall"] = sum(p in text for p in probes) / len(probes)
            if best.id in shorts:
                r["short_valid"] = not (shorts[best.id].validation or {}).get("issues")
            rows.append(r)

    found = [r for r in rows if r["found"]]

    def rate(key: str) -> float | None:
        vals = [r[key] for r in found if key in r]
        return round(sum(vals) / len(vals), 4) if vals else None

    verified = [r for r in found if r.get("auto_verified")]
    seconds = sum((r.finished_at - r.started_at).total_seconds() for r in runs)
    return {
        "truth_jobs": len(truth["jobs"]),
        "extracted_jobs": len(extracted),
        "job_recall": round(len(found) / len(truth["jobs"]), 4) if truth["jobs"] else None,
        "job_precision": round(len(found) / len(extracted), 4) if extracted else None,
        "frequency_accuracy": rate("frequency_ok"),
        "procedure_recall_at_1": rate("proc_at1"),
        "procedure_recall_at_5": rate("proc_at5"),
        "boundary_exact_accuracy": rate("boundary_exact"),
        "auto_verified_precision": round(sum(r["auto_verified_correct"] for r in verified) / len(verified), 4) if verified else None,
        "long_description_probe_recall": rate("probe_recall"),
        "short_description_validation_pass": rate("short_valid"),
        "pages": pages,
        "pages_per_minute": round(pages / (seconds / 60), 1) if seconds else None,
        "details": rows,
        "note": "Precision counts every extracted job not matched to ground truth as a false positive; "
        "ensure the ground truth lists all real jobs in the evaluated manuals.",
    }


def main() -> None:
    ap = argparse.ArgumentParser(prog="mmi.evaluation")
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--project", required=True)
    ap.add_argument("--truth", required=True)
    ap.add_argument("--out")
    a = ap.parse_args()
    from .config import set_paths
    from .db.session import init_engine

    paths = set_paths(Path(a.data_dir))
    init_engine(paths.db, paths.backups)
    report = evaluate(a.project, json.loads(Path(a.truth).read_text(encoding="utf-8")))
    text = json.dumps(report, indent=2, default=str)
    if a.out:
        Path(a.out).write_text(text, encoding="utf-8")
    summary = {k: v for k, v in report.items() if k != "details"}
    json.dump(summary, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
