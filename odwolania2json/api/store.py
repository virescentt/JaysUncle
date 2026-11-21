"""Server-side session store for documents uploaded through the web panel.

Each processed upload gets a random id and a folder in a temporary directory:
    <root>/<id>/document.pdf            the uploaded file under a fixed ASCII name (the original name is kept
                                        in StoredDoc.filename) — no Windows device names (CON.pdf, NUL.pdf),
                                        no over-long paths, nothing user-controlled on the file system
    <root>/<id>/output.json             the exact file the CLI would write (download button)
    <root>/<id>/pages/<n>@<dpi>.png     rendered page previews (cache)
The JSON payload shown by the UI is kept in memory. Old entries are removed by TTL (since last access) and LRU
(max number of documents); the whole root is removed when the app shuts down.

Thread safety: pypdfium2 is NOT thread-safe (concurrent calls from threads fail or crash the process). Every
pdfium call of the application — the ingest (OCR rendering) and the previews rendered here — goes through the
shared `ingest.ocr.PDFIUM_LOCK`, held only for the duration of the call. Documents are processed on a small
dedicated pool (`PdfWorker`) so a long OCR job never blocks the event loop; previews do not queue behind it.
"""

from __future__ import annotations

import io
import logging
import math
import os
import re
import secrets
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TypeVar

from odwolania2json.ingest.ocr import PDFIUM_LOCK

log = logging.getLogger(__name__)

T = TypeVar("T")

DOC_ID_RE = re.compile(r"[a-f0-9]{16}\Z")  # \Z, not $: "$" would also accept a trailing newline
DEFAULT_DPI = 110
MIN_DPI, MAX_DPI = 36, 220
MAX_RENDER_PIXELS = 16_000_000  # preview cap: A3 at 220 dpi ≈ 9.4 Mpx fits; a crafted 200-inch page stays small
STORE_PREFIX = "odwolania2json-web-"
STORED_PDF_NAME = "document.pdf"


class PdfWorker:
    """Small dedicated thread pool for document processing (pipeline: ingest/OCR, classification, extraction).

    pdfium calls inside are serialized by PDFIUM_LOCK and OCR inference by the engine's own lock, so a few
    documents can be in flight at once (useful when the LLM is the bottleneck); `max_workers=1` = strictly
    one document at a time.
    """

    def __init__(self, max_workers: int = 2) -> None:
        self.max_workers = max(1, int(max_workers))
        self._pool: ThreadPoolExecutor | None = None
        self._lock = threading.Lock()

    def submit(self, fn: Callable[..., T], *args: Any) -> Future[T]:
        with self._lock:
            if self._pool is None:
                self._pool = ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix="pdf-worker")
            return self._pool.submit(fn, *args)

    def run(self, fn: Callable[..., T], *args: Any) -> T:
        """Run on the worker pool and wait (for sync code paths)."""
        return self.submit(fn, *args).result()

    def shutdown(self) -> None:
        with self._lock:
            if self._pool is not None:
                self._pool.shutdown(wait=False, cancel_futures=True)
                self._pool = None


def safe_filename(name: str | None, default: str = "dokument.pdf") -> str:
    """Base name only, no path separators / control characters, reasonable length."""
    base = Path(str(name or "").replace("\\", "/")).name
    base = re.sub(r"[\x00-\x1f\x7f/:*?\"<>|]", "_", base).strip(" .")
    if not base:
        return default
    if len(base) > 150:
        stem, dot, ext = base.rpartition(".")
        base = (stem[:140] + dot + ext) if dot else base[:150]
    return base


def render_scale(width_pt: float, height_pt: float, dpi: int) -> float:
    """Scale factor for rendering at `dpi`, reduced so that the bitmap stays below MAX_RENDER_PIXELS."""
    scale = dpi / 72
    area = max(1.0, float(width_pt) * float(height_pt))
    return min(scale, math.sqrt(MAX_RENDER_PIXELS / area))


def render_page_png(pdf_path: Path, page_number: int, dpi: int, out_path: Path) -> Path:
    """Render one page (1-based) to PNG (thread-safe: pdfium work under PDFIUM_LOCK, PNG encoding outside)."""
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            if not 1 <= page_number <= len(pdf):
                raise IndexError(f"page {page_number} out of range 1..{len(pdf)}")
            page = pdf[page_number - 1]
            try:
                bitmap = page.render(scale=render_scale(*page.get_size(), dpi))
                try:
                    image = bitmap.to_pil().copy()  # independent of the pdfium buffer, which is freed below
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            pdf.close()
    buf = io.BytesIO()
    image.convert("RGB").save(buf, "PNG", compress_level=6)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    # unique temp name: two requests may render the same page at the same time
    fd, tmp = tempfile.mkstemp(dir=out_path.parent, prefix=f".{out_path.stem}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(buf.getvalue())
        try:
            os.replace(tmp, out_path)
        except OSError:  # Windows: the target is being sent to a client right now → it already exists, keep it
            if not out_path.exists():
                raise
    finally:
        Path(tmp).unlink(missing_ok=True)
    return out_path


def pdf_page_count(pdf_path: Path) -> int:
    """Number of pages of the PDF (0 if it cannot be opened)."""
    import pypdfium2 as pdfium

    with PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(str(pdf_path))
        except Exception:  # noqa: BLE001 — broken upload: the pipeline reports the real error
            return 0
        try:
            return len(pdf)
        finally:
            pdf.close()


@dataclass
class StoredDoc:
    id: str
    filename: str  # original (sanitized) file name — shown in the UI, used for the .json / .pdf download names
    dir: Path
    pdf_path: Path
    created: float = field(default_factory=time.time)
    accessed: float = field(default_factory=time.time)
    payload: dict[str, Any] = field(default_factory=dict)
    page_count: int = 0

    @property
    def output_path(self) -> Path:
        return self.dir / "output.json"

    def page_png_path(self, page_number: int, dpi: int) -> Path:
        return self.dir / "pages" / f"{page_number}@{dpi}.png"


class DocStore:
    """Thread-safe store of uploaded documents with TTL + LRU cleanup."""

    def __init__(self, root: Path | None = None, max_docs: int = 200, ttl_s: float = 6 * 3600):
        self._root_arg = Path(root) if root else None
        self._root: Path | None = None
        self.max_docs = max_docs
        self.ttl_s = ttl_s
        self._docs: OrderedDict[str, StoredDoc] = OrderedDict()
        self._lock = threading.RLock()

    # ------------------------------------------------------------------ root dir
    @property
    def root(self) -> Path:
        with self._lock:
            if self._root is None:
                if self._root_arg is not None:
                    self._root_arg.mkdir(parents=True, exist_ok=True)
                    self._root = self._root_arg
                else:
                    _remove_stale_roots()
                    self._root = Path(tempfile.mkdtemp(prefix=STORE_PREFIX))
            return self._root

    def close(self) -> None:
        """Remove all stored files (app shutdown)."""
        with self._lock:
            self._docs.clear()
            if self._root is not None:
                shutil.rmtree(self._root, ignore_errors=True)
                self._root = None

    # ------------------------------------------------------------------ entries
    def new(self, filename: str) -> StoredDoc:
        """Create an empty entry (folder) for an upload; the caller writes the PDF to `pdf_path`."""
        self.cleanup()
        doc_id = secrets.token_hex(8)
        d = self.root / doc_id
        d.mkdir(parents=True)
        doc = StoredDoc(id=doc_id, filename=safe_filename(filename), dir=d, pdf_path=d / STORED_PDF_NAME)
        with self._lock:
            self._docs[doc_id] = doc
        return doc

    def get(self, doc_id: str) -> StoredDoc | None:
        if not DOC_ID_RE.match(doc_id or ""):
            return None
        with self._lock:
            doc = self._docs.get(doc_id)
            if doc is None:
                return None
            if time.time() - doc.accessed > self.ttl_s:
                self._drop(doc_id)
                return None
            doc.accessed = time.time()
            self._docs.move_to_end(doc_id)
            return doc

    def delete(self, doc_id: str) -> bool:
        with self._lock:
            if doc_id not in self._docs:
                return False
            self._drop(doc_id)
            return True

    def __len__(self) -> int:
        with self._lock:
            return len(self._docs)

    def cleanup(self) -> None:
        now = time.time()
        with self._lock:
            for doc_id in [k for k, d in self._docs.items() if now - d.accessed > self.ttl_s]:
                self._drop(doc_id)
            while len(self._docs) >= self.max_docs:
                self._drop(next(iter(self._docs)))  # least recently used

    def _drop(self, doc_id: str) -> None:
        doc = self._docs.pop(doc_id, None)
        if doc is not None:
            shutil.rmtree(doc.dir, ignore_errors=True)


def _remove_stale_roots(max_age_s: float = 24 * 3600) -> None:
    """Remove store folders left behind by servers that were killed (older than a day)."""
    tmp = Path(tempfile.gettempdir())
    now = time.time()
    for d in tmp.glob(STORE_PREFIX + "*"):
        try:
            if d.is_dir() and now - d.stat().st_mtime > max_age_s:
                shutil.rmtree(d, ignore_errors=True)
        except OSError:
            continue
