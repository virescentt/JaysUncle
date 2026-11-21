"""Pipeline settings: config/settings.yaml, overridden by environment variables (.env)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from odwolania2json import CONFIG_DIR, PROJECT_ROOT


class IngestSettings(BaseModel):
    """PDF → pages (odwolania2json/ingest): text layer via pdfplumber, OCR for scans / garbage text layers."""

    min_chars_per_page: int = 40  # fewer characters in the text layer → the page is a scan → OCR
    min_text_quality: float = 0.5  # min share of letters/digits in a text layer; lower ("(cid:..)", junk) → OCR
    image_page_min_chars: int = 200  # page covered by an image (scan) with less text layer than this → OCR
    ocr_engine: Literal["auto", "rapidocr", "easyocr", "tesseract", "none"] = "auto"
    ocr_model: str = "PP-OCRv6-small"  # rapidocr: PP-OCRv6-tiny | PP-OCRv6-small | PP-OCRv6-medium | PP-OCRv5-latin
    ocr_dpi: int = 300  # page rendering resolution for OCR
    ocr_preprocess: list[str] = Field(default_factory=list)  # grayscale | autocontrast | binarize | denoise | deskew
    ocr_det_side: int = 2000  # rapidocr: longest image side (px) for text detection (0 = library default)
    ocr_max_side: int = 0  # rapidocr: downscale the page to this longest side before det+rec (0 = never)
    ocr_use_cls: bool = False  # rapidocr: per-line 0/180° classifier (flips bleed-through lines by mistake)
    ocr_auto_rotate: bool = True  # page read with low confidence → try it turned by 180/90/270° (upside-down scans)
    ocr_min_score: float = 0.5  # recognized text with lower confidence is dropped
    ocr_retry_score: float = 0.9  # rapidocr: lines read with lower confidence are re-read from enhanced crops (0=off)
    ocr_polish_letters: bool = True  # OCR letters absent from Polish (ı ñ š ž à é ç…) → Polish / base letter
    ocr_lang: str = "pol"  # tesseract languages ("pol+eng"); easyocr: pol → pl
    max_pages: int = 20
    tesseract_cmd: str | None = None
    cache_dir: str | None = ".cache/ingest"  # cache of extracted/OCR-ed pages (None = off)


class ClassificationSettings(BaseModel):
    head_chars: int = 1500
    min_margin: float = 2.0
    use_llm_fallback: bool = True


class ExtractionSettings(BaseModel):
    mode: Literal["rules", "llm", "hybrid"] = "hybrid"
    min_confidence: float = 0.5
    method_priority: list[str] = Field(default_factory=lambda: ["rules", "llm", "derived"])


class LLMSettings(BaseModel):
    base_url: str | None = None
    api_key: str | None = None
    model: str | None = None
    temperature: float = 0.0
    max_tokens: int = 2500
    timeout_s: float = 120
    structured_output: Literal["json_schema", "json_object", "prompt"] = "json_schema"
    max_retries: int = 2
    cache_dir: str = ".cache/llm"
    prompt_suffix: str = ""  # appended to every system prompt, e.g. "/no_think" for Qwen3 models
    # extra JSON fields sent with every request, provider-specific, e.g. for vLLM-based servers (OVHcloud):
    # {"chat_template_kwargs": {"enable_thinking": false}} — env LLM_EXTRA_BODY (JSON)
    extra_body: dict = Field(default_factory=dict)

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)


class PipelineSettings(BaseModel):
    workers: int = 4  # documents processed in parallel (LLM calls are I/O-bound)


class OutputSettings(BaseModel):
    write_traces: bool = True
    traces_dir: str = "traces"
    # Unrecognised document (not one of the classes): organizer README → "zwrócić pusty plik .json"
    unknown_output: Literal["empty_file", "empty_object", "none"] = "empty_file"
    indent: int = 2


class Settings(BaseModel):
    ingest: IngestSettings = IngestSettings()
    classification: ClassificationSettings = ClassificationSettings()
    extraction: ExtractionSettings = ExtractionSettings()
    llm: LLMSettings = LLMSettings()
    output: OutputSettings = OutputSettings()
    pipeline: PipelineSettings = PipelineSettings()
    config_dir: Path = CONFIG_DIR

    @classmethod
    def load(cls, config_dir: Path | None = None) -> Settings:
        load_dotenv(PROJECT_ROOT / ".env", override=False)
        config_dir = Path(config_dir or CONFIG_DIR)
        f = config_dir / "settings.yaml"
        raw = yaml.safe_load(f.read_text(encoding="utf-8")) if f.exists() else {}
        s = cls.model_validate({**(raw or {}), "config_dir": config_dir})

        env = os.environ.get
        if v := env("EXTRACTION_MODE"):
            s.extraction.mode = v  # type: ignore[assignment]
        if v := env("OCR_ENGINE"):
            s.ingest.ocr_engine = v  # type: ignore[assignment]
        if v := env("TESSERACT_CMD"):
            s.ingest.tesseract_cmd = v
        s.llm.base_url = env("LLM_BASE_URL") or s.llm.base_url
        s.llm.api_key = env("LLM_API_KEY") or s.llm.api_key
        s.llm.model = env("LLM_MODEL") or s.llm.model
        if (v := env("LLM_PROMPT_SUFFIX")) is not None:
            s.llm.prompt_suffix = v
        if v := env("LLM_EXTRA_BODY"):
            s.llm.extra_body = json.loads(v)
        if v := env("PIPELINE_WORKERS"):
            s.pipeline.workers = max(1, int(v))
        return s
