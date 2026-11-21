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
from PIL import Image, ImageFilter, ImageOps

from odwolania2json.models import Page, Word
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


def preprocess_image(image: Image.Image) -> Image.Image:
    result = ImageOps.grayscale(image)
    result = ImageOps.autocontrast(result)
    return result.filter(ImageFilter.MedianFilter(size=3))


def scaled_bbox(
    left: float,
    top: float,
    width: float,
    height: float,
    image_width: float,
    image_height: float,
    page_width: float,
    page_height: float,
) -> tuple[float, float, float, float]:
    sx = page_width / image_width
    sy = page_height / image_height
    return (left * sx, top * sy, (left + width) * sx, (top + height) * sy)


class TesseractEngine:
    name = "tesseract"

    def __init__(self, cmd: str | None = None):
        import pytesseract

        if cmd:
            pytesseract.pytesseract.tesseract_cmd = cmd
        self._pt = pytesseract

    def ocr_page(self, pdf_path: Path, page_index: int, page: Page, settings: IngestSettings) -> Page:
        img = render_page(pdf_path, page_index, settings.ocr_dpi)
        img = preprocess_image(img)
        data = self._pt.image_to_data(
            img,
            lang=settings.ocr_lang,
            config="--psm 3",
            output_type=self._pt.Output.DICT,
        )
        page.words = []
        confidences: list[float] = []
        text_lines: dict[tuple[int, int, int, int], list[str]] = {}
        for i, raw_text in enumerate(data["text"]):
            text = str(raw_text).strip()
            if not text:
                continue
            confidence = float(data["conf"][i])
            if confidence >= 0:
                confidences.append(confidence / 100.0)
            if confidence < 45:
                continue
            bbox = scaled_bbox(
                float(data["left"][i]),
                float(data["top"][i]),
                float(data["width"][i]),
                float(data["height"][i]),
                float(img.width),
                float(img.height),
                page.width,
                page.height,
            )
            page.words.append(Word(text, bbox))
            line_key = (
                int(data["block_num"][i]),
                int(data["par_num"][i]),
                int(data["line_num"][i]),
                int(data["word_num"][i]),
            )
            text_lines.setdefault(line_key[:3], []).append(text)
        page.text = "\n".join(
            " ".join(text_lines[key])
            for key in sorted(text_lines)
        ).strip()
        page.ocr_confidence = sum(confidences) / len(confidences) if confidences else None
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
        img = preprocess_image(img)
        res = self._engine(np.array(img.convert("RGB")))
        raw_texts = getattr(res, "txts", None)
        raw_boxes = getattr(res, "boxes", None)
        raw_scores = getattr(res, "scores", None)
        texts = list(raw_texts) if raw_texts is not None else []
        boxes = list(raw_boxes) if raw_boxes is not None else []
        scores = list(raw_scores) if raw_scores is not None else []
        page.text = "\n".join(texts).strip()
        page.words = []
        for index, text in enumerate(texts):
            if index >= len(boxes):
                continue
            box = boxes[index]
            xs = [float(point[0]) for point in box]
            ys = [float(point[1]) for point in box]
            bbox = scaled_bbox(
                min(xs),
                min(ys),
                max(xs) - min(xs),
                max(ys) - min(ys),
                float(img.width),
                float(img.height),
                page.width,
                page.height,
            )
            page.words.append(Word(str(text), bbox))
        valid_scores = [float(score) for score in scores if score is not None]
        page.ocr_confidence = sum(valid_scores) / len(valid_scores) if valid_scores else None
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
