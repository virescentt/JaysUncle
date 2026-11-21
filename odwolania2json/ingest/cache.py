"""Ingest cache: the extracted / OCR-ed pages of a PDF stored as JSON — OCR runs once per file and settings.

key   = sha256(file bytes) + sha256(relevant ingest settings, resolved OCR engine, INGEST_VERSION)
value = {"version", "file", "pages": [{number, text, words: [[text, x0, top, x1, bottom], …], width, height,
         source, ocr_engine, ocr_confidence, …}]}  — every Page field is stored, words in compact form.

Location: settings.ingest.cache_dir (relative paths → project root; None = cache off). Writes are atomic
(temporary file in the same folder + os.replace), so parallel document threads or processes never see a
half-written entry; a broken or outdated entry is simply ignored and rebuilt.
"""

from __future__ import annotations

import dataclasses
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from odwolania2json import PROJECT_ROOT
from odwolania2json.models import Page, Word
from odwolania2json.settings import IngestSettings

log = logging.getLogger(__name__)

# Bump whenever pdf.py / ocr.py change the pages they produce (text, words, reading order…) — old entries
# then stop matching and are rebuilt.
INGEST_VERSION = "2026-11-21.9"

_NOT_IN_KEY = {"cache_dir", "tesseract_cmd"}  # do not change the produced pages
_PAGE_FIELDS = [f.name for f in dataclasses.fields(Page)]


def file_sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def settings_fingerprint(settings: IngestSettings, engine_label: str | None) -> str:
    relevant = settings.model_dump(exclude=_NOT_IN_KEY)
    payload = json.dumps({"v": INGEST_VERSION, "engine": engine_label, "settings": relevant}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def page_to_dict(page: Page) -> dict[str, Any]:
    d: dict[str, Any] = {}
    for name in _PAGE_FIELDS:
        value = getattr(page, name)
        if name == "words":
            value = [[w.text, *(round(float(c), 2) for c in w.bbox)] for w in value]
        d[name] = value
    return d


def page_from_dict(d: dict[str, Any]) -> Page:
    kwargs = {k: v for k, v in d.items() if k in _PAGE_FIELDS}
    kwargs["words"] = [Word(w[0], (w[1], w[2], w[3], w[4])) for w in d.get("words", [])]
    return Page(**kwargs)


class IngestCache:
    def __init__(self, root: Path):
        self.root = Path(root)

    @classmethod
    def from_settings(cls, settings: IngestSettings) -> IngestCache | None:
        if not settings.cache_dir:
            return None
        root = Path(settings.cache_dir)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        try:
            root.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log.warning("Ingest cache disabled (%s): %s", root, e)
            return None
        return cls(root)

    def key(self, pdf_path: Path, settings: IngestSettings, engine_label: str | None) -> str:
        return f"{file_sha256(pdf_path)[:40]}-{settings_fingerprint(settings, engine_label)[:16]}"

    def path(self, key: str) -> Path:
        return self.root / f"{key}.json"

    def get(self, key: str) -> list[Page] | None:
        f = self.path(key)
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
            if data.get("version") != INGEST_VERSION:
                return None
            return [page_from_dict(p) for p in data["pages"]]
        except FileNotFoundError:
            return None
        except Exception as e:  # noqa: BLE001 — corrupt / foreign file → rebuild
            log.warning("Ignoring broken ingest cache entry %s: %s", f.name, e)
            return None

    def put(self, key: str, pages: list[Page], source: Path | None = None) -> None:
        payload = {
            "version": INGEST_VERSION,
            "file": source.name if source else None,
            "pages": [page_to_dict(p) for p in pages],
        }
        tmp = None
        try:
            fd, tmp = tempfile.mkstemp(dir=self.root, prefix=f".{key}.", suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, separators=(",", ":"))
            os.replace(tmp, self.path(key))  # atomic; concurrent writers of the same key: last one wins
            tmp = None
        except OSError as e:  # e.g. Windows: target momentarily open by a reader — the cache is best-effort
            log.warning("Could not write ingest cache entry %s: %s", key, e)
        finally:
            if tmp is not None:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
