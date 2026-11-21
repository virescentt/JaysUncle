"""DocumentResult → JSON payload of the web API (document list, field table with highlight boxes, page sizes)."""

from __future__ import annotations

import logging
from dataclasses import asdict, replace
from pathlib import Path
from typing import Any

from fastapi.encoders import jsonable_encoder

from odwolania2json.api.highlight import field_boxes, index_document
from odwolania2json.models import DocumentResult
from odwolania2json.paths import generic
from odwolania2json.spec import Registry

log = logging.getLogger(__name__)

MAX_CANDIDATES_SHOWN = 8


def _candidate_detail(c: Any) -> dict[str, Any]:
    ev = getattr(c, "evidence", None)
    return {
        "value": c.value,
        "method": c.method,
        "confidence": round(float(c.confidence or 0.0), 3),
        "page": getattr(ev, "page", None),
        "note": getattr(c, "note", ""),
    }


def build_payload(
    res: DocumentResult,
    *,
    doc_id: str,
    filename: str,
    registry: Registry,
    json_text: str | None,
    page_count: int = 0,
    test_doc_type: str | None = None,
) -> dict[str, Any]:
    """Everything the panel shows for one document. Keys of the original /api/process contract are kept."""
    c = res.classification
    spec = registry.doc_types.get(c.doc_type) if c.doc_type else None
    leaf_specs = spec.leaf_specs() if spec else {}
    pages_index = index_document(res.document)

    fields = []
    for fr in res.fields:
        item = asdict(replace(fr, candidates=[]))
        item["candidates"] = len(fr.candidates)
        item["candidates_detail"] = [_candidate_detail(cand) for cand in fr.candidates[:MAX_CANDIDATES_SHOWN]]
        fspec = leaf_specs.get(generic(fr.path))
        item["description"] = fspec.description.strip() if fspec else ""
        item["derived_from"] = fspec.derived_from if fspec else None  # panel shows it next to its source field
        try:
            item["boxes"] = field_boxes(fr, pages_index)
        except Exception:  # noqa: BLE001 — a highlighting problem must never hide the extracted data
            log.exception("highlight failed for %s / %s", filename, fr.path)
            item["boxes"] = []
        fields.append(item)

    page_sizes = []
    if res.document is not None:
        for p in res.document.pages:
            page_sizes.append(
                {
                    "number": p.number,
                    "width": round(float(p.width or 0.0), 2),
                    "height": round(float(p.height or 0.0), 2),
                    "source": p.source,
                    "ocr_engine": p.ocr_engine,
                    "ocr_confidence": getattr(p, "ocr_confidence", None),
                    "words": len(p.words),
                }
            )

    payload = {
        "id": doc_id,
        "filename": filename,
        "json_filename": Path(filename).stem + ".json",
        "doc_type": c.doc_type,
        "doc_type_name": spec.name if spec else None,
        "confidence": c.confidence,
        "method": c.method,
        "scores": c.scores,
        "reason": c.reason,
        "data": res.data,
        "json_text": json_text,
        "fields": fields,
        "pages": res.pages,
        "page_count": max(page_count, res.pages),
        "page_sizes": page_sizes,
        "scanned": res.scanned,
        "errors": res.errors,
        "timings": {k: round(v, 3) for k, v in res.timings.items()},
        "total_time": round(sum(res.timings.values()), 3),
        "test_doc_type": test_doc_type,
        "head_lines": _head_lines(res.document),
    }
    return jsonable_encoder(payload)


HEAD_LINES = 25


def _head_lines(document) -> list[str]:
    """First non-empty text lines of the document (panel: pick recognition phrases for a new document type)."""
    out: list[str] = []
    for page in document.pages if document is not None else []:
        for line in (page.text or "").splitlines():
            line = " ".join(line.split())
            if len(line) >= 3:
                out.append(line[:200])
                if len(out) >= HEAD_LINES:
                    return out
    return out
