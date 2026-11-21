"""Merge candidates → final field values (+ the reasons, for transparency).

For every leaf field:
  1. normalize each candidate (normalizers from YAML),
  2. adjust confidence:  grounded in text (+) / not found (−),  validators pass (+) / fail (−),
     several methods agree on the same value (+),
  3. pick the best candidate (confidence, then settings.method_priority),
  4. confidence < settings.min_confidence → null  (a wrong value costs FP+FN, a null only FN),
  5. derived fields (e.g. czynnosc_opis from czynnosc_powod_id via dictionary).

TODO(merge): OCR-confusion repair for identifiers failing checksums; cross-document consistency checks
within a case (verification only — never fill a field from another document).
"""

from __future__ import annotations

import re
from collections import defaultdict

from rapidfuzz import fuzz

from odwolania2json.models import Candidate, Document, Evidence, FieldResult
from odwolania2json.normalize import apply_normalizers
from odwolania2json.paths import flatten, generic, get, set_value
from odwolania2json.settings import ExtractionSettings
from odwolania2json.spec import DocTypeSpec, FieldSpec, Registry
from odwolania2json.validate import run_validators

BONUS_GROUNDED, PENALTY_UNGROUNDED = 0.1, 0.35
BONUS_VALID, PENALTY_INVALID = 0.05, 0.15
BONUS_AGREE = 0.15


def locate(value, mode: str, doc: Document) -> Evidence | None:
    """Find the value in the document text. Returns Evidence or None (not found)."""
    s = str(value).strip()
    if not s:
        return None
    for page in doc.pages:
        text = page.text
        span = None
        if mode == "digits":
            digits = re.sub(r"\D", "", s)
            if len(digits) >= 4:
                m = re.search(r"\D{0,5}".join(digits), text)
                span = m.span() if m else None
        elif mode == "exact":
            m = re.search(re.escape(s), text, re.IGNORECASE)
            span = m.span() if m else None
        elif mode == "fuzzy":
            al = fuzz.partial_ratio_alignment(s.lower(), text.lower())
            if al is not None and al.score >= 90:
                span = (al.dest_start, al.dest_end)
        if span:
            a, b = span
            return Evidence(page=page.number, snippet=text[max(0, a - 40) : b + 40])
    return None


def _score(c: Candidate, spec: FieldSpec, doc: Document, issues: list[str]) -> float:
    conf = c.confidence
    mode = spec.grounding
    if mode != "none":
        ev = c.evidence if c.method.startswith("rules") else None
        ev = ev or locate(c.value, mode, doc)
        if ev:
            c.evidence = c.evidence or ev
            conf += BONUS_GROUNDED
        else:
            conf -= PENALTY_UNGROUNDED
            issues.append(f"{c.method}: value not found in text")
    checks = run_validators(spec.validators, c.value)
    if any(v is False for v in checks.values()):
        conf -= PENALTY_INVALID
        issues.append(f"{c.method}: validator failed {[k for k, v in checks.items() if v is False]}")
    elif checks and all(v is True for v in checks.values()):
        conf += BONUS_VALID
    return max(0.0, min(1.0, conf))


def merge(
    doc: Document,
    spec: DocTypeSpec,
    registry: Registry,
    candidates: list[Candidate],
    settings: ExtractionSettings,
) -> tuple[dict, list[FieldResult]]:
    leaf_specs = spec.leaf_specs()
    by_path: dict[str, list[Candidate]] = defaultdict(list)
    for c in candidates:
        if generic(c.path) in leaf_specs:
            by_path[c.path].append(c)

    chosen: dict[str, FieldResult] = {}
    for path, cands in by_path.items():
        fspec = leaf_specs[generic(path)]
        issues: list[str] = []
        scored: list[tuple[float, int, Candidate, object]] = []
        for c in cands:
            raw = c.value
            c.value, n_issues = apply_normalizers(fspec.normalize, c.value)
            issues += n_issues
            if c.value is None:
                continue
            conf = _score(c, fspec, doc, issues)
            method_rank = _rank(c.method, settings.method_priority)
            scored.append((conf, -method_rank, c, raw))
        if not scored:
            chosen[path] = FieldResult(path, None, candidates=cands, issues=issues)
            continue
        # agreement bonus: several distinct methods produced the same normalized value
        values = defaultdict(set)
        for _, _, c, _ in scored:
            values[repr(c.value)].add(c.method.split(":")[0])
        scored = [
            (min(1.0, conf + (BONUS_AGREE if len(values[repr(c.value)]) > 1 else 0.0)), r, c, raw)
            for conf, r, c, raw in scored
        ]
        conf, _, best, raw = max(scored, key=lambda t: (t[0], t[1]))
        keep = conf >= settings.min_confidence
        if not keep:
            issues.append(f"best confidence {conf:.2f} < {settings.min_confidence} → null")
        chosen[path] = FieldResult(
            path=path,
            value=best.value if keep else None,
            raw_value=raw,
            method=best.method,
            confidence=round(conf, 3),
            evidence=best.evidence,
            candidates=cands,
            issues=issues,
        )

    values = {p: fr.value for p, fr in chosen.items() if fr.value is not None}
    data = spec.build_output(values)

    # derived fields + full list of leaves (including nulls) for the trace
    results: list[FieldResult] = []
    for path in flatten(data):
        fspec = leaf_specs.get(generic(path))
        if fspec is not None and fspec.derived_from:
            parent = path.rsplit(".", 1)[0] if "." in path else ""
            src_path = f"{parent}.{fspec.derived_from}" if parent else fspec.derived_from
            src_val = get(data, src_path)
            # a missing dictionary is a config error (Registry.check reports it): null, not a lost document
            dictionary = registry.dictionaries.get(fspec.dictionary) if fspec.dictionary else None
            entry = dictionary.get(src_val) if dictionary is not None else None
            val = getattr(entry, fspec.derived_attr, None) if entry else None
            set_value(data, path, val)
            src_conf = chosen[src_path].confidence if src_path in chosen else 0.0
            results.append(FieldResult(path, val, src_val, "derived", src_conf))
            continue
        results.append(chosen.get(path) or FieldResult(path, None))
    return data, results


def _rank(method: str, priority: list[str]) -> int:
    base = method.split(":")[0]
    return priority.index(base) if base in priority else len(priority)
