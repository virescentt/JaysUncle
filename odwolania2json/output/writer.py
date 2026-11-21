"""Writing results: output/<case>/<same name>.json (+ traces/<case>/<same name>.trace.json)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def output_path_for(pdf: Path, input_root: Path, output_root: Path, suffix: str = ".json") -> Path:
    """input/case1/odwolanie_nr_1.pdf → output/case1/odwolanie_nr_1.json (tree mirrored 1:1)."""
    rel = Path(pdf).resolve().relative_to(Path(input_root).resolve())
    return Path(output_root) / rel.with_name(rel.stem + suffix)


def write_json(path: Path, data: Any, indent: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=indent, default=str) + "\n", encoding="utf-8")
