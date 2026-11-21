"""Pipeline settings: config/settings.yaml, overridden by environment variables (.env)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import yaml
from dotenv import load_dotenv
from pydantic import BaseModel, Field

from odwolania2json import CONFIG_DIR, PROJECT_ROOT


class IngestSettings(BaseModel):
    min_chars_per_page: int = 40
    ocr_engine: Literal["auto", "tesseract", "rapidocr", "none"] = "auto"
    ocr_dpi: int = 300
    ocr_lang: str = "pol"
    max_pages: int = 20
    tesseract_cmd: str | None = None


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

    @property
    def enabled(self) -> bool:
        return bool(self.base_url and self.model)


class OutputSettings(BaseModel):
    write_traces: bool = True
    traces_dir: str = "traces"
    skip_unknown: bool = True
    indent: int = 2


class Settings(BaseModel):
    ingest: IngestSettings = IngestSettings()
    classification: ClassificationSettings = ClassificationSettings()
    extraction: ExtractionSettings = ExtractionSettings()
    llm: LLMSettings = LLMSettings()
    output: OutputSettings = OutputSettings()
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
        return s
