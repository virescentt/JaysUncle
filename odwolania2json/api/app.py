"""HTTP API + web panel (FastAPI). Start: `./start.sh web` (or `python -m odwolania2json web`) → http://localhost:8000

Everything runs locally (on-premise): the panel in web/ is plain HTML/CSS/JS without a build step or CDN.

Endpoints (contract for the frontend — web/):
  GET    /api/health                  → {"status", "version", "mode", "llm_model", "doc_types": [...], "error"?}
  GET    /api/doc-types[?all=true]    → [{"id", "name", "description", "fields": [generic paths], "file", "enabled",
                                          "signals"}]   (all=true: every YAML file, also disabled / broken ones)
  POST   /api/process                 → multipart "files" (1..n PDF) → [document payload]
  GET    /api/doc/{id}                → document payload (as returned by /api/process)
  GET    /api/doc/{id}/page/{n}.png   → page preview rendered with pypdfium2 (?dpi=110)
  GET    /api/doc/{id}/json           → the output file exactly as the CLI writes it (empty for unrecognised documents)
  GET    /api/doc/{id}/pdf            → the uploaded PDF
  DELETE /api/doc/{id}                → remove the document from the session store
  Class workshop (config/doc_types/*.yaml, no code changes needed for a new class):
  GET    /api/doc-types/template      → YAML skeleton of a new class
  GET    /api/doc-types/{id}/yaml     → raw YAML of a class
  POST   /api/doc-types/validate      → body {"yaml": "..."} (or raw YAML text) → {"ok", "id", "name", "errors",
                                          "warnings", "summary"};  errors/warnings: [{"message", "line"}]
  POST   /api/doc-types/test          → multipart "yaml" + "file" (1 PDF) → document payload + "test" — classified and
                                          extracted with a temporary registry (class added/replaced); nothing is saved
  PUT    /api/doc-types/{id}/yaml     → body like validate → validate, save <config_dir>/doc_types/<id>.yaml, reload
  Form editor (the same class as plain data; validate / test / save go through the YAML endpoints above):
  GET    /api/doc-types/meta          → {"field_types", "scopes", "grounds", "extraction_modes", "normalizers",
                                          "validators", "dictionaries", "id_pattern", "template" (data of a new class)}
  POST   /api/doc-types/parse         → body like validate → validate report + {"data" (JSON | null), "roundtrip_ok",
                                          "losses": [what saving from the form would drop: comments, anchors, …]}
  POST   /api/doc-types/dump          → body {"data": {...}} → {"yaml": "..."} (key order kept, unknown keys kept)

Document payload: {"id", "filename", "json_filename", "doc_type", "doc_type_name", "confidence", "method", "scores",
  "reason", "data" (canonical JSON | null), "json_text" (output file content), "fields": [{"path", "value", "raw_value",
  "method", "confidence", "evidence": {"page", "snippet", "bbox"}, "candidates" (count), "candidates_detail", "issues",
  "description", "boxes": [{"page", "bbox": [x0, top, x1, bottom], "kind": "value" | "snippet" | "bbox"}]}],
  "pages", "page_count", "page_sizes": [{"number", "width", "height", "source", "ocr_engine", "words"}], "scanned",
  "errors", "timings", "total_time", "test_doc_type"}.   Coordinates: PDF points, origin top-left (models.BBox).

Configuration: create_app(config_dir=..., mode=...) or env ODWOLANIA2JSON_CONFIG_DIR (default ./config);
env ODWOLANIA2JSON_WEB_READONLY=1 disables saving classes from the panel.

Limits: a request body may not exceed MAX_REQUEST_BYTES (checked from Content-Length before anything is read and
while streaming), one PDF MAX_UPLOAD_BYTES, a class YAML MAX_YAML_CHARS / workshop.MAX_YAML_NODES (alias bombs).
Threads: documents are processed on a small pool (store.PdfWorker, WEB_WORKERS); YAML validation and previews
run in the threadpool, so nothing heavy runs on the event loop. pdfium calls are serialized by PDFIUM_LOCK.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import threading
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.encoders import jsonable_encoder
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse, Response
from fastapi.staticfiles import StaticFiles

from odwolania2json import CONFIG_DIR, PROJECT_ROOT, __version__
from odwolania2json.api import classform, workshop
from odwolania2json.api.results import build_payload
from odwolania2json.api.store import (
    DEFAULT_DPI,
    MAX_DPI,
    MIN_DPI,
    DocStore,
    PdfWorker,
    StoredDoc,
    pdf_page_count,
    render_page_png,
    safe_filename,
)
from odwolania2json.output import write_result
from odwolania2json.pipeline import Pipeline
from odwolania2json.settings import Settings
from odwolania2json.spec import Registry

log = logging.getLogger(__name__)

WEB_DIR = PROJECT_ROOT / "web"
MAX_FILES_PER_REQUEST = 50
MAX_UPLOAD_BYTES = 64 * 1024 * 1024  # one PDF
MAX_REQUEST_BYTES = 256 * 1024 * 1024  # whole request body (several PDFs in one /api/process call)
MAX_YAML_BYTES = workshop.MAX_YAML_CHARS * 4  # UTF-8: at most 4 bytes per character
WEB_WORKERS = 2  # documents processed at the same time (the panel sends 2 requests in parallel)
_CHUNK = 1024 * 1024


class BodyLimitMiddleware:
    """Pure ASGI middleware: 413 for request bodies above `max_bytes`.

    Starlette spools a multipart upload to disk completely before the endpoint runs, so a size check in the
    endpoint comes too late — without this an arbitrarily large upload fills the temp disk. Requests with a
    Content-Length above the limit are refused before reading; chunked bodies are counted while streaming.
    """

    def __init__(self, app, max_bytes: int = MAX_REQUEST_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        length = dict(scope.get("headers") or []).get(b"content-length")
        # (> 15 digits: int() of a 5000-digit header would raise instead of answering 413)
        if length is not None and (not length.isdigit() or len(length) > 15 or int(length) > self.max_bytes):
            response = JSONResponse({"detail": _too_large_message(self.max_bytes)}, status_code=413)
            await response(scope, receive, send)
            return
        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message.get("type") == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:  # FastAPI re-raises HTTPException from body parsing → 413
                    raise HTTPException(413, _too_large_message(self.max_bytes))
            return message

        await self.app(scope, limited_receive, send)


def _too_large_message(limit: int) -> str:
    return f"Żądanie jest za duże (limit {limit // 2**20} MB)"


class AppState:
    """Pipeline (lazy, reloadable), session store and the pdfium worker of one app instance."""

    def __init__(self, config_dir: Path, mode: str | None, store: DocStore, readonly: bool = False):
        self.config_dir = Path(config_dir)
        self.mode = mode
        self.store = store
        self.readonly = readonly
        self.worker = PdfWorker(WEB_WORKERS)
        self._pipeline: Pipeline | None = None
        self._lock = threading.RLock()
        self.save_lock = threading.Lock()

    # ------------------------------------------------------------------ pipeline
    def _build(self) -> Pipeline:
        settings = Settings.load(self.config_dir)
        if self.mode:
            settings.extraction.mode = self.mode  # type: ignore[assignment]
        return Pipeline(settings)

    def pipeline(self) -> Pipeline:
        with self._lock:
            if self._pipeline is None:
                try:
                    self._pipeline = self._build()
                except Exception as e:  # noqa: BLE001 — e.g. a broken YAML in config/doc_types
                    log.exception("Cannot load the pipeline")
                    raise HTTPException(503, f"Nie można wczytać konfiguracji: {e}") from e
            return self._pipeline

    def reload(self) -> Pipeline:
        with self._lock:
            new = self._build()  # raises → the old pipeline stays active
            self._pipeline = new
            return new

    def derived_pipeline(self, registry: Registry) -> Pipeline:
        """Same settings and LLM client as the active pipeline, but another registry (class workshop test)."""
        base = self.pipeline()
        offline = base.settings.model_copy(deep=True)
        offline.extraction.mode = "rules"  # constructor without the LLM reachability check …
        pipe = Pipeline(settings=offline, registry=registry)
        pipe.settings, pipe.llm = base.settings, base.llm  # … then the real settings and client
        return pipe

    def dictionaries(self) -> dict:
        with self._lock:
            if self._pipeline is not None:
                return self._pipeline.registry.dictionaries
        return workshop.load_dictionaries(self.config_dir)

    # ------------------------------------------------------------------ documents
    def process_job(self, pipe: Pipeline, doc: StoredDoc, test_doc_type: str | None = None) -> dict[str, Any]:
        """Process one stored upload. Runs on the processing pool (store.PdfWorker), never on the event loop."""
        res = pipe.safe_process(doc.pdf_path)
        out = pipe.settings.output
        try:
            write_result(doc.output_path, res.data, out.unknown_output, out.indent)
        except Exception:  # noqa: BLE001
            log.exception("Cannot write the output file for %s", doc.filename)
        # bytes → str without newline translation: identical to the file also on Windows (CRLF)
        json_text = doc.output_path.read_bytes().decode("utf-8") if doc.output_path.exists() else None
        doc.page_count = pdf_page_count(doc.pdf_path)
        try:
            payload = build_payload(
                res,
                doc_id=doc.id,
                filename=doc.filename,
                registry=pipe.registry,
                json_text=json_text,
                page_count=doc.page_count,
                test_doc_type=test_doc_type,
            )
        except Exception as e:  # noqa: BLE001 — never hide the result itself because of the UI extras
            log.exception("Cannot build the UI payload for %s", doc.filename)
            payload = _minimal_payload(res, doc, json_text, test_doc_type, f"panel: {type(e).__name__}: {e}")
        for page in payload["page_sizes"]:  # previews ready before the UI asks for them
            n = page["number"]
            try:
                render_page_png(doc.pdf_path, n, DEFAULT_DPI, doc.page_png_path(n, DEFAULT_DPI))
            except Exception as e:  # noqa: BLE001
                log.warning("Cannot render page %s of %s: %s", n, doc.filename, e)
        doc.payload = payload
        return payload

    async def run_in_worker(self, fn, *args):
        return await asyncio.wrap_future(self.worker.submit(fn, *args))


def _minimal_payload(res, doc: StoredDoc, json_text: str | None, test_doc_type: str | None, error: str) -> dict:
    """Fallback payload (no field table / highlights) with the same keys as results.build_payload."""
    c = res.classification
    return jsonable_encoder(
        {
            "id": doc.id,
            "filename": doc.filename,
            "json_filename": Path(doc.filename).stem + ".json",
            "doc_type": c.doc_type,
            "doc_type_name": None,
            "confidence": c.confidence,
            "method": c.method,
            "scores": c.scores,
            "reason": c.reason,
            "data": res.data,
            "json_text": json_text,
            "fields": [],
            "pages": res.pages,
            "page_count": max(doc.page_count, res.pages),
            "page_sizes": [],
            "scanned": res.scanned,
            "errors": [*res.errors, error],
            "timings": res.timings,
            "total_time": round(sum(res.timings.values()), 3),
            "test_doc_type": test_doc_type,
        }
    )


async def _save_upload(upload: UploadFile, doc: StoredDoc) -> None:
    """Copy the upload into the store (size limit, must look like a PDF)."""
    size = 0
    head = b""
    with doc.pdf_path.open("wb") as fh:
        while chunk := await upload.read(_CHUNK):
            size += len(chunk)
            if size > MAX_UPLOAD_BYTES:
                raise HTTPException(413, f"{doc.filename}: plik większy niż {MAX_UPLOAD_BYTES // 2**20} MB")
            if len(head) < 1024:
                head += chunk[: 1024 - len(head)]
            fh.write(chunk)
    if b"%PDF-" not in head:
        raise HTTPException(400, f"{doc.filename}: to nie jest plik PDF")


def _check_pdf_name(upload: UploadFile) -> str:
    name = safe_filename(upload.filename)
    if not name.lower().endswith(".pdf"):
        raise HTTPException(400, f"{name}: tylko pliki PDF")
    return name


async def _yaml_from_request(request: Request) -> str:
    """YAML text from a JSON body {"yaml": "..."} or from a raw text body (read with a size limit)."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_YAML_BYTES + 1024:  # + room for the JSON wrapper
            raise HTTPException(413, "Za duży plik YAML")
        chunks.append(chunk)
    body = b"".join(chunks)
    if "application/json" in request.headers.get("content-type", ""):
        try:
            data = json.loads(body or b"{}")
        except json.JSONDecodeError as e:
            raise HTTPException(400, f"Niepoprawny JSON: {e}") from e
        text = data.get("yaml") if isinstance(data, dict) else None
        if not isinstance(text, str):
            raise HTTPException(400, 'Oczekiwano {"yaml": "<treść pliku YAML>"}')
        return text
    try:
        return body.decode("utf-8")
    except UnicodeDecodeError as e:
        raise HTTPException(400, "YAML musi być zakodowany w UTF-8") from e


async def _json_from_request(request: Request) -> Any:
    """JSON body read with the same size limit as a class YAML."""
    chunks: list[bytes] = []
    size = 0
    async for chunk in request.stream():
        size += len(chunk)
        if size > MAX_YAML_BYTES + 1024:
            raise HTTPException(413, "Za duże dane klasy")
        chunks.append(chunk)
    try:
        return json.loads(b"".join(chunks) or b"{}")
    except (json.JSONDecodeError, UnicodeDecodeError, RecursionError) as e:
        raise HTTPException(400, f"Niepoprawny JSON: {e}") from e


def _guard_id(doc_type_id: str) -> str:
    if not workshop.valid_class_id(doc_type_id):
        raise HTTPException(400, "Nieprawidłowy identyfikator klasy (dozwolone: a-z, 0-9, _; maks. 64 znaki)")
    return doc_type_id


def create_app(
    config_dir: Path | str | None = None,
    *,
    mode: str | None = None,
    store_dir: Path | str | None = None,
    readonly: bool | None = None,
    web_dir: Path = WEB_DIR,
) -> FastAPI:
    """App factory. `config_dir` (default: env ODWOLANIA2JSON_CONFIG_DIR or ./config) is where classes are read from
    and saved to; `mode` overrides the extraction mode; `store_dir` holds uploads (default: a temp dir)."""
    config_dir = Path(config_dir or os.environ.get("ODWOLANIA2JSON_CONFIG_DIR") or CONFIG_DIR)
    if readonly is None:
        readonly = os.environ.get("ODWOLANIA2JSON_WEB_READONLY", "").lower() in ("1", "true", "yes")
    state = AppState(config_dir, mode, DocStore(Path(store_dir) if store_dir else None), readonly=readonly)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        state.worker.shutdown()
        state.store.close()

    # Swagger UI / ReDoc load their assets from a CDN → disabled (on-premise); the schema stays available.
    app = FastAPI(
        title="Odwołania2JSON",
        version=__version__,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.o2j = state
    app.add_middleware(BodyLimitMiddleware, max_bytes=MAX_REQUEST_BYTES)

    @app.middleware("http")
    async def no_cache_for_ui(request: Request, call_next):
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")  # uploads are served back (PDF, PNG)
        if request.url.path == "/" or request.url.path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # ------------------------------------------------------------------ status & classes
    @app.get("/api/health")
    def health():
        try:
            pipe = state.pipeline()
        except HTTPException as e:
            return {"status": "error", "version": __version__, "error": e.detail, "mode": None, "llm_model": None,
                    "doc_types": []}  # fmt: skip
        return {
            "status": "ok",
            "version": __version__,
            "mode": pipe.settings.extraction.mode,
            "llm_model": pipe.settings.llm.model if pipe.llm else None,
            "doc_types": list(pipe.registry.doc_types),
            "doc_type_names": {k: v.name for k, v in pipe.registry.doc_types.items()},
            "ocr_engine": pipe.settings.ingest.ocr_engine,
            "readonly": state.readonly,
        }

    @app.get("/api/doc-types")
    def doc_types(all: bool = False):  # noqa: A002 — query parameter name
        files = workshop.class_files(state.config_dir)
        if all:
            try:
                active = set(state.pipeline().registry.doc_types)
            except HTTPException:
                active = set()
            return [f | {"active": f["id"] in active} for f in files]
        file_of = {f["id"]: f["file"] for f in files}
        reg = state.pipeline().registry
        return [
            {
                "id": dt.id,
                "name": dt.name,
                "description": dt.description,
                "fields": list(dt.leaf_specs()),
                "file": file_of.get(dt.id),
                "enabled": dt.enabled,
                "signals": len(dt.classification.signals),
            }
            for dt in reg.doc_types.values()
        ]

    # ------------------------------------------------------------------ documents
    @app.post("/api/process")
    async def process(files: list[UploadFile] = File(...)):
        if len(files) > MAX_FILES_PER_REQUEST:
            raise HTTPException(400, f"Maksymalnie {MAX_FILES_PER_REQUEST} plików w jednym żądaniu")
        names = [_check_pdf_name(f) for f in files]
        pipe = await run_in_threadpool(state.pipeline)
        stored = []
        try:
            for f, name in zip(files, names, strict=True):
                doc = state.store.new(name)
                stored.append(doc)
                await _save_upload(f, doc)
        except HTTPException:
            for doc in stored:
                state.store.delete(doc.id)
            raise
        # all files go to the processing pool at once (it bounds the parallelism); results in upload order
        return list(await asyncio.gather(*(state.run_in_worker(state.process_job, pipe, doc) for doc in stored)))

    def _doc(doc_id: str) -> StoredDoc:
        doc = state.store.get(doc_id)
        if doc is None:
            raise HTTPException(404, "Nie ma takiego dokumentu (sesja wygasła?) — wgraj plik ponownie")
        return doc

    @app.get("/api/doc/{doc_id}")
    def get_doc(doc_id: str):
        return _doc(doc_id).payload

    @app.delete("/api/doc/{doc_id}", status_code=204)
    def delete_doc(doc_id: str):
        if not state.store.delete(doc_id):
            raise HTTPException(404, "Nie ma takiego dokumentu")
        return Response(status_code=204)

    @app.get("/api/doc/{doc_id}/page/{n}.png")
    async def page_png(doc_id: str, n: int, dpi: int = DEFAULT_DPI):
        doc = _doc(doc_id)
        dpi = max(MIN_DPI, min(MAX_DPI, dpi))
        if n < 1 or (doc.page_count and n > doc.page_count):
            raise HTTPException(404, f"Dokument nie ma strony {n}")
        path = doc.page_png_path(n, dpi)
        if not path.exists():
            try:  # threadpool, not the processing pool: a preview must not wait behind a long OCR job
                await run_in_threadpool(render_page_png, doc.pdf_path, n, dpi, path)
            except IndexError as e:
                raise HTTPException(404, f"Dokument nie ma strony {n}") from e
            except Exception as e:  # noqa: BLE001
                log.warning("Cannot render page %s of %s: %s", n, doc.filename, e)
                raise HTTPException(500, "Nie udało się wyrenderować strony") from e
        return FileResponse(path, media_type="image/png", headers={"Cache-Control": "private, max-age=3600"})

    @app.get("/api/doc/{doc_id}/json")
    def download_json(doc_id: str):
        doc = _doc(doc_id)
        if not doc.output_path.exists():
            raise HTTPException(404, "Brak pliku wynikowego (ustawienie output.unknown_output: none)")
        return FileResponse(
            doc.output_path, media_type="application/json", filename=doc.payload.get("json_filename", "wynik.json")
        )

    @app.get("/api/doc/{doc_id}/pdf")
    def download_pdf(doc_id: str):
        doc = _doc(doc_id)
        return FileResponse(
            doc.pdf_path, media_type="application/pdf", filename=doc.filename, content_disposition_type="inline"
        )

    # ------------------------------------------------------------------ class workshop
    @app.get("/api/doc-types/template", response_class=PlainTextResponse)
    def doc_type_template():
        return PlainTextResponse(workshop.TEMPLATE_YAML, media_type="text/yaml; charset=utf-8")

    @app.get("/api/doc-types/meta")
    def doc_type_meta():
        return classform.form_meta(state.dictionaries())

    @app.post("/api/doc-types/parse")
    async def parse_doc_type(request: Request):
        text = await _yaml_from_request(request)
        dictionaries = await run_in_threadpool(state.dictionaries)
        return await run_in_threadpool(classform.parse_class_yaml, text, dictionaries)

    @app.post("/api/doc-types/dump")
    async def dump_doc_type(request: Request):
        body = await _json_from_request(request)
        data = body.get("data") if isinstance(body, dict) else None
        try:
            text = await run_in_threadpool(classform.dump_class, data)
        except (ValueError, RecursionError) as e:
            raise HTTPException(400, str(e)) from e
        return {"yaml": text}

    @app.get("/api/doc-types/{doc_type_id}/yaml", response_class=PlainTextResponse)
    def get_doc_type_yaml(doc_type_id: str):
        f = workshop.find_class_file(state.config_dir, _guard_id(doc_type_id))
        if f is None:
            raise HTTPException(404, f"Nie ma klasy „{doc_type_id}”")
        return PlainTextResponse(
            f.read_text(encoding="utf-8"), media_type="text/yaml; charset=utf-8", headers={"X-File-Name": f.name}
        )

    @app.post("/api/doc-types/validate")
    async def validate_doc_type(request: Request):
        text = await _yaml_from_request(request)
        dictionaries = await run_in_threadpool(state.dictionaries)
        report = await run_in_threadpool(workshop.validate_yaml, text, dictionaries)
        return report.to_dict()

    @app.post("/api/doc-types/test")
    async def test_doc_type(yaml_text: str = Form(..., alias="yaml"), file: UploadFile = File(...)):
        name = _check_pdf_name(file)
        pipe = await run_in_threadpool(state.pipeline)
        report = await run_in_threadpool(workshop.validate_yaml, yaml_text, pipe.registry.dictionaries)
        if not report.ok:
            return JSONResponse(
                {"detail": "Konfiguracja zawiera błędy — popraw je przed testem", **report.to_dict()}, status_code=422
            )
        spec = report.spec
        registry = Registry(
            config_dir=pipe.registry.config_dir,
            doc_types={**pipe.registry.doc_types, spec.id: spec},
            dictionaries=pipe.registry.dictionaries,
        )
        test_pipe = await run_in_threadpool(state.derived_pipeline, registry)
        doc = state.store.new(name)
        try:
            await _save_upload(file, doc)
        except HTTPException:
            state.store.delete(doc.id)
            raise
        payload = await state.run_in_worker(state.process_job, test_pipe, doc, spec.id)
        payload["test"] = {
            "doc_type": spec.id,
            "name": spec.name,
            "recognised": payload["doc_type"] == spec.id,
            "replaced": spec.id in pipe.registry.doc_types,
            "min_score": {k: v.classification.min_score for k, v in registry.doc_types.items()},
            "warnings": report.warnings,
        }
        doc.payload = payload
        return payload

    @app.put("/api/doc-types/{doc_type_id}/yaml")
    async def put_doc_type_yaml(doc_type_id: str, request: Request):
        _guard_id(doc_type_id)
        if state.readonly:
            raise HTTPException(403, "Zapisywanie klas jest wyłączone (ODWOLANIA2JSON_WEB_READONLY)")
        text = await _yaml_from_request(request)
        return await run_in_threadpool(_save_class, doc_type_id, text)

    def _save_class(doc_type_id: str, text: str):
        report = workshop.validate_yaml(text, state.dictionaries(), expected_id=doc_type_id)
        if not report.ok:
            return JSONResponse({"detail": "Nie zapisano — konfiguracja zawiera błędy", **report.to_dict()}, 422)
        with state.save_lock:
            try:
                target, previous = workshop.save_class_yaml(state.config_dir, doc_type_id, text)
            except FileExistsError as e:
                raise HTTPException(409, f"Nie zapisano: {e}") from e
            except OSError as e:  # read-only folder, file locked by another program (Windows) …
                log.exception("Cannot save class %s", doc_type_id)
                raise HTTPException(500, f"Nie zapisano pliku klasy: {e}") from e
            try:
                pipe = state.reload()
            except Exception as e:  # noqa: BLE001 — keep the previous, working configuration
                workshop.restore_class_yaml(target, previous)
                log.exception("Reload after saving %s failed", target)
                raise HTTPException(
                    500, f"Nie zapisano: po zapisie nie udało się wczytać konfiguracji ({e}); przywrócono poprzednią"
                ) from e
        return {
            "ok": True,
            "id": doc_type_id,
            "file": target.name,
            "created": previous is None,
            "active": doc_type_id in pipe.registry.doc_types,
            "doc_types": list(pipe.registry.doc_types),
            "warnings": report.warnings,
        }

    # ------------------------------------------------------------------ UI
    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(web_dir / "index.html")

    app.mount("/static", StaticFiles(directory=web_dir), name="static")
    return app


app = create_app()
