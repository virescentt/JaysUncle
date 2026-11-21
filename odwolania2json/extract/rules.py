"""Rule-based extractor driven entirely by the YAML hints of each field.

IMPLEMENTED: `patterns` (leaves outside arrays, with `scope`) and `labels`.
TODO(rules): 2. `section`  3. arrays + `item_split`  4. named heuristics.
"""

from __future__ import annotations

import re

from odwolania2json.models import Candidate, Document, Evidence
from odwolania2json.normalize.text import fold_diacritics, normalize_for_matching
from odwolania2json.spec import DocTypeSpec, FieldSpec

CONF_PATTERN = 0.8
CONF_LABEL = 0.7
MAX_VALUE_LINES = 5  # multi-line value: at most this many lines after the label
_UNITS = {"pln", "zl", "eur", "usd", "gbp", "chf"}  # glued to the previous token in column rows


def _page_of(doc: Document, text: str, pos: int) -> int | None:
    """Map a char offset in '\\n\\n'.join(page texts) back to a page number."""
    offset = 0
    for p in doc.pages:
        end = offset + len(normalize_for_matching(p.text))
        if pos <= end:
            return p.number
        offset = end + 2
    return None


def _search(pattern: str, text: str, case_sensitive: bool = False) -> re.Match | None:
    return re.search(pattern, text, re.MULTILINE | (0 if case_sensitive else re.IGNORECASE))


def _evidence(doc: Document, full: str, first: str, text: str, a: int, b: int) -> Evidence:
    page = 1 if text is first else _page_of(doc, full, a)
    return Evidence(page, text[max(0, a - 40) : b + 40])


# ----------------------------------------------------------------------------- labels
def _compile_labels(labels: list[str], case_sensitive: bool = False) -> re.Pattern | None:
    """One regex for a set of labels. Matched on diacritic-folded text (OCR loses Polish letters);
    folding is 1:1 per character, so offsets are identical in folded and original text."""
    parts = sorted({lab.strip() for lab in labels if lab and lab.strip()}, key=len, reverse=True)
    if not parts:
        return None
    alts = ["[ \t]+".join(re.escape(w) for w in fold_diacritics(p).split()) for p in parts]
    flags = 0 if case_sensitive else re.IGNORECASE
    return re.compile(r"(?<!\w)(?:" + "|".join(alts) + r")(?!\w)", flags)


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
    """Value for `field` located via its labels. Returns (value, start, end) in `text` or None.

    Supported layouts (all labels of the whole document class are known → `all_re`):
      1. "Label: value"                       — value after the colon, same line
      2. "Label" (end of line) + next line    — the label may follow a short heading: "Płatnik Rachunek obciążany"
      3. header row with several labels       — "Kwota operacji Data transakcji" / "200,00 PLN 2026-03-21":
                                                value line is split into columns (only if the counts match)
    Multi-line values (until the next label / blank line) only for fields normalized with `collapse_whitespace`.
    """
    own = _compile_labels(field.labels, field.case_sensitive)
    if own is None:
        return None
    multiline = "collapse_whitespace" in field.normalize

    for m in own.finditer(folded):
        ls, le = _line_bounds(folded, m.start())
        before, after = folded[ls : m.start()], folded[m.end() : le]
        # the label must stand at the start of the line or after a short heading (no digits / punctuation)
        if before.strip() and (len(before.split()) > 3 or re.search(r"[\d:;,]", before)):
            continue
        stripped = after.lstrip()
        if stripped.startswith(":"):
            vs = m.end() + (len(after) - len(stripped)) + 1
            value = text[vs:le].strip()
            if value:
                return value, vs, le
        elif all_re.sub("", after).strip(" \t:"):
            continue  # text follows the label without a colon → it's prose, not a label

        # value on the following line(s)
        collected: list[tuple[int, int]] = []
        pos, limit = le + 1, (MAX_VALUE_LINES if multiline else 1)
        while pos < len(folded) and len(collected) < limit:
            e = folded.find("\n", pos)
            e = len(folded) if e == -1 else e
            line = folded[pos:e]
            if line.strip():
                if all_re.search(line):
                    break  # next label reached
                collected.append((pos, e))
            elif collected:
                break  # blank line ends a multi-line value
            pos = e + 1
        if not collected:
            continue

        row = list(all_re.finditer(folded, ls, le))
        if len(row) > 1:  # header row with several columns
            s, e = collected[0]
            tokens = _glue_units(text[s:e].split())
            idx = next((i for i, h in enumerate(row) if h.start() == m.start()), None)
            if idx is None or len(tokens) != len(row):
                continue  # ambiguous columns → better no candidate than a wrong one
            value = tokens[idx]
            vs = text.find(value, s, e)
            vs = s if vs == -1 else vs
            return value, vs, vs + len(value)

        vs, ve = collected[0][0], collected[-1][1]
        value = text[vs:ve].strip()
        if value:
            return value, vs, ve
    return None


# ----------------------------------------------------------------------------- main
def extract_rules(doc: Document, spec: DocTypeSpec) -> list[Candidate]:
    full = normalize_for_matching("\n\n".join(p.text for p in doc.pages))
    first = normalize_for_matching(doc.first_page_text)
    full_f, first_f = fold_diacritics(full), fold_diacritics(first)
    leaves = spec.leaf_specs()
    all_re = _compile_labels([lab for f in leaves.values() for lab in f.labels])

    out: list[Candidate] = []
    for path, field in leaves.items():
        if "[]" in path:
            continue  # arrays → TODO 3
        text, text_f = (first, first_f) if field.scope in ("first_page", "head") else (full, full_f)

        for pattern in field.patterns:
            m = _search(pattern, text, field.case_sensitive)
            if not m:
                continue
            value = m.group("value") if "value" in m.groupdict() else (m.group(1) if m.groups() else m.group(0))
            ev = _evidence(doc, full, first, text, m.start(), m.end())
            out.append(Candidate(path, value.strip(), "rules:pattern", CONF_PATTERN, ev))
            break

        if field.labels and all_re is not None:
            hit = _from_labels(field, text, text_f, all_re)
            if hit:
                value, a, b = hit
                ev = _evidence(doc, full, first, text, a, b)
                out.append(Candidate(path, value, "rules:label", CONF_LABEL, ev))
    return out