"""LLM extractor: JSON Schema + field descriptions + class instructions — all generated from the YAML config.

TODO(llm):
  * few-shot examples (ExtractionSpec.few_shot) — 1–2 short (text → JSON) pairs from the training set,
  * ask for a source quote per field (evidence) and verify it in merge.py,
  * per-field confidence from the model (or self-consistency: 2 runs, agreement → higher confidence),
  * long documents: smarter context selection than first pages + keyword windows,
  * prompt tuning on evaluation results (keep prompts in config/prompts — they are configuration, not code).
"""

from __future__ import annotations

import re

from odwolania2json.models import Candidate, Document
from odwolania2json.paths import flatten
from odwolania2json.spec import DocTypeSpec, Registry

CONF_LLM = 0.7
WINDOW = 600  # chars around a keyword hit taken from pages beyond max_pages


def build_context(doc: Document, spec: DocTypeSpec) -> str:
    n = spec.extraction.max_pages
    context = doc.text_with_page_markers(n)
    if n is not None and len(doc.pages) > n and spec.extraction.keyword_windows:
        snippets: list[str] = []
        for page in doc.pages[n:]:
            for kw in spec.extraction.keyword_windows:
                for m in re.finditer(kw, page.text, re.IGNORECASE):
                    a, b = max(0, m.start() - WINDOW // 2), m.end() + WINDOW // 2
                    snippets.append(f"[Strona {page.number}, fragment]\n…{page.text[a:b]}…")
        if snippets:
            context += "\n\n" + "\n\n".join(dict.fromkeys(snippets))  # dedupe, keep order
    return context


def build_system_prompt(spec: DocTypeSpec, registry: Registry) -> str:
    used = {f.dictionary for f in spec.leaf_specs().values() if f.dictionary}
    dicts = []
    for name in sorted(used):
        d = registry.dictionaries.get(name)
        if d:
            lines = [f"  {e.id} = {e.opis}" + (f" — {e.hint}" if e.hint else "") for e in d.entries]
            dicts.append(f"{d.id} ({d.description}):\n" + "\n".join(lines))
    return registry.prompt("extract_system.md").format(
        instructions=spec.extraction.instructions.strip() or "-",
        dictionaries="\n\n".join(dicts) or "-",
    )


def extract_llm(doc: Document, spec: DocTypeSpec, registry: Registry, llm) -> list[Candidate]:
    schema = spec.json_schema(registry.dictionaries, for_llm=True)
    user = f"Typ dokumentu: {spec.name}\n\nTreść dokumentu:\n{build_context(doc, spec)}"
    data = llm.complete_json(
        system=build_system_prompt(spec, registry), user=user, schema=schema, name=spec.id
    )
    return [
        Candidate(path=path, value=value, method="llm", confidence=CONF_LLM)
        for path, value in flatten(data).items()
        if value not in (None, "")
    ]
