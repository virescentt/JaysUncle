"""Client for any OpenAI-compatible API serving an open-source model (Ollama, vLLM, llama.cpp, hosted
open-weights providers). Configured only through .env / settings — no provider-specific code.

Features: JSON output (json_schema → json_object → plain prompt, downgraded automatically when the server
does not support a mode), retries with error feedback, on-disk cache (fast repeated evaluation runs).
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any

from odwolania2json import PROJECT_ROOT
from odwolania2json.settings import LLMSettings

log = logging.getLogger(__name__)

_MODES = ["json_schema", "json_object", "prompt"]


class LLMError(RuntimeError):
    pass


def parse_json(text: str) -> dict[str, Any]:
    """Parse a JSON object from a model answer (strips <think> blocks and ``` fences)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text).strip()
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        raise ValueError("no JSON object in the answer")
    return json.loads(text[start : end + 1])


class LLMClient:
    def __init__(self, settings: LLMSettings):
        from openai import OpenAI

        self.settings = settings
        self.mode = settings.structured_output
        self.client = OpenAI(
            base_url=settings.base_url,
            api_key=settings.api_key or "not-needed",
            timeout=settings.timeout_s,
        )
        self.cache_dir = PROJECT_ROOT / settings.cache_dir
        self.cache_dir.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_settings(cls, settings: LLMSettings, check: bool = True) -> LLMClient | None:
        """Client or None. With check=True an unreachable server is detected ONCE here (one warning,
        pipeline continues in rules-only mode) instead of failing on every document."""
        if not settings.enabled:
            return None
        try:
            client = cls(settings)
        except Exception as e:  # noqa: BLE001
            log.warning("LLM client unavailable: %s", e)
            return None
        if check and not client.is_reachable():
            log.warning(
                "LLM niedostępny pod %s — pracuję w trybie reguł. Uruchom serwer LLM albo ustaw "
                "EXTRACTION_MODE=rules w .env, żeby ukryć to ostrzeżenie.",
                settings.base_url,
            )
            return None
        return client

    def is_reachable(self) -> bool:
        """Quick connectivity check (GET /models). Only connection problems count as 'unreachable'."""
        from openai import APIConnectionError, APITimeoutError

        try:
            self.client.with_options(timeout=5, max_retries=0).models.list()
        except (APIConnectionError, APITimeoutError):
            return False
        except Exception:  # noqa: BLE001 — e.g. /models not implemented or auth error: let real calls decide
            return True
        return True

    # ------------------------------------------------------------------ cache
    def _key(self, *parts: Any) -> str:
        raw = json.dumps([self.settings.model, *parts], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(raw.encode()).hexdigest()

    def _cache_path(self, key: str) -> Path:
        return self.cache_dir / f"{key}.json"

    # ------------------------------------------------------------------ main API
    def complete_json(self, system: str, user: str, schema: dict | None = None, name: str = "output") -> dict:
        if self.settings.prompt_suffix:
            system = f"{system}\n{self.settings.prompt_suffix}"
        key = self._key(system, user, schema)
        cached = self._cache_path(key)
        if cached.exists():
            return json.loads(cached.read_text(encoding="utf-8"))

        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        last_error: Exception | None = None
        for _attempt in range(self.settings.max_retries + 1):
            kwargs: dict[str, Any] = {
                "model": self.settings.model,
                "messages": messages,
                "temperature": self.settings.temperature,
                "max_tokens": self.settings.max_tokens,
            }
            if self.mode == "json_schema" and schema is not None:
                kwargs["response_format"] = {
                    "type": "json_schema",
                    "json_schema": {"name": name, "schema": schema, "strict": False},
                }
            elif self.mode in ("json_schema", "json_object"):
                kwargs["response_format"] = {"type": "json_object"}
            if self.mode == "prompt" and schema is not None and not messages[0]["content"].endswith("</schema>"):
                messages[0]["content"] += "\n\nOdpowiedz wyłącznie JSON-em zgodnym ze schematem:\n<schema>\n"
                messages[0]["content"] += json.dumps(schema, ensure_ascii=False) + "\n</schema>"
            try:
                resp = self.client.chat.completions.create(**kwargs)
            except Exception as e:  # noqa: BLE001
                last_error = e
                if _is_unsupported_format(e) and self.mode != "prompt":
                    self.mode = _MODES[_MODES.index(self.mode) + 1]
                    log.info("LLM server rejected response_format → switching to '%s'", self.mode)
                    continue
                raise LLMError(f"LLM request failed: {e}") from e
            content = resp.choices[0].message.content or ""
            try:
                data = parse_json(content)
            except Exception as e:  # noqa: BLE001
                last_error = e
                messages = messages + [
                    {"role": "assistant", "content": content},
                    {"role": "user", "content": f"Błąd parsowania JSON: {e}. Zwróć wyłącznie poprawny JSON."},
                ]
                continue
            cached.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
            return data
        raise LLMError(f"LLM did not return valid JSON: {last_error}")


def _is_unsupported_format(e: Exception) -> bool:
    msg = str(e).lower()
    return "response_format" in msg or "json_schema" in msg or "not supported" in msg
