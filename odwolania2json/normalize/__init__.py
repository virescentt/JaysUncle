"""Normalizer registry. YAML fields reference normalizers by name: `normalize: [date_pl]`.

Contract of a normalizer:  f(value) -> normalized value | None (None = could not parse → field becomes null).
A normalizer that raises NotImplementedError is skipped (value passes through) — so the pipeline works
while normalizers are still being written.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

log = logging.getLogger(__name__)

NORMALIZERS: dict[str, Callable[[Any], Any]] = {}


def normalizer(name: str):
    def deco(fn: Callable[[Any], Any]) -> Callable[[Any], Any]:
        NORMALIZERS[name] = fn
        return fn

    return deco


def apply_normalizers(names: list[str], value: Any) -> tuple[Any, list[str]]:
    """Apply normalizers in order. Returns (value, issues)."""
    issues: list[str] = []
    for name in names:
        if value is None:
            break
        fn = NORMALIZERS.get(name)
        if fn is None:
            issues.append(f"unknown normalizer '{name}'")
            continue
        try:
            value = fn(value)
        except NotImplementedError:
            issues.append(f"normalizer '{name}' not implemented yet")
        except Exception as e:  # noqa: BLE001 — a broken normalizer must not kill the document
            issues.append(f"normalizer '{name}' failed: {e}")
            value = None
    return value, issues


# register built-in normalizers
from odwolania2json.normalize import text as _text  # noqa: E402,F401
from odwolania2json.normalize import values as _values  # noqa: E402,F401
