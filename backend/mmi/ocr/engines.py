"""Local OCR engines behind a common interface.

* ``rapidocr`` (default): PaddleOCR detection/recognition models running on
  ONNX Runtime. Small, CPU-friendly and easy to package.
* ``paddleocr``: the reference PaddlePaddle implementation, used when the
  optional ``paddleocr`` package is installed (better GPU support, much
  larger install).

Engines are created lazily per worker process and reused.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Protocol

log = logging.getLogger(__name__)


@dataclass
class OcrLine:
    text: str
    box: tuple[float, float, float, float]  # pixel coords x0, y0, x1, y1
    confidence: float


class OcrEngine(Protocol):
    name: str

    def recognise(self, png_bytes: bytes) -> list[OcrLine]: ...


class OcrUnavailable(RuntimeError):
    pass


def _quad_to_box(quad) -> tuple[float, float, float, float]:  # noqa: ANN001
    xs = [float(p[0]) for p in quad]
    ys = [float(p[1]) for p in quad]
    return min(xs), min(ys), max(xs), max(ys)


class RapidOcrEngine:
    name = "rapidocr"

    def __init__(self) -> None:
        try:
            from rapidocr import RapidOCR  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - depends on install
            raise OcrUnavailable(f"RapidOCR is not installed: {exc}") from exc
        for noisy in ("RapidOCR", "rapidocr"):
            logging.getLogger(noisy).setLevel(logging.WARNING)
        self._engine = RapidOCR()

    def recognise(self, png_bytes: bytes) -> list[OcrLine]:
        result = self._engine(png_bytes)
        if result is None or result.boxes is None or result.txts is None:
            return []
        out: list[OcrLine] = []
        for quad, text, score in zip(result.boxes, result.txts, result.scores, strict=False):
            if text and text.strip():
                out.append(OcrLine(text.strip(), _quad_to_box(quad), float(score)))
        return out


class PaddleOcrEngine:
    name = "paddleocr"

    def __init__(self) -> None:
        try:
            from paddleocr import PaddleOCR  # type: ignore[import-not-found]
        except Exception as exc:  # pragma: no cover - optional dependency
            raise OcrUnavailable(f"PaddleOCR is not installed: {exc}") from exc
        self._engine = PaddleOCR(
            use_doc_orientation_classify=False, use_doc_unwarping=False, use_textline_orientation=False
        )

    def recognise(self, png_bytes: bytes) -> list[OcrLine]:  # pragma: no cover - optional dependency
        import numpy as np
        import pymupdf

        pix = pymupdf.Pixmap(png_bytes)
        img = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.height, pix.width, pix.n)[:, :, :3]
        out: list[OcrLine] = []
        for res in self._engine.predict(img):
            data = res.json.get("res", res.json) if hasattr(res, "json") else res
            for text, score, poly in zip(data["rec_texts"], data["rec_scores"], data["rec_polys"], strict=False):
                if text and text.strip():
                    out.append(OcrLine(text.strip(), _quad_to_box(poly), float(score)))
        return out


_engines: dict[str, OcrEngine] = {}
_failures: dict[str, str] = {}


def get_engine(preference: str = "auto") -> OcrEngine:
    """Return a cached engine. ``auto`` prefers RapidOCR, then PaddleOCR."""
    order = {"auto": ["rapidocr", "paddleocr"], "rapidocr": ["rapidocr"], "paddleocr": ["paddleocr"]}.get(
        preference, []
    )
    if not order:
        raise OcrUnavailable("OCR is disabled in settings")
    errors = []
    for name in order:
        if name in _engines:
            return _engines[name]
        if name in _failures:
            errors.append(_failures[name])
            continue
        try:
            engine: OcrEngine = RapidOcrEngine() if name == "rapidocr" else PaddleOcrEngine()
            _engines[name] = engine
            return engine
        except OcrUnavailable as exc:
            _failures[name] = str(exc)
            errors.append(str(exc))
    raise OcrUnavailable("; ".join(errors))


def available_engines() -> dict[str, bool]:
    import importlib.util

    return {
        "rapidocr": importlib.util.find_spec("rapidocr") is not None,
        "paddleocr": importlib.util.find_spec("paddleocr") is not None,
    }
