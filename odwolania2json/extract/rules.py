"""Rule-based extractor driven entirely by the YAML hints of each field (config/doc_types/*.yaml).

The code knows no document class: every class-specific word (labels, section headers, trigger phrases,
dictionary keywords) lives in YAML. It provides generic mechanisms, combinable per field:

  patterns       regex; value = group "value" (or group 1); every matching pattern gives a candidate, in order
  labels         'Label: value', 'LABEL ...... value', 'Label' with the value on the next line(s), labels split
                 over lines by OCR ('Kwota' / 'przelewu'); case- and diacritics-insensitive ('Tytutem' = 'Tytułem')
  value_pattern  shape of a label's value: the value starts with it or contains exactly one match
  max_lines      continuation lines of a label's value; a blank line, any label of the class, `stop` or a
                 'Something:' line ends it
  section        region (start … end) where the field / its children are searched; `optional` → parent region
  item_split     array items inside the region → paths X[0]…, X[1]…; `item_prefer` puts matching items first
                 (e.g. the consortium leader when max_items: 1); `max_items` cuts the list
  strategy       named generic heuristic (STRATEGIES) with `params` — see the functions decorated @strategy
  confidence     base confidence of the field's candidates (merge turns < min_confidence into null)
  scope          all | first_page | head (first HEAD_CHARS characters)

Text is matched on a diacritics-folded copy with identical offsets (patterns are folded too — Polish letters
are not regex metacharacters); values are cut from the original text. Every candidate carries Evidence.
"""

from __future__ import annotations

import bisect
import functools
import logging
import re
from collections.abc import Callable, Iterator
from dataclasses import dataclass, replace
from typing import Any

import yaml

from odwolania2json import CONFIG_DIR
from odwolania2json.models import Candidate, Document, Evidence
from odwolania2json.normalize.text import fold_diacritics, normalize_for_matching, person_name
from odwolania2json.paths import join
from odwolania2json.spec import Dictionary, DocTypeSpec, FieldSpec, Registry, Section

try:  # common Polish given names (folded, lower-case) of the person_name normalizer — one list for both modules
    from odwolania2json.normalize.text import _GIVEN_NAMES as GIVEN_NAMES
except ImportError:  # pragma: no cover — the normalizer module changed; only inflection detection gets weaker
    GIVEN_NAMES: frozenset[str] = frozenset()

log = logging.getLogger(__name__)

CONF_PATTERN = 0.8
CONF_LABEL = 0.7
CONF_STRATEGY = 0.7
HEAD_CHARS = 5000  # scope "head": the beginning of the document (parties, subject of the procedure)

Span = tuple[int, int]


def _conf(field: FieldSpec, default: float) -> float:
    """Base confidence of a field's candidates: its YAML `confidence` (an explicit 0 included) or the default."""
    return default if field.confidence is None else field.confidence


# --- shapes and helper regexes (generic, not tied to a document class) ------------------------------------------
_MONTH_WORD = r"(?:stycz|lut|mar|kwie|maj|czerw|lip|sierp|wrze|pa[zź]dz|listop|grud)[^\W\d_]*"
_DATE_SHAPE = (
    r"(?<!\d)(?:\d{1,2}[ \t]?[./-][ \t]?\d{1,2}[ \t]?[./-][ \t]?\d{4}|\d{4}[ \t]?[./-][ \t]?\d{1,2}[ \t]?[./-][ \t]?"
    rf"\d{{1,2}}|\d{{1,2}}\s*{_MONTH_WORD}\.?\s*\d{{4}}|\d{{1,2}} \d{{1,2}} \d{{4}})(?!\d)"
)
_DATE_RX = re.compile(_DATE_SHAPE, re.IGNORECASE)
_DEFAULT_SHAPES = {"date": _DATE_SHAPE}
_LINE_PREFIX = re.compile(r"[ \t]*(?:[•·*>\-–]|\(?\d{1,2}[.)]|[a-z]\))?[ \t]*")  # bullets / numbering before a label
_SEPARATOR = re.compile(r"[ \t]*(?:[:=>|…_.·\-–—]+[ \t]*)*")  # ':', dot leaders, dashes between label and value
_WORDS_COLON = re.compile(r"(?:[^\W\d_]+\.?[ \t]+){0,2}[^\W\d_]+\.?[ \t]*:[ \t]*")  # 'transakcji: ' after a label
_GENERIC_LABEL_LINE = re.compile(r"[ \t]*[^\W\d_]+\.?(?:[ \t]+[^\W\d_]+\.?){0,3}[ \t]*:(?:[ \t]|$)")
_ITEM_MARKER = re.compile(r"\s*(?:\(?\d{1,2}[.)]|[a-z]\)|[•·*\-–])\s*")  # use with .match(text, pos)
_DOTTED = re.compile(r"[.…_]{4,}")
_QUOTES_OPEN, _QUOTES_CLOSE = '„“"«', '”“"»'
_TITLE = re.compile(  # professional titles: a name preceded by one belongs to an attorney, not to a signatory
    r"(?<![^\W\d_])(?:r\.\s*pr\.?|radc\w*\s+prawn\w*|adw\.|adwokat\w*|mec\.|mecenas\w*|apl\.\s*(?:adw|radc)\.?)",
    re.IGNORECASE,
)
# endings of inflected (non-nominative) name forms: 'Kowalskiego', 'Kowalskiemu', 'Janowi', 'Kowalskiej', 'Kowalskim',
# 'Annę', 'Anną' — the nominative is the LLM's job, the rules only lower the confidence
_INFLECTED = re.compile(r"(?:ego|emu|owi|[sc]kiej|dzkiej|[sc]kim|dzkim|ę|ą)$", re.IGNORECASE)
# inflected given names: male stem + ending ('Jana', 'Janem', 'Janie'), female stem + ending ('Anny', 'Marii', 'Annie')
_MALE_ENDINGS, _FEMALE_ENDINGS = ("a", "em", "ie"), ("y", "i", "ie")


# ================================================================================================ text model
_ODD_SPACE = re.compile(r"[^\S\n]")  # \r, \f, \v, thin / narrow no-break spaces … → ' '
_EDGE_SPACES = re.compile(r"^ +| +$", re.MULTILINE)  # indentation: 'X' patterns anchored with '^' match every line
_BLANK_RUN = re.compile(r"\n{3,}")  # 2+ blank lines → 1: bounds every '^\s*' scan (no quadratic cost)


def _page_text(text: str) -> str:
    """Page text for matching: Windows / old Mac line ends → '\\n', any other whitespace → ' ', no spaces at line
    ends, runs of blank lines → one blank line (a page of 10 000 empty lines made every '^\\s*' pattern
    quadratic: 6.7 s)."""
    text = normalize_for_matching(_ODD_SPACE.sub(" ", text.replace("\r\n", "\n").replace("\r", "\n")))
    return _BLANK_RUN.sub("\n\n", _EDGE_SPACES.sub("", text))


class DocText:
    """All pages joined by blank lines (normalized for matching) + a diacritics-folded copy with equal offsets."""

    def __init__(self, doc: Document):
        parts: list[str] = []
        self.page_starts: list[int] = []
        pos = 0
        for p in doc.pages:
            t = _page_text(p.text)
            self.page_starts.append(pos)
            parts.append(t)
            pos += len(t) + 2
        self.page_numbers = [p.number for p in doc.pages]
        self.raw = "\n\n".join(parts)
        self.fold = fold_diacritics(self.raw)
        self.line_starts = [0] + [m.end() for m in re.finditer("\n", self.raw)]

    def __len__(self) -> int:
        return len(self.raw)

    def page_at(self, pos: int) -> int | None:
        i = bisect.bisect_right(self.page_starts, pos) - 1
        return self.page_numbers[i] if 0 <= i < len(self.page_numbers) else None

    def page_end(self, number_index: int) -> int:
        """End offset of the page with the given 0-based index."""
        if number_index + 1 < len(self.page_starts):
            return self.page_starts[number_index + 1] - 2
        return len(self.raw)

    def line_start(self, pos: int) -> int:
        return self.line_starts[bisect.bisect_right(self.line_starts, pos) - 1]

    def line_end(self, pos: int) -> int:
        e = self.raw.find("\n", pos)
        return len(self.raw) if e < 0 else e

    def lines(self, a: int, b: int) -> Iterator[Span]:
        """Lines intersecting [a, b), clipped to it (the first one may start mid-line)."""
        pos = a
        while pos < b:
            e = min(self.line_end(pos), b)
            yield pos, e
            pos = e + 1

    def evidence(self, a: int, b: int) -> Evidence:
        return Evidence(page=self.page_at(a), snippet=self.raw[max(0, a - 40) : min(len(self.raw), b + 40)].strip())


@functools.lru_cache(maxsize=4096)
def _rx(pattern: str, case_sensitive: bool = False) -> re.Pattern:
    """Compile a YAML regex for the folded text (Polish letters in the pattern are folded as well)."""
    return re.compile(fold_diacritics(pattern), re.MULTILINE | (0 if case_sensitive else re.IGNORECASE))


@functools.lru_cache(maxsize=2048)
def _label_body(label: str) -> str:
    """Regex of a label on the folded text: words separated by any (or no) whitespace — OCR splits labels over
    lines and glues words ('Zrachunku') —, 'ł' also matches 't' (OCR: 'Tytutem'), a trailing dot is optional."""
    out: list[str] = []
    for ch in label.strip():
        if ch.isspace():
            if out and out[-1] != r"\s*":
                out.append(r"\s*")
        elif ch in "łŁ":
            out.append("[lt]")
        elif ch == ".":
            out.append(r"\.?")
        else:
            out.append(re.escape(fold_diacritics(ch)))
    return "".join(out)


@functools.lru_cache(maxsize=2048)
def _label_rx(label: str, case_sensitive: bool = False) -> re.Pattern:
    body = _label_body(label)
    return re.compile(rf"(?<![^\W\d_]){body}(?![^\W\d_])", re.MULTILINE | (0 if case_sensitive else re.IGNORECASE))


# ================================================================================================ context
@dataclass(frozen=True)
class Where:
    """Where a field is searched: its region + information about the enclosing array item."""

    region: Span
    fallback: Span | None = None  # the enclosing array's region (item fields may fall back to it)
    index: int = 0  # index of the enclosing array item
    count: int = 1  # number of items of the enclosing array
    focus: Span | None = None  # the text that produced the item (e.g. one person's name) — see item_text


class Ctx:
    def __init__(self, doc: Document, spec: DocTypeSpec, dictionaries: dict[str, Dictionary]):
        self.doc = doc
        self.spec = spec
        self.text = DocText(doc)
        self.dictionaries = dictionaries
        labels = sorted({lab for f in spec.leaf_specs().values() for lab in f.labels}, key=len, reverse=True)
        body = "|".join(f"(?:{_label_body(lab)})" for lab in labels)
        self._labels = re.compile(rf"(?:{body})(?![^\W\d_])", re.IGNORECASE) if labels else None

    def label_at(self, pos: int, end: int | None = None) -> bool:
        """Does a label of this document class start at pos (after spaces)?"""
        if self._labels is None:
            return False
        t = self.text.fold
        while pos < len(t) and t[pos] in " \t":
            pos += 1
        return self._labels.match(t, pos, end if end is not None else len(t)) is not None

    def candidate(self, path: str, value: Any, method: str, conf: float, span: Span, note: str = "") -> Candidate:
        conf = max(0.0, min(1.0, conf))
        return Candidate(path, value, method, round(conf, 3), self.text.evidence(*span), note)


# ================================================================================================ strategies
@dataclass(frozen=True)
class Strategy:
    leaf: Callable[..., list[Candidate]] | None = None  # (ctx, field, path, where) -> candidates
    array: Callable[..., list[Span | tuple[Span, Span]]] | None = None  # (ctx, field, path, where) -> item
    # regions, optionally as (region, focus) pairs


STRATEGIES: dict[str, Strategy] = {}


def strategy(name: str, kind: str = "leaf"):
    """Register a generic heuristic usable from YAML (`strategy: name`, parameters in `params`)."""

    def deco(fn):
        old = STRATEGIES.get(name, Strategy())
        STRATEGIES[name] = replace(old, **{kind: fn})
        return fn

    return deco


# ================================================================================================ entry point
def extract_rules(doc: Document, spec: DocTypeSpec, registry: Registry | None = None) -> list[Candidate]:
    """Candidates for every field of the document class, from the YAML hints."""
    ctx = Ctx(doc, spec, registry.dictionaries if registry is not None else _default_dictionaries())
    out: list[Candidate] = []
    root = Where(region=(0, len(ctx.text)))
    for key, field in spec.output.items():
        _walk(ctx, field, key, root, out)
    return out


@functools.lru_cache(maxsize=1)
def _default_dictionaries() -> dict[str, Dictionary]:
    """Dictionaries from config/dictionaries — used when the caller does not pass the registry."""
    out: dict[str, Dictionary] = {}
    for f in sorted((CONFIG_DIR / "dictionaries").glob("*.y*ml")):
        try:
            d = Dictionary.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
            out[d.id] = d
        except Exception as e:  # noqa: BLE001 — a broken dictionary must not stop extraction
            log.warning("Cannot load dictionary %s: %s", f.name, e)
    return out


def _scoped(ctx: Ctx, field: FieldSpec, region: Span) -> Span:
    a, b = region
    if field.scope == "first_page":
        b = min(b, ctx.text.page_end(0))
    elif field.scope == "head":
        b = min(b, HEAD_CHARS)
    return a, max(a, b)


def _find_section(ctx: Ctx, sec: Section, region: Span, case_sensitive: bool) -> Span | None:
    m = _rx(sec.start, case_sensitive).search(ctx.text.fold, *region)
    if not m:
        return None
    start, end = m.end(), region[1]
    if sec.end:
        m2 = _rx(sec.end, case_sensitive).search(ctx.text.fold, start, end)
        if m2:
            end = m2.start()
    return start, end


def _walk(ctx: Ctx, field: FieldSpec, path: str, where: Where, out: list[Candidate]) -> None:
    region = _scoped(ctx, field, where.region)
    split = True
    if field.section is not None:
        sec = _find_section(ctx, field.section, region, field.case_sensitive)
        if sec is None and where.fallback is not None:
            sec = _find_section(ctx, field.section, _scoped(ctx, field, where.fallback), field.case_sensitive)
        if sec is None:
            if not field.section.optional:
                return
            sec, split = region, False  # optional section absent: the whole region is one item, never split
        region = sec
    here = replace(where, region=region)
    if field.type == "object":
        for key, child in (field.properties or {}).items():
            _walk(ctx, child, join(path, key), here, out)
    elif field.type == "array":
        if field.items is None:
            return
        try:
            items = _array_items(ctx, field, path, here, split)
        except Exception:  # noqa: BLE001 — a failing strategy must not lose the rest of the document
            log.exception("rules: array %s failed", path)
            return
        for i, (item, focus) in enumerate(items):
            item_where = Where(item, fallback=region, index=i, count=len(items), focus=focus)
            _walk(ctx, field.items, f"{path}[{i}]", item_where, out)
    else:
        try:
            out.extend(_leaf(ctx, field, path, here))
        except Exception:  # noqa: BLE001 — one bad hint must not lose the other fields
            log.exception("rules: field %s failed", path)


def _array_items(
    ctx: Ctx, field: FieldSpec, path: str, where: Where, split: bool = True
) -> list[tuple[Span, Span | None]]:
    """Item regions (+ focus spans) of an array: from its strategy, or `item_split` inside the region."""
    a, b = where.region
    items: list[tuple[Span, Span | None]]
    if field.strategy:
        fn = STRATEGIES.get(field.strategy, Strategy()).array
        raw = fn(ctx, field, path, where) if fn else []
        items = [(x[0], x[1]) if isinstance(x[0], tuple) else (x, None) for x in raw]  # type: ignore[index]
    elif field.item_split and split:
        starts = [m.start() for m in _rx(field.item_split, field.case_sensitive).finditer(ctx.text.fold, a, b)]
        starts = sorted(set(starts))
        if starts and not _list_header(ctx.text.raw[a : starts[0]]):
            starts = []  # numbered lines nested in one item (e.g. the attorneys of a single party), not items
        items = [((s, e), None) for s, e in zip(starts, [*starts[1:], b], strict=False)] or [((a, b), None)]
    else:
        items = [((a, b), None)]
    if field.item_prefer:
        rx = _rx(field.item_prefer, field.case_sensitive)
        items = sorted(items, key=lambda it: 0 if rx.search(ctx.text.fold, *it[0]) else 1)
    if field.max_items is not None:
        items = items[: field.max_items]
    return items


_ENTITY_DETAIL = re.compile(  # details of one entity: postal code, identifier / phone (5+ digits), street, e-mail
    r"\d{2}[ \t]?-[ \t]?\d{3}(?!\d)|\d(?:[ -]?\d){4,}|(?<![^\W\d_])(?:ul|al|pl|os)\.|@", re.IGNORECASE
)


def _list_header(text: str) -> bool:
    """Is `text` — the region before the first `item_split` marker — only the heading of a list? Lists of parties
    follow their heading ('Odwołujący: Konsorcjum wykonawców … w' ⏎ 'składzie:' ⏎ '1) …'). An address or an
    identifier before the first marker means that the numbered lines belong to one entity already described
    ('Odwołujący: Alfa sp. z o.o.' ⏎ 'ul. …, KRS …' ⏎ 'reprezentowana przez:' ⏎ '1) adw. …')."""
    return _ENTITY_DETAIL.search(text) is None


# ================================================================================================ leaves
def _leaf(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[Candidate]:
    t = ctx.text
    out: list[Candidate] = []
    for pattern in field.patterns:
        m = _rx(pattern, field.case_sensitive).search(t.fold, *where.region)
        if m:
            a, b = _value_span(m)
            value = t.raw[a:b].strip()
            if value:
                out.append(ctx.candidate(path, value, "rules:pattern", _conf(field, CONF_PATTERN), (a, b)))
    for label in field.labels:
        got = _label_value(ctx, field, label, where.region)
        if got:
            value, span, loose = got
            conf = _conf(field, CONF_LABEL) - (0.1 if loose else 0.0)  # an exact label of another hint wins
            out.append(ctx.candidate(path, value, "rules:label", conf, span, label))
    if field.strategy:
        fn = STRATEGIES.get(field.strategy, Strategy()).leaf
        if fn is not None:
            out.extend(fn(ctx, field, path, where))
    return out


def _value_span(m: re.Match) -> Span:
    if "value" in m.re.groupindex and m.group("value") is not None:
        return m.span("value")
    if m.re.groups and m.group(1) is not None:
        return m.span(1)
    return m.span()


def _label_value(ctx: Ctx, field: FieldSpec, label: str, region: Span) -> tuple[str, Span, bool] | None:
    """(value, span, loose) of the first occurrence of the label that has a value. An occurrence followed directly
    by its value beats a loose one with extra words before the colon ('Kwota prowizji: 0,00' for the label
    'Kwota'), wherever it is."""
    t = ctx.text
    loose = None
    for m in _label_rx(label, field.case_sensitive).finditer(t.fold, *region):
        got = _value_after_label(ctx, field, m.start(), m.end(), region)
        if got and not got[2]:
            return got
        loose = loose or got
    if loose:
        return loose
    # OCR of two-line labels in a narrow column puts the value between the label words:
    # 'Kwota 10 000,00 PLN' / 'przelewu', 'Numer 305494' / 'transakcji'
    words = label.split()
    for k in range(1, len(words)):
        head, tail = _label_body(" ".join(words[:k])), _label_body(" ".join(words[k:]))
        rx = re.compile(rf"^[ \t]*{head}(?![^\W\d_])(?P<rest>[^\n]*)\n[ \t]*{tail}[ \t]*:?[ \t]*$", re.M | re.I)
        for m in rx.finditer(t.fold, *region):
            vs = _SEPARATOR.match(t.raw, m.start("rest"), m.end("rest")).end()
            value = _finish_value(field, t.raw[vs : m.end("rest")])
            if value:
                return value, (m.start(), m.end()), False
    return None


def _finish_value(field: FieldSpec, value: str, anchored: bool = True) -> str | None:
    """Clean a label's value and cut it to the field's shape (value_pattern / default shape of its type)."""
    value = _DOTTED.sub(" ", value).strip(" \t:;|")
    shape = field.value_pattern or _DEFAULT_SHAPES.get(field.type)
    if shape and value:
        return _shaped(value, shape, field.case_sensitive, anchored)
    return value or None


def _value_after_label(ctx: Ctx, field: FieldSpec, ls_: int, le_: int, region: Span) -> tuple[str, Span, bool] | None:
    """(value, span, loose) of a label occurrence [ls_, le_): rest of its line, or the next non-empty line(s).
    loose = words between the label and the colon ('Numer referencyjny transakcji: X' for 'Numer referencyjny',
    but also 'Kwota prowizji: 0,00' for 'Kwota')."""
    t = ctx.text
    a, b = region
    line_start = max(a, t.line_start(ls_))
    at_line_start = _LINE_PREFIX.fullmatch(t.raw, line_start, ls_) is not None
    line_end = min(t.line_end(le_), b)
    sep = _SEPARATOR.match(t.raw, le_, line_end)
    vs = sep.end()
    colon, loose = ":" in t.raw[le_:vs], False
    if not colon and (w := _WORDS_COLON.match(t.raw, vs, line_end)) and not ctx.label_at(vs, line_end):
        vs, colon, loose = w.end(), True, True
    rest = t.raw[vs:line_end]
    shaped = bool(field.value_pattern or field.type in _DEFAULT_SHAPES or field.type == "number")
    lines: list[Span] = []
    same_line = bool(rest.strip()) and not ctx.label_at(vs, line_end)
    if same_line:
        if not (at_line_start or colon):
            return None  # the label words inside running text
        lines.append((vs, line_end))
    else:  # label alone or a row of column headers ('Kwota Data transakcji'): the value is below
        nxt = _next_nonempty_line(ctx, line_end + 1, b)
        # a value line may start with a row label of a table ('Data operacji … -17,00 PLN') — typed fields only
        if nxt is None or (ctx.label_at(nxt[0], nxt[1]) and not shaped):
            return None
        lines.append(nxt)
    while len(lines) < max(1, field.max_lines):
        nxt = _next_line(ctx, lines[-1][1] + 1, b)
        if nxt is None or not _continues(ctx, field, nxt, lines):
            break
        lines.append(nxt)
    value = _finish_value(field, " ".join(t.raw[x:y].strip() for x, y in lines), anchored=same_line)
    if value is None and same_line and shaped and field.type != "number":
        # 'Data operacji  PRZELEW ELIXIR -17,00 PLN' / '2026-04-09 …': the shaped value is on the next line
        nxt = _next_nonempty_line(ctx, line_end + 1, b, max_skip=0)
        if nxt is not None and (value := _finish_value(field, t.raw[nxt[0] : nxt[1]], anchored=False)):
            lines = [*lines, nxt]
    return (value, (lines[0][0], lines[-1][1]), loose) if value else None


def _next_line(ctx: Ctx, pos: int, end: int) -> Span | None:
    if pos >= end:
        return None
    return pos, min(ctx.text.line_end(pos), end)


def _next_nonempty_line(ctx: Ctx, pos: int, end: int, max_skip: int = 2) -> Span | None:
    for _ in range(max_skip + 1):
        line = _next_line(ctx, pos, end)
        if line is None:
            return None
        if ctx.text.raw[line[0] : line[1]].strip() and not _DOTTED.fullmatch(ctx.text.raw[line[0] : line[1]].strip()):
            return line
        pos = line[1] + 1
    return None


def _continues(ctx: Ctx, field: FieldSpec, line: Span, lines: list[Span]) -> bool:
    """Is `line` a continuation of a multi-line label value?"""
    t = ctx.text
    s = t.raw[line[0] : line[1]]
    if not s.strip() or ctx.label_at(line[0], line[1]):
        return False
    indent = line[0] + len(s) - len(s.lstrip())
    if any(_rx(p, field.case_sensitive).match(t.fold, indent, line[1]) for p in field.stop):
        return False
    if _GENERIC_LABEL_LINE.match(s):
        return False
    so_far = " ".join(t.raw[x:y] for x, y in lines).strip()
    if so_far and so_far[0] in _QUOTES_OPEN and any(q in so_far[1:] for q in _QUOTES_CLOSE):
        return False  # the quoted value is already closed
    return True


def _shaped(value: str, shape: str, case_sensitive: bool, anchored: bool = True) -> str | None:
    """Cut the value to its shape: a match at its start (`anchored`: the value directly follows its label), else
    the only distinct match inside it — several (a row of columns below a header) → ambiguous → None."""
    rx = re.compile(shape, re.IGNORECASE if not case_sensitive else 0)
    if anchored and (m := rx.match(value.lstrip(" \t\"'„“”«»("))) and m.group(0).strip():
        return m.group(0).strip()
    found = {re.sub(r"\s+", " ", x.group(0).strip()) for x in rx.finditer(value) if x.group(0).strip()}
    return found.pop() if len(found) == 1 else None


# ================================================================================================ text helpers
def _clean_line(s: str) -> str:
    s = s.strip()
    if m := _ITEM_MARKER.match(s):
        s = s[m.end() :]
    return _DOTTED.sub(" ", s).strip()


def _first_match(ctx: Ctx, patterns: list[str], region: Span, earliest: bool = True) -> re.Match | None:
    """First match among patterns: the earliest in the text (or the first pattern that matches)."""
    best = None
    for p in patterns:
        m = _rx(p).search(ctx.text.fold, *region)
        if m and (best is None or (earliest and m.start() < best.start())):
            best = m
            if not earliest:
                break
    return best


def _cut_at(ctx: Ctx, patterns: list[str], a: int, b: int) -> int:
    """End of [a, b) cut at the earliest match of any pattern."""
    for p in patterns:
        m = _rx(p).search(ctx.text.fold, a, b)
        if m:
            b = min(b, m.start())
    return b


def _split_depth0(text: str, sep: re.Pattern, base: int) -> list[Span]:
    """Split text at separator matches outside parentheses; spans are absolute (base offset)."""
    spans: list[Span] = []
    depth, start, pos = 0, 0, 0
    while pos < len(text):
        ch = text[pos]
        if ch in "([":
            depth += 1
        elif ch in ")]":
            depth = max(0, depth - 1)
        elif depth == 0 and (m := sep.match(text, pos)) and m.end() > pos:
            spans.append((base + start, base + pos))
            start = pos = m.end()
            continue
        pos += 1
    spans.append((base + start, base + len(text)))
    return [(x, y) for x, y in spans if text[x - base : y - base].strip()]


_LIST_SEP = re.compile(r"\s*(?:[,;]|\s(?:oraz|i|a\s+także)\s)\s*", re.IGNORECASE)


def _people_in(ctx: Ctx, a: int, b: int, sep: re.Pattern = _LIST_SEP) -> list[tuple[str, Span]]:
    """Plausible person names listed in [a, b): split at ',', ';', 'oraz', 'i' (outside parentheses); a piece
    spread over several lines is split into lines when that yields more names (one name per line lists)."""
    raw = ctx.text.raw
    out: list[tuple[str, Span]] = []
    for x, y in _split_depth0(raw[a:b], sep, a):
        while x < y and raw[x] in " \t\n:;,.-–—":  # ': r. pr. Jan Kowalski' → 'r. pr. Jan Kowalski'
            x += 1
        while y > x and raw[y - 1] in " \t\n,;:":
            y -= 1
        piece = raw[x:y]
        joined = person_name(piece.replace("\n", " "))
        by_line = []
        if "\n" in piece.strip():
            pos = x
            for line in piece.split("\n"):
                if (name := person_name(line)) is not None:
                    by_line.append((name, (pos, pos + len(line))))
                pos += len(line) + 1
        if len(by_line) > (1 if joined else 0):
            out.extend(by_line)
        elif joined:
            out.append((joined, (x, y)))
    return out


def _is_inflected(name: str) -> bool:
    """Is a (normalized) person name in an inflected form? 'Jana Kowalskiego', 'Janowi Kowalskiemu', 'Annie
    Kowalskiej', 'Jana Nowaka' (a known male given name + '-a') → True; 'Jan Kowalski', 'Anna Nowak' → False."""
    words = name.split()
    if not words:
        return False
    if any(_INFLECTED.search(w) for w in words):
        return True
    first = fold_diacritics(words[0]).lower()
    if first in GIVEN_NAMES:
        return False
    male = any(first.endswith(e) and (s := first[: -len(e)]) in GIVEN_NAMES and s[-1:] != "a" for e in _MALE_ENDINGS)
    return male or any(first.endswith(e) and first[: -len(e)] + "a" in GIVEN_NAMES for e in _FEMALE_ENDINGS)


def _same_person(a: str, b: str) -> bool:
    """The same person, possibly in another grammatical case: 'Jana Nowaka' ~ 'Jan Nowak', 'Pawła Kowalskiego' ~
    'Paweł Kowalski' — one of the forms is inflected and every word shares a stem (an ending adds at most 3
    letters). Two nominative names are different people: 'Marek Kurek' ≠ 'Marta Kurek', 'Jan' ≠ 'Janusz'."""
    wa, wb = fold_diacritics(a).lower().split(), fold_diacritics(b).lower().split()
    if wa == wb:
        return True
    if len(wa) != len(wb) or not (_is_inflected(a) or _is_inflected(b)):
        return False
    for x, y in zip(wa, wb, strict=True):
        common = next((i for i, (p, q) in enumerate(zip(x, y, strict=False)) if p != q), min(len(x), len(y)))
        if abs(len(x) - len(y)) > 3 or common < max(3, min(len(x), len(y)) - 2):
            return False
    return True


def _locate(name: str, text: str, start: int = 0) -> Span:
    """Position of a (normalized) name in the text, compared folded and lower-case; whole text if not found."""
    hay, needle = fold_diacritics(text).lower(), fold_diacritics(name).lower()
    i = hay.find(needle, start)
    return (i, i + len(needle)) if i >= 0 else (0, len(text))


def _names_in_line(text: str) -> list[tuple[str, Span]]:
    """All person names in one line of a signature block (with their spans in the line), without the attorneys
    (names after a professional title):
    'Halina Mazurkiewicz r. pr. Cezary Walczak' → [Halina Mazurkiewicz];
    'Dorota Topolska - Członek Zarządu Halina Baran - Prezes Zarządu' → [Dorota Topolska, Halina Baran];
    'Zbigniew Dobrowolski Marek Rutkiewicz' (two signature columns) → both."""
    first = _TITLE.split(text, maxsplit=1)[0]
    names: list[tuple[str, Span]] = []
    pos = 0
    for seg in re.split(r"\s[-–—]+\s*|[-–—]+\s|[,;|]|\s{3,}", first):
        seg_start = first.find(seg, pos)
        pos = seg_start + len(seg) if seg_start >= 0 else pos
        if not seg.strip():
            continue
        found = []
        if (name := person_name(seg)) is not None:
            found = [name]
        else:
            words = seg.split()
            if len(words) == 4:  # two 2-word names side by side (signature columns)
                left, right = person_name(" ".join(words[:2])), person_name(" ".join(words[2:]))
                if left and right:
                    found = [left, right]
        cursor = max(seg_start, 0)
        for name in found:  # role words around a name ('Członek Zarządu Halina Baran') are dropped by person_name
            a, b = _locate(name, text, cursor)
            names.append((name, (a, b)))
            cursor = b
    return names


# ================================================================================================ strategies
@strategy("item_text")
def _item_text(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[Candidate]:
    """The text of an array item made by a list strategy (its focus, e.g. one person's name) is the value. A person
    name in an inflected form ('adwokatowi Janowi Kowalskiemu') is only a weak guess — the field wants the
    nominative."""
    a, b = where.focus or where.region
    value = ctx.text.raw[a:b].strip()
    if not value:
        return []
    conf = _conf(field, CONF_STRATEGY)
    if (name := person_name(value)) is not None and _is_inflected(name):
        conf = min(conf, 0.35)
    return [ctx.candidate(path, value, "rules:item_text", conf, (a, b))]


_NAME_STOP = (  # lines that are not a name: address, identifiers, contact data. Used with .match() at the content
    # start of a line (after indentation / a list marker), so no '^' — it can never match mid-line ('1) ul. …')
    r"\s*(?:ul\.|ul\s|al\.|pl\.|os\.|ulica|aleja|\d{2}\s?-\s?\d{3}\b|tel\b|tel\.|fax|e-?mail|www\.|adres|KRS|NIP|"
    r"REGON|PESEL|nr\s+wpisu)"
)
_NAME_JOIN = r"(?:\s(?:w|we|i|z|ze|im\.?|oraz|na|do|dla|pod|nr|ds\.)|[-–&])\s*$"


@strategy("first_line")
def _first_line(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[Candidate]:
    """Entity name = the first meaningful line of the region (e.g. the line after 'Zamawiający:').

    params: skip  — regexes of lines to skip (role lines, 'Konsorcjum…'); lines ending with ':' are skipped too
            stop  — regexes of lines meaning "no name here", added to the built-in address / identifier / contact
                    stops (e.g. a person with a professional title where a company name is expected); matched at
                    the start of the line's content (after a list marker such as '1)')
            cut   — regexes where the name ends inside the line (', ul.', ' z siedzibą', '(Lider…')
            max_scan — how many non-empty lines to look at (default 3)
    The name continues on the next line when the line ends with a connector ('… w', '… i') or the next line
    starts with a lower-case letter (and is not an address)."""
    p = field.params
    t = ctx.text
    a, b = where.region
    stop = [_NAME_STOP, *p.get("stop", [])]
    lines = [(ls, le) for ls, le in t.lines(a, b) if _clean_line(t.raw[ls:le])]
    for ls, le in lines[: int(p.get("max_scan", 3))]:
        off = _content_start(t.raw, ls, le)
        text = t.raw[off:le].strip()
        if any(_rx(x).search(t.fold, off, le) for x in p.get("skip", [])) or (text.endswith(":") and len(text) < 90):
            continue  # a role / label line ('1) Zamawiający – Lider:'), the name is below
        if any(_rx(x).match(t.fold, off, le) for x in stop):
            break  # an address where the name should be — no name
        end = _cut_at(ctx, p.get("cut", []), off, _name_end(ctx, off, le, b, stop))
        value = re.sub(r"\s+", " ", t.raw[off:end]).strip(" \t,;:-–")
        if len(re.findall(r"[^\W\d_]", value)) >= 2:
            return [ctx.candidate(path, value, "rules:first_line", _conf(field, CONF_STRATEGY), (off, end))]
        break
    return []


def _content_start(raw: str, ls: int, le: int) -> int:
    """Offset of the first character of a line after indentation and a list marker ('1)', '•')."""
    off = ls + len(raw[ls:le]) - len(raw[ls:le].lstrip())
    m = _ITEM_MARKER.match(raw, off, le)
    return m.end() if m else off


def _name_end(ctx: Ctx, off: int, le: int, end: int, stop: list[str]) -> int:
    """End of a name that starts at `off` on a line ending at `le`: the next line is joined when the name is
    obviously unfinished ('Komenda … w' / 'Jarzębinie', 'Spółka z ograniczoną' / 'odpowiedzialnością')."""
    t = ctx.text
    nxt = _next_line(ctx, le + 1, end)
    if nxt is None:
        return le
    ns = _content_start(t.raw, *nxt)
    following = t.raw[ns : nxt[1]].strip()
    if not following or any(_rx(x).match(t.fold, ns, nxt[1]) for x in stop):
        return le
    if re.search(_NAME_JOIN, t.raw[off:le]) or following[0].islower():
        return nxt[1]
    return le


def _list_region(ctx: Ctx, params: dict, region: Span) -> Span | None:
    """Region of a list that follows a trigger phrase: after the earliest `after` match (no `after` → from the
    start of the region, e.g. a section that begins with the trigger), up to `until` / max_chars."""
    if params.get("after"):
        m = _first_match(ctx, params["after"], region)
        if not m:
            return None
        s = m.end()
    else:
        s = region[0]
    e = min(region[1], s + int(params.get("max_chars", 400)))
    return s, _cut_at(ctx, params.get("until", []), s, e)


@strategy("persons")
def _persons_leaf(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[Candidate]:
    """First person named after a trigger phrase (params: after, until, max_chars), e.g. the first attorney
    after 'reprezentowany przez'. Titles are removed by the person_name normalizer."""
    reg = _list_region(ctx, field.params, _fallback_region(field, where))
    if reg is None:
        return []
    people = _people_in(ctx, *reg)
    if not people:
        return []
    name, span = people[0]
    conf = _conf(field, CONF_STRATEGY)
    if _is_inflected(name):
        conf = min(conf, 0.35)  # 'Jana Kowalskiego' — inflected form, nominative needs the LLM
    return [ctx.candidate(path, ctx.text.raw[span[0] : span[1]].strip(), "rules:persons", conf, span)]


@strategy("persons", kind="array")
def _persons_array(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[tuple[Span, Span]]:
    """Array items = persons listed after a trigger phrase; an item spans until the next person (so details such
    as a PESEL in parentheses stay with their person). params: after, until, max_chars, max."""
    reg = _list_region(ctx, field.params, where.region)
    if reg is None:
        return []
    people = _people_in(ctx, *reg)
    if not people:  # trigger phrase without a recognisable name after it
        return []
    ends = [span[0] for _, span in people[1:]] + [reg[1]]
    items = [((span[0], end), span) for (_, span), end in zip(people, ends, strict=True)]
    return items[: int(field.params.get("max", 20))]


def _fallback_region(field: FieldSpec, where: Where) -> Span:
    if field.params.get("fallback") and where.fallback is not None:
        return where.fallback
    return where.region


@strategy("flag")
def _flag(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[Candidate]:
    """`present` (default 1) when any of params.patterns occurs in the region (params.fallback: also the enclosing
    array's region); else `absent` (default 0) when any of params.absent_patterns occurs in the document head
    (explicit evidence, e.g. 'w imieniu własnym'); else `absent` with params.absent_confidence (if set)."""
    p = field.params
    region = where.region
    conf = _conf(field, CONF_STRATEGY)
    m = _first_match(ctx, p.get("patterns", []), region)
    if m is None and p.get("fallback") and where.fallback is not None:
        m = _first_match(ctx, p.get("patterns", []), where.fallback)
    if m is not None:
        return [ctx.candidate(path, p.get("present", 1), "rules:flag", conf, m.span())]
    if m := _first_match(ctx, p.get("absent_patterns", []), (0, min(len(ctx.text), HEAD_CHARS))):
        return [ctx.candidate(path, p.get("absent", 0), "rules:flag", conf, m.span())]
    if p.get("absent_confidence"):
        return [ctx.candidate(path, p.get("absent", 0), "rules:flag", float(p["absent_confidence"]), region)]
    return []


@strategy("item_flag")
def _item_flag(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[Candidate]:
    """For items of an array: no value when the array has one item; else `present` (default 1) for the items
    matching params.pattern and `absent` (default 0) for the others (e.g. prowadzący postępowanie)."""
    if where.count < 2:
        return []
    p = field.params
    m = _rx(p.get("pattern", r"(?!)")).search(ctx.text.fold, *where.region)
    conf = _conf(field, CONF_STRATEGY)
    if m:
        return [ctx.candidate(path, p.get("present", 1), "rules:item_flag", conf, m.span())]
    return [ctx.candidate(path, p.get("absent", 0), "rules:item_flag", conf - 0.1, where.region)]


@strategy("entity_type")
def _entity_type(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[Candidate]:
    """Natural vs legal person. params: natural (regexes, e.g. CEIDG / 'prowadzący działalność gospodarczą'),
    legal (regexes, e.g. company forms / KRS), natural_value, legal_value. Natural markers win; neither → the
    legal value with a lower confidence."""
    p = field.params
    region = where.region
    conf = _conf(field, CONF_STRATEGY)
    if m := _first_match(ctx, p.get("natural", []), region):
        return [ctx.candidate(path, p.get("natural_value"), "rules:entity_type", conf, m.span())]
    if m := _first_match(ctx, p.get("legal", []), region):
        return [ctx.candidate(path, p.get("legal_value"), "rules:entity_type", conf, m.span())]
    return [ctx.candidate(path, p.get("legal_value"), "rules:entity_type", conf - 0.15, region)]


def _anchored(ctx: Ctx, params: dict, region: Span) -> Span | None:
    """Text after the earliest anchor (params.anchors: regex or {at, until, max_chars}) before params.end,
    up to `until` (default params.until) or max_chars. With `first_item` (e.g. '1.' / '1)'; per anchor or in
    params) and an anchor that ends its line ('polegających na:' ⏎), a list item marker found shortly after the
    anchor starts the text — page footers between the anchor and the list are skipped. An anchor followed by
    text on its own line ('polegającej na wyborze oferty …') starts the text right there: a numbered list further
    down is something else (e.g. the violated provisions)."""
    a, b = region
    b = _cut_at(ctx, params.get("end", []), a, b)
    best: tuple[re.Match, dict] | None = None
    for anchor in params.get("anchors", []):
        spec = anchor if isinstance(anchor, dict) else {"at": anchor}
        m = _rx(spec["at"]).search(ctx.text.fold, a, b)
        if m and (best is None or m.start() < best[0].start()):
            best = (m, spec)
    if best is None:
        return None
    m, spec = best
    s = m.end()
    max_chars = int(spec.get("max_chars", params.get("max_chars", 400)))
    first_item = spec.get("first_item", params.get("first_item"))
    core = m.start() + len(m.group(0).rstrip())  # the anchor without the whitespace / line end it swallowed
    ends_line = not ctx.text.raw[core : min(b, ctx.text.line_end(core))].strip(" \t:")
    if first_item and ends_line and (item := _rx(first_item).search(ctx.text.fold, s, min(b, s + 600))):
        s = item.end()
    e = min(b, s + max_chars)
    return s, _cut_at(ctx, spec.get("until", params.get("until", [])), s, e)


@strategy("dictionary")
def _dictionary(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[Candidate]:
    """Dictionary entry whose keyword occurs EARLIEST in the anchored text (see _anchored; e.g. the first charge
    of an appeal) — 'zaniechanie odrzucenia' starts before 'odrzucenia', so the omission wins over the action.
    Ties: the longer keyword match."""
    d = ctx.dictionaries.get(field.dictionary or field.params.get("dictionary", ""))
    reg = _anchored(ctx, field.params, where.region)
    if d is None or reg is None:
        return []
    best: tuple[int, int, Any, Span] | None = None
    for entry in d.entries:
        for kw in entry.keywords:
            m = _rx(kw).search(ctx.text.fold, *reg)
            if m and (best is None or (m.start(), -(m.end() - m.start())) < (best[0], best[1])):
                best = (m.start(), -(m.end() - m.start()), entry.id, m.span())
    if best is None:
        return []
    value = int(best[2]) if field.type == "integer" else best[2]
    return [ctx.candidate(path, value, "rules:dictionary", _conf(field, 0.6), best[3])]


@strategy("date")
def _date(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[Candidate]:
    """First date in the anchored text (see _anchored), e.g. the date of the action in the first charge.
    params.skip_preceded_by: regexes (ending with '$') of the text just before a date that disqualify it — e.g. the
    date of an act cited in the charge ('ustawy z dnia 11 września 2019 r.')."""
    reg = _anchored(ctx, field.params, where.region)
    if reg is None:
        return []
    fold = ctx.text.fold
    skip = [_rx(x) for x in field.params.get("skip_preceded_by", [])]
    for m in _DATE_RX.finditer(fold, *reg):
        a, b = m.span()
        if any(x.search(fold, max(0, a - 80), a) for x in skip):
            continue
        return [ctx.candidate(path, ctx.text.raw[a:b], "rules:date", _conf(field, 0.6), (a, b))]
    return []


@strategy("signatories", kind="array")
def _signatories(ctx: Ctx, field: FieldSpec, path: str, where: Where) -> list[Span]:
    """Persons signing on behalf of the party (array items = one line/segment per person).

    Sources, deduplicated in order:
      inline — params.inline: regexes followed by the signers ('reprezentowana przez:'), up to inline_until;
      esign  — params.esign: regexes followed by one signer on the same line ('Dokument podpisany przez');
      block  — the signature block: from the first params.block_start match (or dotted signature line) after
               the body, up to params.block_end (at a line start, e.g. 'Pełnomocnictwo przyjmuję:'). Names after
               a professional title (adw., r. pr.) are attorneys and are skipped. With several groups
               (params.group_start, e.g. 'W imieniu X:') and as many array items as groups, group i belongs to
               item i of the enclosing array; lines matching params.block_skip (company stamps, addresses)
               are ignored.
    """
    p = field.params
    t = ctx.text
    doc_region = (0, len(t))
    found: list[tuple[str, Span]] = []

    def add(name: str | None, span: Span) -> None:  # 'Jana Nowaka' (inline, genitive) = 'Jan Nowak' (signature)
        if name and not any(_same_person(name, n) for n, _ in found):
            found.append((name, span))

    def inline(region: Span) -> None:
        for pattern in p.get("inline", []):
            for m in _rx(pattern).finditer(t.fold, *region):
                e = _cut_at(ctx, p.get("inline_until", []), m.end(), min(region[1], m.end() + 200))
                for name, span in _people_in(ctx, m.end(), e):
                    add(name, span)

    groups = _signature_groups(ctx, p, doc_region)
    if where.count > 1:  # several parties (consortium): only what can be attributed to this party
        if len(groups) == where.count:
            for name, span in groups[where.index]:
                add(name, span)
        inline(where.region)
        return [span for _, span in found][: int(p.get("max", 10))]
    for group in groups:  # signature lines first — usually the cleanest spelling of a name
        for name, span in group:
            add(name, span)
    inline(where.region)
    for pattern in p.get("esign", []):
        for m in _rx(pattern).finditer(t.fold, *doc_region):
            le = t.line_end(m.end())
            add(person_name(t.raw[m.end() : le]), (m.end(), le))
    return [span for _, span in found][: int(p.get("max", 10))]


def _signature_groups(ctx: Ctx, p: dict, region: Span) -> list[list[tuple[str, Span]]]:
    """Names in the signature block, grouped by params.group_start headers ('W imieniu X:')."""
    t = ctx.text
    m = _first_match(ctx, p.get("block_start", []), region)
    if m is None:
        return []
    e = _cut_at(ctx, p.get("block_end", []), m.end(), region[1])
    group_rx = [_rx(g) for g in p.get("group_start", [])]
    skip_rx = [_rx(x) for x in p.get("block_skip", [])]
    groups: list[list[tuple[str, Span]]] = [[]]
    for ls, le in t.lines(m.start(), e):
        if any(g.match(t.fold, ls, le) for g in group_rx):
            if groups[-1]:
                groups.append([])
            continue
        if any(x.search(t.fold, ls, le) for x in skip_rx):
            continue  # company stamp, address, contact lines
        line = _DOTTED.sub(lambda d: " " * len(d.group(0)), t.raw[ls:le])  # keep offsets
        if not line.strip() or line.strip().startswith("("):
            continue
        for name, (a, b) in _names_in_line(line):
            groups[-1].append((name, (ls + a, ls + b)))
    return [g for g in groups if g]
