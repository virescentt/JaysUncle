"""Pipeline orchestration: ingest → classify → extract → merge/normalize → write.

pipe = Pipeline()                                   # settings + config/ + optional LLM client
result = pipe.process_file(Path("input/case1/x.pdf"))
results = pipe.run(Path("input"), Path("output"))   # whole tree, mirrored output, documents in parallel
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

from odwolania2json.classify import classify
from odwolania2json.extract import extract_candidates
from odwolania2json.extract.merge import merge
from odwolania2json.ingest import load_document
from odwolania2json.llm import LLMClient
from odwolania2json.models import ClassificationResult, DocumentResult
from odwolania2json.output import output_path_for, write_json, write_result
from odwolania2json.settings import Settings
from odwolania2json.spec import Registry, load_registry

log = logging.getLogger(__name__)


def find_pdfs(root: Path) -> list[Path]:
    return sorted(p for p in Path(root).rglob("*") if p.is_file() and p.suffix.lower() == ".pdf")


class Pipeline:
    def __init__(self, settings: Settings | None = None, registry: Registry | None = None):
        self.settings = settings or Settings.load()
        self.registry = registry or load_registry(self.settings.config_dir)
        self.llm = None
        needs_llm = self.settings.extraction.mode != "rules" or any(
            dt.extraction.mode in ("llm", "hybrid") for dt in self.registry.doc_types.values()
        )
        if needs_llm:  # rules everywhere = fully offline, no LLM at all
            self.llm = LLMClient.from_settings(self.settings.llm)

    def process_file(self, pdf: Path) -> DocumentResult:
        timings: dict[str, float] = {}
        t = time.perf_counter()
        doc = load_document(pdf, self.settings.ingest)
        timings["ingest"] = time.perf_counter() - t

        t = time.perf_counter()
        cls = classify(doc, self.registry, self.settings, self.llm)
        timings["classify"] = time.perf_counter() - t
        result = DocumentResult(
            source=Path(pdf),
            classification=cls,
            pages=len(doc.pages),
            scanned=doc.is_scanned,
            timings=timings,
            document=doc,
        )
        if cls.doc_type is None:
            return result

        spec = self.registry.doc_types[cls.doc_type]
        t = time.perf_counter()
        candidates, errors = extract_candidates(doc, spec, self.registry, self.settings, self.llm)
        timings["extract"] = time.perf_counter() - t

        t = time.perf_counter()
        result.data, result.fields = merge(doc, spec, self.registry, candidates, self.settings.extraction)
        timings["merge"] = time.perf_counter() - t
        result.errors += errors
        return result

    def safe_process(self, pdf: Path) -> DocumentResult:
        """process_file that never raises — one broken PDF must not stop the batch."""
        try:
            return self.process_file(pdf)
        except Exception as e:  # noqa: BLE001
            log.exception("Failed to process %s", pdf)
            res = DocumentResult(source=Path(pdf), classification=ClassificationResult(None, 0.0, "none"))
            res.errors.append(f"{type(e).__name__}: {e}")
            return res

    def process_many(self, pdfs: Iterable[Path], workers: int | None = None) -> Iterator[DocumentResult]:
        """Process documents in parallel threads (settings.pipeline.workers); yields results as they finish."""
        pdfs = list(pdfs)
        workers = max(1, workers or self.settings.pipeline.workers)
        if workers == 1 or len(pdfs) <= 1:
            for pdf in pdfs:
                yield self.safe_process(pdf)
            return
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="doc") as pool:
            futures = [pool.submit(self.safe_process, pdf) for pdf in pdfs]
            for fut in as_completed(futures):
                yield fut.result()

    def run(
        self,
        input_root: Path,
        output_root: Path,
        traces_root: Path | None = None,
        limit: int | None = None,
        on_result: Callable[[DocumentResult], None] | None = None,
    ) -> list[DocumentResult]:
        out_cfg = self.settings.output
        if traces_root is None and out_cfg.write_traces:
            traces_root = Path(out_cfg.traces_dir)
        pdfs = find_pdfs(input_root)[:limit]
        order = {p: i for i, p in enumerate(pdfs)}
        results: list[DocumentResult] = []
        for res in self.process_many(pdfs):
            pdf = res.source
            write_result(
                output_path_for(pdf, input_root, output_root), res.data, out_cfg.unknown_output, out_cfg.indent
            )
            if traces_root is not None:
                write_json(output_path_for(pdf, input_root, traces_root, ".trace.json"), res.to_trace())
            results.append(res)
            if on_result:
                on_result(res)
        return sorted(results, key=lambda r: order.get(r.source, 0))
