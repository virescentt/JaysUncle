"""HTTP API for the web UI (FastAPI). Start: `python -m odwolania2json web` → http://localhost:8000

Endpoints (contract for the frontend — web/):
  GET  /api/health      → {"status", "mode", "llm_model", "doc_types": [...]}
  GET  /api/doc-types   → [{"id", "name", "description", "fields": [generic paths]}]
  POST /api/process     → multipart "files" (1..n PDF) →
                          [{"filename", "doc_type", "confidence", "method", "scores", "reason",
                            "data" (canonical JSON | null), "fields" (trace: value, method, confidence,
                            evidence{page, snippet, bbox}, issues), "pages", "scanned", "errors", "timings"}]
"""

from __future__ import annotations

import shutil
import tempfile
from dataclasses import asdict
from functools import lru_cache
from pathlib import Path

from fastapi import FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

from odwolania2json import PROJECT_ROOT, __version__
from odwolania2json.pipeline import Pipeline

WEB_DIR = PROJECT_ROOT / "web"

app = FastAPI(title="Odwołania2JSON", version=__version__)


@lru_cache(maxsize=1)
def get_pipeline() -> Pipeline:
    return Pipeline()


@app.get("/api/health")
def health():
    pipe = get_pipeline()
    return {
        "status": "ok",
        "version": __version__,
        "mode": pipe.settings.extraction.mode,
        "llm_model": pipe.settings.llm.model if pipe.llm else None,
        "doc_types": list(pipe.registry.doc_types),
    }


@app.get("/api/doc-types")
def doc_types():
    reg = get_pipeline().registry
    return [
        {"id": dt.id, "name": dt.name, "description": dt.description, "fields": list(dt.leaf_specs())}
        for dt in reg.doc_types.values()
    ]


@app.post("/api/process")
async def process(files: list[UploadFile] = File(...)):
    pipe = get_pipeline()
    out = []
    with tempfile.TemporaryDirectory() as tmp:
        for f in files:
            name = Path(f.filename or "dokument.pdf").name
            if not name.lower().endswith(".pdf"):
                raise HTTPException(400, f"{name}: tylko pliki PDF")
            path = Path(tmp) / name
            with path.open("wb") as fh:
                shutil.copyfileobj(f.file, fh)
            res = pipe.process_file(path)
            c = res.classification
            out.append(
                {
                    "filename": name,
                    "doc_type": c.doc_type,
                    "confidence": c.confidence,
                    "method": c.method,
                    "scores": c.scores,
                    "reason": c.reason,
                    "data": res.data,
                    "fields": [asdict(fr) | {"candidates": len(fr.candidates)} for fr in res.fields],
                    "pages": res.pages,
                    "scanned": res.scanned,
                    "errors": res.errors,
                    "timings": res.timings,
                }
            )
    return out


@app.get("/")
def index():
    return FileResponse(WEB_DIR / "index.html")


app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")
