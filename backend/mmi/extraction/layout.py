"""Layout helpers shared by digital-text and OCR pipelines.

Lines are dicts: ``{"t": text, "b": [x0, y0, x1, y1], "s": size, "f": bold}``
with coordinates normalised to 0..1 of the page width/height so evidence can be
highlighted at any render scale.
"""

from __future__ import annotations

import re
from collections import Counter

SECTION_NO = re.compile(r"^\s*((?:\d{1,3}[.\-]){0,5}\d{1,3}[a-z]?)\.?\s+(?=\S)")
STEP_MARKER = re.compile(r"^\s*(?:(?:step\s+)?\d{1,2}[.)]|\(\d{1,2}\)|[a-z][.)]|\([a-z]\)|[•\-–▪●◦]\s)\s*", re.I)
CAPTION = re.compile(r"^\s*(?:fig(?:ure)?|table|tab|abb(?:ildung)?|diagram|sketch)\.?\s*[A-Z]?\d", re.I)
# Bold safety callouts ("WARNING: ...") look like headings but are part of the procedure body.
SAFETY_CALLOUT = re.compile(
    r"^\s*(?:⚠\s*)?(?:(?i:DANGER|WARNING|CAUTION|ATTENTION|NOTICE|IMPORTANT|NOTE|INFO)\b\s*[:!.\-]|"
    r"(?:DANGER|WARNING|CAUTION|ATTENTION|NOTICE|INFO|ALWAYS|NEVER|DO NOT|MUST)\b)"
)
_CONTINUED = re.compile(r"\(?\s*(?:cont(?:inued|'d|\.)|fortsetzung)\s*\)?", re.I)


def body_font_size(lines: list[dict]) -> float:
    weights: Counter[float] = Counter()
    body = [ln for ln in lines if not is_running_margin(ln)] or lines
    for ln in body:
        weights[round(float(ln.get("s") or 0), 1)] += len(ln.get("t", ""))
    if not weights:
        return 0.0
    return weights.most_common(1)[0][0]


def is_running_margin(line: dict) -> bool:
    """Running header/footer: marked by the extractor (document code / page counter rows), or the outer 6% band."""
    if line.get("hdr"):
        return True
    cy = (line["b"][1] + line["b"][3]) / 2
    return cy < 0.06 or cy > 0.94


def detect_headings(lines: list[dict]) -> list[dict]:
    """Heuristic heading detection from font size, weight and numbering.

    Returns ``[{"t", "y", "s", "b", "sec", "level"}]`` sorted top-to-bottom.
    ``level`` is the numbering depth when available (1 = "4", 2 = "4.2", ...),
    otherwise a size rank so callers can compare heading strength.
    """
    if not lines:
        return []
    body = body_font_size(lines) or 10.0
    out: list[dict] = []
    for ln in lines:
        text = ln.get("t", "").strip()
        if len(text) < 3 or len(text) > 140 or not re.search(r"[A-Za-z]{3}", text):
            continue
        if is_running_margin(ln) or CAPTION.match(text) or SAFETY_CALLOUT.match(text):
            continue
        if text[0].islower():
            continue  # continuation of a wrapped line, not a heading
        size = float(ln.get("s") or body)
        bold = bool(ln.get("f"))
        sec_m = SECTION_NO.match(text)
        sec = sec_m.group(1) if sec_m else ""
        bigger = size >= body * 1.12
        ends_sentence = text.endswith((".", ";", ",")) and not re.search(r"\b\d+\.$", text)
        upper = text.isupper() and len(text) > 5
        is_heading = False
        if bigger and not ends_sentence:
            is_heading = True
        elif bold and not ends_sentence and len(text) <= 100 and not STEP_MARKER.match(text):
            is_heading = True
        elif sec and "." in sec and (bold or upper) and not ends_sentence:
            is_heading = True
        elif upper and not ends_sentence and len(text) <= 80 and len(text.split()) >= 2:
            is_heading = True
        if not is_heading:
            continue
        if sec:
            level = sec.count(".") + sec.count("-") + 1
        else:
            ratio = size / body if body else 1.0
            level = 1 if ratio >= 1.6 else 2 if ratio >= 1.3 else 3 if ratio >= 1.12 else 4
        out.append(
            {
                "t": text,
                "y": round(ln["b"][1], 4),
                "s": round(size, 1),
                "b": bold,
                "sec": sec,
                "level": level,
                "cont": bool(_CONTINUED.search(text)),
            }
        )
    out.sort(key=lambda h: h["y"])
    return out


def group_rows(lines: list[dict], tol: float = 0.006) -> list[list[dict]]:
    """Cluster lines into visual rows by vertical centre (used for table-like layouts and OCR)."""
    items = sorted(lines, key=lambda ln: ((ln["b"][1] + ln["b"][3]) / 2, ln["b"][0]))
    rows: list[list[dict]] = []
    centres: list[float] = []
    for ln in items:
        cy = (ln["b"][1] + ln["b"][3]) / 2
        h = max(ln["b"][3] - ln["b"][1], 0.004)
        if rows and abs(cy - centres[-1]) <= max(tol, h * 0.45):
            rows[-1].append(ln)
            centres[-1] = (centres[-1] * (len(rows[-1]) - 1) + cy) / len(rows[-1])
        else:
            rows.append([ln])
            centres.append(cy)
    for row in rows:
        row.sort(key=lambda ln: ln["b"][0])
    return rows


def lines_to_text(lines: list[dict]) -> str:
    return "\n".join(" ".join(ln["t"] for ln in row) for row in group_rows(lines))
