"""Safe PDF inspection: validation, hashing and metadata without loading page content."""

from __future__ import annotations

import hashlib
import shutil
from dataclasses import dataclass, field
from pathlib import Path

import pymupdf

MAX_FILE_BYTES = 4 * 1024**3  # 4 GiB hard limit; larger files are rejected with a clear message.


class PdfProblem(Exception):
    """A document-specific problem (corrupt, encrypted, unsupported). Not an application error."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass
class PdfInfo:
    page_count: int
    title: str
    metadata: dict
    encrypted: bool
    warnings: list[str] = field(default_factory=list)


def validate_pdf_path(path: Path) -> None:
    if not path.exists() or not path.is_file():
        raise PdfProblem("not_found", f"File not found: {path.name}")
    if path.suffix.lower() != ".pdf":
        raise PdfProblem("unsupported", f"{path.name} is not a PDF file")
    size = path.stat().st_size
    if size == 0:
        raise PdfProblem("empty", f"{path.name} is empty")
    if size > MAX_FILE_BYTES:
        raise PdfProblem("too_large", f"{path.name} exceeds the 4 GB limit")
    with path.open("rb") as fh:
        head = fh.read(1024)
    if b"%PDF-" not in head:
        raise PdfProblem("unsupported", f"{path.name} does not have a PDF header")


def copy_and_hash(src: Path, dest: Path, chunk: int = 4 * 1024 * 1024) -> tuple[str, int]:
    """Stream-copy ``src`` to ``dest`` while hashing; never holds the file in memory."""
    h = hashlib.sha256()
    size = 0
    tmp = dest.with_suffix(dest.suffix + ".part")
    dest.parent.mkdir(parents=True, exist_ok=True)
    with src.open("rb") as fin, tmp.open("wb") as fout:
        while True:
            buf = fin.read(chunk)
            if not buf:
                break
            h.update(buf)
            size += len(buf)
            fout.write(buf)
    shutil.move(str(tmp), str(dest))
    return h.hexdigest(), size


def hash_file(path: Path, chunk: int = 4 * 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        while True:
            buf = fh.read(chunk)
            if not buf:
                break
            h.update(buf)
    return h.hexdigest()


def inspect_pdf(path: Path) -> PdfInfo:
    """Open lazily (PyMuPDF only parses the xref/page tree) and gather metadata."""
    try:
        doc = pymupdf.open(path)
    except Exception as exc:  # pymupdf raises various types for corrupt files
        raise PdfProblem("corrupt", f"The PDF could not be opened: {exc}") from exc
    try:
        warnings: list[str] = []
        encrypted = bool(doc.is_encrypted)
        if doc.needs_pass:
            # Try an empty user password (common for "print-protected" manuals).
            if not doc.authenticate(""):
                raise PdfProblem("encrypted", "The PDF is password protected and cannot be read.")
            warnings.append("Document is encrypted with an empty user password; opened read-only.")
        if not doc.is_pdf:
            raise PdfProblem("unsupported", "File is not a PDF document")
        page_count = doc.page_count
        if page_count == 0:
            raise PdfProblem("empty", "The PDF has no pages")
        if doc.is_repaired:
            warnings.append("The PDF structure was damaged and has been repaired on open; verify page content.")
        meta = {k: v for k, v in (doc.metadata or {}).items() if v}
        title = (meta.get("title") or "").strip()
        return PdfInfo(page_count=page_count, title=title, metadata=meta, encrypted=encrypted, warnings=warnings)
    finally:
        doc.close()


def open_document(path: str | Path) -> pymupdf.Document:
    doc = pymupdf.open(path)
    if doc.needs_pass and not doc.authenticate(""):
        doc.close()
        raise PdfProblem("encrypted", "The PDF is password protected and cannot be read.")
    return doc
