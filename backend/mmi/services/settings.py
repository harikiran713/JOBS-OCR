"""Persistent application settings stored in the ``settings`` table."""

from __future__ import annotations

import copy
from typing import Any

from sqlalchemy.orm import Session

from ..db.models import Setting

DEFAULTS: dict[str, Any] = {
    # Processing engine
    "processing.concurrency": 2,
    "processing.batch_size": 16,
    "processing.max_attempts": 3,
    "processing.min_free_memory_mb": 1024,
    "processing.max_procedure_pages": 30,
    # OCR
    "ocr.enabled": True,
    "ocr.engine": "auto",  # auto | rapidocr | paddleocr | none
    "ocr.min_chars": 40,  # pages with fewer extractable characters are OCR candidates
    "ocr.dpi": 200,
    "ocr.min_confidence": 0.6,
    # Local AI (all optional; the app works without a model)
    "ai.provider": "none",  # none | ollama | openai_compatible
    "ai.base_url": "http://127.0.0.1:11434",
    "ai.chat_model": "",
    "ai.embedding_model": "",
    "ai.vision_model": "",
    "ai.allow_remote": False,  # privacy guard: only loopback endpoints unless explicitly allowed
    "ai.timeout_s": 240,
    "ai.use_for_job_extraction": True,
    "ai.use_for_procedure_verification": True,
    "ai.use_for_short_descriptions": True,  # minimal: the model may only delete words from copied points
    "ai.use_vision_for_unreadable_pages": False,
    # Extraction
    "extraction.custom_fields": [],  # [{"key": "criticality", "label": "Criticality"}]
    # UI
    "ui.theme": "system",
    "ui.reviewer_name": "Reviewer",
}


def get_all(session: Session) -> dict[str, Any]:
    values = copy.deepcopy(DEFAULTS)
    for row in session.query(Setting).all():
        values[row.key] = row.value
    return values


def get(session: Session, key: str) -> Any:
    row = session.get(Setting, key)
    if row is not None:
        return row.value
    return copy.deepcopy(DEFAULTS.get(key))


def update(session: Session, changes: dict[str, Any]) -> dict[str, Any]:
    unknown = [k for k in changes if k not in DEFAULTS]
    if unknown:
        raise KeyError(f"Unknown setting(s): {', '.join(sorted(unknown))}")
    for key, value in changes.items():
        default = DEFAULTS[key]
        if default is not None and not isinstance(value, type(default)):
            # bool is a subclass of int; keep the check strict for bools.
            if isinstance(default, bool) or not (isinstance(default, (int, float)) and isinstance(value, (int, float))):
                raise TypeError(f"Setting {key} expects {type(default).__name__}")
        row = session.get(Setting, key)
        if row is None:
            session.add(Setting(key=key, value=value))
        else:
            row.value = value
    session.flush()
    return get_all(session)
