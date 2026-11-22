"""PDF loading: text layer via pdfplumber; scanned pages / garbage text layers go to OCR (see ocr.py).

Per page:
  * text layer (pdfplumber extract_text / extract_words — words with bboxes for highlighting in the UI),
  * OCR instead when the text layer is too short (min_chars_per_page), garbage (min_text_quality: "(cid:12)"
    glyphs, private-use / replacement characters…) or the page is a scan with only a few characters of text
    layer on top (image_page_min_chars, e.g. a stamp added by an e-filing system),
  * de-hyphenation of words split across line ends ("postępo-\\nwania" → "postępowania\\n"); the line
    structure is kept (the rest of the next line stays on its own line).
Documents are cached (cache.py): OCR runs once per file + settings. If pdfplumber cannot parse a file or finds
no page in it (pdfminer gives up on some scanner PDFs with a damaged xref), PDFium reads it instead: its text
layer where usable, OCR otherwise.

TODO(ingest): justified-text artefacts (single words split into separate lines) without breaking real lines.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pdfplumber
import pypdfium2 as pdfium

from odwolania2json.ingest.cache import IngestCache
from odwolania2json.ingest.ocr import PDFIUM_LOCK, OCREngine, engine_label, get_ocr_engine
from odwolania2json.models import Document, Page, Word
from odwolania2json.settings import IngestSettings

log = logging.getLogger(__name__)

# characters that are normal in Polish documents besides letters and digits
_COMMON_PUNCT = set(".,;:!?()[]{}<>-–—‐‑'\"„”“«»‚‘’/\\|%&@#*+=_~^`§°€$£©®•·…")
_FILLER_RUN = re.compile(r"([^\w\s])\1{2,}")  # dot leaders "......", rules "-----", "****"
_CID = re.compile(r"\(cid:\d+\)")
# word fragment + hyphen at line end, then the first token of the next line (with its punctuation); the
# lookbehind starts a match only at the beginning of a letter run — without it a long run of letters is retried
# from every position (quadratic: a 30k-letter line took ~18 s)
_HYPHEN_BREAK = re.compile(r"(?<![^\W\d_])([^\W\d_]{2,})([-\xad¬])[ \t]*\n[ \t]*([^\W\d_]\S*)[ \t]*")
_LETTERS = re.compile(r"[^\W\d_]+")


def text_quality(text: str) -> float:
    """Share of plausible characters (letters, digits, common punctuation) among non-space characters.

    "(cid:NN)" glyph placeholders, replacement/private-use/control characters count as garbage; runs of repeated
    punctuation (dot leaders, horizontal rules) are ignored. 1.0 for a normal text layer.
    """
    cid_chars = sum(len(m) for m in _CID.findall(text))
    rest = _FILLER_RUN.sub("", _CID.sub("", text))
    chars = [c for c in rest if not c.isspace()]
    total = len(chars) + cid_chars
    if not total:
        return 0.0
    good = sum(1 for c in chars if c.isalnum() or c in _COMMON_PUNCT)
    return good / total


def dehyphenate(text: str, context: str | None = None) -> str:
    """Join words split across line ends when both parts are lowercase letters; keep the line structure.

    'prowadzonego postępo-\\nwania, o udzielenie' → 'prowadzonego postępowania,\\no udzielenie' (the first token of
    the next line moves up, the rest of that line stays on its own line).
    Not joined: 'Bielsko-\\nBiała' (capital), '5-\\ndniowy' (digit), 'e-\\nmail' (one-letter head), '-\\nlista'.
    A compound that is written with the hyphen elsewhere in `context` (the whole document) and never without it
    keeps the hyphen: 'architektoniczno-\\nbudowlany' → 'architektoniczno-budowlany'.
    """
    if "\n" not in text:
        return text.replace("\xad", "")
    ctx = (context if context is not None else text).casefold()

    def join(m: re.Match) -> str:
        head, mark, token = m.group(1), m.group(2), m.group(3)
        word = _LETTERS.match(token).group(0)
        if not (head[-1].islower() and word[0].islower()):
            return m.group(0)
        hyphenated, joined = f"{head}-{word}".casefold(), f"{head}{word}".casefold()
        keep = mark == "-" and hyphenated in ctx and joined not in ctx
        out = head + ("-" if keep else "") + token
        rest_of_line_empty = m.end() >= len(m.string) or m.string[m.end()] == "\n"
        return out if rest_of_line_empty else out + "\n"

    for _ in range(3):  # a one-word line that is itself hyphenated needs another pass
        new = _HYPHEN_BREAK.sub(join, text)
        if new == text:
            break
        text = new
    return text.replace("\xad", "")


def _image_coverage(page) -> float:
    """Largest share of the page area covered by a single image (pdfplumber page)."""
    area = float(page.width * page.height) or 1.0
    best = 0.0
    for im in page.images:
        w = max(0.0, min(float(im["x1"]), float(page.width)) - max(float(im["x0"]), 0.0))
        h = max(0.0, min(float(im["bottom"]), float(page.height)) - max(float(im["top"]), 0.0))
        best = max(best, w * h / area)
    return best


def ocr_reason(text: str, page, settings: IngestSettings) -> str | None:
    """Why the page should be OCR-ed instead of using its text layer (None = keep the text layer)."""
    n = len(text.strip())
    if n < settings.min_chars_per_page:
        return "short"
    if text_quality(text) < settings.min_text_quality:
        return "garbage"
    if n < settings.image_page_min_chars and page is not None and _image_coverage(page) >= 0.5:
        return "image"
    return None


class _LazyOCR:
    def __init__(self, settings: IngestSettings):
        self.settings = settings
        self._engine: OCREngine | None = None
        self._tried = False

    def get(self) -> OCREngine | None:
        if not self._tried:
            self._engine, self._tried = get_ocr_engine(self.settings), True
        return self._engine


def _ocr_into(page: Page, ocr: _LazyOCR, path: Path, index: int, settings: IngestSettings) -> bool:
    """OCR the page in place. False when no engine is available or OCR failed (page keeps its text layer)."""
    engine = ocr.get()
    if engine is None:
        return False
    text, words = page.text, page.words
    try:
        engine.ocr_page(path, index, page, settings)
        return True
    except Exception as e:  # noqa: BLE001 — one bad page must not stop the document
        log.warning("OCR failed for %s page %d: %s", path.name, index + 1, e)
        page.text, page.words, page.source = text, words, "text"
        return False


def _read_with_pdfplumber(path: Path, settings: IngestSettings, ocr: _LazyOCR) -> tuple[list[Page], bool]:
    pages: list[Page] = []
    complete = True
    with pdfplumber.open(path) as pdf:
        for i, p in enumerate(pdf.pages[: settings.max_pages]):
            try:
                text = p.extract_text() or ""
                words = [Word(w["text"], (w["x0"], w["top"], w["x1"], w["bottom"])) for w in p.extract_words()]
                reason = ocr_reason(text, p, settings)
            except Exception as e:  # noqa: BLE001 — unreadable text layer of one page → OCR that page
                log.warning("Text layer of %s page %d unreadable (%s) — OCR", path.name, i + 1, e)
                text, words, reason = "", [], "error"
            page = Page(number=i + 1, text=text, words=words, width=float(p.width), height=float(p.height))
            if reason:
                if _ocr_into(page, ocr, path, i, settings):
                    log.debug("%s p.%d: OCR (%s)", path.name, i + 1, reason)
                else:
                    complete = False
                    if not text.strip():
                        page.source = "empty"
            pages.append(page)
    return pages, complete


def _pdfium_text(page) -> str:
    """Text layer of a PDFium page ("" when it cannot be read)."""
    try:
        textpage = page.get_textpage()
        try:
            text = textpage.get_text_range()
        finally:
            textpage.close()
    except Exception:  # noqa: BLE001 — no usable text layer → OCR
        return ""
    return text.replace("\r\n", "\n").replace("\r", "\n")


def _read_with_pdfium(path: Path, settings: IngestSettings, ocr: _LazyOCR) -> tuple[list[Page], bool]:
    """Fallback for files pdfplumber cannot read (exception, or no pages found — e.g. scanner PDFs with a damaged
    xref that PDFium still opens): page sizes and text layer from PDFium (no word boxes), OCR where the text
    layer is missing or garbage."""
    infos: list[tuple[float, float, str]] = []
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(path))
        try:
            for i in range(min(len(pdf), settings.max_pages)):
                page = pdf[i]
                try:
                    w, h = page.get_size()
                    infos.append((float(w), float(h), _pdfium_text(page)))
                finally:
                    page.close()
        finally:
            pdf.close()
    pages, complete = [], True
    for i, (w, h, text) in enumerate(infos):
        page = Page(number=i + 1, text=text, width=w, height=h)
        if reason := ocr_reason(text, None, settings):
            if _ocr_into(page, ocr, path, i, settings):
                log.debug("%s p.%d: OCR (%s)", path.name, i + 1, reason)
            else:
                complete = False
                if not text.strip():
                    page.source = "empty"
        pages.append(page)
    return pages, complete


def read_pages(path: Path, settings: IngestSettings) -> tuple[list[Page], bool]:
    """Pages of the PDF (no cache). The flag is False when some page needed OCR that was not available."""
    ocr = _LazyOCR(settings)
    error: Exception | None = None
    try:
        pages, complete = _read_with_pdfplumber(path, settings, ocr)
    except Exception as e:  # noqa: BLE001 — broken/unusual PDF: read it with PDFium (text layer or OCR)
        log.warning("pdfplumber failed on %s (%s) — reading it with PDFium", path.name, e)
        pages, complete, error = [], True, e
    if not pages:
        # pdfminer finds no page at all in some scanner PDFs (damaged xref / page tree) that PDFium opens fine
        try:
            pages, complete = _read_with_pdfium(path, settings, ocr)
        except Exception as e:  # noqa: BLE001
            if error is not None:
                raise
            log.warning("PDFium cannot read %s either (%s)", path.name, e)
    context = "\n".join(p.text for p in pages)
    for page in pages:
        page.text = dehyphenate(page.text, context)
    return pages, complete


def load_document(path: Path, settings: IngestSettings | None = None) -> Document:
    """PDF → Document, transparently cached (settings.cache_dir)."""
    settings = settings or IngestSettings()
    path = Path(path)
    cache = IngestCache.from_settings(settings)
    key = label = None
    if cache is not None:
        try:
            label = engine_label(settings)
            key = cache.key(path, settings, label)
            if (pages := cache.get(key)) is not None:
                return Document(path=path, pages=pages)
        except OSError as e:
            log.warning("Ingest cache unavailable for %s: %s", path.name, e)
            key = None
    pages, complete = read_pages(path, settings)
    # cache only complete results produced by the expected engine (a failed model download falls back to another)
    engines = {p.ocr_engine for p in pages if p.source == "ocr"}
    if cache is not None and key is not None and complete and engines <= {label}:
        cache.put(key, pages, path)
    return Document(path=path, pages=pages)
