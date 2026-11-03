"""Page-type classification from structural and lexical signals.

Scores are features for downstream stages, not final decisions. A page can
score highly as both ``schedule`` and ``procedure`` (e.g. a procedure card
with an interval header); job extraction and procedure ranking consume the raw
scores rather than only the winning label.
"""

from __future__ import annotations

import re

from .frequency import find_frequencies
from .layout import STEP_MARKER

ACTIONS: dict[str, list[str]] = {
    "inspect": ["inspect", "inspection", "check", "checking", "examine", "examination", "verify", "survey", "visual"],
    "overhaul": ["overhaul", "overhauling", "recondition", "reconditioning", "dismantle", "dismantling", "disassemble"],
    "replace": ["replace", "replacement", "renew", "renewal", "exchange", "change", "changing"],
    "lubricate": ["lubricate", "lubrication", "grease", "greasing", "oil change", "re-grease", "regrease"],
    "clean": ["clean", "cleaning", "flush", "flushing", "wash", "washing", "descale", "descaling", "purge", "blow"],
    "test": ["test", "testing", "function test", "trial", "operational check", "simulate"],
    "adjust": ["adjust", "adjustment", "set", "setting", "align", "alignment"],
    "calibrate": ["calibrate", "calibration"],
    "repair": ["repair", "repairing", "grind", "grinding", "lap", "lapping", "recondition"],
    "service": ["service", "servicing", "maintain", "maintenance"],
    "measure": ["measure", "measurement", "measuring", "record", "reading", "gauge", "clearance"],
    "tighten": ["tighten", "retighten", "re-tighten", "torque"],
    "drain": ["drain", "draining", "vent", "venting", "sample", "sampling"],
}

_ACTION_RX = {
    k: re.compile(r"\b(?:" + "|".join(re.escape(w) for w in sorted(v, key=len, reverse=True)) + r")\b", re.I)
    for k, v in ACTIONS.items()
}

SCHEDULE_TITLE = re.compile(
    r"maintenance\s+(?:schedule|programme|program|plan|intervals?|table|chart|overview|list)|planned\s+maintenance|"
    r"(?:inspection|service|overhaul|maintenance)\s+intervals?|periodic(?:al)?\s+(?:maintenance|inspection|checks?)|"
    r"checking\s+and\s+maintenance|time\s+between\s+overhauls?|preventive\s+maintenance|routine\s+maintenance|"
    r"maintenance\s+(?:and|&)\s+inspection\s+(?:schedule|table)|wartungsplan",
    re.I,
)
IMPERATIVE = re.compile(
    r"^\s*(?:(?:\d{1,2}[.)]|[a-z][.)]|[•\-–▪●])\s*)?(?:remove|install|refit|fit|mount|dismantle|disassemble|assemble|"
    r"reassemble|disconnect|connect|reconnect|loosen|tighten|unscrew|screw|lift|lower|insert|withdraw|pull|push|turn|"
    r"rotate|open|close|drain|fill|refill|clean|inspect|check|measure|record|replace|renew|apply|lubricate|grease|"
    r"ensure|make\s+sure|verify|adjust|set|test|start|stop|shut|isolate|lock|release|place|position|align|attach|"
    r"detach|secure|mark|note|wipe|blow|flush|vent|torque|grind|lap|polish|use|take|put|hold|support|switch|press)\b",
    re.I,
)
SAFETY = re.compile(r"\b(?:WARNING|CAUTION|DANGER|ATTENTION|NOTICE|SAFETY|IMPORTANT|NOTE)\b")
UNITS = re.compile(r"\b\d+(?:[.,]\d+)?\s*(?:N\s?·?m|Nm|kNm|kgf?m|mm|µm|um|bar|MPa|kPa|psi|°C|deg|rpm|ft[\s-]?lbs?|in[\s-]?lbs?|l|kg)\b")
PART_NUMBER = re.compile(r"\b(?:[A-Z]{1,4}[-\s]?\d{4,}[\w\-./]*|\d{3,}[-./]\d{2,}[\w\-./]*|\d{6,})\b")
PARTS_HEADER = re.compile(
    r"\b(?:spare\s+parts?|parts?\s+list|part\s+no\.?|part\s+number|item\s+no\.?|qty|quantity|plate\s+no|drawing\s+no|"
    r"ordering|designation|ident\.?\s*no|pcs)\b",
    re.I,
)
TOC_LINE = re.compile(r"(?:\.{3,}|…{2,}|\s{3,})\s*\d{1,4}\s*$")
TOC_TITLE = re.compile(r"\b(?:table\s+of\s+contents|contents|index)\b", re.I)


def detect_action(text: str) -> str:
    """Return the first maintenance action found (by position), or ''."""
    best = ("", 10**9)
    for action, rx in _ACTION_RX.items():
        m = rx.search(text)
        if m and m.start() < best[1]:
            best = (action, m.start())
    return best[0]


def actions_in(text: str) -> set[str]:
    return {a for a, rx in _ACTION_RX.items() if rx.search(text)}


_DOC_REFERENCE = re.compile(r"(?:work\s*card|description|data|plate|drawing|see|refer\w*|chapter|section|doc(?:ument)?)\W{0,3}\s*$", re.I)


def _part_number_line(t: str) -> bool:
    """A part number, not a reference to another document ("see work card 0545-0100")."""
    for m in PART_NUMBER.finditer(t):
        if not _DOC_REFERENCE.search(t[: m.start()]):
            return True
    return False


def classify_page(text: str, lines: list[dict], headings: list[dict]) -> tuple[str, dict]:
    from .layout import is_running_margin

    # Running headers/footers (document codes, page counters) say nothing about the page type.
    lines = [ln for ln in lines if not is_running_margin(ln)] if lines else lines
    raw_lines = [ln["t"] for ln in lines] if lines else text.splitlines()
    n_lines = max(len(raw_lines), 1)
    heading_text = " ".join(h["t"] for h in headings[:6])
    top_text = " ".join(raw_lines[:12])

    freq_lines = sum(1 for t in raw_lines if find_frequencies(t))
    action_lines = sum(1 for t in raw_lines if detect_action(t))
    step_lines = sum(1 for t in raw_lines if STEP_MARKER.match(t) and len(t) > 12)
    imperative_lines = sum(1 for t in raw_lines if IMPERATIVE.match(t))
    safety_hits = len(SAFETY.findall(text))
    unit_hits = len(UNITS.findall(text))
    partno_lines = sum(1 for t in raw_lines if _part_number_line(t))
    toc_lines = sum(1 for t in raw_lines if TOC_LINE.search(t))

    schedule_title = 1.0 if SCHEDULE_TITLE.search(heading_text) or SCHEDULE_TITLE.search(top_text) else 0.0
    schedule = 0.4 * schedule_title + 0.4 * min(freq_lines / 6, 1.0) + 0.2 * min(action_lines / 6, 1.0)
    procedure = (
        0.3 * min(step_lines / 5, 1.0)
        + 0.25 * min(imperative_lines / 6, 1.0)
        + 0.15 * min(safety_hits / 2, 1.0)
        + 0.15 * min(unit_hits / 3, 1.0)
        + 0.15 * (1.0 if any(detect_action(h["t"]) for h in headings) else 0.0)
    )
    parts = 0.5 * (1.0 if PARTS_HEADER.search(text) else 0.0) + 0.5 * min(partno_lines / max(6, n_lines * 0.3), 1.0)
    toc = min(toc_lines / 6, 1.0) * (1.0 if toc_lines / n_lines > 0.3 else 0.6)
    if TOC_TITLE.search(top_text):
        toc = min(1.0, toc + 0.3)

    # A parts list with interval-free part numbers should not look like a schedule.
    if parts > 0.6 and freq_lines < 2:
        schedule *= 0.5
    if toc > 0.6:
        schedule *= 0.4
        procedure *= 0.4

    scores = {
        "schedule": round(schedule, 3),
        "procedure": round(procedure, 3),
        "parts_list": round(parts, 3),
        "toc": round(toc, 3),
        "freq_lines": freq_lines,
        "step_lines": step_lines,
    }
    if len(text.strip()) < 5:
        return "blank", scores
    ranked = sorted(
        [("schedule", schedule), ("procedure", procedure), ("parts_list", parts), ("toc", toc)],
        key=lambda kv: kv[1],
        reverse=True,
    )
    kind, best = ranked[0]
    if best < 0.4:
        kind = "general"
    return kind, scores
