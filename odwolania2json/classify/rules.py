"""Rule-based classification: weighted regex signals defined per class in YAML.

score(class) = Σ weight of matched signals − Σ weight of matched negative signals  (each signal counted once)
winner       = best class if score ≥ class.min_score, else None (→ "unknown", no JSON is written)
confidence   = high if the winner beats the runner-up by ≥ settings.min_margin, lower otherwise.
Text and patterns are diacritics-folded (ł→l, ą→a, …) so OCR output without Polish letters still matches.
"""

from __future__ import annotations

import re

from odwolania2json.models import ClassificationResult, Document
from odwolania2json.normalize.text import fold_diacritics, normalize_for_matching
from odwolania2json.settings import ClassificationSettings
from odwolania2json.spec import Registry, Signal


def _hit(sig: Signal, full: str, head: str) -> bool:
    flags = re.MULTILINE | (0 if sig.case_sensitive else re.IGNORECASE)
    return re.search(fold_diacritics(sig.pattern), head if sig.scope == "head" else full, flags) is not None


def score_document(doc: Document, registry: Registry, settings: ClassificationSettings) -> dict[str, float]:
    full = fold_diacritics(normalize_for_matching(doc.text))
    head = full[: settings.head_chars]
    scores: dict[str, float] = {}
    for dt in registry.doc_types.values():
        s = sum(sig.weight for sig in dt.classification.signals if _hit(sig, full, head))
        s -= sum(sig.weight for sig in dt.classification.negative_signals if _hit(sig, full, head))
        scores[dt.id] = s
    return scores


def classify_rules(
    doc: Document, registry: Registry, settings: ClassificationSettings
) -> ClassificationResult:
    if not doc.text.strip():
        return ClassificationResult(None, 0.0, "rules", {}, "empty document (no text, no OCR)")
    scores = score_document(doc, registry, settings)
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    best_id, best = ranked[0]
    second = ranked[1][1] if len(ranked) > 1 else 0.0
    if best < registry.doc_types[best_id].classification.min_score:
        return ClassificationResult(None, 0.0, "rules", scores, f"best={best_id} score {best} < min_score")
    margin = best - second
    confidence = 0.95 if margin >= settings.min_margin else 0.5 + 0.2 * max(margin, 0) / settings.min_margin
    return ClassificationResult(best_id, round(confidence, 3), "rules", scores, f"margin {margin:.1f}")
