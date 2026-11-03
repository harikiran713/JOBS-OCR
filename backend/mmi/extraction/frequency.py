"""Maintenance interval parsing and normalisation.

The parser is deliberately conservative:
* The original wording is always preserved by callers; this module only adds a
  normalised interpretation.
* Hours are never silently treated as calendar or operating hours. If the text
  (or the surrounding table/page context) does not say which, the type is
  ``hours_unqualified`` so a reviewer can see the basis was not stated.
* Nothing is invented: text with no recognisable interval yields
  ``type="unspecified"`` and ``value=None``.
"""

from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field

FREQUENCY_TYPES = {
    "calendar": "Calendar",
    "operating_hours": "Operating hours",
    "running_hours": "Running hours",
    "engine_hours": "Engine hours",
    "hours_unqualified": "Hours (basis not stated)",
    "cycle": "Cycles / starts",
    "event": "Event-based",
    "condition": "Condition-based",
    "unspecified": "Not specified",
}

WORD_NUMBERS = {
    "one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "fifteen": 15, "eighteen": 18,
    "twenty": 20, "twenty-four": 24, "thirty": 30, "forty-eight": 48,
}  # fmt: skip


@dataclass
class Frequency:
    original: str
    value: float | None = None
    unit: str = ""  # hour | day | week | month | year | cycle | ""
    type: str = "unspecified"
    event: str = ""  # e.g. dry_docking, before_start (for event-based intervals)
    uncertain: bool = False
    notes: list[str] = field(default_factory=list)
    alternatives: list[dict] = field(default_factory=list)
    span: tuple[int, int] = (0, 0)

    @property
    def found(self) -> bool:
        return self.type != "unspecified"

    def label(self) -> str:
        """Human-readable normalised form, e.g. '3000 operating hours'."""
        if self.type in ("event", "condition"):
            return self.event.replace("_", " ") if self.event else FREQUENCY_TYPES[self.type]
        if self.value is None:
            return FREQUENCY_TYPES.get(self.type, self.type)
        v = int(self.value) if float(self.value).is_integer() else self.value
        unit = self.unit
        if self.type == "operating_hours":
            unit = "operating hours"
        elif self.type == "running_hours":
            unit = "running hours"
        elif self.type == "engine_hours":
            unit = "engine hours"
        elif self.type == "hours_unqualified":
            unit = "hours"
        elif self.type == "cycle":
            unit = "cycles"
        else:
            unit = unit + ("s" if v != 1 else "")
        return f"{v} {unit}"

    def to_dict(self) -> dict:
        d = asdict(self)
        d.pop("span", None)
        d["label"] = self.label()
        return d


_NUM = r"(\d+(?:\.\d+)?)"
_WORDNUM = "|".join(sorted((re.escape(w) for w in WORD_NUMBERS), key=len, reverse=True))


def _normalise_numbers(text: str) -> str:
    # 3,000 / 3.000 / 3 000 -> 3000 (only exact thousands groups, so 1.5 stays 1.5)
    prev = None
    while prev != text:
        prev = text
        text = re.sub(r"(?<=\d)[,.  ](?=\d{3}(?!\d))", "", text)
    return text


def _num(s: str) -> float:
    s = s.lower()
    if s in WORD_NUMBERS:
        return float(WORD_NUMBERS[s])
    return float(s)


_HOUR_BASIS = [
    (re.compile(r"engine\s*h(ou)?rs?|\behrs?\b", re.I), "engine_hours"),
    (re.compile(r"running\s*h(ou)?rs?|\brh\b|\brhs\b|running", re.I), "running_hours"),
    (re.compile(r"operat\w*\s*h(ou)?rs?|hours?\s+of\s+operation|\boh\b|\bo\.h\.|operating|service\s+hours?|in\s+operation", re.I), "operating_hours"),
]  # fmt: skip


def hour_basis_from_context(context: str | None) -> str | None:
    if not context:
        return None
    for rx, kind in _HOUR_BASIS:
        if rx.search(context):
            return kind
    return None


# --- pattern table --------------------------------------------------------

_CAL_UNITS = {
    "day": "day", "days": "day", "d": "day",
    "week": "week", "weeks": "week", "wk": "week", "wks": "week", "w": "week",
    "month": "month", "months": "month", "mth": "month", "mths": "month", "mo": "month", "mos": "month", "m": "month",
    "year": "year", "years": "year", "yr": "year", "yrs": "year", "y": "year",
}  # fmt: skip

_CAL_WORDS = [
    (r"\bdaily\b|\bevery\s+day\b|\bonce\s+a\s+day\b|\bper\s+day\b", 1, "day"),
    (r"\bweekly\b|\bevery\s+week\b|\bonce\s+a\s+week\b", 1, "week"),
    (r"\bfortnightly\b|\bbi-?weekly\b", 2, "week"),
    (r"\bmonthly\b|\bevery\s+month\b|\bonce\s+a\s+month\b", 1, "month"),
    (r"\bbi-?monthly\b|\bevery\s+other\s+month\b", 2, "month"),
    (r"\bquarterly\b", 3, "month"),
    (r"\bhalf[\s-]?yearly\b|\bsemi[\s-]?annual(?:ly)?\b|\bsix[\s-]?monthly\b|\b6[\s-]?monthly\b|\btwice\s+a\s+year\b", 6, "month"),
    (r"\bannual(?:ly)?\b|\byearly\b|\bevery\s+year\b|\bonce\s+a\s+year\b", 1, "year"),
    (r"\bbiennial(?:ly)?\b|\bevery\s+other\s+year\b", 2, "year"),
]  # fmt: skip

_EVENTS = [
    (r"\b(?:every|each|at|during)?\s*(?:dry[\s-]?dock(?:ing)?s?|docking)\b", "dry_docking"),
    (r"\b(?:at|during)?\s*(?:every|each)?\s*(?:special|class|annual|intermediate|renewal)\s+survey\b", "class_survey"),
    (r"\bbefore\s+(?:each\s+)?(?:start(?:ing|-?up)?|commissioning)(?:\s+(?:of\s+)?(?:the\s+)?(?:engine|unit|machine|pump|plant))?\b", "before_start"),
    (r"\bafter\s+(?:each\s+)?(?:stop(?:ping)?|shut\s*-?down)\b", "after_stop"),
    (r"\b(?:at|on)\s+(?:each|every)\s+(?:major\s+)?overhaul\b|\bat\s+overhaul\b", "at_overhaul"),
    (r"\bafter\s+(?:each|every)\s+(?:voyage|trip)\b|\b(?:on|after)\s+arrival(?:\s+in\s+port)?\b|\bbefore\s+departure\b", "voyage"),
    (r"\bafter\s+running[\s-]?in\b", "after_running_in"),
    (r"\bat\s+(?:each|every)\s+(?:port\s+call|bunkering)\b|\bafter\s+bunkering\b", "port_call"),
]  # fmt: skip

_CONDITIONS = [
    r"\bas\s+(?:required|necessary|needed)\b",
    r"\bwhen\s+(?:required|necessary|needed)\b",
    r"\bif\s+(?:required|necessary|needed)\b",
    r"\bon[\s-]+condition\b",
    r"\bcondition[\s-]+(?:based|monitoring)\b",
    r"\baccording\s+to\s+(?:the\s+)?condition\b",
    # Allow abbreviations such as "diff. press." inside the condition.
    r"\bwhen\s+(?:the\s+)?[\w\s/.-]{1,40}?\s+(?:exceeds?|reaches?|indicates?|is\s+(?:dirty|worn|clogged|blocked|damaged|leaking)|leaks?)\b",
    r"\bif\s+(?:the\s+)?[\w\s/.-]{1,40}?\s+(?:exceeds?|reaches?|is\s+(?:dirty|worn|clogged|blocked|damaged|leaking)|leaks?)\b",
    r"\bin\s+case\s+of\s+(?:abnormal|malfunction|failure|leakage)\w*\b",
]  # fmt: skip

_RX_HOURS = re.compile(
    rf"(?:\b(?:every|each|at|after)\s+)?(?:(?:the\s+)?first\s+)?\b(?P<n>{_NUM}|{_WORDNUM})\s*-?\s*"
    r"(?P<pre>operating|running|engine|service|working)?\s*"
    r"(?P<u>hours?|hrs?\.?|h|rh|oh|ehrs?)\b"
    r"(?P<post>\s+(?:of\s+operation|operating|operation|running|in\s+operation|engine\s+running))?",
    re.I,
)
_RX_CAL = re.compile(
    rf"(?:\b(?:every|each|at|after)\s+)?\b(?P<n>{_NUM}|{_WORDNUM})\s*-?\s*"
    r"(?P<u>days?|weeks?|wks?|months?|mths?|mos?|years?|yrs?)\b",
    re.I,
)
_RX_EVERY_UNIT = re.compile(r"\bevery\s+(?P<u>day|week|month|year)\b", re.I)
_RX_CYCLE = re.compile(
    rf"(?:\b(?:every|each|after)\s+)?\b(?P<n>{_NUM}|{_WORDNUM})\s*(?P<u>cycles?|starts?|operations|strokes|start[\s-]?ups)\b",
    re.I,
)
# Compact schedule codes (only trusted when the whole cell is a code): 3M, 1Y, 500H, 4000RH, 2W, 7D
_RX_CODE = re.compile(r"^\s*(?P<n>\d+(?:\.\d+)?)\s*(?P<u>d|w|m|y|h|rh|oh|eh|hrs?)\s*\.?\s*$", re.I)
_RX_LETTER_CODE = re.compile(r"^\s*(?P<u>d|w|m|q|y)\s*$", re.I)
_RX_WHICHEVER = re.compile(r"whichever\s+(?:comes|occurs|is)\s+(?:first|earlier|sooner)", re.I)


def _hour_type(pre: str | None, unit: str, post: str | None, context_basis: str | None) -> tuple[str, bool, list[str]]:
    words = " ".join(x for x in (pre or "", unit, post or "") if x).lower()
    for rx, kind in _HOUR_BASIS:
        if rx.search(words):
            return kind, False, []
    if context_basis:
        return context_basis, False, [f"Hour basis taken from table/page context ({FREQUENCY_TYPES[context_basis]})."]
    return "hours_unqualified", False, ["Hour basis (calendar vs operating) not stated in source."]


def _collect(text: str, context_basis: str | None) -> list[Frequency]:
    found: list[Frequency] = []

    for m in _RX_HOURS.finditer(text):
        n = _num(m.group("n"))
        ftype, unc, notes = _hour_type(m.group("pre"), m.group("u"), m.group("post"), context_basis)
        f = Frequency(text[m.start() : m.end()].strip(), n, "hour", ftype, uncertain=unc, notes=list(notes), span=m.span())
        if re.search(r"\bfirst\b", m.group(0), re.I):
            f.notes.append("One-time interval after the first hours of operation (running-in).")
            f.event = "after_first_hours"
        found.append(f)

    for m in _RX_CAL.finditer(text):
        n = _num(m.group("n"))
        unit = _CAL_UNITS[m.group("u").lower().rstrip(".")]
        found.append(Frequency(text[m.start() : m.end()].strip(), n, unit, "calendar", span=m.span()))

    for m in _RX_EVERY_UNIT.finditer(text):
        found.append(Frequency(m.group(0), 1.0, m.group("u").lower(), "calendar", span=m.span()))

    for pattern, n, unit in _CAL_WORDS:
        for m in re.finditer(pattern, text, re.I):
            f = Frequency(m.group(0), float(n), unit, "calendar", span=m.span())
            if "twice" in m.group(0).lower():
                f.notes.append("'Twice a year' normalised to every 6 months.")
            found.append(f)

    for m in _RX_CYCLE.finditer(text):
        found.append(Frequency(m.group(0).strip(), _num(m.group("n")), "cycle", "cycle", span=m.span()))

    for pattern, event in _EVENTS:
        for m in re.finditer(pattern, text, re.I):
            if not m.group(0).strip():
                continue
            found.append(Frequency(m.group(0).strip(), None, "", "event", event=event, span=m.span()))

    for pattern in _CONDITIONS:
        for m in re.finditer(pattern, text, re.I):
            found.append(Frequency(m.group(0).strip(), None, "", "condition", event=m.group(0).strip().lower(), span=m.span()))

    # Remove overlapping matches, keeping the longest (most specific) one.
    found.sort(key=lambda f: (f.span[0], -(f.span[1] - f.span[0])))
    result: list[Frequency] = []
    for f in found:
        if result and f.span[0] < result[-1].span[1]:
            if (f.span[1] - f.span[0]) > (result[-1].span[1] - result[-1].span[0]):
                result[-1] = f
            continue
        result.append(f)
    return result


def find_frequencies(text: str, context: str | None = None) -> list[Frequency]:
    """All interval expressions in ``text`` with character spans (on the original text where possible)."""
    if not text:
        return []
    # Rejoin words hyphenated across a line break ("ar- rival" -> "arrival").
    normalised = _normalise_numbers(re.sub(r"(?<=[a-z])-\s+(?=[a-z])", "", text))
    basis = hour_basis_from_context(context)
    freqs = _collect(normalised, basis)
    if normalised != text:
        # Spans refer to the normalised text; map back approximately by searching originals.
        for f in freqs:
            f.span = _map_span(text, normalised, f.span)
            f.original = text[f.span[0] : f.span[1]].strip() or f.original
    return freqs


def _map_span(original: str, normalised: str, span: tuple[int, int]) -> tuple[int, int]:
    # Build index map from normalised -> original by walking both strings.
    mapping: list[int] = []
    i = j = 0
    while j < len(normalised) and i < len(original):
        if original[i] == normalised[j]:
            mapping.append(i)
            i += 1
            j += 1
        else:
            i += 1  # character removed during normalisation
    mapping.append(len(original))
    start = mapping[min(span[0], len(mapping) - 1)]
    end = mapping[min(span[1], len(mapping) - 1)] if span[1] < len(mapping) else len(original)
    return start, end


def parse_frequency(text: str, context: str | None = None, cell: bool = False) -> Frequency:
    """Parse a single frequency expression.

    ``cell=True`` means the text came from a dedicated interval column/header,
    which allows compact codes such as ``3M`` or ``500H`` to be interpreted.
    """
    original = (text or "").strip()
    if not original:
        return Frequency(original="", notes=["No interval given in source."])

    basis = hour_basis_from_context(context)
    if cell:
        m = _RX_CODE.match(_normalise_numbers(original))
        if m:
            n = float(m.group("n"))
            u = m.group("u").lower()
            if u in ("d", "w", "m", "y"):
                return Frequency(original, n, _CAL_UNITS[u], "calendar", span=(0, len(original)))
            ftype, unc, notes = _hour_type(None, u, None, basis)
            return Frequency(original, n, "hour", ftype, uncertain=unc, notes=notes, span=(0, len(original)))
        m = _RX_LETTER_CODE.match(original)
        if m:
            u = m.group("u").lower()
            if u == "q":
                return Frequency(original, 3.0, "month", "calendar", notes=["'Q' interpreted as quarterly."], uncertain=True)
            return Frequency(
                original, 1.0, _CAL_UNITS[u], "calendar", uncertain=True,
                notes=[f"Single-letter code '{original}' interpreted as every 1 {_CAL_UNITS[u]}."],
            )  # fmt: skip

    freqs = find_frequencies(original, context)
    if not freqs:
        return Frequency(original=original, notes=["No recognisable interval in source text."], uncertain=bool(original))
    primary = freqs[0]
    primary.original = original
    if len(freqs) > 1:
        primary.alternatives = [f.to_dict() for f in freqs[1:]]
        if _RX_WHICHEVER.search(original):
            primary.notes.append("Multiple intervals — whichever comes first.")
        else:
            primary.uncertain = True
            primary.notes.append("Several interval expressions found; review which applies.")
    return primary


def looks_like_frequency_cell(text: str, context: str | None = None) -> bool:
    return parse_frequency(text, context, cell=True).found
