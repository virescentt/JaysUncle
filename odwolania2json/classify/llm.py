"""LLM zero-shot classification — fallback for documents the rules are unsure about.

The list of classes and their descriptions comes from the YAML configs, so a new class is
automatically known to the LLM classifier as well.

TODO(classify): few-shot examples, test on OCR-ed scans, calibrate the 0.6 threshold in classify/__init__.py.
"""

from __future__ import annotations

import logging

from odwolania2json.models import ClassificationResult, Document
from odwolania2json.spec import Registry

log = logging.getLogger(__name__)


def classify_llm(
    doc: Document, registry: Registry, llm, head_chars: int = 3000
) -> ClassificationResult | None:
    classes = "\n".join(
        f'- "{dt.id}": {dt.name}. {(dt.classification.description or dt.description).strip()}'
        for dt in registry.doc_types.values()
    )
    system = registry.prompt("classify_system.md").format(classes=classes)
    try:
        answer = llm.complete_json(system=system, user=doc.text[:head_chars])
    except Exception as e:  # noqa: BLE001
        log.warning("LLM classification failed: %s", e)
        return None
    doc_type = answer.get("doc_type")
    if doc_type not in registry.doc_types:
        doc_type = None
    return ClassificationResult(
        doc_type=doc_type,
        confidence=float(answer.get("confidence") or 0.0),
        method="llm",
        reason=str(answer.get("reason", ""))[:300],
    )
