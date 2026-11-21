"""PDF → Document (text layer, OCR for scanned pages, cached). See pdf.py, ocr.py, cache.py.

load_document(path, settings)  — pages with text + words (bboxes in PDF points) — what the pipeline uses
render_page(path, index, dpi)  — page image (PIL), thread-safe (e.g. page previews in the web UI)
"""

from odwolania2json.ingest.ocr import PDFIUM_LOCK, render_page
from odwolania2json.ingest.pdf import load_document

__all__ = ["PDFIUM_LOCK", "load_document", "render_page"]
