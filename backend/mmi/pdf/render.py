"""Page rendering and text-layer data for the in-app PDF viewer.

Rendering happens in the API process under a lock (MuPDF is not thread-safe)
and results are cached on disk keyed by document hash, so repeat views are
instant and memory stays flat. Only one page is rasterised at a time.
"""

from __future__ import annotations

import threading
from pathlib import Path

import pymupdf

from ..config import get_paths
from .inspect import open_document

_lock = threading.Lock()
ALLOWED_SCALES = (0.25, 0.5, 1.0, 1.5, 2.0, 3.0)


def _snap(scale: float) -> float:
    return min(ALLOWED_SCALES, key=lambda s: abs(s - scale))


def render_page(stored_path: str, sha256: str, page_no: int, scale: float) -> Path:
    scale = _snap(scale)
    out_dir = get_paths().renders / sha256[:2] / sha256
    out = out_dir / f"{page_no}_{scale:g}.png"
    if out.exists():
        return out
    out_dir.mkdir(parents=True, exist_ok=True)
    with _lock:
        if out.exists():
            return out
        doc = open_document(stored_path)
        try:
            if not 1 <= page_no <= doc.page_count:
                raise IndexError(f"Page {page_no} out of range")
            pix = doc[page_no - 1].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
            tmp = out.with_suffix(".tmp")
            pix.save(str(tmp), output="png")
            tmp.replace(out)
        finally:
            doc.close()
    return out


def page_geometry(stored_path: str, page_no: int) -> dict:
    with _lock:
        doc = open_document(stored_path)
        try:
            r = doc[page_no - 1].rect
            return {"width": r.width, "height": r.height, "rotation": doc[page_no - 1].rotation}
        finally:
            doc.close()


def page_words(stored_path: str, page_no: int) -> list[dict]:
    """Words with normalised boxes, for a selectable text layer over the rendered image."""
    with _lock:
        doc = open_document(stored_path)
        try:
            page = doc[page_no - 1]
            w, h = page.rect.width or 1, page.rect.height or 1
            return [
                {"t": wd[4], "b": [round(wd[0] / w, 4), round(wd[1] / h, 4), round(wd[2] / w, 4), round(wd[3] / h, 4)]}
                for wd in page.get_text("words", sort=True)
            ]
        finally:
            doc.close()


def search_page(stored_path: str, page_no: int, query: str) -> list[list[float]]:
    with _lock:
        doc = open_document(stored_path)
        try:
            page = doc[page_no - 1]
            w, h = page.rect.width or 1, page.rect.height or 1
            return [
                [round(r.x0 / w, 4), round(r.y0 / h, 4), round(r.x1 / w, 4), round(r.y1 / h, 4)]
                for r in page.search_for(query, quads=False)[:200]
            ]
        finally:
            doc.close()
