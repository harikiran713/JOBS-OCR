"""Excel export with configurable column templates.

* Uses openpyxl's write-only mode so very large checklists stream to disk.
* Source references are exported only when they are real: procedure pages are
  written only for verified/confirmed matches. Unconfirmed candidates are
  never presented as the procedure source.
* Excel limits are respected: cells are capped at 32,767 characters (with a
  visible truncation marker and an export warning) and sheets roll over
  before 1,048,576 rows.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.cell import WriteOnlyCell
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

from .. import __version__

CELL_LIMIT = 32767
MAX_ROWS = 1_000_000

Row = dict[str, Any]


def _bool(v: Any) -> str:
    return "Yes" if v else "No"


FIELDS: dict[str, tuple[str, Callable[[Row], Any]]] = {
    "job_code": ("Job ID", lambda r: r["code"]),
    "source_task_id": ("Maker task ID", lambda r: (r.get("extra_fields") or {}).get("source_task_id", "")),
    "system": ("System", lambda r: (r.get("extra_fields") or {}).get("system", "")),
    "schedule_name": ("Schedule", lambda r: (r.get("extra_fields") or {}).get("schedule", "")),
    "schedule_comments": ("Schedule comments", lambda r: "\n".join((r.get("extra_fields") or {}).get("schedule_comments", []))),
    "title": ("Job title", lambda r: r["title"]),
    "original_title": ("Original job title", lambda r: r["original_title"]),
    "equipment": ("Equipment", lambda r: r["equipment"]),
    "component": ("Component", lambda r: r["component"]),
    "action": ("Action", lambda r: r["action"]),
    "frequency_original": ("Original frequency text", lambda r: r["frequency_original"]),
    "frequency_label": ("Normalized frequency", lambda r: r["frequency_label"]),
    "frequency_value": ("Frequency value", lambda r: r["frequency_value"]),
    "frequency_unit": ("Frequency unit", lambda r: r["frequency_unit"]),
    "frequency_type": ("Frequency type", lambda r: r["frequency_type_label"]),
    "frequency_uncertain": ("Frequency uncertain", lambda r: _bool(r["frequency_uncertain"])),
    "long_description": ("Long description", lambda r: r.get("long") or ""),
    "short_description": ("Short description", lambda r: r.get("short") or ""),
    "schedule_document": ("Schedule source manual", lambda r: r["schedule_document"]),
    "schedule_page": ("Schedule page", lambda r: r["schedule_page_no"]),
    "procedure_document": ("Procedure source manual", lambda r: r["proc_doc"]),
    "procedure_start_page": ("Procedure start page", lambda r: r["proc_start"]),
    "procedure_end_page": ("Procedure end page", lambda r: r["proc_end"]),
    "procedure_status": ("Procedure match status", lambda r: r["procedure_status"]),
    "verification_status": ("Verification status", lambda r: r["verification"]),
    "review_status": ("Review status", lambda r: r["review_status"]),
    "description_method": ("Short description method", lambda r: r.get("short_method") or ""),
}

DESCRIPTION_FIELDS = {"long_description", "short_description"}

BUILTIN_TEMPLATES: list[dict[str, Any]] = [
    {
        "id": "builtin-standard",
        "name": "Standard – all checklist fields",
        "description": "Every checklist field with long and short descriptions in the same sheet.",
        "description_layout": "same_sheet",
        "columns": [{"field": k, "header": v[0]} for k, v in FIELDS.items() if k not in ("action", "frequency_value", "frequency_unit", "description_method")],
    },
    {
        "id": "builtin-pms",
        "name": "Generic PMS import",
        "description": "Compact column set commonly needed by planned-maintenance systems. Adjust headers to your system.",
        "description_layout": "same_sheet",
        "columns": [
            {"field": "job_code", "header": "Job Code"},
            {"field": "source_task_id", "header": "Maker Task ID"},
            {"field": "equipment", "header": "Equipment"},
            {"field": "component", "header": "Component"},
            {"field": "title", "header": "Job Title"},
            {"field": "frequency_value", "header": "Interval"},
            {"field": "frequency_unit", "header": "Interval Unit"},
            {"field": "frequency_type", "header": "Interval Type"},
            {"field": "short_description", "header": "Job Description"},
            {"field": "long_description", "header": "Job Procedure"},
            {"field": "procedure_document", "header": "Reference Manual"},
            {"field": "procedure_start_page", "header": "Ref. Page From"},
            {"field": "procedure_end_page", "header": "Ref. Page To"},
        ],
    },
    {
        "id": "builtin-review",
        "name": "Review workbook – descriptions on separate sheets",
        "description": "Checklist sheet plus separate long and short description sheets, for offline review.",
        "description_layout": "separate_sheets",
        "columns": [{"field": k, "header": v[0]} for k, v in FIELDS.items() if k not in ("action", "description_method")],
    },
]


@dataclass
class ExportResult:
    path: str
    rows: int
    sheets: list[str]
    warnings: list[str] = field(default_factory=list)


def resolve(row: Row, field_key: str, custom: dict[str, str]) -> Any:
    if field_key.startswith("extra."):
        return (row.get("extra_fields") or {}).get(field_key[6:], "")
    if field_key in FIELDS:
        return FIELDS[field_key][1](row)
    raise KeyError(field_key)


def _cap(value: Any, warnings: list[str], code: str, col: str) -> Any:
    if isinstance(value, str) and len(value) > CELL_LIMIT:
        marker = "\n[TRUNCATED – exceeds Excel's 32,767 character cell limit; see source pages]"
        warnings.append(f"{code}: '{col}' truncated from {len(value):,} characters to fit Excel's cell limit.")
        return value[: CELL_LIMIT - len(marker)] + marker
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@")):
        return "'" + value  # never let source text be interpreted as a formula
    return value


def build_preview(rows: list[Row], columns: list[dict], layout: str, limit: int = 50) -> dict[str, Any]:
    cols = [c for c in columns if layout == "same_sheet" or c["field"] not in DESCRIPTION_FIELDS]
    warnings: list[str] = []
    out = []
    for r in rows[:limit]:
        vals = []
        for c in cols:
            v = resolve(r, c["field"], {})
            v = _cap(v, warnings, r["code"], c["header"])
            vals.append(v if not isinstance(v, str) or len(v) < 400 else v[:400] + "…")
        out.append(vals)
    for r in rows:
        if r["procedure_status"] not in ("verified", "confirmed") and any(c["field"].startswith("procedure_") for c in columns):
            warnings.append(f"{r['code']}: procedure not confirmed – procedure source columns left empty.")
    return {"headers": [c["header"] for c in cols], "rows": out, "total_rows": len(rows), "warnings": warnings[:200]}


def write_workbook(
    path: Path, rows: list[Row], columns: list[dict], layout: str, meta: dict[str, Any], unresolved: list[Row] | None = None
) -> ExportResult:
    wb = Workbook(write_only=True)
    warnings: list[str] = []
    sheets: list[str] = []
    header_font = Font(bold=True, color="FFFFFF")
    header_fill = PatternFill("solid", fgColor="1F3A5F")
    wrap = Alignment(wrap_text=True, vertical="top")

    def header_cells(ws, headers: list[str]) -> list[WriteOnlyCell]:  # noqa: ANN001
        cells = []
        for h in headers:
            c = WriteOnlyCell(ws, value=h)
            c.font, c.fill = header_font, header_fill
            cells.append(c)
        return cells

    def add_sheet(title: str, headers: list[str], widths: list[int], data: list[list[Any]], wrap_cols: set[int]) -> None:
        for part in range(0, max(len(data), 1), MAX_ROWS):
            name = title if part == 0 else f"{title} ({part // MAX_ROWS + 1})"
            ws = wb.create_sheet(name[:31])
            ws.freeze_panes = "A2"
            for i, w in enumerate(widths, 1):
                ws.column_dimensions[get_column_letter(i)].width = w
            ws.append(header_cells(ws, headers))
            for vals in data[part : part + MAX_ROWS]:
                row_cells = []
                for i, v in enumerate(vals):
                    c = WriteOnlyCell(ws, value=v)
                    if i in wrap_cols:
                        c.alignment = wrap
                    row_cells.append(c)
                ws.append(row_cells)
            ws.auto_filter.ref = f"A1:{get_column_letter(len(headers))}{min(len(data) - part, MAX_ROWS) + 1}"
            sheets.append(name[:31])

    main_cols = [c for c in columns if layout == "same_sheet" or c["field"] not in DESCRIPTION_FIELDS]
    data = []
    for r in rows:
        data.append([_cap(resolve(r, c["field"], {}), warnings, r["code"], c["header"]) for c in main_cols])
    widths = [60 if c["field"] in DESCRIPTION_FIELDS else 34 if c["field"] in ("title", "original_title") else 18 for c in main_cols]
    wrap_cols = {i for i, c in enumerate(main_cols) if c["field"] in DESCRIPTION_FIELDS or c["field"] == "title"}
    add_sheet("Maintenance checklist", [c["header"] for c in main_cols], widths, data, wrap_cols)

    if layout == "separate_sheets":
        for key, title in (("long_description", "Long descriptions"), ("short_description", "Short descriptions")):
            if not any(c["field"] == key for c in columns):
                continue
            hdr = next(c["header"] for c in columns if c["field"] == key)
            d = [[r["code"], r["title"], _cap(resolve(r, key, {}), warnings, r["code"], hdr)] for r in rows]
            add_sheet(title, ["Job ID", "Job title", hdr], [12, 40, 100], d, {2})

    if unresolved:
        d = [[r["code"], r["title"], r["equipment"], r["frequency_original"], r["procedure_status"], r["description_status"],
              r["review_status"], ", ".join(r.get("attention", []))] for r in unresolved]  # fmt: skip
        add_sheet("Unresolved", ["Job ID", "Job title", "Equipment", "Original frequency", "Procedure status",
                                 "Description status", "Review status", "Open issues"], [12, 40, 24, 22, 20, 20, 16, 50], d, set())  # fmt: skip

    info = [
        ["Project", meta.get("project", "")], ["Vessel", meta.get("vessel", "")], ["Exported at", datetime.now().strftime("%Y-%m-%d %H:%M")],
        ["Scope", meta.get("scope", "")], ["Template", meta.get("template", "")], ["Jobs exported", len(rows)],
        ["Application", f"Marine Maintenance Intelligence {__version__}"],
        ["Note", "Procedure source pages are given only for verified or reviewer-confirmed procedures."],
    ] + [["Warning", w] for w in warnings[:500]]  # fmt: skip
    add_sheet("Export info", ["Item", "Value"], [22, 110], info, {1})

    tmp = path.with_name(path.name + ".part")
    wb.save(tmp)
    tmp.replace(path)
    return ExportResult(str(path), len(rows), sheets, warnings)
