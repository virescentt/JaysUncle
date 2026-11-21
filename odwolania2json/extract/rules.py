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
_UNITS = {"pln", "zl", "eur", "usd", "gbp", "chf"}


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


def _glue_units(tokens: list[str]) -> list[str]:
    out: list[str] = []
    for t in tokens:
        if out and fold_diacritics(t).lower().rstrip(".") in _UNITS:
            out[-1] += " " + t
        else:
            out.append(t)
    return out


def _from_labels(field: FieldSpec, text: str, folded: str, all_re: re.Pattern) -> tuple[str, int, int] | None:
    """Value for `field` located via its labels → (value, start, end) in `text`. See layouts in the docstring of
    the previous version: "Label: value", "Label" + next line(s), header row with several labels."""
    own = _compile_labels(field.labels, field.case_sensitive)
    if own is None:
        return None
    multiline = "collapse_whitespace" in field.normalize

    for m in own.finditer(folded):
        ls, le = _line_bounds(folded, m.start())
        before, after = folded[ls : m.start()], folded[m.end() : le]
        if before.strip() and (len(before.split()) > 3 or re.search(r"[\d:;,]", before)):
            continue
        stripped = after.lstrip()
        if stripped.startswith(":"):
            vs = m.end() + (len(after) - len(stripped)) + 1
            value = text[vs:le].strip()
            if value:
                return value, vs, le
        elif all_re.sub("", after).strip(" \t:"):
            continue

        collected: list[tuple[int, int]] = []
        pos, limit = le + 1, (MAX_VALUE_LINES if multiline else 1)
        while pos < len(folded) and len(collected) < limit:
            e = folded.find("\n", pos)
            e = len(folded) if e == -1 else e
            line = folded[pos:e]
            if line.strip():
                if all_re.search(line):
                    break
                collected.append((pos, e))
            elif collected:
                break
            pos = e + 1
        if not collected:
            continue

        row = list(all_re.finditer(folded, ls, le))
        if len(row) > 1:
            s, e = collected[0]
            tokens = _glue_units(text[s:e].split())
            idx = next((i for i, h in enumerate(row) if h.start() == m.start()), None)
            if idx is None or len(tokens) != len(row):
                continue
            value = tokens[idx]
            vs = text.find(value, s, e)
            vs = s if vs == -1 else vs
            return value, vs, vs + len(value)

        vs, ve = collected[0][0], collected[-1][1]
        value = text[vs:ve].strip()
        if value:
            return value, vs, ve
    return None


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
            _walk(field.items, f"{cpath}[{i}]", a, b, ctx, out)
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