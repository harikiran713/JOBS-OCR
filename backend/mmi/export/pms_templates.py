"""Fixed-layout PMS import workbooks: "OCR Template" and "Capital Template".

Interval columns come from the stored, normalised frequency of each job (no
model is asked to re-interpret titles). Descriptions are written exactly as
stored — they are copied from the manual — with only layout changes (one row
per point, or joined into one line where the template requires it).
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

HOUR_TYPES = {"operating_hours", "running_hours", "engine_hours", "hours_unqualified"}
UNIT_WORD = {"day": "Days", "week": "Weeks", "month": "Months", "year": "Years"}
UNIT_CODE = {"day": "D", "week": "W", "month": "M", "year": "Y"}
_LIST_START = re.compile(r"^\s*(?:[-•▪●]|\d{1,2}[.)])\s+")
CELL_LIMIT = 32767


def _num(v: float | None) -> int | float | None:
    if v is None:
        return None
    return int(v) if float(v).is_integer() else v


def hours(job: dict) -> int | float | None:
    return _num(job["frequency_value"]) if job["frequency_type"] in HOUR_TYPES else None


def calendar(job: dict) -> tuple[int | float | None, str]:
    if job["frequency_type"] == "calendar" and job["frequency_unit"] in UNIT_WORD:
        return _num(job["frequency_value"]), job["frequency_unit"]
    return None, ""


def category(title: str) -> str:
    t = title.lower()
    if "overhaul" in t:
        return "GENERAL OVERHAUL"
    if "replace" in t:  # replace / replacement
        return "GENERAL REPLACEMENT"
    if "check" in t or "inspect" in t:
        return "GENERAL CHECKING"
    if "clean" in t:
        return "GENERAL CLEANING"
    return "GENERAL MAINTENANCE"


def class_item(equipment: str, component: str) -> str:
    """Equipment name as one word, e.g. 'Main Engine' + 'Fuel Pump' -> 'MainEngineFuelPump'."""
    words: list[str] = []
    for w in re.findall(r"[A-Za-z0-9]+", f"{equipment} {component}"):
        word = w if w[0].isdigit() or (w.isupper() and len(w) <= 4) else w.capitalize()
        if not words or words[-1].lower() != word.lower():
            words.append(word)
    # Drop exact repeats (equipment name often repeats in the component).
    seen, out = set(), []
    for w in words:
        if w.lower() not in seen:
            seen.add(w.lower())
            out.append(w)
    return "".join(out)


def points(text: str) -> list[str]:
    """Split a description into points: a new point starts at a list marker; wrapped lines join their point."""
    out: list[str] = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not out or _LIST_START.match(line):
            out.append(line)
        else:
            out[-1] = f"{out[-1]} {line}"
    return out


def description(job: dict, kind: str) -> str:
    long_t, short_t = (job.get("long") or "").strip(), (job.get("short") or "").strip()
    if kind == "long":
        return long_t or short_t
    if short_t:
        return short_t
    return " ".join(points(long_t))  # no summary: the long description's points on one line


def _cap(v: Any) -> Any:
    if isinstance(v, str):
        if v.startswith(("=", "+", "-", "@")):
            v = "'" + v  # never let manual text be read as a formula
        if len(v) > CELL_LIMIT:
            v = v[: CELL_LIMIT - 40] + "\n[text truncated: Excel cell limit]"
    return v


def _sheet(wb: Workbook, title: str, headers: list[str], rows: list[list[Any]], widths: list[int], wrap: set[int]) -> None:
    ws = wb.create_sheet(title)
    ws.append(headers)
    for c in ws[1]:
        c.font = Font(bold=True, color="FFFFFF")
        c.fill = PatternFill("solid", fgColor="1F3A5F")
    for r in rows:
        ws.append([_cap(v) for v in r])
    for i, w in enumerate(widths, 1):
        ws.column_dimensions[get_column_letter(i)].width = w
    for col in wrap:
        for cell in ws.iter_cols(min_col=col + 1, max_col=col + 1, min_row=2):
            for c in cell:
                c.alignment = Alignment(wrap_text=True, vertical="top")
    ws.freeze_panes = "A2"


def _save(wb: Workbook, path: Path) -> None:
    wb.remove(wb.active)
    tmp = path.with_name(path.name + ".part")
    wb.save(tmp)
    tmp.replace(path)


def write_ocr_template(path: Path, jobs: list[dict], kind: str) -> int:
    rows = []
    for i, j in enumerate(jobs, 1):
        cal_n, cal_u = calendar(j)
        rows.append([
            i, j["title"], "", j.get("pages", ""), hours(j) or 0, cal_n, UNIT_WORD.get(cal_u, ""), "",
            description(j, kind), class_item(j["equipment"], j["component"]),
        ])  # fmt: skip
    wb = Workbook()
    _sheet(
        wb, "OCR Template",
        ["Work ID", "job Title", "Work Code", "Work Code 2", "Period In hours", "Calender Periods",
         "Calender unit of measurement", "Critical", "Job Description", "Class item"],
        rows, [9, 45, 12, 14, 15, 16, 26, 10, 80, 32], {1, 8},
    )  # fmt: skip
    _save(wb, path)
    return len(rows)


def write_capital_template(path: Path, jobs: list[dict], kind: str) -> int:
    works, tasks, docs = [], [], []
    for i, j in enumerate(jobs, 1):
        h = hours(j)
        cal_n, cal_u = calendar(j)
        has_interval = h is not None or cal_n is not None
        works.append(["", "", i, category(j["title"]), h, cal_n, UNIT_CODE.get(cal_u, ""), "No" if has_interval else "yes"])
        text = description(j, kind)
        if kind == "short" or (text and _LIST_START.match(text.lstrip().splitlines()[0])):
            for p in (points(text) if kind == "long" else [ln.strip() for ln in text.splitlines() if ln.strip()]):
                tasks.append([i, p])
        elif text:
            tasks.append([i, text])
        docs.append([i, "", "", j.get("manual_name", ""), j.get("pages", "")])
    wb = Workbook()
    _sheet(wb, "works", ["MasterComponentId", "Equipment", "WorkID", "JobTitle", "Period In Hours", "Calendar Period",
                         "Calendar Unit of Measure", "Based On Observation"], works, [18, 14, 9, 24, 16, 16, 24, 20], set())  # fmt: skip
    _sheet(wb, "tasks", ["WorkID", "JOB Description"], tasks, [9, 110], {1})
    _sheet(wb, "Documents", ["WorkID", "Equipment", "Attachment Path", "Manual Name", "Page No"], docs, [9, 14, 20, 40, 12], set())
    _save(wb, path)
    return len(works)
