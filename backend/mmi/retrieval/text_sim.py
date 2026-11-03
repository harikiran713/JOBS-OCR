"""Token-level similarity tuned for maintenance vocabulary.

Action synonyms collapse to a canonical token (``ACT:inspect``) so that
"Check bearing clearance" matches "Clearance Check" and "Pressure test"
matches "Pressure Testing".
"""

from __future__ import annotations

import re
from functools import lru_cache

from ..extraction.classify import ACTIONS

STOPWORDS = {
    "the", "and", "for", "with", "from", "into", "onto", "all", "any", "its", "his", "her", "are", "was", "were",
    "this", "that", "these", "those", "out", "off", "per", "via", "etc", "unit", "every", "each", "after", "before",
    "of", "to", "in", "on", "at", "by", "or", "an", "a", "as", "be", "is", "if", "it", "no", "not",
    "procedure", "instruction", "instructions", "chapter", "section", "page", "see", "maintenance", "work",
}  # fmt: skip

_ACTION_WORD: dict[str, str] = {}
for _canon, _words in ACTIONS.items():
    for _w in _words:
        if " " not in _w:
            _ACTION_WORD.setdefault(_w, _canon)
# Related actions that commonly describe the same procedure.
ACTION_FAMILY = {
    "inspect": {"inspect", "measure", "test"},
    "measure": {"measure", "inspect"},
    "test": {"test", "inspect"},
    "overhaul": {"overhaul", "repair", "replace"},
    "repair": {"repair", "overhaul"},
    "replace": {"replace", "overhaul"},
    "clean": {"clean", "service"},
    "service": {"service", "clean", "inspect", "lubricate"},
    "lubricate": {"lubricate", "service"},
    "adjust": {"adjust", "calibrate"},
    "calibrate": {"calibrate", "adjust", "test"},
}

_WORD = re.compile(r"[A-Za-z][A-Za-z\-]{1,}|\d+(?:\.\d+)*")


@lru_cache(maxsize=20000)
def _stem(w: str) -> str:
    w = w.lower().strip("-")
    if w in _ACTION_WORD:
        return "ACT:" + _ACTION_WORD[w]
    for suf in ("ings", "ing", "ers", "es", "s"):
        if len(w) > len(suf) + 3 and w.endswith(suf):
            base = w[: -len(suf)]
            if base in _ACTION_WORD:
                return "ACT:" + _ACTION_WORD[base]
            if suf in ("es", "s"):
                return base if suf == "s" else w[:-1]
            return base
    return w


def tokens(text: str) -> list[str]:
    out = []
    for m in _WORD.finditer(text or ""):
        w = m.group(0)
        if w.lower() in STOPWORDS or (len(w) < 3 and not w[0].isdigit()):
            continue
        out.append(_stem(w))
    return out


def content_tokens(text: str) -> set[str]:
    return {t for t in tokens(text) if not t.startswith("ACT:") and not t[0].isdigit()}


def action_tokens(text: str) -> set[str]:
    return {t[4:] for t in tokens(text) if t.startswith("ACT:")}


def coverage(needles: set[str], haystack: set[str]) -> float:
    if not needles:
        return 0.0
    return len(needles & haystack) / len(needles)


def action_match(job_action: str, other_actions: set[str]) -> float:
    if not job_action:
        return 0.5
    if not other_actions:
        return 0.5  # heading names a component without an action, e.g. "Fuel Valve"
    if job_action in other_actions:
        return 1.0
    if ACTION_FAMILY.get(job_action, set()) & other_actions:
        return 0.7
    return 0.0


def normalise_ref(ref: str) -> str:
    """'Procedure 909-1.2' -> '909-1.2' ; used to match cross-references against headings."""
    m = re.search(r"[A-Z]{0,4}[-\s]?\d[\w.\-/]*", ref or "", re.I)
    return m.group(0).strip().lower() if m else ""


def fts_terms(text: str) -> list[str]:
    """Safe FTS5 tokens (alphanumeric only, quoted by the caller)."""
    return [w.lower() for w in re.findall(r"[A-Za-z]{3,}|\d{2,}", text or "") if w.lower() not in STOPWORDS]
