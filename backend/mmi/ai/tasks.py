"""AI-assisted steps. Every model output is validated against source text.

The model is never trusted to produce source references: page numbers and
documents always come from the pipeline. Model claims that cannot be found in
the source text are discarded or flagged.
"""

from __future__ import annotations

import re
from difflib import SequenceMatcher

from .providers import AIResponseError, AIUnavailable

_WS = re.compile(r"\s+")


def _norm(s: str) -> str:
    return _WS.sub(" ", re.sub(r"[^\w.,/%°-]+", " ", s.lower())).strip()


def quote_in_source(quote: str, source: str, threshold: float = 0.85) -> bool:
    """True if ``quote`` (or a near-identical span) appears in ``source``."""
    q, s = _norm(quote), _norm(source)
    if not q:
        return False
    if q in s:
        return True
    if len(q) > 400:
        return False
    # Sliding fuzzy match for OCR noise / hyphenation differences.
    window = len(q)
    step = max(1, window // 4)
    best = 0.0
    for i in range(0, max(1, len(s) - window + 1), step):
        r = SequenceMatcher(None, q, s[i : i + window + 10]).ratio()
        best = max(best, r)
        if best >= threshold:
            return True
    return False


# ---------------------------------------------------------------------------
# Job extraction assistance
# ---------------------------------------------------------------------------

JOBS_SCHEMA = {
    "type": "object",
    "properties": {
        "jobs": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "task": {"type": "string"},
                    "component": {"type": "string"},
                    "interval_text": {"type": "string"},
                    "source_quote": {"type": "string"},
                },
                "required": ["task", "interval_text", "source_quote"],
            },
        }
    },
    "required": ["jobs"],
}

JOBS_SYSTEM = (
    "You extract planned maintenance jobs from ship equipment manual pages. A job is a single maintenance "
    "task (inspect, overhaul, replace, lubricate, clean, test, adjust, calibrate, repair, renew, service...) that the "
    "page schedules at an interval or on an event/condition. Do NOT list spare parts, technical data, general "
    "descriptions or warnings as jobs. Copy wording exactly from the page; never invent tasks or intervals. If a "
    "task has no interval on the page, use an empty interval_text. source_quote must be copied verbatim from the page."
)


def llm_extract_jobs(provider, page_text: str) -> list[dict]:  # noqa: ANN001
    text = page_text[:7000]
    data = provider.chat_json(JOBS_SYSTEM, f"PAGE TEXT:\n{text}", JOBS_SCHEMA)
    out = []
    for j in data.get("jobs", []) if isinstance(data, dict) else []:
        task = str(j.get("task", "")).strip()
        quote = str(j.get("source_quote", "")).strip()
        interval = str(j.get("interval_text", "")).strip()
        if not task:
            continue
        grounded = quote_in_source(quote or task, page_text) and quote_in_source(task, page_text, 0.75)
        interval_grounded = not interval or quote_in_source(interval, page_text, 0.8)
        out.append(
            {
                "task": task,
                "component": str(j.get("component", "")).strip(),
                "interval_text": interval if interval_grounded else "",
                "source_quote": quote,
                "grounded": grounded,
                "interval_grounded": interval_grounded,
            }
        )
    return out


# ---------------------------------------------------------------------------
# Procedure verification
# ---------------------------------------------------------------------------

VERIFY_SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["match", "partial", "no_match"]},
        "reason": {"type": "string"},
        "evidence_quote": {"type": "string"},
    },
    "required": ["verdict", "reason", "evidence_quote"],
}


def llm_verify_procedure(provider, job_desc: str, excerpt: str) -> dict:  # noqa: ANN001
    system = (
        "You check whether a manual excerpt is the step-by-step procedure for a given maintenance job. "
        "Answer 'match' only if the excerpt describes how to carry out exactly this job on this component. "
        "'partial' if it covers only part of it or a closely related task. evidence_quote must be copied verbatim."
    )
    user = f"JOB: {job_desc}\n\nEXCERPT:\n{excerpt[:6000]}"
    data = provider.chat_json(system, user, VERIFY_SCHEMA)
    verdict = data.get("verdict", "no_match")
    quote = str(data.get("evidence_quote", ""))
    grounded = quote_in_source(quote, excerpt) if quote else False
    if verdict == "match" and not grounded:
        verdict = "partial"  # an ungrounded "match" is not accepted as verification
    return {"verdict": verdict, "reason": str(data.get("reason", ""))[:500], "quote": quote, "grounded": grounded}


# ---------------------------------------------------------------------------
# Short descriptions
# ---------------------------------------------------------------------------

SHORT_SYSTEM = (
    "You write short maintenance job descriptions (4 to 6 lines) for a ship maintenance system, using ONLY the "
    "procedure text provided. Rules: keep every critical safety precaution; keep key actions in order; copy "
    "measurements, clearances, torque values and units exactly; include inspection criteria and the final "
    "verification/test. Never add tools, values, steps or advice that are not in the text. Plain text lines, no "
    "markdown headings. If the procedure is complex you may exceed 6 lines rather than drop safety-critical content."
)


def llm_short_description(provider, long_text: str) -> str:  # noqa: ANN001
    return provider.chat_text(SHORT_SYSTEM, f"PROCEDURE:\n{long_text[:12000]}").strip()


CONDENSE_SCHEMA = {
    "type": "object",
    "properties": {
        "points": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {"sources": {"type": "array", "items": {"type": "integer"}}, "text": {"type": "string"}},
                "required": ["sources", "text"],
            },
        }
    },
    "required": ["points"],
}

CONDENSE_SYSTEM = (
    "You tidy the points of a ship maintenance job summary. The points are copied from the equipment manual. "
    "You may ONLY delete words to make a point shorter, and you may merge a numbered step title with the point "
    "directly after it. Never add, replace, reword or reorder words. Never remove numbers, units, 'not', 'no', "
    "'never', 'only', 'unless' or safety words. Keep every point. If a point is already concise, return it unchanged. "
    "For each output point give 'sources' = the input point numbers it came from."
)


def llm_condense_points(provider, points: list[str]) -> list[dict]:  # noqa: ANN001
    numbered = "\n".join(f"{i}: {p}" for i, p in enumerate(points))
    data = provider.chat_json(CONDENSE_SYSTEM, f"POINTS:\n{numbered}", CONDENSE_SCHEMA)
    return [p for p in data.get("points", []) if isinstance(p, dict)] if isinstance(data, dict) else []


def transcribe_page_image(provider, png: bytes) -> str:  # noqa: ANN001
    prompt = (
        "Transcribe all text on this ship manual page exactly, preserving table rows as lines with ' | ' between "
        "cells. For symbols in interval tables (e.g. ●, X, ✓) write the symbol. Do not add commentary."
    )
    return provider.vision_text(png, prompt)


__all__ = [
    "AIResponseError",
    "AIUnavailable",
    "llm_extract_jobs",
    "llm_short_description",
    "llm_verify_procedure",
    "quote_in_source",
    "transcribe_page_image",
]
