"""Helpers for addressing leaf fields inside nested JSON with string paths.

Concrete path:  "Odwolujacy[0].pelnomocnik.mail"   "mocodawca[1].podpisujacy[0].imie_nazwisko"
Generic path:   "Odwolujacy[].pelnomocnik.mail"    (indices removed — used for per-field statistics)
"""

from __future__ import annotations

import re
from typing import Any

_TOKEN = re.compile(r"([^.\[\]]+)|\[(\d*)\]")


def parse(path: str) -> list[str | int]:
    """'a[0].b' → ['a', 0, 'b']"""
    out: list[str | int] = []
    for name, idx in _TOKEN.findall(path):
        if name:
            out.append(name)
        else:
            out.append(int(idx) if idx else -1)
    return out


def generic(path: str) -> str:
    """'Odwolujacy[0].nip' → 'Odwolujacy[].nip'"""
    return re.sub(r"\[\d+\]", "[]", path)


def join(prefix: str, key: str | int) -> str:
    if isinstance(key, int):
        return f"{prefix}[{key}]"
    return f"{prefix}.{key}" if prefix else key


def flatten(data: Any, prefix: str = "") -> dict[str, Any]:
    """Nested dict/list → {concrete_path: leaf_value}. Empty containers produce no leaves."""
    out: dict[str, Any] = {}
    if isinstance(data, dict):
        for k, v in data.items():
            out.update(flatten(v, join(prefix, k)))
    elif isinstance(data, list):
        for i, v in enumerate(data):
            out.update(flatten(v, join(prefix, i)))
    else:
        out[prefix] = data
    return out


def get(data: Any, path: str, default: Any = None) -> Any:
    cur = data
    for tok in parse(path):
        try:
            cur = cur[tok]
        except (KeyError, IndexError, TypeError):
            return default
    return cur


def set_value(data: Any, path: str, value: Any) -> None:
    """Set a leaf in an existing structure (containers must already exist — see spec.build_output)."""
    toks = parse(path)
    cur = data
    for tok in toks[:-1]:
        cur = cur[tok]
    cur[toks[-1]] = value
