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


def write_result(path: Path, data: dict | None, unknown_output: str = "empty_file", indent: int = 2) -> None:
    """Write the canonical JSON, or the 'unrecognised document' marker (organizer README: empty .json file)."""
    if data is not None:
        write_json(path, data, indent)
    elif unknown_output == "empty_file":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("", encoding="utf-8")
    elif unknown_output == "empty_object":
        write_json(path, {}, indent)
    elif path.exists():
        path.unlink()  # "none": no file (and no stale result from a previous run)
