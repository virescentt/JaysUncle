"""Document classification: rules (signals from YAML) → optional LLM fallback when uncertain."""

from __future__ import annotations

from odwolania2json.classify.llm import classify_llm
from odwolania2json.classify.rules import classify_rules
from odwolania2json.models import ClassificationResult, Document
from odwolania2json.settings import Settings
from odwolania2json.spec import Registry


def classify(doc: Document, registry: Registry, settings: Settings, llm=None) -> ClassificationResult:
    result = classify_rules(doc, registry, settings.classification)
    # The LLM is asked only when the rules hesitate BETWEEN classes (a winner with a small margin).
    # A document that reaches no class's min_score stays a negative: asking the LLM about every negative
    # (KIO rulings, TED notices…) would risk a whole JSON of false positives.
    uncertain = result.doc_type is not None and result.confidence < 0.6
    if uncertain and llm is not None and settings.classification.use_llm_fallback:
        llm_result = classify_llm(doc, registry, llm)
        if llm_result is not None:
            llm_result.scores = result.scores  # keep rule scores in the trace
            return llm_result
    return result
