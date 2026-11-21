"""PDF loading: text layer via pdfplumber; pages without text go to OCR (see ocr.py).

TODO(ingest):
  * fix justified-text artefacts (single words split into separate lines) without breaking real line structure,
  * de-hyphenation of words split across lines ("postępo-\nwania"),
  * detect mixed pages (text layer present but garbage/too short) → OCR anyway,
  * keep words with bboxes for OCR pages too (needed for highlighting in the UI).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pdfplumber

from odwolania2json.ingest.ocr import get_ocr_engine
from odwolania2json.models import Document, Page, Word
from odwolania2json.settings import IngestSettings

log = logging.getLogger(__name__)


def load_document(path: Path, settings: IngestSettings | None = None) -> Document:
    settings = settings or IngestSettings()
    pages: list[Page] = []
    ocr = None
    with pdfplumber.open(path) as pdf:
        for i, p in enumerate(pdf.pages[: settings.max_pages]):
            text = p.extract_text() or ""
            words = [Word(w["text"], (w["x0"], w["top"], w["x1"], w["bottom"])) for w in p.extract_words()]
            page = Page(number=i + 1, text=text, words=words, width=float(p.width), height=float(p.height))
            if len(text.strip()) < settings.min_chars_per_page:
                ocr = ocr or get_ocr_engine(settings)
                if ocr is not None:
                    page = ocr.ocr_page(path, i, page, settings)
                else:
                    page.source = "empty"
            pages.append(page)
    return Document(path=Path(path), pages=pages)
