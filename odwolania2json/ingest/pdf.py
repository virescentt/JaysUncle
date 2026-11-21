"""PDF loading: text layer via pdfplumber; pages without text go to OCR (see ocr.py).

TODO(ingest):
  * fix justified-text artefacts (single words split into separate lines) without breaking real line structure,
  * de-hyphenation of words split across lines ("postępo-\nwania"),
  * detect mixed pages (text layer present but garbage/too short) → OCR anyway,
  * keep words with bboxes for OCR pages too (needed for highlighting in the UI).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path

import pdfplumber

from odwolania2json.ingest.ocr import get_ocr_engine
from odwolania2json.models import Document, Page, Word
from odwolania2json.settings import IngestSettings

log = logging.getLogger(__name__)


def clean_extracted_text(text: str) -> str:
    text = text.replace("\u00ad", "")
    text = re.sub(r"(?<=\w)-[ \t]*\n[ \t]*(?=\w)", "", text)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def needs_ocr(text: str, min_chars: int) -> bool:
    cleaned = text.strip()
    if len(cleaned) < min_chars:
        return True
    alphanumeric = sum(character.isalnum() for character in cleaned)
    ratio = alphanumeric / max(1, len(cleaned))
    return ratio < 0.45 or cleaned.count("\ufffd") >= 3


def _cache_path(path: Path, settings: IngestSettings) -> Path:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    config = json.dumps(
        {"schema": "ingest-v2", **settings.model_dump()},
        sort_keys=True,
    )
    key = hashlib.sha256(f"{digest}:{config}".encode()).hexdigest()
    cache_dir = Path(__file__).resolve().parents[2] / ".cache" / "ingest"
    cache_dir.mkdir(parents=True, exist_ok=True)
    return cache_dir / f"{key}.json"


def _save_cache(cache_path: Path, document: Document) -> None:
    cache_path.write_text(
        json.dumps(
            {
                "path": str(document.path),
                "pages": [
                    {
                        "number": page.number,
                        "text": page.text,
                        "words": [{"text": word.text, "bbox": word.bbox} for word in page.words],
                        "width": page.width,
                        "height": page.height,
                        "source": page.source,
                        "ocr_engine": page.ocr_engine,
                        "ocr_confidence": getattr(page, "ocr_confidence", None),
                    }
                    for page in document.pages
                ],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def _load_cache(cache_path: Path) -> Document | None:
    if not cache_path.exists():
        return None
    try:
        data = json.loads(cache_path.read_text(encoding="utf-8"))
        pages = []
        for item in data["pages"]:
            page = Page(
                number=item["number"],
                text=item["text"],
                words=[Word(word["text"], tuple(word["bbox"])) for word in item["words"]],
                width=item["width"],
                height=item["height"],
                source=item["source"],
                ocr_engine=item["ocr_engine"],
            )
            page.ocr_confidence = item.get("ocr_confidence")
            pages.append(page)
        return Document(path=Path(data["path"]), pages=pages)
    except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
        cache_path.unlink(missing_ok=True)
        return None


def load_document(path: Path, settings: IngestSettings | None = None) -> Document:
    settings = settings or IngestSettings()
    cache_path = _cache_path(path, settings)
    cached = _load_cache(cache_path)
    if cached is not None:
        return cached

    pages: list[Page] = []
    ocr = None
    with pdfplumber.open(path) as pdf:
        for i, p in enumerate(pdf.pages[: settings.max_pages]):
            text = clean_extracted_text(p.extract_text() or "")
            words = [Word(w["text"], (w["x0"], w["top"], w["x1"], w["bottom"])) for w in p.extract_words()]
            page = Page(number=i + 1, text=text, words=words, width=float(p.width), height=float(p.height))
            if needs_ocr(text, settings.min_chars_per_page):
                ocr = ocr or get_ocr_engine(settings)
                if ocr is not None:
                    page = ocr.ocr_page(path, i, page, settings)
                else:
                    page.source = "empty"
            pages.append(page)
    document = Document(path=Path(path), pages=pages)
    _save_cache(cache_path, document)
    return document


def document_to_json(document: Document) -> dict:
    """Serialize an ingested PDF for direct PDF → JSON conversion."""
    return {
        "source": str(document.path),
        "pages": len(document.pages),
        "scanned": document.is_scanned,
        "text": document.text,
        "pages_detail": [
            {
                "number": page.number,
                "text": page.text,
                "source": page.source,
                "ocr_engine": page.ocr_engine,
                "ocr_confidence": getattr(page, "ocr_confidence", None),
                "words": [
                    {
                        "text": word.text,
                        "bbox": list(word.bbox),
                    }
                    for word in page.words
                ],
            }
            for page in document.pages
        ],
    }


def pdf_to_json(
    pdf_path: str | Path,
    output_path: str | Path | None = None,
    settings: IngestSettings | None = None,
) -> Path:
    """Convert one PDF to JSON beside the PDF or at an explicit output path."""
    pdf_path = Path(pdf_path)
    if not pdf_path.is_file():
        raise FileNotFoundError(pdf_path)
    if pdf_path.suffix.lower() != ".pdf":
        raise ValueError(f"Expected a PDF file: {pdf_path}")

    json_path = Path(output_path) if output_path is not None else pdf_path.with_suffix(".json")
    if json_path.suffix.lower() != ".json":
        json_path = json_path / pdf_path.with_suffix(".json").name
    json_path.parent.mkdir(parents=True, exist_ok=True)

    document = load_document(pdf_path, settings)
    json_path.write_text(
        json.dumps(document_to_json(document), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return json_path


def pdf_folder_to_json(
    input_dir: str | Path,
    output_dir: str | Path,
    settings: IngestSettings | None = None,
) -> list[Path]:
    """Convert all PDFs recursively while mirroring the folder structure."""
    input_dir = Path(input_dir)
    output_dir = Path(output_dir)
    if not input_dir.is_dir():
        raise NotADirectoryError(input_dir)

    results: list[Path] = []
    for pdf_path in sorted(input_dir.rglob("*")):
        if not pdf_path.is_file() or pdf_path.suffix.lower() != ".pdf":
            continue
        relative = pdf_path.relative_to(input_dir)
        json_path = output_dir / relative.with_suffix(".json")
        results.append(pdf_to_json(pdf_path, json_path, settings))
    return results
