"""Rule-based extractor driven entirely by the YAML hints of each field.

IMPLEMENTED (minimal example): `patterns` for leaves that are NOT inside arrays, with `scope`.

TODO(rules) — to implement, in this order (measure F1 after each step with `python -m evaluation.run_eval`):
  1. `labels`: value after "Label:" on the same line, or on the next non-empty line
     (bank confirmations: "Rachunek odbiorcy\\nPL68 4382 …"). Case-insensitive, label at line start.
  2. `section` on object/array fields: restrict children's search to text between section.start and section.end
     (e.g. Odwołujący block vs Zamawiający block — both have KRS/NIP/REGON!).
  3. arrays: `item_split` splits the section into items → children extracted per item → paths "X[0].…", "X[1].…".
  4. heuristics that are hard to express as a single regex, as small named functions referenced from YAML
     (e.g. `nazwa` = first non-empty line of the section). Keep them generic, not tied to one class.
Every candidate should carry Evidence(page, snippet) — the UI shows it.
"""

from __future__ import annotations

import re

from odwolania2json.models import Candidate, Document, Evidence
from odwolania2json.normalize.text import normalize_for_matching
from odwolania2json.spec import DocTypeSpec

CONF_PATTERN = 0.8
CONF_LABEL = 0.7


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


def extract_rules(doc: Document, spec: DocTypeSpec) -> list[Candidate]:
    full = normalize_for_matching("\n\n".join(p.text for p in doc.pages))
    first = normalize_for_matching(doc.first_page_text)
    out: list[Candidate] = []
    for path, field in spec.leaf_specs().items():
        if "[]" in path or not field.patterns:
            continue  # arrays / labels / sections → TODO above
        text = first if field.scope in ("first_page", "head") else full
        for pattern in field.patterns:
            m = _search(pattern, text, field.case_sensitive)
            if not m:
                continue
            value = (
                m.group("value") if "value" in m.groupdict() else (m.group(1) if m.groups() else m.group(0))
            )
            snippet = text[max(0, m.start() - 40) : m.end() + 40]
            page = 1 if text is first else _page_of(doc, full, m.start())
            out.append(Candidate(path, value.strip(), "rules:pattern", CONF_PATTERN, Evidence(page, snippet)))
            break
    return out
