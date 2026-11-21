"""Rule-based extractor driven entirely by the YAML hints of each field.

IMPLEMENTED: `patterns`, `labels`, `section` (objects/arrays), arrays with `item_split`.
TODO(rules): 4. named heuristics (first-line values already work via a pattern: '\\A\\s*(?P<value>[^\\n]+)').

Semantics:
  * section.start / section.end: regexes searched (case-insensitive, diacritics folded) inside the parent region;
    the region starts AFTER the start match and ends BEFORE the end match (or at the parent's end).
  * item_split: regex marking the BEGINNING of each array item (use a lookahead, e.g. '(?=r\\. ?pr\\.)').
    No match → the whole region is one item. No item_split → one item.
  * inside a region, patterns/labels see only the region text ('\\A' = start of region).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from odwolania2json.models import Candidate, Document, Evidence
from odwolania2json.normalize.text import fold_diacritics, normalize_for_matching
from odwolania2json.paths import join
from odwolania2json.spec import DocTypeSpec, FieldSpec, Section

CONF_PATTERN = 0.8
CONF_LABEL = 0.7
MAX_VALUE_LINES = 5

_CUR = r"(?:PLN|zl|EUR|USD)"  # matched on diacritic-folded text ("zł" → "zl")
_SHAPES = {
    "account": r"(?<!\d)(?:PL\s?)?\d{2}(?:\s?\d{4}){6}(?!\d)",
    "date": r"(?<!\d)(?:\d{4}-\d{2}-\d{2}|\d{1,2}[./-]\d{1,2}[./-]\d{4}|\d{1,2}\s+[a-z]+\s+\d{4})",
    "amount": r"(?<![\d.,])(?:\d{1,3}(?:[ \u00a0]\d{3})+|\d+)(?:[.,]\d{2}\s?" + _CUR + r"?|\s?" + _CUR + r")",
}
_SHAPE_RE = {k: re.compile(v, re.IGNORECASE) for k, v in _SHAPES.items()}
_SHAPE_BY_NORMALIZER = {"account_number": "account", "date_pl": "date", "amount_pl": "amount"}
_SHAPE_BY_TYPE = {"date": "date", "number": "amount"}
_FILLER = {"", "nr", "numer", "no"}  # allowed between a mid-sentence label and its value
_OPEN_Q, _CLOSE_Q = "„“\"«", "”“\"»"
_LEADER = " \t.:_-"  # separators between a label and its value ("Label ..... value")


def _shape(field: FieldSpec) -> re.Pattern | None:
    for n in field.normalize:
        if n in _SHAPE_BY_NORMALIZER:
            return _SHAPE_RE[_SHAPE_BY_NORMALIZER[n]]
    k = _SHAPE_BY_TYPE.get(field.type)
    return _SHAPE_RE[k] if k else None


def _shaped(shape, m, le, text, folded, all_re):
    """Typed value near the label: same line (after leaders / 'nr') or up to 2 lines below."""
    for s in shape.finditer(folded, m.end(), min(len(folded), m.end() + 160)):
        if folded.count("\n", m.end(), s.start()) > 2:
            return None
        for lab in all_re.finditer(folded, m.end(), s.start()):
            if not (lab.start() < le < s.start()):  # only a header row ("Kwota Data" / values below) is OK
                return None
        if s.start() <= le:  # same line as the label → only filler words in between
            gap = re.sub(r"[\s:.\-_]+", "", all_re.sub("", folded[m.end() : s.start()])).lower()
            if gap not in _FILLER:
                return None
        return text[s.start() : s.end()].strip(), s.start(), s.end()
    return None


def _open_quote(s: str) -> bool:
    s = s.lstrip()
    return bool(s) and s[0] in _OPEN_Q and not any(c in s[1:] for c in _CLOSE_Q)


def _clean(value: str) -> str:
    v = value.strip()
    if v and v[0] in _OPEN_Q:  # „value” → value (cut at the last closing quote)
        idx = max((v.rfind(c) for c in _CLOSE_Q), default=-1)
        v = v[1:idx] if idx > 0 else v[1:]
    if v and " " not in v and "\n" not in v and len(v) > 1 and v[-1] in ".,;" and v[-2].isdigit():
        v = v[:-1]  # "CEN2611091860728." → "CEN2611091860728"; "S.A." and "ZP.431.2026" stay intact
    return v.strip()


def _from_labels(field: FieldSpec, text: str, folded: str, all_re: re.Pattern) -> tuple[str, int, int] | None:
    """Value for `field` via its labels → (value, start, end) in `text`.

    Typed fields (date / amount / account, inferred from normalizer or type): shape search near the label.
    Other fields: "Label: value", "Label ..... value", "Label value" (label at line start),
    or the line(s) below; multi-line only with `collapse_whitespace`, continuing while the next line
    starts lowercase or a quote is still open. Stops at the next known label or a blank line.
    """
    own = _compile_labels(field.labels, field.case_sensitive)
    if own is None:
        return None
    shape = _shape(field)
    multiline = "collapse_whitespace" in field.normalize

    for m in own.finditer(folded):
        ls, le = _line_bounds(folded, m.start())
        if shape is not None:
            hit = _shaped(shape, m, le, text, folded, all_re)
            if hit:
                return hit
            continue

        before, after = folded[ls : m.start()], folded[m.end() : le]
        if before.strip() and (len(before.split()) > 3 or re.search(r"[\d:;,]", before)):
            continue
        rest = after.lstrip(_LEADER)
        residual = all_re.sub("", rest).strip(_LEADER)
        lines: list[tuple[int, int]] = []
        if residual:
            if all_re.search(rest) or (before.strip() and not after.lstrip().startswith(":")):
                continue  # mixed header row, or the label sits inside a sentence → not a field label
            lines.append((m.end() + len(after) - len(rest), le))
        pos = (lines[-1][1] if lines else le) + 1
        limit = MAX_VALUE_LINES if multiline else 1
        while pos < len(folded) and len(lines) < limit:
            e = folded.find("\n", pos)
            e = len(folded) if e == -1 else e
            raw = folded[pos:e]
            if not raw.strip():
                if lines:
                    break
            elif all_re.search(raw):
                break
            else:
                so_far = folded[lines[0][0] : lines[-1][1]] if lines else ""
                if lines and not (raw.lstrip()[0].islower() or _open_quote(so_far)):
                    break
                lines.append((pos, e))
            pos = e + 1
        if lines:
            a, b = lines[0][0], lines[-1][1]
            value = _clean(text[a:b])
            if value:
                return value, a, b
    return None


@dataclass
class _Ctx:
    doc: Document
    full: str
    first: str
    full_f: str
    first_f: str
    all_re: re.Pattern | None


def _page_of(doc: Document, text: str, pos: int) -> int | None:
    offset = 0
    for p in doc.pages:
        end = offset + len(normalize_for_matching(p.text))
        if pos <= end:
            return p.number
        offset = end + 2
    return None


def _search(pattern: str, text: str, case_sensitive: bool = False) -> re.Match | None:
    return re.search(pattern, text, re.MULTILINE | (0 if case_sensitive else re.IGNORECASE))


def _evidence(ctx: _Ctx, text: str, a: int, b: int) -> Evidence:
    page = 1 if text is ctx.first else _page_of(ctx.doc, ctx.full, a)
    return Evidence(page, text[max(0, a - 40) : b + 40])


# ----------------------------------------------------------------------------- labels
def _compile_labels(labels: list[str], case_sensitive: bool = False) -> re.Pattern | None:
    parts = sorted({lab.strip() for lab in labels if lab and lab.strip()}, key=len, reverse=True)
    if not parts:
        return None
    alts = ["[ \t]+".join(re.escape(w) for w in fold_diacritics(p).split()) for p in parts]
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)", 0 if case_sensitive else re.IGNORECASE)


def _line_bounds(text: str, pos: int) -> tuple[int, int]:
    start = text.rfind("\n", 0, pos) + 1
    end = text.find("\n", pos)
    return start, len(text) if end == -1 else end


# ----------------------------------------------------------------------------- regions
def _region(section: Section | None, folded: str, lo: int, hi: int) -> tuple[int, int] | None:
    """Region of `section` inside [lo, hi), or None if its start is not found."""
    if section is None:
        return lo, hi
    m = _search(fold_diacritics(section.start), folded[lo:hi])
    if not m:
        return None
    a, b = lo + m.end(), hi
    if section.end:
        e = _search(fold_diacritics(section.end), folded[a:hi])
        if e:
            b = a + e.start()
    return a, b


def _items(field: FieldSpec, folded: str, lo: int, hi: int) -> list[tuple[int, int]]:
    if not folded[lo:hi].strip():
        return []
    if not field.item_split:
        return [(lo, hi)]
    starts = sorted({lo + m.start() for m in re.finditer(fold_diacritics(field.item_split), folded[lo:hi],
                                                         re.MULTILINE | re.IGNORECASE)})
    if not starts:
        return [(lo, hi)]
    return [(a, starts[i + 1] if i + 1 < len(starts) else hi) for i, a in enumerate(starts)]


# ----------------------------------------------------------------------------- walk
def _leaf(field: FieldSpec, cpath: str, lo: int, hi: int, ctx: _Ctx, out: list[Candidate]) -> None:
    first_only = field.scope in ("first_page", "head")
    text, text_f = (ctx.first, ctx.first_f) if first_only else (ctx.full, ctx.full_f)
    hi = min(hi, len(text))
    lo = min(lo, hi)  # `first` is a prefix of `full`, so offsets are shared
    sub, sub_f = text[lo:hi], text_f[lo:hi]

    for pattern in field.patterns:
        m = _search(pattern, sub, field.case_sensitive)
        if not m:
            continue
        value = m.group("value") if "value" in m.groupdict() else (m.group(1) if m.groups() else m.group(0))
        ev = _evidence(ctx, text, lo + m.start(), lo + m.end())
        out.append(Candidate(cpath, value.strip(), "rules:pattern", CONF_PATTERN, ev))
        break

    if field.labels and ctx.all_re is not None:
        hit = _from_labels(field, sub, sub_f, ctx.all_re)
        if hit:
            value, a, b = hit
            out.append(Candidate(cpath, value, "rules:label", CONF_LABEL, _evidence(ctx, text, lo + a, lo + b)))


def _walk(field: FieldSpec, cpath: str, lo: int, hi: int, ctx: _Ctx, out: list[Candidate]) -> None:
    if field.type == "object":
        reg = _region(field.section, ctx.full_f, lo, hi)
        if reg is None:
            return
        for k, child in (field.properties or {}).items():
            _walk(child, join(cpath, k), *reg, ctx, out)
    elif field.type == "array":
        reg = _region(field.section, ctx.full_f, lo, hi)
        if reg is None:
            return
        for i, (a, b) in enumerate(_items(field, ctx.full_f, *reg)):
            _walk(field.items, f"{cpath}[{i}]", a, b, ctx, out) # type: ignore
    else:
        _leaf(field, cpath, lo, hi, ctx, out)


def extract_rules(doc: Document, spec: DocTypeSpec) -> list[Candidate]:
    full = normalize_for_matching("\n\n".join(p.text for p in doc.pages))
    first = normalize_for_matching(doc.first_page_text)
    all_re = _compile_labels([lab for f in spec.leaf_specs().values() for lab in f.labels])
    ctx = _Ctx(doc, full, first, fold_diacritics(full), fold_diacritics(first), all_re)

    out: list[Candidate] = []
    for key, field in spec.output.items():
        _walk(field, key, 0, len(full), ctx, out)
    return out