"""Short job description, built strictly from text printed in the manual.

The short description is a *selection*, never a rewrite: every line is a complete
sentence or line copied unchanged from the long description (which is itself
copied from the procedure pages). No words, punctuation, abbreviations or
ellipses are added, nothing is paraphrased, and no AI model writes text.

Definition: a concise, point-wise summary of the job that highlights its key
steps, main activities and essential information — a quick understanding of
what the job involves without the complete procedure. Up to 25 points, more
when the job's complexity requires it. A verbatim check removes any point
that cannot be found word for word in the long description.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .long import VALUE_RX

# Sentence end: a full stop after a word or unit — not after a step number such as "4."
_SENT = re.compile(r"(?<=[A-Za-z%°)\]][.!?])\s+(?=[A-Z0-9])")
_NUM = re.compile(r"\d+(?:[.,]\d+)?")
_SAFETY_WORDS = re.compile(r"\b(?:danger|warning|caution|always|never|isolate|shut\s+off|lock|block|switch\s+off|safety)\b", re.I)
_FINAL = re.compile(r"\b(?:test|check|verify|start|refit|reassemble|record|close|mount)\b", re.I)
_WS = re.compile(r"\s+")


@dataclass
class ShortDescription:
    content: str
    method: str
    validation: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.validation.get("issues")


def _pieces(text: str) -> list[str]:
    """Verbatim lines and sentences of a block (split only at line breaks and sentence ends)."""
    out: list[str] = []
    pending = ""  # a bare step number ("1.") is kept together with the line after it
    for line in text.splitlines():
        for p in (p.strip() for p in _SENT.split(line)):
            if not p:
                continue
            if re.fullmatch(r"(?:step\s+)?\d{1,2}[.)]", p, re.I):
                pending = p
                continue
            out.append(f"{pending} {p}" if pending else p)
            pending = ""
    return out


def _norm(s: str) -> str:
    return _WS.sub(" ", s).strip()


def is_verbatim(line: str, long_text: str) -> bool:
    return _norm(line) in _norm(long_text)


def validate_short(short: str, long_text: str, long_blocks: list[dict], verbatim: bool = True) -> dict[str, Any]:
    issues: list[str] = []
    for line in short.splitlines():
        if verbatim and line.strip() and not is_verbatim(line, long_text):
            issues.append(f"Line not found word for word in the long description: '{line[:80]}'")
    long_nums = set(_NUM.findall(long_text.replace(",", ".")))
    for n in _NUM.findall(short.replace(",", ".")):
        if n not in long_nums:
            issues.append(f"Value '{n}' does not appear in the long description.")
    safety = [b for b in long_blocks if b["type"] == "safety" and b.get("level") in ("DANGER", "WARNING", "CAUTION")]
    if safety and not _SAFETY_WORDS.search(short):
        issues.append("Long description contains safety precautions that are missing from the short description.")
    n_lines = len([ln for ln in short.splitlines() if ln.strip()])
    return {"issues": issues, "notes": [], "line_count": n_lines}


MAX_POINTS = 25  # target; more points are kept when a job has more steps
_REFERENCE_ONLY = re.compile(r"^(?:see|refer to|for (?:correct|further|more)|according to)\b", re.I)
_NOTICE_LEVELS = ("DANGER", "WARNING", "CAUTION")


def extractive_short(long_text: str, blocks: list[dict]) -> ShortDescription:
    """Point-wise summary: key steps, main activities and essential information.

    One point per line, every point copied unchanged from the long description:
    * safety precautions (DANGER / WARNING / CAUTION, upper-case ALWAYS / NEVER notices),
    * for every step its key line (the step title, or its first instruction),
    * sentences carrying essential values (measurements, clearances, torques, limits).
    Up to 25 points; more when the job has more steps than that. The full procedure
    (every sentence of every step) stays in the long description.
    """
    points: list[str] = []

    def add(piece: str) -> None:
        piece = piece.strip()
        if piece and piece not in points and not _REFERENCE_ONLY.match(piece):
            points.append(piece)

    # 1. Safety precautions first.
    for b in blocks:
        if b["type"] == "safety" and b.get("level") in _NOTICE_LEVELS:
            parts = _pieces(b["text"])
            if parts:
                add(parts[0])

    # 2. Key steps / main activities, in procedure order, with their essential values.
    steps = [b for b in blocks if b["type"] == "step"]
    body = steps or [b for b in blocks if b["type"] == "text"]
    for s in body:
        parts = _pieces(s["text"])
        if not parts:
            continue
        key = parts[0]
        # A short step title ("Leaks during running") is followed by its first instruction.
        if len(parts) > 1 and len(re.sub(r"^\s*(?:step\s+)?\d{1,2}[.)]\s*", "", key)) < 40:
            add(key)
            key = parts[1]
        add(key)
        for p in parts[1:]:
            if VALUE_RX.search(p):  # measurement / torque / clearance / limit
                add(p)

    # 3. Essential values stated outside numbered steps (e.g. limits in a note or paragraph).
    if steps:
        for b in blocks:
            if b["type"] == "text":
                for p in _pieces(b["text"]):
                    if VALUE_RX.search(p):
                        add(p)

    if not points and long_text.strip():
        points = _pieces(long_text)[:MAX_POINTS]

    limit = max(MAX_POINTS, len(body) + sum(1 for b in blocks if b["type"] == "safety"))
    points = [p for p in points if is_verbatim(p, long_text)][:limit]
    content = "\n".join(points)
    return ShortDescription(content, "extractive", validate_short(content, long_text, blocks))


_TOKEN = re.compile(r"[A-Za-z]+|\d+(?:[.,]\d+)*")
_PROTECTED = {"not", "no", "never", "nor", "without", "cannot", "only", "except", "unless", "danger", "warning", "caution", "always"}


def _tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def deletion_only(edited: str, source: str) -> bool:
    """True if ``edited`` is ``source`` with words removed — nothing added, reworded or reordered.

    Numbers and the word after each number (its unit), negations, limiting words
    ("only", "unless") and safety words may never be removed.
    """
    src, out = _tokens(source), _tokens(edited)
    if not out:
        return False
    it = iter(src)
    if not all(t in it for t in out):  # subsequence: same words, same order
        return False
    # Numbers, negations, limiting and safety words: every occurrence is kept.
    exact = {t for t in src if t in _PROTECTED or t[0].isdigit()}
    if not all(out.count(t) >= src.count(t) for t in exact):
        return False
    # The unit after a number (mm, Nm, bar ...) must still be present.
    units = {src[i + 1] for i, t in enumerate(src[:-1]) if t[0].isdigit() and src[i + 1].isalpha()}
    return all(u in out for u in units)


def condense_points(provider, points: list[str]) -> tuple[list[str], int]:  # noqa: ANN001
    """Minimal AI pass over copied points. Returns (points, number of model edits rejected)."""
    from ..ai.tasks import llm_condense_points

    proposals = llm_condense_points(provider, points)
    used: set[int] = set()
    out: list[tuple[int, str]] = []
    rejected = 0
    for prop in proposals:
        idx = sorted({int(i) for i in prop.get("sources", []) if isinstance(i, int) and 0 <= i < len(points)})
        text = str(prop.get("text", "")).strip()
        if not idx or idx != list(range(idx[0], idx[-1] + 1)) or used & set(idx):
            rejected += 1
            continue
        source = " ".join(points[i] for i in idx)
        if deletion_only(text, source):
            out.append((idx[0], text))
        else:
            rejected += 1
            out.extend((i, points[i]) for i in idx)  # keep the manual's wording
        used.update(idx)
    # Every copied point stays represented, even if the model skipped it.
    out.extend((i, p) for i, p in enumerate(points) if i not in used)
    return [t for _i, t in sorted(out, key=lambda x: x[0])], rejected


def generate_short(long_text: str, blocks: list[dict], provider=None, model_name: str = "") -> ShortDescription:  # noqa: ANN001
    """Copied points from the manual; optionally tidied by a local model that may only delete words."""
    base = extractive_short(long_text, blocks)
    if provider is None or not base.content:
        return base
    try:
        points, rejected = condense_points(provider, base.content.splitlines())
    except Exception as exc:  # noqa: BLE001 - model unavailable: keep the copied version
        base.validation.setdefault("notes", []).append(f"AI condensing not applied: {exc}")
        return base
    content = "\n".join(points)
    validation = validate_short(content, long_text, blocks, verbatim=False)
    validation["notes"].append(
        "Condensed by a local model that may only remove words; every point was checked against the manual's sentence."
        + (f" {rejected} model edit(s) failed the check and the manual's wording was kept." if rejected else "")
    )
    return ShortDescription(content, f"llm-condensed:{model_name or 'model'}", validation)


__all__ = ["VALUE_RX", "ShortDescription", "condense_points", "deletion_only", "extractive_short", "generate_short", "is_verbatim", "validate_short"]
