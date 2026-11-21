"""Ingest: text-layer checks, de-hyphenation, OCR layout/coordinates, preprocessing, cache, engine choice."""

from __future__ import annotations

import importlib.util
import json
import math
import threading
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from PIL import Image, ImageDraw, ImageFont

from odwolania2json import PROJECT_ROOT
from odwolania2json.ingest import cache as cache_mod
from odwolania2json.ingest import ocr, pdf
from odwolania2json.ingest.cache import IngestCache, page_from_dict, page_to_dict
from odwolania2json.models import Page, Word
from odwolania2json.settings import IngestSettings

HAS_RAPIDOCR = importlib.util.find_spec("rapidocr") is not None
SCAN = PROJECT_ROOT / "data" / "uzp" / "dane" / "skany" / "pelnomocnictwa" / "0001" / "0001.pdf"


# ------------------------------------------------------------------------------------------ text layer quality
def test_text_quality_normal_text_with_leaders_and_rules():
    text = "KWOTA PRZELEWU ........................ PLN 34.00\n-----------------------\nTYTUŁ: opłata skarbowa"
    assert pdf.text_quality(text) == pytest.approx(1.0)


@pytest.mark.parametrize(
    "garbage",
    ["(cid:3)(cid:4)(cid:5)(cid:6) (cid:7)(cid:8) ab", "\ufffd\ufffd\ufffd\ue000\ue001\ue002 ab", "\x01\x02\x03\x04 a"],
)
def test_text_quality_garbage(garbage):
    assert pdf.text_quality(garbage) < 0.5


def test_ocr_reason():
    s = IngestSettings()
    page = SimpleNamespace(width=100, height=100, images=[])
    scan = SimpleNamespace(width=100, height=100, images=[{"x0": 0, "top": 0, "x1": 100, "bottom": 90}])
    normal = "Treść pisma " * 30
    assert pdf.ocr_reason("", page, s) == "short"
    assert pdf.ocr_reason("(cid:1)" * 30, page, s) == "garbage"
    assert pdf.ocr_reason(normal, page, s) is None
    assert pdf.ocr_reason("Dokument podpisany elektronicznie przez Jan Kowalski", scan, s) == "image"
    assert pdf.ocr_reason(normal, scan, s) is None  # full text layer on top of a scan (searchable PDF) is kept


# ------------------------------------------------------------------------------------------------ hyphenation
@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("prowadzonego postępo-\nwania o udzielenie", "prowadzonego postępowania\no udzielenie"),
        ("koniec postępo-\nwania\nnastępna linia", "koniec postępowania\nnastępna linia"),
        ("Bielsko-\nBiała", "Bielsko-\nBiała"),
        ("w ciągu 5-\ndniowego terminu", "w ciągu 5-\ndniowego terminu"),
        ("adres e-\nmail", "adres e-\nmail"),
        ("pozycja -\nlista", "pozycja -\nlista"),
        ("ZAMÓ-\nWIENIE", "ZAMÓ-\nWIENIE"),
        ("miękki\xad\nłącznik i w\xadśrodku", "miękkiłącznik\ni wśrodku"),
        ("bez łamania", "bez łamania"),
        ("(cel złoże-\nnia, okres, którego dotyczy)", "(cel złożenia,\nokres, którego dotyczy)"),
        ("budowlany - zamienny ab-\ncd-\nef gh", "budowlany - zamienny abcdef\ngh"),
    ],
)
def test_dehyphenate(text, expected):
    assert pdf.dehyphenate(text) == expected


def test_dehyphenate_keeps_compound_seen_elsewhere():
    page = "Projekt architektoniczno-\nbudowlany - zamienny"
    doc = page + "\n…zgodnie z projektem architektoniczno-budowlanym oraz projekt architektoniczno-budowlany"
    assert pdf.dehyphenate(page, doc) == "Projekt architektoniczno-budowlany\n- zamienny"
    assert pdf.dehyphenate(page) == "Projekt architektonicznobudowlany\n- zamienny"  # no evidence → joined


def test_dehyphenate_keeps_line_count_for_text_after_fragment():
    text = "a b postępo-\nwania c d\ne f"
    assert pdf.dehyphenate(text).count("\n") == text.count("\n")


# --------------------------------------------------------------------------------------- layout & coordinates
def _seg(text: str, x0: float, y0: float, x1: float, y1: float, dy: float = 0.0) -> ocr.Segment:
    """Segment with a quad whose right edge is shifted vertically by dy (skew)."""
    return ocr.Segment(text, [(x0, y0), (x1, y0 + dy), (x1, y1 + dy), (x0, y1)])


def test_group_lines_reading_order():
    segs = [
        _seg("Kwota", 50, 300, 200, 340),
        _seg("7 500,00 PLN", 900, 302, 1200, 342),
        _seg("Potwierdzenie przelewu", 50, 100, 700, 150),
        _seg("Tytuł", 50, 400, 160, 440),
        _seg("wpis od odwołania", 900, 398, 1400, 438),
    ]
    lines = ocr.group_lines(segs)
    assert [" ".join(s.text for s in ln) for ln in lines] == [
        "Potwierdzenie przelewu",
        "Kwota 7 500,00 PLN",
        "Tytuł wpis od odwołania",
    ]


def test_group_lines_skewed_scan():
    # page rotated by ~1.5°: a far-right value is ~40 px lower than its label, but on the same text line
    tan = math.tan(math.radians(1.5))
    segs = []
    for i, y in enumerate((100, 160, 220)):
        segs.append(_seg(f"etykieta{i}", 50, y + 50 * tan, 900, y + 900 * tan + 40, dy=850 * tan))
        segs.append(_seg(f"wartość{i}", 1500, y + 1500 * tan, 2300, y + 2300 * tan + 40, dy=800 * tan))
    lines = [" ".join(s.text for s in ln) for ln in ocr.group_lines(segs)]
    assert lines == ["etykieta0 wartość0", "etykieta1 wartość1", "etykieta2 wartość2"]


def test_segments_to_page_points_and_words():
    seg = ocr.Segment(
        "NIP: 123",
        [(100, 200), (300, 200), (300, 240), (100, 240)],
        0.9,
        words=[
            ("NIP:", [(100, 200), (180, 200), (180, 240), (100, 240)]),
            ("123", [(200, 200), (300, 200), (300, 240), (200, 240)]),
        ],
    )
    page = Page(number=1, text="", width=595.0, height=842.0)
    ocr.segments_to_page([seg], page, dpi=144, angle=0.0, size=(1190, 1684), engine="test")
    assert page.text == "NIP: 123"
    assert page.source == "ocr" and page.ocr_engine == "test" and page.ocr_confidence == pytest.approx(0.9)
    assert page.words == [Word("NIP:", (50.0, 100.0, 90.0, 120.0)), Word("123", (100.0, 100.0, 150.0, 120.0))]


def test_split_words_proportional_fallback():
    seg = ocr.Segment("ab cd", [(0, 0), (50, 0), (50, 10), (0, 10)])
    words = ocr.split_words(seg)
    assert [w for w, _ in words] == ["ab", "cd"]
    assert words[0][1] == (0, 0, 20, 10) and words[1][1] == (30, 0, 50, 10)


def test_unrotate_point_inverts_pil_rotation():
    img = Image.new("L", (400, 300), 255)
    ImageDraw.Draw(img).rectangle((300, 50, 320, 70), fill=0)  # small black square, centre (310, 60)
    angle = 7.0
    rot = np.asarray(img.rotate(angle, resample=Image.NEAREST, fillcolor=255))
    ys, xs = np.nonzero(rot < 128)
    x, y = ocr.unrotate_point(float(xs.mean()), float(ys.mean()), angle, 200, 150)
    assert x == pytest.approx(310, abs=1.5) and y == pytest.approx(60, abs=1.5)


# ------------------------------------------------------------------------------------------------ preprocessing
def _lines_image(angle: float = 0.0) -> Image.Image:
    img = Image.new("L", (1200, 1600), 255)
    d = ImageDraw.Draw(img)
    for y in range(150, 1450, 60):
        d.rectangle((120, y, 1080, y + 18), fill=0)  # text-line-like bars
    return img.rotate(angle, resample=Image.BICUBIC, fillcolor=255)


@pytest.mark.parametrize("angle", [0.0, 1.5, -2.2])
def test_estimate_skew(angle):
    assert ocr.estimate_skew(_lines_image(angle)) == pytest.approx(-angle, abs=0.2)


def test_preprocess_steps():
    rgb = _lines_image(1.0).convert("RGB")
    out, angle = ocr.preprocess(rgb, ["grayscale", "autocontrast", "binarize", "denoise", "deskew"])
    assert out.mode == "L" and out.size == rgb.size
    assert set(np.unique(np.asarray(out))) <= set(range(256))
    assert angle == pytest.approx(-1.0, abs=0.2)
    assert ocr.preprocess(rgb, [])[0].mode == "RGB"


def test_otsu_threshold_bimodal():
    arr = np.concatenate([np.full(1000, 30, np.uint8), np.full(1000, 220, np.uint8)])
    assert 30 <= ocr.otsu_threshold(arr) < 220


# --------------------------------------------------------------------------------------------------------- cache
def _pages() -> list[Page]:
    return [
        Page(1, "Tekst strony", [Word("Tekst", (1.0, 2.0, 3.0, 4.0))], 595.0, 842.0, "text"),
        Page(2, "Skan", [Word("Skan", (10.5, 20.25, 30.0, 40.0))], 595.0, 842.0, "ocr", "rapidocr:x", 0.91),
    ]


def test_page_dict_roundtrip():
    for p in _pages():
        assert page_from_dict(json.loads(json.dumps(page_to_dict(p)))) == p


def test_cache_key_depends_on_file_and_settings(tmp_path):
    f1, f2 = tmp_path / "a.pdf", tmp_path / "b.pdf"
    f1.write_bytes(b"%PDF-1.4 one")
    f2.write_bytes(b"%PDF-1.4 two")
    c = IngestCache(tmp_path / "cache")
    s = IngestSettings()
    k = c.key(f1, s, "rapidocr:PP-OCRv6-small")
    assert k == c.key(f1, s.model_copy(), "rapidocr:PP-OCRv6-small")
    assert k != c.key(f2, s, "rapidocr:PP-OCRv6-small")
    assert k != c.key(f1, s.model_copy(update={"ocr_dpi": 200}), "rapidocr:PP-OCRv6-small")
    assert k != c.key(f1, s, "easyocr:pl")
    assert k == c.key(f1, s.model_copy(update={"cache_dir": "elsewhere"}), "rapidocr:PP-OCRv6-small")


def test_cache_put_get_and_broken_entry(tmp_path):
    c = IngestCache(tmp_path)
    assert c.get("missing") is None
    c.put("k1", _pages())
    assert c.get("k1") == _pages()
    c.path("k2").write_text("{not json", encoding="utf-8")
    assert c.get("k2") is None
    c.path("k3").write_text(json.dumps({"version": "old", "pages": []}), encoding="utf-8")
    assert c.get("k3") is None
    assert not list(tmp_path.glob("*.tmp"))


def test_cache_parallel_writers_and_readers(tmp_path):
    c = IngestCache(tmp_path)
    errors: list[BaseException] = []

    def work(i: int) -> None:
        try:
            for _ in range(20):
                c.put("same", _pages())
                got = c.get("same")
                assert got is None or got == _pages()
        except BaseException as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=work, args=(i,)) for i in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    assert c.get("same") == _pages()
    assert not list(tmp_path.glob("*.tmp"))


def test_load_document_uses_cache(tmp_path, monkeypatch):
    f = tmp_path / "doc.pdf"
    f.write_bytes(b"%PDF-1.4 fake")
    calls = []

    def fake_read(path, settings):
        calls.append(path)
        return _pages(), True

    monkeypatch.setattr(pdf, "read_pages", fake_read)
    monkeypatch.setattr(pdf, "engine_label", lambda s: "rapidocr:x")
    s = IngestSettings(cache_dir=str(tmp_path / "cache"))
    d1 = pdf.load_document(f, s)
    d2 = pdf.load_document(f, s)
    assert len(calls) == 1 and d1.pages == d2.pages == _pages()
    # incomplete results (OCR missing) are not cached
    monkeypatch.setattr(pdf, "read_pages", lambda p, st: (calls.append(p) or _pages(), False))
    s2 = s.model_copy(update={"ocr_dpi": 150})
    pdf.load_document(f, s2)
    pdf.load_document(f, s2)
    assert len(calls) == 3


def test_cache_dir_relative_to_project_root():
    c = IngestCache.from_settings(IngestSettings(cache_dir=".cache/ingest"))
    assert c is not None and c.root == PROJECT_ROOT / ".cache" / "ingest"
    assert IngestCache.from_settings(IngestSettings(cache_dir=None)) is None
    assert cache_mod.INGEST_VERSION


# ------------------------------------------------------------------------------------------------ engine choice
def test_engine_none_and_resolution(monkeypatch):
    assert ocr.get_ocr_engine(IngestSettings(ocr_engine="none")) is None
    assert ocr.resolve_engine_name(IngestSettings(ocr_engine="none")) is None
    monkeypatch.setattr(ocr, "engine_available", lambda name, s: name == "tesseract")
    assert ocr.resolve_engine_name(IngestSettings(ocr_engine="auto")) == "tesseract"
    monkeypatch.setattr(ocr, "engine_available", lambda name, s: False)
    assert ocr.resolve_engine_name(IngestSettings(ocr_engine="auto")) is None
    assert ocr.get_ocr_engine(IngestSettings(ocr_engine="auto")) is None


@pytest.mark.skipif(not HAS_RAPIDOCR, reason="rapidocr not installed")
def test_rapidocr_engine_is_shared_and_thread_safe():
    s = IngestSettings(ocr_engine="rapidocr")
    engine = ocr.get_ocr_engine(s)
    assert engine is not None and engine is ocr.get_ocr_engine(s.model_copy())
    img = Image.new("RGB", (900, 200), "white")
    ImageDraw.Draw(img).text((40, 60), "PELNOMOCNICTWO 2026", fill="black", font=ImageFont.load_default(size=56))
    results: list[str] = []

    def run() -> None:
        results.append(" ".join(seg.text for seg in engine.ocr_image(img)))

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 4 and len(set(results)) == 1
    assert "2026" in results[0]


@pytest.mark.skipif(not HAS_RAPIDOCR, reason="rapidocr not installed")
def test_scanned_pdf_end_to_end(tmp_path):
    img = Image.new("L", (1240, 1754), 255)  # A4 at 150 dpi
    d = ImageDraw.Draw(img)
    font = ImageFont.load_default(size=40)
    d.text((150, 200), "ODWOLANIE", fill=0, font=font)
    d.text((150, 300), "NIP: 999-295-65-50, REGON: 999913617", fill=0, font=font)
    f = tmp_path / "scan.pdf"
    img.save(f, "PDF", resolution=150)
    s = IngestSettings(cache_dir=str(tmp_path / "cache"))
    doc = pdf.load_document(f, s)
    page = doc.pages[0]
    assert page.source == "ocr" and page.ocr_engine and page.ocr_engine.startswith("rapidocr")
    assert "ODWOLANIE" in page.text and "999913617" in page.text
    word = next(w for w in page.words if "999913617" in w.text)
    x0, top, x1, bottom = word.bbox  # PDF points: the word was drawn at y≈300 px of a 150 dpi page → ~144 pt
    assert 0 <= x0 < x1 <= page.width and 130 < top < bottom < 175
    assert list((tmp_path / "cache").glob("*.json"))  # cached
    assert pdf.load_document(f, s).pages == doc.pages


@pytest.mark.skipif(not (HAS_RAPIDOCR and SCAN.exists()), reason="needs rapidocr + data/uzp (scripts/fetch_data.py)")
def test_real_scan_polish_diacritics():
    s = IngestSettings(cache_dir=None)
    page = ocr.get_ocr_engine(s).ocr_page(SCAN, 0, Page(number=1, text="", width=595.0, height=842.0), s)
    assert "Halina Jodłowska" in page.text and "pełnomocnictwa" in page.text  # PP-OCRv5 latin: "Jodtowska"
    assert all(0 <= w.bbox[0] <= w.bbox[2] <= 596 and 0 <= w.bbox[1] <= w.bbox[3] <= 843 for w in page.words)


def test_render_page_is_serialized(monkeypatch, tmp_path):
    """Every PDFium call goes through PDFIUM_LOCK (pdfium is not thread-safe)."""
    img = Image.new("L", (200, 300), 255)
    f = tmp_path / "x.pdf"
    img.save(f, "PDF", resolution=72)
    active, peak = [0], [0]
    real = ocr.pdfium.PdfDocument

    class Spy:
        def __init__(self, *a, **k):
            active[0] += 1
            peak[0] = max(peak[0], active[0])
            self._d = real(*a, **k)

        def __getitem__(self, i):
            return self._d[i]

        def close(self):
            active[0] -= 1
            self._d.close()

    monkeypatch.setattr(ocr.pdfium, "PdfDocument", Spy)
    threads = [threading.Thread(target=ocr.render_page, args=(Path(f), 0, 72)) for _ in range(6)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert peak[0] == 1


def test_fix_polish_letters():
    segs = [ocr.Segment("Lublın GDAÑSK ul. Lešna możliwośçi wynikà", [(0, 0), (9, 0), (9, 1), (0, 1)])]
    segs[0].words = [("Lublın", [(0, 0), (1, 0), (1, 1), (0, 1)])]
    out = ocr.fix_polish_letters(segs)
    assert out[0].text == "Lublin GDAŃSK ul. Leśna możliwości wynika"
    assert out[0].words[0][0] == "Lublin"
    polish = "zażółć gęślą jaźń ZAŻÓŁĆ GĘŚLĄ JAŹŃ"
    assert polish.translate(ocr.POLISH_LETTER_FIXES) == polish  # Polish letters are never touched


def test_engine_labels_match_page_labels():
    s = IngestSettings(ocr_lang="pol+eng")
    assert ocr.engine_label(s, "rapidocr") == "rapidocr:PP-OCRv6-small"
    assert ocr.engine_label(s, "easyocr") == "easyocr:pl+en"
    assert ocr.engine_label(s, "tesseract") == "tesseract:pol+eng"
    assert ocr.engine_label(IngestSettings(ocr_engine="none")) is None


@pytest.mark.parametrize(
    ("raw", "fixed"),
    [
        ("KRS: 9994328o29", "KRS: 9994328029"),
        ("dnia 2O26 r.", "dnia 2026 r."),
        ("20l6", "2016"),
        ("ul. Jana Pawła II 11A/15", "ul. Jana Pawła II 11A/15"),
        ("lok. 1o", "lok. 1o"),
    ],
)
def test_fix_text_letters_inside_numbers(raw, fixed):
    assert ocr.fix_text(raw) == fixed


def test_engine_instances_per_relevant_settings(monkeypatch):
    built = []

    def fake_build(name, settings):
        built.append((name, settings.ocr_retry_score))
        return SimpleNamespace(name=name, label=name)

    monkeypatch.setattr(ocr, "_ENGINES", {})
    monkeypatch.setattr(ocr, "_build", fake_build)
    monkeypatch.setattr(ocr, "engine_available", lambda name, s: name == "rapidocr")
    a = ocr.get_ocr_engine(IngestSettings(ocr_retry_score=0.9))
    b = ocr.get_ocr_engine(IngestSettings(ocr_retry_score=0.9, ocr_dpi=200))  # dpi is per page, not per engine
    c = ocr.get_ocr_engine(IngestSettings(ocr_retry_score=0.0))
    assert a is b and a is not c and built == [("rapidocr", 0.9), ("rapidocr", 0.0)]


@pytest.mark.parametrize(
    ("raw", "fixed"),
    [
        ("Orbisol Budownictwo Sp. z 0.0. (dalej", "Orbisol Budownictwo Sp. z o.o. (dalej"),
        ("SP. Z 0.O.", "SP. Z O.O."),
        ("Nordenix Sp. z 0.o. z siedzibą", "Nordenix Sp. z o.o. z siedzibą"),
        ("sp. z o.o.", "sp. z o.o."),
    ],
)
def test_fix_text_company_form(raw, fixed):
    assert ocr.fix_text(raw) == fixed
    assert ocr.fix_text(raw, polish_letters=False) == raw


@pytest.mark.parametrize("rotation", [0, 90, 180, 270])
def test_unturn_point_inverts_transpose(rotation):
    img = Image.new("L", (400, 300), 255)
    ImageDraw.Draw(img).rectangle((300, 50, 320, 70), fill=0)  # centre (310, 60)
    methods = {0: None, 90: Image.Transpose.ROTATE_90, 180: Image.Transpose.ROTATE_180, 270: Image.Transpose.ROTATE_270}
    turned = img if rotation == 0 else img.transpose(methods[rotation])
    ys, xs = np.nonzero(np.asarray(turned) < 128)
    x, y = ocr.unturn_point(float(xs.mean()) + 0.5, float(ys.mean()) + 0.5, rotation, img.size)
    assert x == pytest.approx(310.5, abs=1.0) and y == pytest.approx(60.5, abs=1.0)


class _FakeEngine(ocr.BaseEngine):
    """Reads well only when the page is upside down (simulates a page scanned upside down)."""

    def __init__(self):
        super().__init__("fake")
        self.calls = []

    def read(self, image):
        upright = image.getpixel((0, 0)) == 0  # black marker pixel in the top-left corner after turning
        self.calls.append(upright)
        score = 0.97 if upright else 0.4
        return [ocr.Segment("Tekst strony", [(10, 10), (300, 10), (300, 40), (10, 40)], score)]


def test_orientation_fallback_turns_upside_down_page():
    img = Image.new("L", (400, 600), 255)
    img.putpixel((399, 599), 0)  # bottom-right marker → top-left once turned by 180°
    eng = _FakeEngine()
    segs, rotation = eng.ocr_image_oriented(img, auto_rotate=True)
    assert rotation == 180 and segs[0].score == 0.97 and eng.calls == [False, True]
    eng2 = _FakeEngine()
    assert eng2.ocr_image_oriented(img, auto_rotate=False)[1] == 0 and eng2.calls == [False]


def test_failed_model_download_falls_back_to_bundled_model(monkeypatch):
    class FakeRapid:
        def __init__(self, settings, model=None):
            model = model or settings.ocr_model
            if model != ocr.DEFAULT_RAPIDOCR_MODEL:
                raise OSError("download failed")
            self.label = f"rapidocr:{model}"

    monkeypatch.setattr(ocr, "RapidOCREngine", FakeRapid)
    engine = ocr._build("rapidocr", IngestSettings(ocr_model="PP-OCRv6-medium"))
    assert engine is not None and engine.label == "rapidocr:PP-OCRv6-small"


# ------------------------------------------------------------------------------------- review fixes (regressions)
def test_dehyphenate_long_letter_run_is_linear():
    """The hyphen regex used to retry a run of letters from every position (30k letters on one line: ~18 s)."""
    import time

    text = "a" * 200_000 + "\nb ab-\ncd"
    t = time.perf_counter()
    assert pdf.dehyphenate(text).endswith("abcd")
    assert time.perf_counter() - t < 1.0


def _broken_xref_pdf(path: Path, img: Image.Image, resolution: float = 72) -> Path:
    """Image-only PDF whose xref subsection starts at 1 instead of 0 (seen in a real scanner PDF of the negatives):
    pdfminer silently finds no page in it, PDFium repairs the xref and reads the page."""
    import io
    import re

    buf = io.BytesIO()
    img.save(buf, "PDF", resolution=resolution)
    raw = re.sub(rb"xref\n0 (\d+)\n", rb"xref\n1 \1\n", buf.getvalue())
    path.write_bytes(raw)
    return path


class _StubOCR:
    """OCR engine stub: fills the page with a fixed text and records the calls."""

    name, label = "stub", "stub:1"

    def __init__(self):
        self.calls: list[int] = []

    def ocr_page(self, pdf_path, page_index, page, settings):
        self.calls.append(page_index)
        page.text, page.source, page.ocr_engine = "ODWOŁANIE z OCR", "ocr", self.label
        return page


def test_pdf_without_pages_for_pdfplumber_is_read_with_pdfium(tmp_path, monkeypatch):
    f = _broken_xref_pdf(tmp_path / "scan.pdf", Image.new("L", (400, 600), 255))
    with __import__("pdfplumber").open(f) as p:
        assert len(p.pages) == 0  # the bug: the scan used to become an empty document
    stub = _StubOCR()
    monkeypatch.setattr(pdf, "get_ocr_engine", lambda s: stub)
    pages, complete = pdf.read_pages(f, IngestSettings(cache_dir=None))
    assert complete and stub.calls == [0]
    assert [(p.number, p.source, p.text, p.width, p.height) for p in pages] == [
        (1, "ocr", "ODWOŁANIE z OCR", 400.0, 600.0)
    ]


def test_pdfium_fallback_keeps_a_usable_text_layer(tmp_path, monkeypatch):
    """pdfplumber failing on a text PDF: PDFium's text layer is used, no OCR."""
    f = tmp_path / "text.pdf"
    _text_pdf(f, "Pelnomocnictwo udzielone w Lodzi dnia 1 kwietnia 2026 r. dla radcy prawnego Jana Kowalskiego")

    def boom(*a, **k):
        raise ValueError("pdfminer cannot parse this file")

    stub = _StubOCR()
    monkeypatch.setattr(pdf, "_read_with_pdfplumber", boom)
    monkeypatch.setattr(pdf, "get_ocr_engine", lambda s: stub)
    pages, complete = pdf.read_pages(f, IngestSettings(cache_dir=None))
    assert complete and stub.calls == [] and pages[0].source == "text"
    assert "Pelnomocnictwo udzielone w Lodzi dnia 1 kwietnia 2026" in pages[0].text


def test_unreadable_file_still_raises(tmp_path):
    f = tmp_path / "broken.pdf"
    f.write_bytes(b"this is not a pdf")
    with pytest.raises(Exception):  # noqa: B017 — the pipeline reports the error for the document
        pdf.read_pages(f, IngestSettings(cache_dir=None, ocr_engine="none"))


def _text_pdf(path: Path, line: str) -> Path:
    """One-page PDF with a real text layer (written with PDFium; standard Helvetica = WinAnsi, so ASCII only)."""
    import pypdfium2 as pdfium
    import pypdfium2.raw as pdfium_c

    doc = pdfium.PdfDocument.new()
    page = doc.new_page(595, 842)
    obj = pdfium_c.FPDFPageObj_NewTextObj(doc, b"Helvetica", 12.0)
    text = (line + "\x00").encode("utf-16-le")
    pdfium_c.FPDFText_SetText(obj, (pdfium_c.FPDF_WCHAR * (len(text) // 2)).from_buffer_copy(text))
    pdfium_c.FPDFPageObj_Transform(obj, 1, 0, 0, 1, 40, 780)
    pdfium_c.FPDFPage_InsertObject(page, obj)
    pdfium_c.FPDFPage_GenerateContent(page)
    doc.save(str(path))
    doc.close()
    return path


class _BoxEngine(ocr.BaseEngine):
    """Reads one word in the middle 20 % of whatever image it gets."""

    def __init__(self):
        super().__init__("box")
        self.sizes: list[tuple[int, int]] = []

    def read(self, image):
        w, h = image.size
        self.sizes.append((w, h))
        return [ocr.Segment("Środek", [(0.4 * w, 0.4 * h), (0.6 * w, 0.4 * h), (0.6 * w, 0.6 * h), (0.4 * w, 0.6 * h)])]


def test_huge_page_is_rendered_below_the_pixel_cap_with_correct_coordinates(tmp_path, monkeypatch):
    f = tmp_path / "poster.pdf"
    Image.new("L", (300, 200), 255).save(f, "PDF", resolution=2)  # 150 × 100 inches = 10800 × 7200 pt
    monkeypatch.setattr(ocr, "MAX_RENDER_PIXELS", 1_000_000)
    eng = _BoxEngine()
    page = Page(number=1, text="", width=10800.0, height=7200.0)
    eng.ocr_page(f, 0, page, IngestSettings(ocr_dpi=300, ocr_auto_rotate=False))
    ((w, h),) = eng.sizes
    assert w * h <= 1_000_000 * 1.01  # at 300 dpi it would be 45000 × 30000 px (1.35 gigapixels)
    (word,) = page.words
    assert word.text == "Środek"
    assert word.bbox == pytest.approx((4320, 2880, 6480, 4320), abs=15)  # PDF points despite the lower dpi


def test_render_scale():
    assert ocr.render_scale(595, 842, 300) == pytest.approx(300 / 72)  # A4: unchanged
    assert ocr.render_scale(14400, 14400, 300) == pytest.approx(math.sqrt(ocr.MAX_RENDER_PIXELS) / 14400)


@pytest.mark.parametrize(
    ("raw", "fixed"),
    [
        ("Kowalski Sp. Z o.o. z siedzibą", "Kowalski Sp. Z o.o. z siedzibą"),  # printed case kept (was → "O.O.")
        ("Kowalski Sp. Z 0.o.", "Kowalski Sp. Z o.o."),
        ("Kowalski SP. Z O.0.", "Kowalski SP. Z O.O."),
        ("termin 12.04.2026|15:30", "termin 12.04.2026|15:30"),  # a table rule is not a "1"
    ],
)
def test_fix_text_keeps_printed_letters(raw, fixed):
    assert ocr.fix_text(raw) == fixed


class _OrientedEngine(ocr.BaseEngine):
    """Returns canned readings per orientation (found from a marker pixel) and records which turns were read."""

    def __init__(self, readings: dict[int, list[ocr.Segment]]):
        super().__init__("oriented")
        self.readings, self.read_turns = readings, []

    def read(self, image):
        # the marker pixel sits in the top-left corner of the stored image; find where it went
        w, h = image.size
        corners = {0: (0, 0), 90: (0, h - 1), 180: (w - 1, h - 1), 270: (w - 1, 0)}
        turn = next(t for t, xy in corners.items() if image.getpixel(xy) == 0)
        self.read_turns.append(turn)
        return self.readings[turn]


def _marked(w: int = 400, h: int = 600) -> Image.Image:
    img = Image.new("L", (w, h), 255)
    img.putpixel((0, 0), 0)
    return img


def _line(text: str, score: float, vertical: bool = False) -> ocr.Segment:
    quad = [(10, 10), (40, 10), (40, 400), (10, 400)] if vertical else [(10, 10), (390, 10), (390, 40), (10, 40)]
    return ocr.Segment(text, quad, score)


def test_orientation_keeps_a_poor_upright_page():
    """Poor scan: the upright reading (many characters, mean 0.53) must not lose to an upside-down reading of a few
    characters with a slightly higher mean (measured on a degraded real page: 0.53 vs 0.55)."""
    upright = [_line("Treść pisma odczytana słabo, ale w całości " * 4, 0.53)]
    eng = _OrientedEngine({0: upright, 180: [_line("ssiw wd", 0.62)]})
    segs, rotation = eng.ocr_image_oriented(_marked())
    assert rotation == 0 and segs == upright
    assert eng.read_turns == [0, 180]  # horizontal lines: only the upside-down reading is tried


def test_orientation_sideways_page_tries_only_quarter_turns():
    sideways = [_line("Treść pisma czytana w pionie", 0.95, vertical=True) for _ in range(3)]
    upside_down = [_line("ɐɯsıd ɔsǝɹ┴", 0.6)]
    good = [_line("Treść pisma czytana poziomo, linia po linii", 0.97) for _ in range(3)]
    eng = _OrientedEngine({0: sideways, 90: upside_down, 270: good})
    segs, rotation = eng.ocr_image_oriented(_marked())
    assert rotation == 270 and segs == good
    assert eng.read_turns == [0, 90, 270]


def test_ocr_engine_setting_is_case_insensitive(monkeypatch):
    """OCR_ENGINE=RapidOCR used to switch OCR off silently (no engine of that name → scanned pages empty)."""
    from odwolania2json.settings import Settings

    assert IngestSettings(ocr_engine=" RapidOCR ").ocr_engine == "rapidocr"
    assert IngestSettings(ocr_engine="no-such-engine").ocr_engine == "auto"
    monkeypatch.setenv("OCR_ENGINE", "Tesseract")
    assert Settings.load().ingest.ocr_engine == "tesseract"


@pytest.mark.parametrize(
    ("raw", "fixed"),
    [
        ('infrastrukturą towarzyszącq", nr', 'infrastrukturą towarzyszącą", nr'),  # real scan (odwolania/0008)
        ("zdolnych dq realizacji", "zdolnych dq realizacji"),  # too short to tell ("do" misread)
        ("Aquameter Quantis", "Aquameter Quantis"),
    ],
)
def test_fix_text_word_final_q(raw, fixed):
    assert ocr.fix_text(raw) == fixed
    assert ocr.fix_text(raw, polish_letters=False) == raw


@pytest.mark.skipif(not HAS_RAPIDOCR, reason="rapidocr not installed")
def test_rapidocr_recognizes_lines_one_by_one():
    """Batched recognition pads every line to the longest one of the batch: PP-OCRv6 then drops more spaces
    between words (and runs ~2× slower) — measured on the real scans."""
    engine = ocr.get_ocr_engine(IngestSettings(ocr_engine="rapidocr"))
    assert engine._engine._load_rec_model().rec_batch_num == 1


def test_faint_segments_finds_lines_much_lighter_than_the_page_text():
    gray = np.full((300, 1000), 240, np.uint8)
    gray[20:50, 20:900] = 40  # printed text
    gray[120:150, 20:900] = 60  # printed text
    gray[220:250, 20:900] = 195  # bleed-through: faint
    segs = [_seg(t, 20, y, 900, y + 30) for t, y in (("tekst", 20), ("tekst 2", 120), ("duch", 220))]
    assert [s.text for s in ocr.faint_segments(gray, segs)] == ["duch"]
    only_ghosts = [_seg("duch", 20, 220, 900, 250)]  # blank reverse side: the ghost is all the text
    assert ocr.faint_segments(gray, only_ghosts) == only_ghosts


@pytest.mark.skipif(not HAS_RAPIDOCR, reason="rapidocr not installed")
def test_rapidocr_drops_mirrored_bleed_through_lines():
    """Bleed-through (reverse side shining through) used to come out as garbage lines ("Yo6t6iw6ms5" = mirrored
    "Zamawiający:") next to the real labels, and rules took them for values."""
    font = ImageFont.load_default(size=44)
    img = Image.new("L", (1400, 420), 238)
    ImageDraw.Draw(img).text((60, 60), "Zamawiajacy: Gmina Wrzosowo", fill=30, font=font)
    ghost = Image.new("L", (1400, 120), 238)
    ImageDraw.Draw(ghost).text((60, 30), "Odwolujacy: Firma Budowlana", fill=190, font=font)
    img.paste(ghost.transpose(Image.Transpose.FLIP_LEFT_RIGHT), (0, 250))
    rgb = img.convert("RGB")
    with_filter = ocr.get_ocr_engine(IngestSettings(ocr_engine="rapidocr"))
    without = ocr.get_ocr_engine(IngestSettings(ocr_engine="rapidocr", ocr_drop_mirrored=False))
    kept = [s.text for s in with_filter.ocr_image(rgb)]
    assert any("Gmina Wrzosowo" in t for t in kept)
    assert len(kept) == 1, kept
    assert len(without.ocr_image(rgb)) == 2  # the mirrored ghost is read as a (garbage) line without the filter
