"""Rule-based maintenance job extraction from schedule pages.

Three strategies, applied in order of structural reliability:

1. **Column tables** – a task column plus an interval column (with optional
   component/reference columns). Column roles are inferred from header text
   *and* cell statistics, so unlabelled tables still work.
2. **Interval matrices** – interval headers across the top (``Daily | 500 h |
   3000 h``) with marks (``X``, ``●``, ``✓``) in cells. Each mark yields one
   job with the column's interval; unusual marks are flagged for review.
3. **Line layouts** – rows of text containing an interval expression, or
   bullet lists under an interval heading ("Every 3 months:").

A row only becomes a job when the *context* supports it (schedule-like page or
table, an interval column, maintenance wording) — keywords alone never decide.
Part-number rows, headers and general text are rejected with a reason.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, replace

from .classify import PART_NUMBER, SCHEDULE_TITLE, detect_action
from .frequency import Frequency, find_frequencies, parse_frequency
from .layout import STEP_MARKER, group_rows

TASK_H = re.compile(r"descr|task|work|job|maintenance|operation|action|activit|what|check(?:s|ing)?\b|procedure\s*title", re.I)
FREQ_H = re.compile(r"interval|frequen|period|hours|\bhrs?\b|every|time|schedule|freq|cycle|when|running", re.I)
COMP_H = re.compile(r"component|\bitem\b|\bpart\b|equipment|\bunit\b|system|assembly|object|location|machine|group|\btools?\b", re.I)
REF_H = re.compile(r"\bref|procedure|page|chapter|section|instruction|\bsee\b|card|\bdoc|manual|\bno\.?$|drawing", re.I)
ID_H = re.compile(r"^\s*(?:task|job|item|work)\s*(?:id|no\.?|number|code)\s*$|^\s*id\s*$", re.I)
SECTION_ROW = re.compile(r"^\s*\d{1,3}\s*[-–—.]?\s*[A-Z][A-Za-z ,/&()-]{2,}$")
NO_DOCUMENT = re.compile(r"^\s*(?:n/?a|none|-+|—)\s*$", re.I)
MAKER_DOCUMENT = re.compile(r"manufactur|maker|supplier|vendor|sub-?supplier", re.I)
MARKS = {"x", "●", "•", "○", "◯", "✓", "✔", "√", "*", "■", "□", "◆", "▲", "o", "+", "■"}
MARK_ACTIONS = {"i": "inspect", "c": "clean", "r": "replace", "o": "overhaul", "l": "lubricate", "t": "test",
                "a": "adjust", "m": "measure", "s": "service", "x": ""}  # fmt: skip
CROSS_REF = re.compile(
    r"(?:\bsee\b|\bref\.?|refer\s+to|\bprocedure|\bchapter|\bsection|\bpage|\bp\.|work\s*card|\bcard|\bsheet)\s*"
    r"(?:no\.?|nr\.?|#)?\s*:?\s*\[?([A-Z]{0,4}[-\s]?\d[\w.\-/]*)",
    re.I,
)
_CONNECTORS = re.compile(
    r"^(?:[\s\-–—:|,;/()]|every|each|at|after|or|and|whichever\s+comes\s+first|whichever\s+is\s+(?:first|earlier))+|"
    r"(?:[\s\-–—:|,;/()]|every|each|at|after|or|and|whichever\s+comes\s+first|whichever\s+is\s+(?:first|earlier))+$",
    re.I,
)


@dataclass
class JobCandidate:
    title: str
    original_title: str
    component: str
    equipment: str
    action: str
    frequency: Frequency
    page_no: int
    source_text: str
    bbox: list[float] = field(default_factory=list)
    cross_reference: str = ""
    method: str = ""
    confidence: float = 0.0
    notes: list[str] = field(default_factory=list)
    # Configurable/extra fields: source task ID, system group, schedule name, schedule comments.
    extra: dict = field(default_factory=dict)

    @property
    def needs_review(self) -> bool:
        return self.confidence < 0.65 or self.frequency.uncertain

    def key(self, doc_sha: str) -> str:
        if self.extra.get("source_task_id"):
            # The schedule's own task ID is the most stable identity (survives layout changes).
            raw = f"{doc_sha}|task:{self.extra['source_task_id']}|{self.frequency.original.lower().strip()}"
        else:
            norm = re.sub(r"\W+", " ", self.original_title.lower()).strip()
            raw = f"{doc_sha}|{self.page_no}|{norm}|{self.frequency.original.lower().strip()}|{self.component.lower()}"
        return hashlib.sha256(raw.encode()).hexdigest()[:40]


@dataclass
class PageContext:
    page_no: int
    text: str
    lines: list[dict]
    headings: list[dict]
    scores: dict
    equipment: str
    tables: list[dict] = field(default_factory=list)
    kind: str = ""
    # Text of the document's schedule introduction (e.g. "intervals are running hours").
    doc_context: str = ""
    # Schedule comment legend: {"24": "Check new/overhauled parts after 500, 1000 and 1500 hours"}
    legend: dict = field(default_factory=dict)
    # Schedule name from the page's margin labels, e.g. "50ME/ME-C, Major Overhaul".
    schedule_name: str = ""

    @property
    def schedule_score(self) -> float:
        return float(self.scores.get("schedule", 0.0))

    @property
    def has_schedule_title(self) -> bool:
        return bool(SCHEDULE_TITLE.search(" ".join(h["t"] for h in self.headings[:6])) or SCHEDULE_TITLE.search(self.text[:600]))


def _clean(s: str) -> str:
    s = re.sub(r"\s+", " ", s or "").strip()
    s = STEP_MARKER.sub("", s, count=1) if STEP_MARKER.match(s) and len(s) > 4 else s
    return s.strip(" .:;-–—|")


def _strip_connectors(s: str) -> str:
    prev = None
    while prev != s:
        prev = s
        s = _CONNECTORS.sub("", s).strip()
    return s


def make_title(task: str, component: str) -> str:
    task = _clean(task)
    component = _clean(component)
    if not task:
        return component
    if component and len(task.split()) <= 4:
        comp_tokens = {t for t in re.findall(r"[a-z]{3,}", component.lower())}
        task_tokens = set(re.findall(r"[a-z]{3,}", task.lower()))
        if comp_tokens and not comp_tokens & task_tokens:
            task = f"{component} – {task[0].lower() + task[1:] if not task[:2].isupper() else task}"
    return task[0].upper() + task[1:] if task else task


def equipment_from_page(ctx: PageContext) -> str:
    for h in ctx.headings[:4]:
        if SCHEDULE_TITLE.search(h["t"]):
            rest = SCHEDULE_TITLE.sub("", h["t"])
            rest = re.sub(r"^\s*[\d.]+\s*", "", rest)
            rest = re.sub(r"^[^\w(]+|[^\w)]+$", "", rest)
            rest = re.sub(r"^(?:for|of|the)\s+|\s+(?:for|of|the)$", "", rest, flags=re.I).strip(" -–—:,")
            # Only a real equipment name (e.g. "Auxiliary Boiler Burner"), not a type code like "50ME/ME-C".
            # A schedule name ("Major Overhaul", "Duties During Operation") is not equipment.
            if detect_action(rest) or re.search(r"\b(?:duties|operation|routine|introduction|general)\b", rest, re.I):
                continue
            if ctx.equipment and len(re.findall(r"[A-Za-z]{3,}", rest)) < 2:
                continue
            if len(rest) >= 4 and re.search(r"[A-Za-z]{3}", rest):
                return rest
    return ctx.equipment


def _reject_reason(title: str) -> str | None:
    if len(re.findall(r"[A-Za-z]", title)) < 4:
        return "too little text"
    tokens = title.split()
    pn = len(PART_NUMBER.findall(title))
    if tokens and pn / len(tokens) > 0.4:
        return "looks like a part-number row"
    if (TASK_H.fullmatch(title.strip()) or FREQ_H.fullmatch(title.strip())) and len(tokens) <= 2:
        return "header text"
    if re.fullmatch(r"(?:page|seite)\s*\d+.*", title, re.I):
        return "page label"
    if re.match(r"^\s*(?:see|refer\s+to)\b", title, re.I):
        return "only a cross-reference"
    return None


def _confidence(freq: Frequency, action: str, structured: bool, ctx: PageContext) -> float:
    c = 0.45
    if freq.found:
        c += 0.2
    if action:
        c += 0.15
    if structured:
        c += 0.1
    if ctx.has_schedule_title:
        c += 0.1
    if freq.uncertain:
        c -= 0.15
    return round(max(0.05, min(c, 0.98)), 3)


def _cross_ref(*texts: str) -> str:
    for t in texts:
        m = CROSS_REF.search(t or "")
        if m:
            return m.group(0).strip()
    return ""


# ---------------------------------------------------------------------------
# Strategy 1 & 2: tables
# ---------------------------------------------------------------------------


def _is_mark(cell: str) -> bool:
    c = cell.strip().lower()
    return bool(c) and (c in MARKS or (len(c) == 1 and c in MARK_ACTIONS) or bool(re.fullmatch(r"[xo●•✓✔√*]{1,2}", c)))


def _numeric(cell: str) -> bool:
    return bool(re.fullmatch(r"[\d.\-/\s]+[a-z]?", cell.strip(), re.I))


def _header_frequency(cell: str, basis_ctx: str, numeric_header_count: int) -> Frequency | None:
    """Interval represented by a schedule column header.

    Besides normal expressions ("Daily", "500 h", "3M") this accepts:
    * bare numbers ("8000") when the header row has several of them — an hours matrix.
      The hour basis comes from the schedule's own wording; otherwise it stays unqualified.
    * "B" — "based on observations" (condition-based), the usual meaning in such matrices.
    """
    cell = (cell or "").strip()
    if not cell:
        return None
    f = parse_frequency(cell, basis_ctx, cell=True)
    if f.found and not (len(cell) == 1 and cell.upper() == "B"):
        return f
    hours_matrix = numeric_header_count >= 3 or (numeric_header_count >= 2 and re.search(r"(?:running|operating|engine)\s+hours", basis_ctx, re.I))
    if hours_matrix and re.fullmatch(r"\d{3,6}", cell.replace(",", "").replace(".", "")):
        f = parse_frequency(f"{cell.replace(',', '').replace('.', '')} h", basis_ctx)
        f.original = cell
        f.notes.append("Column header is a number of hours.")
        return f
    if cell.upper() == "B" and re.search(r"based\s+on\s+observation", basis_ctx, re.I):
        return Frequency(cell, None, "", "condition", event="based on observations",
                         notes=["'B' = based on observations (see schedule introduction)."])  # fmt: skip
    return None


def _numeric_headers(row: list[str]) -> int:
    return sum(1 for c in row if re.fullmatch(r"\d{3,6}", (c or "").replace(",", "").replace(".", "").strip()))


def _header_index(rows: list[list[str]], ctx_text: str) -> int | None:
    best, best_score = None, 0
    for i, row in enumerate(rows[:4]):
        score = 0
        nums = _numeric_headers(row)
        for cell in row:
            if not cell:
                continue
            if len(cell) > 60:
                score -= 1  # sentence-length text is a data row, whatever words it contains
                continue
            if TASK_H.search(cell) or COMP_H.search(cell) or REF_H.search(cell) or FREQ_H.search(cell) or ID_H.search(cell):
                score += 1
            if len(cell) <= 20 and _header_frequency(cell, ctx_text, nums) is not None:
                score += 1
        if score > best_score:
            best, best_score = i, score
    return best if best_score >= 2 else None


def _split_component(task: str) -> tuple[str, str]:
    """'Crankcase Relief Valve - Checking' -> ('Crankcase Relief Valve', 'Checking')."""
    m = re.match(r"^(.{3,}?)\s+[-–—]\s+(.{3,})$", task)
    if m and detect_action(m.group(2)):
        return m.group(1).strip(), m.group(2).strip()
    return "", task


def _copy_freq(f: Frequency) -> Frequency:
    return Frequency(**{**f.__dict__, "notes": list(f.notes), "alternatives": list(f.alternatives)})


def jobs_from_table(
    table: dict, ctx: PageContext, interval_above: tuple[Frequency, str, str] | None = None
) -> tuple[list[JobCandidate], list[str]]:
    """``interval_above``: (interval, task wording, line) from a heading such as "Inspection of tools every 12 months"."""
    rows: list[list[str]] = table["rows"]
    row_boxes: list[list[float]] = table.get("row_boxes") or []
    row_refs: list[list[list[str]]] = table.get("refs") or []
    notes: list[str] = []
    ncols = max(len(r) for r in rows)
    rows = [r + [""] * (ncols - len(r)) for r in rows]
    basis_ctx = ctx.text[:1500] + " " + ctx.doc_context
    hdr = _header_index(rows, basis_ctx)
    header = rows[hdr] if hdr is not None else [""] * ncols
    start = (hdr + 1) if hdr is not None else 0
    body = rows[start:]
    if not body:
        return [], notes
    header_ctx = " ".join(header)
    basis_ctx = header_ctx + " " + basis_ctx
    numeric_headers = _numeric_headers(header)
    header_freq = [_header_frequency(h, basis_ctx, numeric_headers) for h in header]

    stats = []
    for c in range(ncols):
        cells = [r[c] for r in body if r[c]]
        n = max(len(cells), 1)
        stats.append(
            {
                "fill": len(cells) / len(body),
                "freq": sum(parse_frequency(x, basis_ctx, cell=True).found for x in cells if len(x) < 60) / n,
                "num": sum(_numeric(x) for x in cells) / n,
                "len": sum(len(x) for x in cells) / n,
                "action": sum(bool(detect_action(x)) for x in cells) / n,
                "mark": sum(_is_mark(x) for x in cells) / n,
            }
        )

    # --- Interval matrix ---
    matrix_cols = [c for c in range(ncols) if header_freq[c] is not None and stats[c]["mark"] >= 0.5]
    roles_identified = hdr is not None
    id_col = next((c for c in range(ncols) if ID_H.search(header[c]) and stats[c]["num"] >= 0.5), None)

    def pick_task(exclude: set[int]) -> int | None:
        named = [c for c in range(ncols) if c not in exclude and TASK_H.search(header[c]) and stats[c]["num"] < 0.5]
        if named:
            return named[0]
        cands = [c for c in range(ncols) if c not in exclude and stats[c]["num"] < 0.5 and stats[c]["mark"] < 0.5]
        if not cands:
            return None
        return max(cands, key=lambda c: stats[c]["action"] * 2 + min(stats[c]["len"], 60) / 30 + stats[c]["fill"])

    freq_col: int | None = None
    if not matrix_cols:
        named_freq = [c for c in range(ncols) if FREQ_H.search(header[c]) and stats[c]["freq"] >= 0.3]
        if named_freq:
            freq_col = named_freq[0]
        else:
            best = max(range(ncols), key=lambda c: stats[c]["freq"])
            if stats[best]["freq"] >= 0.5 and stats[best]["len"] < 50:
                freq_col = best
        if freq_col is None:
            # Without an interval column this is only a schedule table if the page clearly is one,
            # or the line above the table states the interval for all its rows.
            if interval_above is not None:
                notes.append(f"Table interval taken from the line above it: '{interval_above[2]}'.")
            elif not (ctx.has_schedule_title and hdr is not None and any(TASK_H.search(h) for h in header)):
                return [], ["Table skipped: no interval column and no schedule context."]
            else:
                notes.append("Schedule table without an interval column; intervals recorded as not specified.")

    exclude = set(matrix_cols) | ({freq_col} if freq_col is not None else set()) | ({id_col} if id_col is not None else set())
    task_col = pick_task(exclude)
    if task_col is None:
        return [], ["Table skipped: no task/description column."]
    exclude.add(task_col)
    comp_col = next(
        (c for c in range(ncols) if c not in exclude and COMP_H.search(header[c]) and stats[c]["num"] < 0.6), None
    )
    if comp_col is not None:
        exclude.add(comp_col)
    ref_col = next((c for c in range(ncols) if c not in exclude and REF_H.search(header[c])), None)

    equipment = equipment_from_page(ctx)
    out: list[JobCandidate] = []
    group = ""
    last_comp = ""
    for i, row in enumerate(body):
        bbox = row_boxes[start + i] if start + i < len(row_boxes) else table.get("bbox", [])
        nonempty = [c for c in row if c]
        task = row[task_col]
        # Group header rows: a single filled cell spanning the table, no interval.
        if len(nonempty) == 1 and (freq_col is None or not row[freq_col]) and not any(row[c] for c in matrix_cols):
            group = _clean(nonempty[0])
            continue
        if not task:
            continue
        if (
            out and freq_col is not None and not row[freq_col].strip() and task_col > 0
            and not any(row[c].strip() for c in range(task_col)) and out[-1].method == "table"
        ):  # fmt: skip
            # Sub-step of the row above (its number/interval cells are blank): part of that job, not a new one.
            prev = out[-1]
            prev.source_text = f"{prev.source_text}\n{' | '.join(c for c in row if c)}"
            if bbox and prev.bbox:
                prev.bbox = [min(prev.bbox[0], bbox[0]), min(prev.bbox[1], bbox[1]), max(prev.bbox[2], bbox[2]), max(prev.bbox[3], bbox[3])]
            continue
        comp = row[comp_col] if comp_col is not None else ""
        if comp_col is not None:
            comp = comp or last_comp
            last_comp = comp
        split_comp, _action_part = _split_component(task)
        component = _clean(comp) or split_comp or group
        reason = _reject_reason(task)
        if reason:
            notes.append(f"Row '{task[:60]}' skipped: {reason}.")
            continue
        ref = row[ref_col] if ref_col is not None else ""
        source_text = " | ".join(c for c in row if c)
        xref = _cross_ref(ref, task) or (ref.strip() if ref.strip() else "")
        extra: dict = {}
        if id_col is not None and row[id_col].strip():
            extra["source_task_id"] = row[id_col].strip()
        if group and group != component:
            extra["system"] = group
        if ctx.schedule_name:
            extra["schedule"] = ctx.schedule_name
        refs_here = row_refs[start + i][task_col] if start + i < len(row_refs) and task_col < len(row_refs[start + i]) else []
        comment_notes = []
        for r in refs_here:
            if r in ctx.legend:
                comment_notes.append(f"Schedule comment {r}: {ctx.legend[r]}")
            else:
                comment_notes.append(f"Schedule comment {r} (text not found in the schedule legend).")
        if comment_notes:
            extra["schedule_comments"] = comment_notes
        if NO_DOCUMENT.match(ref or ""):
            comment_notes = comment_notes + ["Schedule states that no instruction document is available (N/A)."]
        elif MAKER_DOCUMENT.search(ref or ""):
            comment_notes = comment_notes + ["Schedule refers to the manufacturer's documentation for this task."]

        if matrix_cols:
            for c in matrix_cols:
                mark = row[c].strip()
                if not mark:
                    continue
                hf = header_freq[c]
                freq = Frequency(**{**hf.__dict__, "notes": list(hf.notes), "alternatives": list(hf.alternatives)})
                freq.original = header[c]
                cand_notes = list(freq.notes) + comment_notes
                action = detect_action(task)
                m = mark.lower()
                if m in MARK_ACTIONS and MARK_ACTIONS[m]:
                    action = action or MARK_ACTIONS[m]
                    cand_notes.append(f"Matrix mark '{mark}' interpreted as '{MARK_ACTIONS[m]}' — verify against the table legend.")
                    freq.uncertain = True
                elif not _is_mark(mark):
                    cand_notes.append(f"Unrecognised matrix cell '{mark}'.")
                    freq.uncertain = True
                out.append(
                    JobCandidate(
                        title=make_title(task, component), original_title=task, component=component,
                        equipment=equipment, action=action, frequency=freq, page_no=ctx.page_no,
                        source_text=f"{source_text}  [column: {header[c]}]", bbox=bbox, cross_reference=xref,
                        method="table_matrix", confidence=_confidence(freq, action, True, ctx), notes=cand_notes,
                        extra=dict(extra),
                    )
                )  # fmt: skip
            if not any(row[c].strip() for c in matrix_cols):
                notes.append(f"Row '{task[:60]}' has no interval marked in the schedule.")
                out.append(
                    JobCandidate(
                        title=make_title(task, component), original_title=task, component=component,
                        equipment=equipment, action=detect_action(task), frequency=Frequency(original="", notes=["No interval marked in the schedule."]),
                        page_no=ctx.page_no, source_text=source_text, bbox=bbox, cross_reference=xref,
                        method="table_matrix", confidence=0.3, notes=comment_notes, extra=dict(extra),
                    )
                )  # fmt: skip
            continue

        ftxt = row[freq_col] if freq_col is not None else ""
        freq = parse_frequency(ftxt, basis_ctx, cell=True) if ftxt else Frequency(original="", notes=["No interval given in source."])
        if not ftxt and freq_col is not None:
            # Interval may be embedded in the task text (e.g. "Clean filter every 500 h").
            inline = find_frequencies(task, basis_ctx)
            if inline:
                freq = parse_frequency(inline[0].original, basis_ctx)
        title = make_title(task, component)
        action = detect_action(task)
        confidence = _confidence(freq, action, roles_identified, ctx)
        if freq_col is None and interval_above is not None:
            own = find_frequencies(source_text, basis_ctx)
            what = interval_above[1]
            if own:
                sentence = next((s for s in re.split(r"(?<=\.)\s+", task) if own[0].original in s), "")
                what = sentence.rstrip(".") if 0 < len(sentence) <= 120 else what
                freq = _copy_freq(own[0])
                freq.notes.append("Interval stated in the table row.")
                if len(own) > 1:
                    freq.alternatives = [f.to_dict() for f in own[1:]]
                    freq.uncertain = True
                    freq.notes.append("Several interval expressions in the row; review which applies.")
            else:
                freq = _copy_freq(interval_above[0])
                freq.notes.append(f"Interval from the line above the table: '{interval_above[2]}'.")
            if component and what:
                # The row text is a list of criteria, not a title: name the job by the row's object.
                title = f"{_clean(component)} – {what[0].lower() + what[1:] if not what[:2].isupper() else what}"
            action = action or detect_action(interval_above[1])
            confidence = min(_confidence(freq, action, roles_identified, ctx), 0.6)
        out.append(
            JobCandidate(
                title=title, original_title=task, component=component, equipment=equipment,
                action=action, frequency=freq, page_no=ctx.page_no, source_text=source_text, bbox=bbox,
                cross_reference=xref, method="table",
                confidence=confidence, notes=list(freq.notes) + comment_notes,
                extra=dict(extra),
            )
        )  # fmt: skip
    return out, notes


# ---------------------------------------------------------------------------
# Strategy 3: line layouts
# ---------------------------------------------------------------------------


def jobs_from_lines(ctx: PageContext) -> tuple[list[JobCandidate], list[str]]:
    notes: list[str] = []
    if ctx.schedule_score < 0.4 and not ctx.has_schedule_title:
        return [], notes
    if ctx.kind and ctx.kind != "schedule":
        # Prose that merely mentions intervals (descriptions, introductions) is not a schedule.
        return [], notes
    equipment = equipment_from_page(ctx)
    heading_texts = {h["t"] for h in ctx.headings}
    out: list[JobCandidate] = []
    current_freq: Frequency | None = None
    group = ""
    for row in group_rows(ctx.lines):
        text = re.sub(r"\s+", " ", " ".join(ln["t"] for ln in row)).strip()
        if not text:
            continue
        bbox = [min(ln["b"][0] for ln in row), min(ln["b"][1] for ln in row),
                max(ln["b"][2] for ln in row), max(ln["b"][3] for ln in row)]  # fmt: skip
        if bbox[1] < 0.05 or bbox[3] > 0.95:
            continue  # running header/footer
        lm = _LEGEND_ROW.match(text)
        if lm and lm.group(1) in ctx.legend:
            continue  # a numbered comment of the schedule legend, not a task
        freqs = find_frequencies(text, ctx.text[:1500])
        is_heading = text in heading_texts or any(ln["t"] in heading_texts for ln in row)
        if freqs:
            remainder = text
            for f in sorted(freqs, key=lambda f: f.span[0], reverse=True):
                remainder = remainder[: f.span[0]] + " " + remainder[f.span[1] :]
            remainder = _strip_connectors(_clean(remainder))
            if len(re.findall(r"[A-Za-z]{2,}", remainder)) < 2:
                if len(text) <= 40:  # a list header such as "Every month:"
                    current_freq = parse_frequency(text.strip(" :;-–"), ctx.text[:1500])
                continue
            n_words = len(remainder.split())
            if len(text) > 140 or n_words > 14 or (remainder.rstrip().endswith(".") and n_words > 8):
                notes.append(f"Line '{text[:60]}' skipped: reads as a sentence, not a schedule entry.")
                continue
            if SCHEDULE_TITLE.search(remainder):
                continue
            reason = _reject_reason(remainder)
            if reason:
                notes.append(f"Line '{text[:60]}' skipped: {reason}.")
                continue
            span_text = text[freqs[0].span[0] : freqs[-1].span[1]]
            freq = parse_frequency(span_text, ctx.text[:1500])
            action = detect_action(remainder)
            if not action and (ctx.schedule_score < 0.6 or n_words > 6):
                notes.append(f"Line '{text[:60]}' skipped: interval without a maintenance task.")
                continue
            out.append(
                JobCandidate(
                    title=make_title(remainder, group if not action else ""), original_title=remainder,
                    component=group, equipment=equipment, action=action, frequency=freq, page_no=ctx.page_no,
                    source_text=text, bbox=bbox, cross_reference=_cross_ref(text), method="text_line",
                    confidence=_confidence(freq, action, False, ctx), notes=list(freq.notes),
                )
            )  # fmt: skip
            continue
        if is_heading and not STEP_MARKER.match(text):
            # A new heading ends any "Every N months:" list and scopes following rows.
            current_freq = None
            if not SCHEDULE_TITLE.search(text):
                group = _clean(text)
            continue
        if current_freq and (STEP_MARKER.match(text) or detect_action(text)):
            title = _clean(text)
            words = len(title.split())
            sentence = title.rstrip().endswith(".") and words > 10
            if 2 <= words <= 20 and not sentence and not _reject_reason(title):
                freq = Frequency(**{**current_freq.__dict__, "alternatives": list(current_freq.alternatives), "notes": list(current_freq.notes)})
                action = detect_action(title)
                out.append(
                    JobCandidate(
                        title=make_title(title, ""), original_title=title, component=group, equipment=equipment,
                        action=action, frequency=freq, page_no=ctx.page_no, source_text=f"{current_freq.original}: {text}",
                        bbox=bbox, cross_reference=_cross_ref(text), method="text_list",
                        confidence=_confidence(freq, action, False, ctx), notes=list(freq.notes),
                    )
                )  # fmt: skip
    return out, notes


_LEGEND_ROW = re.compile(r"^(\d{1,3})\s+([^\d\s].{4,})$")


def parse_legend(lines: list[dict]) -> dict[str, str]:
    """Numbered comment legend printed with a schedule ("24  Check new/overhauled parts after ...")."""
    legend: dict[str, str] = {}
    last = None
    for row in group_rows(lines):
        text = re.sub(r"\s+", " ", " ".join(ln["t"] for ln in row)).strip()
        m = _LEGEND_ROW.match(text)
        if m and not re.match(r"^\d{4,}", text):
            last = m.group(1)
            legend[last] = m.group(2).strip()
        elif last and text and not re.match(r"^\d", text) and len(row) == 1 and row[0]["b"][0] > 0.12:
            legend[last] += " " + text  # wrapped comment text
        else:
            last = None
    return legend


def _inside(line: dict, bbox: list[float], pad: float = 0.004) -> bool:
    cx = (line["b"][0] + line["b"][2]) / 2
    cy = (line["b"][1] + line["b"][3]) / 2
    return bbox[0] - pad <= cx <= bbox[2] + pad and bbox[1] - pad <= cy <= bbox[3] + pad


def _heading_above(ctx: PageContext, y: float) -> str:
    above = [h for h in ctx.headings if h["y"] < y and not SCHEDULE_TITLE.search(h["t"])]
    return _clean(above[-1]["t"]) if above else ""


def _interval_above(ctx: PageContext, top: float) -> tuple[Frequency, str, str] | None:
    """Interval stated in the line just above a table ("Inspection of tools prior to usage and every 12 months")."""
    above = [ln for ln in ctx.lines if top - 0.06 <= ln["b"][3] <= top + 0.003]
    if not above:
        return None
    nearest = max(ln["b"][3] for ln in above)
    for ln in [ln for ln in above if ln["b"][3] >= nearest - 0.01]:  # only the row directly above the table
        text = re.sub(r"\s+", " ", ln["t"]).strip()
        freqs = find_frequencies(text, ctx.text[:1500])
        if not freqs or len(text) > 120:
            continue
        rest = text
        for f in sorted(freqs, key=lambda f: f.span[0], reverse=True):
            rest = rest[: f.span[0]] + " " + rest[f.span[1] :]
        return _copy_freq(freqs[0]), _strip_connectors(_clean(rest)), text
    return None


def extract_jobs_from_page(ctx: PageContext) -> tuple[list[JobCandidate], list[str]]:
    jobs: list[JobCandidate] = []
    notes: list[str] = []
    interval_lines: set[str] = set()
    for table in ctx.tables:
        bbox = table.get("bbox") or []
        above = _interval_above(ctx, bbox[1]) if bbox else None
        found, n = jobs_from_table(table, ctx, above)
        if above and found and any(x.startswith("Table interval taken from the line above") for x in n):
            interval_lines.add(above[2])
        # A heading directly above a table (e.g. "Turbocharger") scopes its rows when no
        # component column exists.
        heading = _heading_above(ctx, bbox[1]) if bbox else ""
        if heading:
            for j in found:
                if not j.component:
                    j.component = heading
                    j.notes.append(f"Component taken from the heading above the table: '{heading}'.")
        jobs.extend(found)
        notes.extend(n)
    # Text outside detected tables can still hold schedule entries (prose/bullet layouts).
    boxes = [t["bbox"] for t in ctx.tables if t.get("bbox")]
    outside = [ln for ln in ctx.lines if not any(_inside(ln, b) for b in boxes)] if boxes else ctx.lines
    # A line that gave a table its interval belongs to that table's jobs.
    outside = [ln for ln in outside if re.sub(r"\s+", " ", ln["t"]).strip() not in interval_lines]
    if outside:
        line_ctx = replace(ctx, lines=outside, tables=[])  # keep page type, legend and schedule context
        found, n = jobs_from_lines(line_ctx)
        jobs.extend(found)
        notes.extend(n)
    # De-duplicate identical rows on the same page (common with repeated table headers).
    seen: set[tuple[str, str, str]] = set()
    unique: list[JobCandidate] = []
    for j in jobs:
        k = (j.original_title.lower(), j.frequency.original.lower(), j.component.lower())
        if k in seen:
            continue
        seen.add(k)
        unique.append(j)
    return unique, notes
