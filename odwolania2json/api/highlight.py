"""Highlight boxes: where on the page the value of a field comes from (transparency of extraction in the UI).

The pipeline records `Evidence(page, snippet, bbox)` for most values, but `bbox` is usually None. The UI needs
rectangles, so we locate the value in the word sequence of the page (`Page.words`, bboxes in PDF points with the
origin at the top-left corner — from pdfplumber for text pages, from OCR for scanned pages when available):

1. `evidence.bbox` given by the extractor → used as is (kind "bbox").
2. The value is searched on the evidence page — first inside the region of the evidence snippet (disambiguates
   repeated values), then on the whole page — and, if not found there, on the other pages (distinctive values only):
   * digit-heavy values (NIP, KRS, account numbers, amounts, dates written with digits): exact digit sequence,
     aligned to word boundaries, separators ignored ("554-024-05-49" ↔ "5540240549", "15 000,00" ↔ 15000.0);
     a value with cents must have cents on the page too (1500.00 ↔ "1 500,00", never ↔ "150 000"),
   * text: case- and diacritics-insensitive; whole-word occurrences first ("Kraków", not the street
     "Krakowska"), then (≥ 6 characters) inside a longer word (inflection: "Kowalskiego"), then fuzzy matching
     without spaces/punctuation (OCR errors, hyphenation); short values only as whole words.
   Raw value (as found in the text) is tried before the normalized value; ISO dates also as "1 kwietnia 2026" etc.
3. Value not found, but the snippet was → the snippet region (kind "snippet", approximate).
4. Page without words (e.g. OCR without coordinates) → no boxes.

Result: [{"page": 1, "bbox": [x0, top, x1, bottom], "kind": "value" | "snippet" | "bbox"}], one box per text line.
"""

from __future__ import annotations

import re
import unicodedata
from bisect import bisect_left, bisect_right
from dataclasses import dataclass
from datetime import date
from typing import Any

from rapidfuzz import fuzz

from odwolania2json.models import Document, FieldResult, Word

KIND_VALUE, KIND_SNIPPET, KIND_BBOX = "value", "snippet", "bbox"

_PL_FOLD = str.maketrans("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ", "acelnoszzACELNOSZZ")
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_DECIMAL_TAIL = re.compile(r"\d[.,]\s?\d{2}$")  # "1500.00", "1 500,00", "500, 00" (OCR)
_MONTHS_GEN = (
    "stycznia lutego marca kwietnia maja czerwca lipca sierpnia września października listopada grudnia"
).split()

SNIPPET_CUTOFF = 75.0  # evidence snippets are copies of the page text → normally ~100
SNIPPET_MAX_CHARS = 400  # normalized characters of the snippet used for locating it
REGION_PAD_WORDS = 2  # words added around the snippet region in the second search pass
DISTINCTIVE_CHARS = 6  # values searched on pages other than the evidence page need at least this many chars


def norm_text(s: Any) -> str:
    """Matching form: Polish letters folded, other diacritics removed, case-folded, only letters and digits."""
    s = unicodedata.normalize("NFKD", str(s).translate(_PL_FOLD))
    return "".join(ch for ch in s.casefold() if ch.isalnum())


@dataclass
class _Match:
    page: int
    first: int  # word indices (inclusive)
    last: int


@dataclass
class PageIndex:
    """Normalized word stream of one page: `concat` = normalized words without separators (+ digit-only stream)."""

    number: int
    words: list[Word]
    concat: str
    starts: list[int]
    ends: list[int]
    ocr: bool
    digits: str
    dstarts: list[int]
    dends: list[int]
    start_set: frozenset[int] = frozenset()
    end_set: frozenset[int] = frozenset()

    @classmethod
    def build(cls, number: int, words: list[Word], ocr: bool = False) -> PageIndex:
        parts, starts, ends, dparts, dstarts, dends = [], [], [], [], [], []
        pos = dpos = 0
        for w in words:
            n = norm_text(w.text)
            d = "".join(ch for ch in n if ch.isdigit())
            starts.append(pos)
            pos += len(n)
            ends.append(pos)
            parts.append(n)
            dstarts.append(dpos)
            dpos += len(d)
            dends.append(dpos)
            dparts.append(d)
        return cls(
            number,
            words,
            "".join(parts),
            starts,
            ends,
            ocr,
            "".join(dparts),
            dstarts,
            dends,
            frozenset(starts),
            frozenset(ends),
        )

    def words_in_chars(self, a: int, b: int) -> tuple[int, int] | None:
        """Word range covering concat[a:b]; boundary words count only if mostly covered (alignment slop)."""
        first, last = None, None
        lo = bisect_right(self.ends, a)
        hi = min(len(self.words) - 1, bisect_left(self.starts, b))
        for i in range(lo, hi + 1):
            s, e = self.starts[i], self.ends[i]
            if e <= s:
                continue  # punctuation-only word
            overlap = min(e, b) - max(s, a)
            if overlap <= 0:
                continue
            if overlap >= min(2, e - s) or overlap / (e - s) >= 0.5:
                first = i if first is None else first
                last = i
        return (first, last) if first is not None else None


def index_document(doc: Document | None) -> dict[int, PageIndex]:
    """{page number: PageIndex} for pages that have words (pages without words cannot be highlighted)."""
    if doc is None:
        return {}
    return {p.number: PageIndex.build(p.number, p.words, ocr=p.source == "ocr") for p in doc.pages if p.words}


# --------------------------------------------------------------------------- search targets


def _number_forms(v: int | float) -> list[str]:
    if isinstance(v, int):
        return [str(v)]
    forms = [f"{v:.2f}"]
    if float(v).is_integer():
        forms.append(str(int(v)))
    return forms


def _date_forms(s: str) -> list[str]:
    m = _ISO_DATE.match(s)
    if not m:
        return []
    y, mo, d = m.groups()
    try:
        date(int(y), int(mo), int(d))
    except ValueError:
        return []
    di, mi = int(d), int(mo)
    month = _MONTHS_GEN[mi - 1]
    return [
        f"{di} {month} {y}",
        f"{d}.{mo}.{y}",
        f"{di}.{mo}.{y}",
        f"{di}.{mi}.{y}",
        f"{d} {month} {y}",
    ]


def search_targets(value: Any, raw_value: Any) -> list[str]:
    """Textual forms to look for, most specific first (raw value = as found in the document)."""
    out: list[str] = []
    for v in (raw_value, value):
        if v is None or isinstance(v, bool) or isinstance(v, (dict, list)):
            continue
        if isinstance(v, (int, float)):
            out += _number_forms(v)
            continue
        s = str(v).strip()
        if s:
            out.append(s)
            out += _date_forms(s)
    seen: set[str] = set()
    uniq = []
    for t in out:
        key = norm_text(t)
        if key and key not in seen:
            seen.add(key)
            uniq.append(t)
    return uniq


def _is_digit_heavy(norm: str) -> bool:
    nd = sum(ch.isdigit() for ch in norm)
    return nd >= 4 and nd / len(norm) >= 0.6


def _has_cents(text: str) -> bool:
    """'1500.00', '1 500,00 zł' → True (digits only would also match the unrelated number '150 000')."""
    end = len(text)
    while end and not text[end - 1].isdigit():  # drop " zł", "PLN" … (a loop: r"\D+$" is quadratic)
        end -= 1
    return _DECIMAL_TAIL.search(text, 0, end) is not None


# --------------------------------------------------------------------------- matching


def _find_digits(pi: PageIndex, digits: str, lo: int, hi: int, cents: bool = False) -> _Match | None:
    """Exact digit sequence whose start/end coincide with digit-run boundaries of words lo..hi.
    `cents`: the matched text must end with a decimal separator + 2 digits, like the value."""
    if not digits:
        return None
    a_min = pi.dstarts[lo] if lo < len(pi.dstarts) else 0
    b_max = pi.dends[hi] if hi < len(pi.dends) else len(pi.digits)
    start_ok = {pi.dstarts[i]: i for i in range(lo, hi + 1) if pi.dends[i] > pi.dstarts[i]}
    end_ok = {pi.dends[i]: i for i in range(lo, hi + 1) if pi.dends[i] > pi.dstarts[i]}
    pos = pi.digits.find(digits, a_min)
    while pos != -1 and pos + len(digits) <= b_max:
        end = pos + len(digits)
        if pos in start_ok and end in end_ok:
            first, last = start_ok[pos], end_ok[end]
            if not cents or _has_cents(" ".join(w.text for w in pi.words[first : last + 1])):
                return _Match(pi.number, first, last)
        pos = pi.digits.find(digits, pos + 1)
    return None


def _text_cutoff(n: int, ocr: bool) -> float:
    base = 100.0 if n < 6 else 90.0 if n < 12 else 85.0
    return base if n < 6 else base - (5.0 if ocr else 0.0)


def _digit_edges_ok(pi: PageIndex, a: int, b: int, target: str) -> bool:
    """A target starting/ending with a digit must not be a part of a longer number inside the same word
    ("8marca2026" inside "18 marca 2026"); a digit in the neighbouring word is fine."""
    if target[0].isdigit() and a > 0 and pi.concat[a - 1].isdigit() and a not in pi.start_set:
        return False
    if target[-1].isdigit() and b < len(pi.concat) and pi.concat[b].isdigit() and b not in pi.end_set:
        return False
    return True


def _find_text(pi: PageIndex, target: str, lo: int, hi: int) -> _Match | None:
    a0, b0 = pi.starts[lo], pi.ends[hi]
    hay = pi.concat[a0:b0]
    if not hay:
        return None
    n = len(target)
    # 1) exact occurrence: whole words first ("Kraków", not the street "Krakowska" before it), then — only values
    #    of ≥ 6 characters — inside a longer word (inflection: "Kowalskiego"); short values: whole words only
    partial: tuple[int, int] | None = None
    pos = hay.find(target)
    while pos != -1:
        a, b = a0 + pos, a0 + pos + n
        if a in pi.start_set and b in pi.end_set:
            if rng := pi.words_in_chars(a, b):
                return _Match(pi.number, *rng)
        elif partial is None and n >= 6 and _digit_edges_ok(pi, a, b, target):
            partial = pi.words_in_chars(a, b)
        pos = hay.find(target, pos + 1)
    if partial is not None:
        return _Match(pi.number, *partial)
    if n < 6 or len(hay) < 0.9 * n:
        return None  # a window shorter than the value would only give a partial (misleading) highlight
    # 2) fuzzy (OCR errors, inflection, hyphenation); digits inside the value must still match exactly
    al = fuzz.partial_ratio_alignment(target, hay, score_cutoff=_text_cutoff(n, pi.ocr))
    if al is None:
        return None
    rng = pi.words_in_chars(a0 + al.dest_start, a0 + al.dest_end)
    if not rng:
        return None
    digits = "".join(ch for ch in target if ch.isdigit())
    if digits and _find_digits(pi, digits, rng[0], rng[1]) is None:
        return None
    return _Match(pi.number, *rng)


def _find_value(pi: PageIndex, targets: list[str], lo: int = 0, hi: int | None = None, distinctive: bool = False):
    hi = len(pi.words) - 1 if hi is None else hi
    for t in targets:
        norm = norm_text(t)
        if len(norm) < 3 or (distinctive and len(norm) < DISTINCTIVE_CHARS):
            continue
        if _is_digit_heavy(norm):
            m = _find_digits(pi, "".join(ch for ch in norm if ch.isdigit()), lo, hi, cents=_has_cents(str(t)))
        else:
            m = _find_text(pi, norm, lo, hi)
        if m:
            return m
    return None


def _locate_snippet(pi: PageIndex, snippet: str) -> _Match | None:
    norm = norm_text(snippet)[:SNIPPET_MAX_CHARS]
    if len(norm) < 8 or not pi.concat:
        return None
    al = fuzz.partial_ratio_alignment(norm, pi.concat, score_cutoff=SNIPPET_CUTOFF)
    if al is None:
        return None
    rng = pi.words_in_chars(al.dest_start, al.dest_end)
    return _Match(pi.number, *rng) if rng else None


# --------------------------------------------------------------------------- boxes


def _line_boxes(words: list[Word]) -> list[list[float]]:
    """Union of consecutive words per text line (a new box on a line break or a wide horizontal gap)."""
    boxes: list[list[float]] = []
    cur: list[float] | None = None
    for w in words:
        x0, top, x1, bottom = (float(c) for c in w.bbox)
        if cur is not None:
            h = min(cur[3] - cur[1], bottom - top) or 1.0
            overlap = min(cur[3], bottom) - max(cur[1], top)
            if overlap / h >= 0.5 and cur[2] - 2 <= x0 <= cur[2] + 2.5 * h:
                cur = [min(cur[0], x0), min(cur[1], top), max(cur[2], x1), max(cur[3], bottom)]
                continue
            boxes.append(cur)
        cur = [x0, top, x1, bottom]
    if cur is not None:
        boxes.append(cur)
    return boxes


def _boxes(pi: PageIndex, m: _Match, kind: str) -> list[dict[str, Any]]:
    return [
        {"page": pi.number, "bbox": [round(c, 1) for c in bb], "kind": kind}
        for bb in _line_boxes(pi.words[m.first : m.last + 1])
    ]


def field_boxes(fr: FieldResult, pages: dict[int, PageIndex]) -> list[dict[str, Any]]:
    """Highlight boxes for one field (see module docstring). Empty list when nothing can be located."""
    if fr.method == "derived":  # value comes from a dictionary, not from the text
        return []
    ev = fr.evidence
    ev_page = getattr(ev, "page", None)
    snippet = getattr(ev, "snippet", "") or ""
    bbox = getattr(ev, "bbox", None)
    if bbox and ev_page:
        return [{"page": int(ev_page), "bbox": [round(float(c), 1) for c in bbox], "kind": KIND_BBOX}]
    if fr.value is None and fr.raw_value is None:
        return []
    targets = search_targets(fr.value, fr.raw_value)

    snippet_region: tuple[PageIndex, _Match] | None = None
    if ev_page in pages:
        pi = pages[ev_page]
        region = _locate_snippet(pi, snippet) if snippet else None
        if region:
            snippet_region = (pi, region)
            # snippets are cut around the value → search inside it first, then slightly wider (alignment slop)
            last = len(pi.words) - 1
            for lo, hi in (
                (region.first, region.last),
                (max(0, region.first - REGION_PAD_WORDS), min(last, region.last + REGION_PAD_WORDS)),
            ):
                if m := _find_value(pi, targets, lo, hi):
                    return _boxes(pi, m, KIND_VALUE)
        if m := _find_value(pi, targets):
            return _boxes(pi, m, KIND_VALUE)

    # evidence page unknown or wrong (page mapping off by one) → other pages, distinctive values only
    for number, pi in pages.items():
        if number == ev_page:
            continue
        if m := _find_value(pi, targets, distinctive=True):
            return _boxes(pi, m, KIND_VALUE)

    if snippet_region is None and snippet:
        for number, pi in pages.items():
            if number != ev_page and (region := _locate_snippet(pi, snippet)):
                snippet_region = (pi, region)
                break
    if snippet_region is not None:
        return _boxes(snippet_region[0], snippet_region[1], KIND_SNIPPET)
    return []
