"""OCR engines for scanned pages. Selected by settings.ingest.ocr_engine (auto | tesseract | rapidocr | none).

* tesseract — best quality for Polish (keeps diacritics); needs the system binary + 'pol' language data.
* rapidocr  — pip-only fallback (PP-OCRv5 latin model); loses some Polish diacritics → rely on LLM correction.

TODO(ingest):
  * image preprocessing (deskew, binarisation, denoise) before OCR,
  * word-level bboxes from OCR (pytesseract.image_to_data / rapidocr boxes) → Page.words (scaled to PDF points),
  * per-page OCR confidence → lower field confidence for low-quality pages,
  * optional VLM engine (vision LLM via API) for very bad scans.
"""

from __future__ import annotations

import logging
import shutil
from functools import lru_cache
from pathlib import Path
from typing import Protocol

import pypdfium2 as pdfium

from odwolania2json.models import Page
from odwolania2json.settings import IngestSettings

log = logging.getLogger(__name__)


class OCREngine(Protocol):
    name: str

    def ocr_page(self, pdf_path: Path, page_index: int, page: Page, settings: IngestSettings) -> Page: ...


def render_page(pdf_path: Path, page_index: int, dpi: int = 300):
    """Render one PDF page to a PIL image."""
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        return pdf[page_index].render(scale=dpi / 72).to_pil()
    finally:
        pdf.close()


class TesseractEngine:
    name = "tesseract"

    def __init__(self, cmd: str | None = None):
        import pytesseract

        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        self._pt = pytesseract

    def ocr_page(self, pdf_path: Path, page_index: int, page: Page, settings: IngestSettings) -> Page:
        img = render_page(pdf_path, page_index, settings.ocr_dpi)
        page.text = self._pt.image_to_string(img, lang=settings.ocr_lang, config="--psm 3")
        page.source, page.ocr_engine = "ocr", self.name
        return page


class RapidOCREngine:
    name = "rapidocr"

    def __init__(self):
        from rapidocr import LangRec, ModelType, OCRVersion, RapidOCR

        logging.getLogger("RapidOCR").setLevel(logging.WARNING)
        self._engine = RapidOCR(
            params={
                "Det.ocr_version": OCRVersion.PPOCRV5,
                "Det.model_type": ModelType.MOBILE,
                "Rec.ocr_version": OCRVersion.PPOCRV5,
                "Rec.model_type": ModelType.MOBILE,
                "Rec.lang_type": LangRec.LATIN,
            }
        )

    def ocr_page(self, pdf_path: Path, page_index: int, page: Page, settings: IngestSettings) -> Page:
        import numpy as np

        img = render_page(pdf_path, page_index, min(settings.ocr_dpi, 200))
        res = self._engine(np.array(img.convert("RGB")))
        page.text = "\n".join(res.txts or [])
        page.source, page.ocr_engine = "ocr", self.name
        return page


@lru_cache(maxsize=4)
def _make_engine(name: str, tesseract_cmd: str | None) -> OCREngine | None:
    if name in ("auto", "tesseract"):
        if tesseract_cmd or shutil.which("tesseract"):
            try:
                return TesseractEngine(tesseract_cmd)
            except Exception as e:  # noqa: BLE001
                log.warning("Tesseract unavailable: %s", e)
        if name == "tesseract":
            log.warning("OCR engine 'tesseract' requested but binary not found")
            return None
    if name in ("auto", "rapidocr"):
        try:
            return RapidOCREngine()
        except Exception as e:  # noqa: BLE001
            log.warning("RapidOCR unavailable: %s", e)
    return None


def get_ocr_engine(settings: IngestSettings) -> OCREngine | None:
    if settings.ocr_engine == "none":
        return None
    engine = _make_engine(settings.ocr_engine, settings.tesseract_cmd)
    if engine is None:
        log.warning("No OCR engine available — scanned pages will be empty")
    return engine
