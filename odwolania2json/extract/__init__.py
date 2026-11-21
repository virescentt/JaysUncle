"""Extraction: produce candidate values for every field of a document class.

mode = rules  → only extract/rules.py
mode = llm    → only extract/llm.py
mode = hybrid → both; extract/merge.py picks the best candidate per field and verifies it.
"""

from __future__ import annotations

import logging

from odwolania2json.extract.llm import extract_llm
from odwolania2json.extract.rules import extract_rules
from odwolania2json.models import Candidate, Document
from odwolania2json.settings import Settings
from odwolania2json.spec import DocTypeSpec, Registry

log = logging.getLogger(__name__)


def extract_candidates(
    doc: Document, spec: DocTypeSpec, registry: Registry, settings: Settings, llm=None
) -> tuple[list[Candidate], list[str]]:
    """Returns (candidates, errors)."""
    mode = settings.extraction.mode
    candidates: list[Candidate] = []
    errors: list[str] = []
    if mode in ("rules", "hybrid"):
        candidates += extract_rules(doc, spec, registry)
    if mode in ("llm", "hybrid"):
        if llm is None:
            if mode == "llm":
                errors.append("extraction mode 'llm' but no LLM configured (LLM_BASE_URL / LLM_MODEL)")
        else:
            try:
                candidates += extract_llm(doc, spec, registry, llm)
            except Exception as e:  # noqa: BLE001 — LLM failure must not kill the document
                log.warning("LLM extraction failed for %s: %s", doc.path.name, e)
                errors.append(f"llm: {e}")
    return candidates, errors
