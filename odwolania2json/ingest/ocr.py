"""OCR for scanned pages. Engine: settings.ingest.ocr_engine = auto | rapidocr | easyocr | tesseract | none.

* rapidocr  — default, pip-only (onnxruntime, CPU). PP-OCRv6 multilingual models read Polish diacritics; the
              'small' detection + recognition models ship inside the rapidocr wheel, so nothing is downloaded on
              first run. Other models (PP-OCRv6-tiny / -medium, PP-OCRv5-latin) are downloaded once on first use.
* easyocr   — optional (requirements-ocr.txt, pulls torch ≈ 1 GB); models downloaded on first use (~100 MB).
* tesseract — optional, needs the system binary + 'pol' language data (not installable with pip).
* auto      — the first available of AUTO_ORDER (no model is loaded just to check availability).

Per page:  render (pypdfium2, ocr_dpi; at most MAX_RENDER_PIXELS) → preprocess (Pillow/numpy: grayscale,
autocontrast, binarize, denoise, deskew) → engine.recognize(image) (a page that reads badly is also tried upside
down / sideways) → text segments with pixel boxes → post-correction (fix_text) → reading order (segments grouped
into lines, top-to-bottom, left-to-right; skew-aware) → Page.text + Page.words (PDF points, same coordinates as
pdfplumber: x0, top, x1, bottom, origin top-left) + Page.ocr_confidence.

RapidOCR specifics (measured with scripts/ocr_benchmark.py on the 30 real scans, see config/settings.yaml):
  * recognition on full-resolution crops (RapidOCR's default 2000 px page downscale lost Polish diacritics),
  * lines recognized one at a time (ocr_rec_batch: 1): a batch pads every line to the longest one, and PP-OCRv6
    then dropped more spaces between words and ran ~2× slower,
  * low-confidence lines are re-read from contrast-enhanced crops (ocr_retry_score) — recovers lines hidden by
    bleed-through, pen strokes or stamps, including lines RapidOCR's own 0.5 filter would have dropped.

Thread safety: engine instances are cached and shared by the document threads (Pipeline.process_many); inference
on one instance is serialized with a lock (onnxruntime / torch use all cores for one call anyway). PDFium is not
thread-safe at all, so every pypdfium2 call goes through PDFIUM_LOCK.
"""

from __future__ import annotations

import importlib.util
import logging
import math
import re
import shutil
import threading
import unicodedata
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter, ImageOps

from odwolania2json.models import Page, Word
from odwolania2json.settings import IngestSettings

log = logging.getLogger(__name__)

AUTO_ORDER = ("rapidocr", "easyocr", "tesseract")
DEFAULT_RAPIDOCR_MODEL = "PP-OCRv6-small"  # bundled in the rapidocr wheel — the fallback when a download fails
PREPROCESS_STEPS = ("grayscale", "autocontrast", "binarize", "denoise", "deskew")

# PDFium (pypdfium2) must never be called from two threads at the same time — not even for different documents.
PDFIUM_LOCK = threading.RLock()

Point = tuple[float, float]
Box = tuple[float, float, float, float]  # x0, y0, x1, y1


# ---------------------------------------------------------------------------------------------------- rendering
# Render size cap: A3 at 300 dpi ≈ 17 Mpx. A poster-size or crafted page (a PDF page may be 200 × 200 inches, i.e.
# 3.6 gigapixels at 300 dpi) is rendered at a lower resolution instead of exhausting the memory.
MAX_RENDER_PIXELS = 40_000_000


def render_scale(width_pt: float, height_pt: float, dpi: float, max_pixels: int | None = None) -> float:
    """Pixels per PDF point for rendering at `dpi`, reduced so that the bitmap stays below `max_pixels`."""
    area = max(1.0, float(width_pt) * float(height_pt))
    return min(dpi / 72, math.sqrt((max_pixels or MAX_RENDER_PIXELS) / area))


def render_page_scaled(pdf_path: Path, page_index: int, dpi: float = 300) -> tuple[Image.Image, float]:
    """Render one PDF page (thread-safe). Returns (image, pixels per PDF point actually used)."""
    with PDFIUM_LOCK:
        pdf = pdfium.PdfDocument(str(pdf_path))
        try:
            page = pdf[page_index]
            try:
                scale = render_scale(*page.get_size(), dpi)
                bitmap = page.render(scale=scale)
                try:
                    return bitmap.to_pil().copy(), scale  # copy: independent of the pdfium buffer freed below
                finally:
                    bitmap.close()
            finally:
                page.close()
        finally:
            pdf.close()


def render_page(pdf_path: Path, page_index: int, dpi: int = 300) -> Image.Image:
    """Render one PDF page to a PIL image (thread-safe; huge pages are capped at MAX_RENDER_PIXELS)."""
    return render_page_scaled(pdf_path, page_index, dpi)[0]


# ------------------------------------------------------------------------------------------------ preprocessing
def otsu_threshold(gray: np.ndarray) -> int:
    """Global Otsu threshold of a uint8 grayscale array."""
    hist = np.bincount(gray.ravel(), minlength=256).astype(np.float64)
    levels = np.arange(256, dtype=np.float64)
    w0 = np.cumsum(hist)
    w1 = w0[-1] - w0
    s0 = np.cumsum(levels * hist)
    m0 = s0 / np.maximum(w0, 1)
    m1 = (s0[-1] - s0) / np.maximum(w1, 1)
    return int(np.argmax(w0 * w1 * (m0 - m1) ** 2))


def estimate_skew(img: Image.Image, max_angle: float = 3.0) -> float:
    """Skew of the text lines in degrees (PIL convention: rotating the image by the result straightens it).

    Projection profile: the ink mask of a downscaled page is rotated over candidate angles; straight text lines
    give the sharpest horizontal profile (max sum of squared row differences). Coarse 0.25° grid, then 0.05°.
    """
    gray = img.convert("L")
    scale = 1000 / max(gray.size)
    if scale < 1:
        gray = gray.resize((max(1, round(gray.width * scale)), max(1, round(gray.height * scale))), Image.BILINEAR)
    arr = np.asarray(gray)
    if arr.size == 0:
        return 0.0
    ink = Image.fromarray(((arr < otsu_threshold(arr)) * 255).astype(np.uint8))
    if np.count_nonzero(np.asarray(ink)) < 0.002 * arr.size:  # (almost) blank page
        return 0.0

    def score(angle: float) -> float:
        rot = np.asarray(ink.rotate(angle, resample=Image.NEAREST, fillcolor=0), dtype=np.float32)
        profile = rot.sum(axis=1)
        return float(np.square(np.diff(profile)).sum())

    coarse = np.arange(-max_angle, max_angle + 1e-9, 0.25)
    best = max(coarse, key=score)
    fine = np.arange(best - 0.25, best + 0.25 + 1e-9, 0.05)
    best = float(max(fine, key=score))
    return round(best, 2) if abs(best) >= 0.1 else 0.0


def preprocess(img: Image.Image, steps: Sequence[str]) -> tuple[Image.Image, float]:
    """Apply preprocessing steps in order. Returns (image, deskew angle in degrees applied with Image.rotate)."""
    angle = 0.0
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    for step in steps:
        if step == "grayscale":
            img = img.convert("L")
        elif step == "autocontrast":
            img = ImageOps.autocontrast(img.convert("L"), cutoff=1)
        elif step == "binarize":
            gray = img.convert("L")
            arr = np.asarray(gray)
            img = Image.fromarray(np.where(arr > otsu_threshold(arr), 255, 0).astype(np.uint8))
        elif step == "denoise":
            img = img.filter(ImageFilter.MedianFilter(3))
        elif step == "deskew":
            angle = estimate_skew(img)
            if angle:
                fill = 255 if img.mode == "L" else (255, 255, 255)
                img = img.rotate(angle, resample=Image.BICUBIC, fillcolor=fill)
        else:
            log.warning("Unknown OCR preprocessing step %r (known: %s)", step, ", ".join(PREPROCESS_STEPS))
    return img, angle


def unrotate_point(x: float, y: float, angle: float, cx: float, cy: float) -> Point:
    """Map a point of an image rotated with Image.rotate(angle) (around (cx, cy)) back to the original image."""
    if not angle:
        return x, y
    a = math.radians(angle)
    dx, dy = x - cx, y - cy
    return cx + dx * math.cos(a) - dy * math.sin(a), cy + dx * math.sin(a) + dy * math.cos(a)


# ------------------------------------------------------------------------------------------- segments & layout
@dataclass
class Segment:
    """One piece of recognized text (usually a line fragment) with its quadrilateral in image pixels."""

    text: str
    quad: list[Point]  # 4 points: top-left, top-right, bottom-right, bottom-left
    score: float = 1.0
    words: list[tuple[str, list[Point]]] | None = None  # word boxes from the engine, if it provides them
    _box: Box = field(init=False, repr=False)

    def __post_init__(self) -> None:
        xs = [p[0] for p in self.quad]
        ys = [p[1] for p in self.quad]
        self._box = (min(xs), min(ys), max(xs), max(ys))

    @property
    def box(self) -> Box:
        return self._box

    @property
    def height(self) -> float:
        (x0, y0), (x1, y1), (x2, y2), (x3, y3) = self.quad
        return max(1.0, (math.hypot(x3 - x0, y3 - y0) + math.hypot(x2 - x1, y2 - y1)) / 2)

    @property
    def center(self) -> Point:
        return sum(p[0] for p in self.quad) / 4, sum(p[1] for p in self.quad) / 4

    @property
    def angle(self) -> float | None:
        """Baseline angle in radians, only for clearly horizontal-ish, elongated segments."""
        (x0, y0), (x1, y1) = self.quad[0], self.quad[1]
        w = math.hypot(x1 - x0, y1 - y0)
        if w < 3 * self.height or x1 <= x0:
            return None
        return math.atan2(y1 - y0, x1 - x0)


def dominant_angle(segments: Sequence[Segment]) -> float:
    angles = sorted(a for s in segments if (a := s.angle) is not None and abs(a) < math.radians(10))
    return angles[len(angles) // 2] if angles else 0.0


def group_lines(segments: Sequence[Segment]) -> list[list[Segment]]:
    """Group segments into text lines (reading order: top-to-bottom, then left-to-right).

    Vertical positions are measured perpendicular to the dominant text direction, so a slightly rotated scan
    still puts a left label and a far-right value on the same line.
    """
    if not segments:
        return []
    tan = math.tan(dominant_angle(segments))

    def y_of(s: Segment) -> float:
        cx, cy = s.center
        return cy - cx * tan

    lines: list[dict] = []  # {"y": mean y, "h": mean height, "segs": [...]}
    for s in sorted(segments, key=y_of):
        y, h = y_of(s), s.height
        best = None
        for line in lines[-4:]:
            d = abs(y - line["y"])
            if d <= 0.5 * min(h, line["h"]) and (best is None or d < best[0]):
                best = (d, line)
        if best is None:
            lines.append({"y": y, "h": h, "segs": [s]})
        else:
            line = best[1]
            n = len(line["segs"])
            line["y"] = (line["y"] * n + y) / (n + 1)
            line["h"] = (line["h"] * n + h) / (n + 1)
            line["segs"].append(s)
    lines.sort(key=lambda ln: ln["y"])
    return [sorted(ln["segs"], key=lambda s: s.box[0]) for ln in lines]


def split_words(seg: Segment) -> list[tuple[str, Box]]:
    """Word boxes of a segment (pixels): the engine's word boxes when consistent, else a proportional split."""
    if seg.words:
        joined = "".join(w for w, _ in seg.words).replace(" ", "")
        if joined == seg.text.replace(" ", ""):
            out = []
            for w, quad in seg.words:
                xs, ys = [p[0] for p in quad], [p[1] for p in quad]
                for part in w.split():
                    out.append((part, (min(xs), min(ys), max(xs), max(ys))))
            if out:
                return out
    x0, y0, x1, y1 = seg.box
    text = seg.text
    n = max(1, len(text))
    out = []
    pos = 0
    for part in text.split():
        start = text.index(part, pos)
        end = start + len(part)
        pos = end
        out.append((part, (x0 + (x1 - x0) * start / n, y0, x0 + (x1 - x0) * end / n, y1)))
    return out


def unturn_point(x: float, y: float, rotation: int, size: tuple[int, int]) -> Point:
    """Map a point of the image turned with Image.transpose(ROTATE_<rotation>) back to the image of `size`."""
    w, h = size
    if rotation == 180:
        return w - x, h - y
    if rotation == 90:  # ROTATE_90 = counter-clockwise: (x, y) → (y, w - x)
        return w - y, x
    if rotation == 270:  # ROTATE_270 = clockwise: (x, y) → (h - y, x)
        return y, h - x
    return x, y


def segments_to_page(
    segments: Sequence[Segment],
    page: Page,
    dpi: float,
    angle: float,
    size: tuple[int, int],
    engine: str,
    rotation: int = 0,
) -> Page:
    """Fill page.text / words / ocr_confidence from engine segments.

    Segments are in pixels of the preprocessed image (`size`), turned by `rotation` degrees when the page was read
    upside down / sideways: the reading order is built in that (upright) frame, word boxes are mapped back to the
    page as stored in the PDF (undo the turn, then the deskew), in PDF points.
    """
    k = 72.0 / dpi
    cx, cy = size[0] / 2, size[1] / 2

    def to_points(b: Box) -> tuple[float, float, float, float]:
        corners = [
            unturn_point(x, y, rotation, size) for x, y in ((b[0], b[1]), (b[2], b[1]), (b[2], b[3]), (b[0], b[3]))
        ]
        corners = [unrotate_point(x, y, angle, cx, cy) for x, y in corners]
        xs, ys = [p[0] * k for p in corners], [p[1] * k for p in corners]
        return (round(min(xs), 2), round(min(ys), 2), round(max(xs), 2), round(max(ys), 2))

    lines_text: list[str] = []
    words: list[Word] = []
    conf_sum = conf_len = 0.0
    for line in group_lines(segments):
        lines_text.append(" ".join(s.text for s in line))
        for s in line:
            conf_sum += s.score * len(s.text)
            conf_len += len(s.text)
            words.extend(Word(w, to_points(b)) for w, b in split_words(s))
    page.text = "\n".join(lines_text)
    page.words = words
    page.source = "ocr"
    page.ocr_engine = engine
    page.ocr_confidence = round(conf_sum / conf_len, 4) if conf_len else None
    if not page.width or not page.height:
        page.width, page.height = round(size[0] * k, 2), round(size[1] * k, 2)
    return page


def _clean(text: str) -> str:
    return " ".join(unicodedata.normalize("NFC", text).split())


# Multilingual models sometimes emit letters that do not exist in Polish ("Lublın", "GDAÑSK", "Lešna",
# "możliwośçi", "wynikà"). Measured on the scans: the Polish letter or the plain base letter is (almost) always
# what was printed. Applied to OCR output when settings.ocr_polish_letters is on (foreign names such as "Müller"
# lose their diacritics — rare in these documents). Letters inside numbers are fixed always (fix_text).
POLISH_LETTER_FIXES = str.maketrans(
    {
        **dict.fromkeys("ñň", "ń"),
        **dict.fromkeys("ÑŇ", "Ń"),
        "š": "ś",
        "Š": "Ś",
        "ß": "ś",
        "ž": "ż",
        "Ž": "Ż",
        **dict.fromkeys("öô", "ó"),
        **dict.fromkeys("ÖÔ", "Ó"),
        **dict.fromkeys("òõō", "o"),
        **dict.fromkeys("àáâäåāă", "a"),
        **dict.fromkeys("ÀÁÂÄÅĀĂ", "A"),
        **dict.fromkeys("èéêëėēě", "e"),
        **dict.fromkeys("ÈÉÊËĖĒĚ", "E"),
        **dict.fromkeys("ıìíîïī", "i"),
        **dict.fromkeys("İÌÍÎÏĪ", "I"),
        **dict.fromkeys("ùúûüūųů", "u"),
        **dict.fromkeys("ÙÚÛÜŪŲŮ", "U"),
        **dict.fromkeys("ýÿ", "y"),
        "Ý": "Y",
        **dict.fromkeys("çčċ", "c"),
        **dict.fromkeys("ÇČĊ", "C"),
        "ņ": "n",
        **dict.fromkeys("şș", "s"),
        **dict.fromkeys("ŞȘ", "S"),
        **dict.fromkeys("țţ", "t"),
        **dict.fromkeys("ġğ", "g"),
        "、": ",",
    }
)


_O_IN_NUMBER = re.compile(r"(?<=\d)[oO](?=\d)")  # "9994328o29" → "9994328029"
# "20l6" → "2016". Not "|": between digits it is rather a table rule ("12.04.2026|15:30") than a misread "1"
_L_IN_NUMBER = re.compile(r"(?<=\d)[lI](?=\d)")
# "Sp. z 0.0." — PP-OCRv6 reads the company form "o.o." as zeros in ~1/4 of the cases on the scans
_SP_Z_OO = re.compile(r"\b([Ss][Pp]\.?\s?([Zz])\s?)([oO0])(\.\s?)([oO0])\.")


def _sp_z_oo(m: re.Match) -> str:
    """Only misread zeros are replaced; letters keep the printed case ("Sp. Z o.o." stays as printed)."""

    def letter(c: str, other: str) -> str:
        if c != "0":
            return c
        if other in "oO":
            return other
        return "O" if m.group(2) == "Z" else "o"

    first, second = m.group(3), m.group(5)
    return f"{m.group(1)}{letter(first, second)}{m.group(4)}{letter(second, first)}."


# "towarzyszącq" — "ą" read as "q" (same shape with the tail on the right) at the end of a longer lowercase word;
# Polish words never end in "q" (foreign "Iraq"-like names are not expected in these documents)
_Q_FOR_A_OGONEK = re.compile(r"(?<=[a-ząćęłńóśźż]{3})q(?![^\W\d_])")


def fix_text(text: str, polish_letters: bool = True) -> str:
    """OCR post-correction: letters inside numbers → digits; with polish_letters also non-Polish letters →
    Polish/base letters, "sp. z 0.0." → "sp. z o.o." and a word-final "q" → "ą"."""
    if polish_letters:
        text = _SP_Z_OO.sub(_sp_z_oo, text.translate(POLISH_LETTER_FIXES))
        text = _Q_FOR_A_OGONEK.sub("ą", text)
    return _L_IN_NUMBER.sub("1", _O_IN_NUMBER.sub("0", text))


def fix_segments(segments: list[Segment], polish_letters: bool = True) -> list[Segment]:
    for s in segments:
        s.text = fix_text(s.text, polish_letters)
        if s.words:
            s.words = [(fix_text(w, polish_letters), q) for w, q in s.words]
    return segments


def fix_polish_letters(segments: list[Segment]) -> list[Segment]:
    return fix_segments(segments, polish_letters=True)


# ------------------------------------------------------------------------------------------------------ engines
class OCREngine(Protocol):
    name: str
    label: str

    def ocr_page(self, pdf_path: Path, page_index: int, page: Page, settings: IngestSettings) -> Page: ...


_TURNS = {180: Image.Transpose.ROTATE_180, 90: Image.Transpose.ROTATE_90, 270: Image.Transpose.ROTATE_270}
ORIENTATION_MIN_CONFIDENCE = 0.8  # upright pages score ≥ 0.95 on the scans; upside down ~0.6, sideways < 0
# A turned reading replaces the upright one only with clearly more confidently read horizontal text. Measured on
# the scans (44 clean + 30 degraded pages): the right orientation carries ≥ 1.6× the horizontal_weight of the
# upside-down one, while the mean confidence alone was higher for the upside-down reading of 2 degraded pages.
ORIENTATION_MARGIN = 1.25


def mean_confidence(segments: Sequence[Segment]) -> float | None:
    n = sum(len(s.text) for s in segments)
    return sum(s.score * len(s.text) for s in segments) / n if n else None


def _is_vertical(s: Segment) -> bool:
    x0, y0, x1, y1 = s.box
    return (y1 - y0) > 1.5 * (x1 - x0)


def vertical_share(segments: Sequence[Segment]) -> float:
    """Share of the text in segments taller than wide (a page lying sideways)."""
    n = sum(len(s.text) for s in segments)
    v = sum(len(s.text) for s in segments if _is_vertical(s))
    return v / n if n else 0.0


def horizontal_weight(segments: Sequence[Segment]) -> float:
    """Confidence-weighted number of characters read in horizontal lines (≈ characters read correctly)."""
    return sum(len(s.text) * s.score for s in segments if not _is_vertical(s))


def orientation_score(segments: Sequence[Segment]) -> float | None:
    """Higher = more plausible reading direction: confident text in horizontal lines."""
    conf = mean_confidence(segments)
    return None if conf is None else conf - vertical_share(segments)


class BaseEngine:
    """Common page flow. Subclasses implement read() (first pass) and optionally refine() (second pass)."""

    name = "base"

    def __init__(self, label: str):
        self.label = label  # e.g. "rapidocr:PP-OCRv6-small" — stored in Page.ocr_engine
        self._lock = threading.Lock()

    def read(self, image: Image.Image) -> list[Segment]:
        raise NotImplementedError

    def refine(self, image: Image.Image, segments: list[Segment]) -> list[Segment]:
        return segments

    def recognize(self, image: Image.Image) -> list[Segment]:
        return self.refine(image, self.read(image))

    def ocr_image(self, image: Image.Image) -> list[Segment]:
        with self._lock:
            return self.recognize(image)

    def ocr_image_oriented(self, image: Image.Image, auto_rotate: bool = True) -> tuple[list[Segment], int]:
        """Like ocr_image, but a page that reads badly may have been scanned upside down or sideways: with
        horizontal text lines it is also read turned by 180°, with vertical lines turned by 90° and 270°. A turned
        reading wins only with clearly more confidently read horizontal text (ORIENTATION_MARGIN): on a poor scan
        an upside-down reading of a few characters can have a higher mean confidence than the upright reading of
        the whole page. Returns (segments, rotation)."""
        with self._lock:
            segments, rotation, turned = self.read(image), 0, image
            score = orientation_score(segments)
            if auto_rotate and score is not None and score < ORIENTATION_MIN_CONFIDENCE:
                turns = (90, 270) if vertical_share(segments) >= 0.5 else (180,)
                best = horizontal_weight(segments)
                for rot in turns:
                    candidate = image.transpose(_TURNS[rot])
                    segs = self.read(candidate)
                    weight = horizontal_weight(segs)
                    if weight > ORIENTATION_MARGIN * best:
                        segments, rotation, turned, best = segs, rot, candidate, weight
                        if (orientation_score(segs) or 0.0) >= ORIENTATION_MIN_CONFIDENCE:
                            break
                if rotation:
                    log.info("OCR: page read turned by %d° (score %.2f)", rotation, orientation_score(segments))
            return self.refine(turned, segments), rotation

    def ocr_page(self, pdf_path: Path, page_index: int, page: Page, settings: IngestSettings) -> Page:
        img, scale = render_page_scaled(pdf_path, page_index, settings.ocr_dpi)
        dpi = settings.ocr_dpi if scale == settings.ocr_dpi / 72 else scale * 72  # lower when the page is huge
        img, angle = preprocess(img, settings.ocr_preprocess)
        segments, rotation = self.ocr_image_oriented(img, settings.ocr_auto_rotate)
        segments = fix_segments(segments, settings.ocr_polish_letters)
        return segments_to_page(segments, page, dpi, angle, img.size, self.label, rotation)


_RAPID_MODELS: dict[str, tuple[str, str, str | None]] = {
    # name: (ocr_version, model_type for det+rec, rec language)
    "PP-OCRv6-tiny": ("PP-OCRv6", "tiny", None),
    "PP-OCRv6-small": ("PP-OCRv6", "small", None),
    "PP-OCRv6-medium": ("PP-OCRv6", "medium", None),
    "PP-OCRv5-latin": ("PP-OCRv5", "mobile", "latin"),
}


class RapidOCREngine(BaseEngine):
    name = "rapidocr"

    def __init__(self, settings: IngestSettings, model: str | None = None):
        from rapidocr import EngineType, LangRec, ModelType, OCRVersion, RapidOCR

        model = model or settings.ocr_model
        if model not in _RAPID_MODELS:
            raise ValueError(f"Unknown RapidOCR model {model!r} (known: {', '.join(_RAPID_MODELS)})")
        version, mtype, lang = _RAPID_MODELS[model]
        super().__init__(f"{self.name}:{model}")
        params: dict = {
            "Global.log_level": "error",  # "text detection result is empty" on blank pages is not a problem
            "Global.text_score": 0.0,  # low-confidence lines are re-read first, filtered afterwards (ocr_min_score)
            "Global.use_cls": settings.ocr_use_cls,
            "Global.return_word_box": True,
            # RapidOCR downsizes the whole page to max_side_len BEFORE detection and recognition (default 2000 px
            # ≈ 170 dpi for A4) — that is what lost Polish diacritics. Recognition works on full-resolution crops;
            # only detection is limited (ocr_det_side), which keeps it fast.
            "Global.max_side_len": settings.ocr_max_side or 100_000,
            "Det.engine_type": EngineType.ONNXRUNTIME,
            "Det.ocr_version": OCRVersion(version),
            "Det.model_type": ModelType(mtype),
            "Rec.engine_type": EngineType.ONNXRUNTIME,
            "Rec.ocr_version": OCRVersion(version),
            "Rec.model_type": ModelType(mtype),
            # lines are recognized one by one: in a batch RapidOCR pads every line to the longest one, which made
            # PP-OCRv6 drop more spaces between words and was ~2× slower (measured on the 30 real scans)
            "Rec.rec_batch_num": max(1, settings.ocr_rec_batch),
        }
        if lang:
            params["Rec.lang_type"] = LangRec(lang)
        if settings.ocr_det_side:
            params["Det.limit_type"] = "max"
            params["Det.limit_side_len"] = settings.ocr_det_side
        self._engine = RapidOCR(params=params)
        # load (and, for non-bundled models, download) every model now, so that a failure happens here and
        # _build() can fall back to the bundled model — RapidOCR itself loads them lazily on the first page
        loaders = ["_load_det_model", "_load_rec_model"] + (["_load_cls_model"] if settings.ocr_use_cls else [])
        for loader in loaders:
            getattr(self._engine, loader, lambda: None)()
        self._min_score = settings.ocr_min_score
        self._retry_below = settings.ocr_retry_score
        self._drop_mirrored = settings.ocr_drop_mirrored

    def read(self, image: Image.Image) -> list[Segment]:
        return self._detect_and_read(image)

    def refine(self, image: Image.Image, segments: list[Segment]) -> list[Segment]:
        if self._retry_below > 0:
            self._retry_low_confidence(image, segments)
        segments = [s for s in segments if s.score >= self._min_score]
        if self._drop_mirrored:
            segments = self._drop_bleed_through(image, segments)
        return segments

    def _detect_and_read(self, image: Image.Image) -> list[Segment]:
        res = self._engine(image)  # PIL image: RapidOCR converts RGB/gray to its BGR input itself
        txts = res.txts or ()
        if not txts or res.boxes is None:
            return []
        scores = res.scores or (1.0,) * len(txts)
        word_results = res.word_results if res.word_results and len(res.word_results) == len(txts) else None
        out = []
        for i, (quad, txt) in enumerate(zip(res.boxes, txts, strict=False)):
            text = _clean(txt)
            if not text:
                continue
            words = None
            if word_results is not None:
                words = [
                    (_clean(w[0]), [tuple(map(float, p)) for p in w[2]]) for w in word_results[i] if w[2] is not None
                ]
            out.append(Segment(text, [tuple(map(float, p)) for p in quad], float(scores[i]), words))
        return out

    def _retry_low_confidence(self, image: Image.Image, segments: list[Segment]) -> None:
        """Re-read low-confidence lines from contrast-enhanced crops; keep the reading with the best confidence.

        Typical case: bleed-through (mirrored text of the reverse side) or a stamp over a line — the full-page
        pass returns garbage ("Z2 e e ieh", score 0.59); the darkest-ink version of the same crop is read
        correctly ("ZP/12/2026, ogłoszenie o zamówieniu nr 2026/BZP 00879135/03 …", score 0.99).
        """
        low = [s for s in segments if s.score < self._retry_below]
        if not low:
            return
        try:
            from rapidocr.ch_ppocr_rec import TextRecInput
            from rapidocr.utils.process_img import get_rotate_crop_image

            rec = self._engine._load_rec_model()
        except Exception as e:  # noqa: BLE001 — internal API of the pinned rapidocr version
            log.debug("RapidOCR retry pass unavailable: %s", e)
            return
        gray = np.asarray(image.convert("L"))
        crops, owners = [], []
        for s in low:
            crop = get_rotate_crop_image(gray, np.array(s.quad, dtype=np.float32))
            if crop.size == 0 or min(crop.shape[:2]) < 4:
                continue
            for variant in enhance_line(crop):
                crops.append(np.repeat(variant[..., None], 3, axis=2))
                owners.append(s)
        if not crops:
            return
        out = rec(TextRecInput(img=crops, return_word_box=False))
        best: dict[int, tuple[str, float]] = {}
        for s, txt, score in zip(owners, out.txts or (), out.scores or (), strict=False):
            if float(score) > best.get(id(s), ("", -1.0))[1]:
                best[id(s)] = (txt, float(score))
        for s in low:
            txt, score = best.get(id(s), ("", -1.0))
            txt = _clean(txt)
            if txt and score > s.score + 0.02:
                s.text, s.score, s.words = txt, score, None

    def _drop_bleed_through(self, image: Image.Image, segments: list[Segment]) -> list[Segment]:
        """Drop faint lines whose horizontally mirrored crop reads clearly better: text of the reverse side
        shining through the paper (see is_faint)."""
        if not segments:
            return segments
        try:
            from rapidocr.ch_ppocr_rec import TextRecInput
            from rapidocr.utils.process_img import get_rotate_crop_image

            rec = self._engine._load_rec_model()
        except Exception as e:  # noqa: BLE001 — internal API of the pinned rapidocr version
            log.debug("RapidOCR bleed-through check unavailable: %s", e)
            return segments
        gray = np.asarray(image.convert("L"))
        crops, owners = [], []
        for s in faint_segments(gray, segments):
            crop = get_rotate_crop_image(gray, np.array(s.quad, dtype=np.float32))
            if crop.size == 0 or min(crop.shape[:2]) < 4:
                continue
            crop3 = np.repeat(crop[..., None], 3, axis=2)
            crops += [crop3, np.ascontiguousarray(crop3[:, ::-1])]
            owners.append(s)
        if not crops:
            return segments
        scores = rec(TextRecInput(img=crops, return_word_box=False)).scores or ()
        if len(scores) != len(crops):
            return segments
        mirrored = {
            id(s) for k, s in enumerate(owners) if float(scores[2 * k + 1]) > float(scores[2 * k]) + MIRROR_MARGIN
        }
        if mirrored:
            log.debug("OCR: %d bleed-through line(s) dropped", len(mirrored))
        return [s for s in segments if id(s) not in mirrored]


# Bleed-through: mirrored text of the reverse side shining through thin paper. The recognizer reads such lines as
# garbage with a moderate confidence ("Yo6t6iw6ms5" = mirrored "Zamawiający:", score 0.73) and rules then take the
# garbage for values. They are much fainter than the printed text: candidates are lines whose contrast to the
# paper is below FAINT_CONTRAST_SHARE of the page's typical text contrast or below FAINT_CONTRAST_MIN gray levels
# (a blank reverse side, where ghosts are all the text); a candidate is dropped when its mirrored crop reads with
# a confidence higher by MIRROR_MARGIN (ghosts: 0.95-1.00 mirrored vs 0.5-0.9; real grey footer text of a real
# scan: 0.55-0.69 mirrored vs 0.98-1.00).
FAINT_CONTRAST_SHARE = 0.55
FAINT_CONTRAST_MIN = 80
MIRROR_MARGIN = 0.05


def line_ink(gray: np.ndarray, seg: Segment) -> float:
    """Darkest ink of a line: 5th percentile of its box in a uint8 grayscale page (255 for an empty box)."""
    x0, y0, x1, y1 = (round(v) for v in seg.box)
    crop = gray[max(0, y0) : max(0, y1), max(0, x0) : max(0, x1)]
    return float(np.percentile(crop, 5)) if crop.size else 255.0


def faint_segments(gray: np.ndarray, segments: Sequence[Segment]) -> list[Segment]:
    """Lines printed much fainter than the rest of the page (bleed-through candidates)."""
    if not segments:
        return []
    paper = float(np.percentile(gray, 90))
    inks = [line_ink(gray, s) for s in segments]
    limit = max(FAINT_CONTRAST_MIN, FAINT_CONTRAST_SHARE * (paper - float(np.median(inks))))
    return [s for s, ink in zip(segments, inks, strict=True) if paper - ink < limit]


def enhance_line(crop: np.ndarray) -> list[np.ndarray]:
    """Contrast-enhanced variants of a grayscale line crop that keep only the darkest ink (drop ghosts/stains)."""
    g = crop.astype(np.float32)
    p1, p95 = np.percentile(g, [1, 95])
    if p95 - p1 < 20:  # (almost) uniform crop
        return []
    out = []
    dark = g[g < p95 - 10]
    if dark.size > 50:
        t = otsu_threshold(dark.astype(np.uint8))
        out.append(np.where(g > t, 255, 0).astype(np.uint8))
    hi = p1 + 0.5 * (p95 - p1)
    out.append(np.clip((g - p1) * 255.0 / max(hi - p1, 1.0), 0, 255).astype(np.uint8))
    return out


_EASYOCR_LANGS = {"pol": "pl", "eng": "en", "deu": "de", "fra": "fr", "ces": "cs", "slk": "sk", "ukr": "uk"}


def easyocr_langs(ocr_lang: str) -> list[str]:
    """Tesseract-style language codes ("pol+eng") → EasyOCR codes (["pl", "en"])."""
    return [_EASYOCR_LANGS.get(x, x) for x in ocr_lang.split("+") if x]


class EasyOCREngine(BaseEngine):
    name = "easyocr"

    def __init__(self, settings: IngestSettings):
        import easyocr

        langs = easyocr_langs(settings.ocr_lang)
        super().__init__(engine_label(settings, self.name) or self.name)
        self._reader = easyocr.Reader(langs, gpu=False, verbose=False)
        self._min_score = settings.ocr_min_score

    def read(self, image: Image.Image) -> list[Segment]:
        res = self._reader.readtext(np.asarray(image.convert("RGB")), detail=1, paragraph=False)
        out = []
        for quad, txt, score in res:
            text = _clean(txt)
            # EasyOCR confidences are lower than RapidOCR's; keep low-confidence text unless it is very poor
            if text and float(score) >= min(self._min_score, 0.2):
                out.append(Segment(text, [tuple(map(float, p)) for p in quad], float(score)))
        return out


class TesseractEngine(BaseEngine):
    name = "tesseract"

    def __init__(self, settings: IngestSettings):
        import pytesseract

        if settings.tesseract_cmd:
            pytesseract.pytesseract.tesseract_cmd = settings.tesseract_cmd
        self._pt = pytesseract
        self._lang = settings.ocr_lang
        super().__init__(engine_label(settings, self.name) or self.name)
        pytesseract.get_tesseract_version()  # fail early when the binary is missing

    def read(self, image: Image.Image) -> list[Segment]:
        d = self._pt.image_to_data(image, lang=self._lang, config="--psm 3", output_type=self._pt.Output.DICT)
        lines: dict[tuple[int, int, int], list[int]] = {}
        for i, txt in enumerate(d["text"]):
            if str(txt).strip() and float(d["conf"][i]) >= 0:
                lines.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
        out = []
        for idx in lines.values():
            words = []
            for i in idx:
                x, y, w, h = d["left"][i], d["top"][i], d["width"][i], d["height"][i]
                words.append((_clean(str(d["text"][i])), [(x, y), (x + w, y), (x + w, y + h), (x, y + h)]))
            xs = [p[0] for _, q in words for p in q]
            ys = [p[1] for _, q in words for p in q]
            quad = [(min(xs), min(ys)), (max(xs), min(ys)), (max(xs), max(ys)), (min(xs), max(ys))]
            score = sum(float(d["conf"][i]) for i in idx) / len(idx) / 100
            out.append(Segment(" ".join(w for w, _ in words), quad, score, words))
        return out


# ------------------------------------------------------------------------------------------------ engine choice
def engine_available(name: str, settings: IngestSettings) -> bool:
    """Cheap availability check (imports / binaries only, no model loading)."""
    if name == "rapidocr":
        return importlib.util.find_spec("rapidocr") is not None and importlib.util.find_spec("onnxruntime") is not None
    if name == "easyocr":
        return importlib.util.find_spec("easyocr") is not None
    if name == "tesseract":
        return importlib.util.find_spec("pytesseract") is not None and bool(
            (settings.tesseract_cmd and Path(settings.tesseract_cmd).exists()) or shutil.which("tesseract")
        )
    return False


def resolve_engine_name(settings: IngestSettings) -> str | None:
    """Concrete engine for the settings ('auto' → first available of AUTO_ORDER), None = no OCR."""
    if settings.ocr_engine == "none":
        return None
    if settings.ocr_engine != "auto":
        return settings.ocr_engine
    return next((n for n in AUTO_ORDER if engine_available(n, settings)), None)


def engine_label(settings: IngestSettings, name: str | None = None) -> str | None:
    """What will be stored in Page.ocr_engine, e.g. "rapidocr:PP-OCRv6-small" (also part of the cache key)."""
    name = name or resolve_engine_name(settings)
    if name == "rapidocr":
        return f"rapidocr:{settings.ocr_model}"
    if name == "easyocr":
        return f"easyocr:{'+'.join(easyocr_langs(settings.ocr_lang))}"
    if name == "tesseract":
        return f"tesseract:{settings.ocr_lang}"
    return None


_ENGINES: dict[tuple, OCREngine | None] = {}
_ENGINES_LOCK = threading.Lock()
# settings read when an engine is constructed — one shared instance per distinct combination
_ENGINE_FIELDS = (
    "ocr_model",
    "ocr_det_side",
    "ocr_max_side",
    "ocr_use_cls",
    "ocr_min_score",
    "ocr_retry_score",
    "ocr_rec_batch",
    "ocr_drop_mirrored",
    "ocr_lang",
    "tesseract_cmd",
)


def _build(name: str, settings: IngestSettings) -> OCREngine | None:
    try:
        if name == "rapidocr":
            try:
                return RapidOCREngine(settings)
            except Exception as e:  # noqa: BLE001 — e.g. model download failed → bundled model
                if settings.ocr_model == DEFAULT_RAPIDOCR_MODEL:
                    raise
                log.warning(
                    "RapidOCR model %s unavailable (%s) — using %s", settings.ocr_model, e, DEFAULT_RAPIDOCR_MODEL
                )
                return RapidOCREngine(settings, DEFAULT_RAPIDOCR_MODEL)
        if name == "easyocr":
            return EasyOCREngine(settings)
        if name == "tesseract":
            return TesseractEngine(settings)
    except Exception as e:  # noqa: BLE001
        log.warning("OCR engine %s unavailable: %s", name, e)
    return None


def get_ocr_engine(settings: IngestSettings) -> OCREngine | None:
    """Shared, lazily created engine for the settings (None when OCR is off or nothing is available)."""
    if settings.ocr_engine == "none":
        return None
    candidates = list(AUTO_ORDER) if settings.ocr_engine == "auto" else [settings.ocr_engine]
    for name in candidates:
        if not engine_available(name, settings):
            continue
        key = (name, *(getattr(settings, f) for f in _ENGINE_FIELDS))
        with _ENGINES_LOCK:
            if key not in _ENGINES:
                _ENGINES[key] = _build(name, settings)
            engine = _ENGINES[key]
        if engine is not None:
            return engine
    log.warning("No OCR engine available (%s) — scanned pages will be empty", settings.ocr_engine)
    return None
