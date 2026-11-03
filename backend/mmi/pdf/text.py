"""Per-page text extraction (digital text with OCR fallback).

These functions run inside worker *processes* (MuPDF is not thread-safe), so
they take plain arguments and return plain dicts. Each call opens the PDF,
processes a small batch of pages one at a time, and closes it: memory stays
bounded regardless of document size.
"""

from __future__ import annotations

import re
import traceback
from typing import Any

import pymupdf

from ..extraction.classify import classify_page
from ..extraction.layout import detect_headings, lines_to_text
from ..ocr.engines import OcrUnavailable, get_engine
from .inspect import open_document

EXTRACTOR_VERSION = "1.1"

_GARBAGE = re.compile(r"[�-]")


# Document codes printed in page margins, e.g. "1465-0201-0018" (dates are excluded separately).
DOC_CODE = re.compile(r"\b\d{3,5}(?:[-.]\d{2,5}){2,3}\b")
_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}\b")
PAGE_COUNTER = re.compile(r"^(?:page\s+)?(\d{1,4})\s*(?:\((\d{1,4})\)|/\s*(\d{1,4})|of\s+(\d{1,4}))$", re.I)


def is_footnote_mark(span: dict, prev_text: str, body_size: float | None = None) -> bool:
    """Superscript comment/footnote numbers ("Checking³⁶").

    Detected by the superscript flag or by a clearly smaller font than the surrounding
    text. Units such as m² (superscript after a 1–2 letter token) are not treated as marks.
    """
    if not re.fullmatch(r"[\d,\s]+", span.get("text", "")) or not span.get("text", "").strip():
        return False
    raised = bool(span.get("flags", 0) & 1) or (body_size is not None and span.get("size", 0) < 0.82 * body_size)
    if not raised:
        return False
    return not prev_text or bool(re.search(r"[A-Za-z]{3,}\W*$", prev_text))


def _span_text(spans: list[dict]) -> tuple[str, list[str]]:
    """Join spans, separating superscript footnote marks from the text."""
    text, marks = "", []
    visible = [s for s in spans if s.get("size", 0) >= 1 and s.get("text", "").strip()]
    body = max((s["size"] for s in visible), default=None)
    for s in spans:
        if s.get("size", 0) < 1:
            continue  # invisible text (tagging artefacts)
        if is_footnote_mark(s, text, body) and text:
            marks.extend(m for m in re.split(r"[,\s]+", s["text"]) if m)
            continue
        text += s["text"]
    return re.sub(r"\s+", " ", text).strip(), marks


def _digital_lines(page: pymupdf.Page) -> tuple[list[dict], float, dict]:
    w, h = page.rect.width or 1, page.rect.height or 1
    data = page.get_text("dict", sort=True, flags=pymupdf.TEXT_PRESERVE_WHITESPACE | pymupdf.TEXT_MEDIABOX_CLIP)
    lines: list[dict] = []
    labels: list[tuple[float, str]] = []
    image_area = 0.0
    for block in data.get("blocks", []):
        if block.get("type") == 1:
            x0, y0, x1, y1 = block["bbox"]
            image_area += max(0.0, (x1 - x0) * (y1 - y0))
            continue
        for line in block.get("lines", []):
            spans = [s for s in line.get("spans", []) if s.get("text", "").strip() and s.get("size", 0) >= 1]
            if not spans:
                continue
            text, marks = _span_text(line["spans"])
            if not text:
                continue
            rel_x = line["bbox"][0] / w
            if tuple(line.get("dir", (1.0, 0.0))) != (1.0, 0.0) and (rel_x < 0.12 or rel_x > 0.85):
                # Rotated text in the side margins (document title / type / section labels) is page
                # metadata, not body text. Rotated text inside the page (e.g. table headers) stays.
                if not _DATE.match(text):
                    labels.append((rel_x, text))
                continue
            size = max(s.get("size", 0) for s in spans)
            bold = any((s.get("flags", 0) & 16) or "bold" in s.get("font", "").lower() for s in spans)
            x0, y0, x1, y1 = line["bbox"]
            ln = {
                "t": text,
                "b": [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)],
                "s": round(size, 1),
                "f": bool(bold),
            }
            if marks:
                ln["m"] = marks
            lines.append(ln)
    coverage = min(image_area / (w * h), 1.0)
    return lines, coverage, _layout_meta(lines, labels)


def _layout_meta(lines: list[dict], labels: list[tuple[float, str]]) -> dict:
    """Document code, page counter and margin labels printed in the header/footer bands."""
    meta: dict = {}
    header_rows: list[float] = []
    for ln in lines:
        if 0.11 < ln["b"][1] < 0.89:
            continue
        t = ln["t"].strip()
        m = DOC_CODE.search(t)
        if m and not _DATE.match(t):
            meta.setdefault("doc_code", m.group(0))
            header_rows.append(ln["b"][1])
        c = PAGE_COUNTER.match(t)
        if c:
            total = c.group(2) or c.group(3) or c.group(4)
            if total and int(c.group(1)) <= int(total):
                meta.setdefault("page_n", int(c.group(1)))
                meta.setdefault("page_m", int(total))
                header_rows.append(ln["b"][1])
    # Everything on a header/footer row (e.g. the publisher name beside the code) is running text.
    for ln in lines:
        if any(abs(ln["b"][1] - y) < 0.012 for y in header_rows):
            ln["hdr"] = True
    if labels:
        # Labels read in a stable order (outer to inner); codes and counters become fields.
        seen: list[str] = []
        for _x, t in sorted(labels, key=lambda lt: lt[0]):
            c = PAGE_COUNTER.match(t)
            if c and "page_n" not in meta and (c.group(2) or c.group(3) or c.group(4)):
                meta["page_n"], meta["page_m"] = int(c.group(1)), int(c.group(2) or c.group(3) or c.group(4))
                continue
            if DOC_CODE.fullmatch(t):
                meta.setdefault("doc_code", t)
                continue
            if t not in seen:
                seen.append(t)
        meta["labels"] = seen[:6]
    return meta


def _ocr_lines(page: pymupdf.Page, dpi: int, engine_pref: str) -> tuple[list[dict], float]:
    engine = get_engine(engine_pref)
    pix = page.get_pixmap(dpi=dpi, alpha=False)
    try:
        png = pix.tobytes("png")
        pw, ph = pix.width or 1, pix.height or 1
    finally:
        del pix
    results = engine.recognise(png)
    lines: list[dict] = []
    confs: list[float] = []
    for r in results:
        x0, y0, x1, y1 = r.box
        # OCR has no font info: use box height (in points) as a size proxy for heading detection.
        size = (y1 - y0) * 72.0 / dpi
        lines.append(
            {
                "t": r.text,
                "b": [round(x0 / pw, 4), round(y0 / ph, 4), round(x1 / pw, 4), round(y1 / ph, 4)],
                "s": round(size, 1),
                "f": False,
                "c": round(r.confidence, 3),
            }
        )
        confs.append(r.confidence)
    mean_conf = sum(confs) / len(confs) if confs else 0.0
    return lines, mean_conf


def extract_page(doc: pymupdf.Document, page_no: int, opts: dict[str, Any]) -> dict[str, Any]:
    """Extract one page. Never raises for document problems; returns a status instead."""
    page = doc[page_no - 1]
    result: dict[str, Any] = {
        "page_no": page_no,
        "status": "completed",
        "status_reason": "",
        "source": "digital",
        "text": "",
        "lines": [],
        "headings": [],
        "image_count": 0,
        "ocr_confidence": None,
        "kind": "unknown",
        "scores": {},
    }
    try:
        result["image_count"] = len(page.get_images(full=False))
    except Exception:  # noqa: BLE001 - malformed resources should not abort the page
        result["image_count"] = 0

    lines, coverage, meta = _digital_lines(page)
    result["meta"] = meta
    text = "\n".join(ln["t"] for ln in lines)
    chars = len(re.sub(r"\s", "", text))
    garbage = len(_GARBAGE.findall(text)) / chars if chars else 0.0
    # A page with a digital text layer anywhere (including margin labels and document codes)
    # is a digital page with a figure, not a scan: OCR would only add noise and time.
    digital_chars = chars + sum(len(lb) for lb in meta.get("labels", [])) + (10 if meta.get("doc_code") else 0)

    needs_ocr = (
        digital_chars < opts.get("ocr_min_chars", 40) and (coverage > 0.25 or result["image_count"] > 0)
    ) or garbage > 0.3
    if needs_ocr:
        if not opts.get("ocr_enabled", True):
            result.update(status="needs_review", status_reason="Page appears scanned/image-only and OCR is disabled.")
        else:
            try:
                o_lines, conf = _ocr_lines(page, int(opts.get("ocr_dpi", 200)), opts.get("ocr_engine", "auto"))
                result["ocr_confidence"] = round(conf, 3)
                if o_lines:
                    lines = o_lines
                    result["source"] = "ocr"
                    if conf < float(opts.get("ocr_min_confidence", 0.6)):
                        result.update(
                            status="needs_review", status_reason=f"Low OCR confidence ({conf:.0%}); verify text."
                        )
                elif chars == 0:
                    result.update(status="needs_review", status_reason="OCR found no text on an image-only page.")
            except OcrUnavailable as exc:
                result.update(status="needs_review", status_reason=f"OCR required but unavailable: {exc}")
            except Exception as exc:  # noqa: BLE001
                result.update(status="needs_review", status_reason=f"OCR failed: {exc}")

    if result["source"] == "ocr":
        text = lines_to_text(lines)
    else:
        text = "\n".join(ln["t"] for ln in lines)

    headings = detect_headings(lines)
    kind, scores = classify_page(text, lines, headings)
    # A page that labels itself as a work card / job card is procedure content.
    if kind in ("general", "unknown") and any(
        re.search(r"work\s*card|job\s*card|maintenance\s+procedure", lb, re.I) for lb in meta.get("labels", [])
    ):
        kind = "procedure"
        scores["procedure"] = max(float(scores.get("procedure", 0.0)), 0.5)
    if kind == "blank" and result["image_count"] == 0 and result["status"] == "completed":
        result["status_reason"] = "Blank page"
    result.update(text=text, lines=lines, headings=headings, kind=kind, scores=scores)
    result["char_count"] = len(re.sub(r"\s", "", text))
    return result


def extract_pages_batch(path: str, page_nos: list[int], opts: dict[str, Any]) -> list[dict[str, Any]]:
    """Worker-process entry point. Per-page failures are captured, never propagated."""
    out: list[dict[str, Any]] = []
    doc = open_document(path)
    try:
        for no in page_nos:
            try:
                out.append(extract_page(doc, no, opts))
            except Exception as exc:  # noqa: BLE001
                out.append(
                    {
                        "page_no": no,
                        "status": "failed",
                        "status_reason": f"{type(exc).__name__}: {exc}",
                        "traceback": traceback.format_exc(limit=6),
                    }
                )
    finally:
        doc.close()
    return out


# ---------------------------------------------------------------------------
# Table extraction for schedule pages (also worker-side)
# ---------------------------------------------------------------------------


def extract_tables(path: str, page_no: int) -> list[dict[str, Any]]:
    """Return tables as ``{"rows": [[cell, ...]], "bbox": [...], "row_boxes": [...]}`` (normalised coords)."""
    doc = open_document(path)
    try:
        page = doc[page_no - 1]
        w, h = page.rect.width or 1, page.rect.height or 1
        tables: list[dict[str, Any]] = []
        try:
            found = page.find_tables()
        except Exception:  # noqa: BLE001 - table detection is best-effort
            return []
        # Rebuild cell text from positioned spans: Table.extract() can drop spaces and hyphens
        # with some font encodings, and it glues superscript comment numbers onto words
        # ("Checking36"). Footnote marks are returned separately per cell in "refs".
        spans = [
            s
            for b in page.get_text("dict", flags=pymupdf.TEXT_PRESERVE_WHITESPACE)["blocks"]
            for ln in b.get("lines", [])
            for s in ln["spans"]
            if s.get("text", "").strip() and s.get("size", 0) >= 1
        ]
        for t in found.tables:
            clean: list[list[str]] = []
            refs: list[list[list[str]]] = []
            for r in t.rows:
                row_cells, row_refs = [], []
                for cell in r.cells:
                    if cell is None:
                        row_cells.append("")
                        row_refs.append([])
                        continue
                    cx0, cy0, cx1, cy1 = cell
                    inside = [
                        s for s in spans
                        if cx0 - 1 <= (s["bbox"][0] + s["bbox"][2]) / 2 <= cx1 + 1
                        and cy0 - 1 <= (s["bbox"][1] + s["bbox"][3]) / 2 <= cy1 + 1
                    ]  # fmt: skip
                    body = max((s["size"] for s in inside), default=None)
                    marks: list[str] = []
                    regular = []
                    for s in inside:
                        if body and re.fullmatch(r"[\d,\s]+", s["text"]) and s["size"] < 0.82 * body:
                            marks.extend(m for m in re.split(r"[,\s]+", s["text"]) if m)
                        else:
                            regular.append(s)
                    # Group into visual lines by vertical centre, then left-to-right.
                    regular.sort(key=lambda s: (round((s["bbox"][1] + s["bbox"][3]) / 2 / 5), s["bbox"][0]))
                    text, prev_x1 = "", None
                    for s in regular:
                        if is_footnote_mark(s, text):
                            marks.extend(m for m in re.split(r"[,\s]+", s["text"]) if m)
                            continue
                        gap = prev_x1 is None or s["bbox"][0] - prev_x1 > 0.8 or s["bbox"][0] < prev_x1 - 5
                        text += (" " if text and gap else "") + s["text"]
                        prev_x1 = s["bbox"][2]
                    row_cells.append(re.sub(r"\s+", " ", text).strip())
                    row_refs.append(marks)
                clean.append(row_cells)
                refs.append(row_refs)
            if len(clean) < 2 or max((len(r) for r in clean), default=0) < 2:
                continue
            row_boxes = []
            for r in t.rows:
                x0, y0, x1, y1 = r.bbox
                row_boxes.append([round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)])
            x0, y0, x1, y1 = t.bbox
            tables.append(
                {
                    "rows": clean,
                    "refs": refs,
                    "bbox": [round(x0 / w, 4), round(y0 / h, 4), round(x1 / w, 4), round(y1 / h, 4)],
                    "row_boxes": row_boxes,
                }
            )
        return tables
    finally:
        doc.close()
