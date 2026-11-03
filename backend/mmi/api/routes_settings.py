from __future__ import annotations

import threading
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..ai.hardware import recommendations
from ..ai.providers import AIUnavailable, OllamaProvider, get_provider
from ..db.session import get_session
from ..ocr.engines import OcrUnavailable, available_engines, get_engine
from ..services import settings as settings_service
from ..services.jobs import audit

router = APIRouter()


@router.get("/settings")
def get_settings(s: Session = Depends(get_session)) -> dict:
    return settings_service.get_all(s)


class SettingsPatch(BaseModel):
    changes: dict[str, Any] = Field(min_length=1)


@router.put("/settings")
def put_settings(body: SettingsPatch, s: Session = Depends(get_session)) -> dict:
    before = settings_service.get_all(s)
    try:
        after = settings_service.update(s, body.changes)
    except (KeyError, TypeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    audit(s, "settings", "app", "updated", {k: before.get(k) for k in body.changes}, body.changes, "user")
    return after


@router.get("/ai/hardware")
def hardware() -> dict:
    return recommendations()


@router.get("/ai/models")
def models(s: Session = Depends(get_session)) -> dict:
    cfg = settings_service.get_all(s)
    try:
        provider = get_provider(cfg)
        if provider is None:
            return {"models": [], "message": "AI provider is disabled"}
        return {"models": provider.list_models()}
    except AIUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Inference server error: {exc}") from exc


@router.post("/ai/test")
def test_ai(s: Session = Depends(get_session)) -> dict:
    """Small, explicit connectivity test with a non-sensitive prompt."""
    cfg = settings_service.get_all(s)
    out: dict[str, Any] = {}
    try:
        provider = get_provider(cfg)
        if provider is None:
            raise HTTPException(400, "Select an AI provider first")
        if cfg["ai.chat_model"]:
            data = provider.chat_json(
                "Reply with JSON.", 'Return {"status": "ok"}', {"type": "object", "properties": {"status": {"type": "string"}}, "required": ["status"]}
            )
            out["chat"] = "ok" if data.get("status") else f"unexpected reply: {data}"
        if cfg["ai.embedding_model"]:
            vec = provider.embed(["fuel valve overhaul"])
            out["embedding"] = f"ok ({len(vec[0])} dimensions)" if vec else "no vector returned"
    except AIUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    except HTTPException:
        raise
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"Test failed: {exc}") from exc
    return out


_pulls: dict[str, dict[str, Any]] = {}


class PullIn(BaseModel):
    model: str = Field(min_length=1, max_length=200, pattern=r"^[\w.\-:/]+$")


@router.post("/ai/pull")
def pull(body: PullIn, s: Session = Depends(get_session)) -> dict:
    """User-initiated model download through the local Ollama server (network access by Ollama)."""
    cfg = settings_service.get_all(s)
    if cfg["ai.provider"] != "ollama":
        raise HTTPException(400, "Model downloads are available for the Ollama provider. For llama.cpp, place GGUF files in the models folder.")
    try:
        provider = get_provider(cfg)
    except AIUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    assert isinstance(provider, OllamaProvider)
    state = _pulls.get(body.model)
    if state and state.get("status") == "running":
        return state
    state = {"model": body.model, "status": "running", "completed": 0, "total": 0, "message": "Starting download"}
    _pulls[body.model] = state

    def worker() -> None:
        try:
            for ev in provider.pull(body.model):
                state["message"] = ev.get("status", "")
                if ev.get("total"):
                    state["total"] = ev["total"]
                    state["completed"] = ev.get("completed", 0)
                if ev.get("error"):
                    raise RuntimeError(ev["error"])
            state["status"] = "completed"
        except Exception as exc:  # noqa: BLE001
            state.update(status="failed", message=str(exc))

    threading.Thread(target=worker, daemon=True).start()
    return state


@router.get("/ai/pull")
def pull_status() -> list[dict]:
    return list(_pulls.values())


@router.get("/ocr/status")
def ocr_status(s: Session = Depends(get_session)) -> dict:
    cfg = settings_service.get_all(s)
    status: dict[str, Any] = {"enabled": cfg["ocr.enabled"], "engine": cfg["ocr.engine"], "installed": available_engines()}
    return status


@router.post("/ocr/test")
def ocr_test(s: Session = Depends(get_session)) -> dict:
    import pymupdf

    cfg = settings_service.get_all(s)
    doc = pymupdf.open()
    page = doc.new_page(width=400, height=120)
    page.insert_text((20, 60), "Overhaul fuel valve every 3000 hours", fontsize=16)
    png = page.get_pixmap(dpi=150).tobytes("png")
    doc.close()
    try:
        engine = get_engine(cfg["ocr.engine"])
        lines = engine.recognise(png)
    except OcrUnavailable as exc:
        raise HTTPException(503, str(exc)) from exc
    return {"engine": engine.name, "text": " ".join(ln.text for ln in lines), "confidence": round(sum(ln.confidence for ln in lines) / max(len(lines), 1), 3)}
