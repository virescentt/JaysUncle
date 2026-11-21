"""Document classification: rules (signals from YAML) → optional LLM fallback when uncertain."""

from __future__ import annotations

from odwolania2json.classify.llm import classify_llm
from odwolania2json.classify.rules import classify_rules
from odwolania2json.models import ClassificationResult, Document
from odwolania2json.settings import Settings
from odwolania2json.spec import Registry


def classify(doc: Document, registry: Registry, settings: Settings, llm=None) -> ClassificationResult:
    result = classify_rules(doc, registry, settings.classification)
    uncertain = result.doc_type is None or result.confidence < 0.6
    if uncertain and llm is not None and settings.classification.use_llm_fallback:
        llm_result = classify_llm(doc, registry, llm)
        if llm_result is not None:
            llm_result.scores = result.scores  # keep rule scores in the trace
            return llm_result
    return result
