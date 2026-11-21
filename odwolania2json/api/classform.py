"""Form editor of the class workshop: YAML text ↔ JSON for the friendly "Formularz" mode of the panel.

The form edits the plain data of a class (what yaml.safe_load gives) and sends it back as JSON; this module turns
it into YAML again. Keys are kept in their order and keys the form does not know are passed through untouched,
but a YAML file can carry things JSON cannot: comments, anchors / aliases (&kotwica / *alias), non-JSON scalars
(dates, …). `parse_class_yaml` reports them so the panel can warn and recommend the YAML mode for such a class.
Validation itself is `workshop.validate_yaml` (the same as the YAML mode).
"""

from __future__ import annotations

import inspect
import json
import re
from typing import Any, get_args

import yaml

from odwolania2json.api import workshop
from odwolania2json.spec import Dictionary, ExtractionSpec, FieldType, Ground, Scope


class _Dumper(yaml.SafeDumper):
    """safe_dump without aliases (JSON from the form shares no objects) and with block style for multi-line text."""

    def ignore_aliases(self, data: Any) -> bool:
        return True


def _str_representer(dumper: yaml.SafeDumper, value: str) -> yaml.ScalarNode:
    style = "|" if "\n" in value else None  # PyYAML falls back to quotes when "|" cannot hold the text
    return dumper.represent_scalar("tag:yaml.org,2002:str", value, style=style)


_Dumper.add_representer(str, _str_representer)


def dump_class(data: Any) -> str:
    """JSON data of a class → YAML text (key order kept, Polish characters as they are)."""
    if not isinstance(data, dict):
        raise ValueError("oczekiwano obiektu z danymi klasy (id, name, classification, output…)")
    text = yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10_000)
    if len(text) > workshop.MAX_YAML_CHARS:
        raise ValueError(f"wynikowy YAML jest za duży (> {workshop.MAX_YAML_CHARS} znaków)")
    return text


def _to_json(value: Any) -> Any:
    """safe_load data → JSON-compatible data (dates and other scalars → str, keys → str)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _has_anchors(text: str) -> bool:
    try:
        return any(isinstance(t, (yaml.AnchorToken, yaml.AliasToken)) for t in yaml.scan(text, Loader=yaml.SafeLoader))
    except yaml.YAMLError:
        return False


def _has_comments(text: str) -> bool:
    """A '#' at a line start or after whitespace that is not inside a scalar (quoted or plain) = a comment."""
    try:
        spans = [
            (t.start_mark.index, t.end_mark.index)
            for t in yaml.scan(text, Loader=yaml.SafeLoader)
            if isinstance(t, yaml.ScalarToken)
        ]
    except yaml.YAMLError:
        return False
    for m in re.finditer(r"(?:^|(?<=\s))#", text, re.M):
        if not any(a <= m.start() < b for a, b in spans):
            return True
    return False


def parse_class_yaml(text: str, dictionaries: dict[str, Dictionary]) -> dict[str, Any]:
    """YAML text → {"data": JSON data or None, "ok", "errors", "warnings" (validation, like /validate),
    "roundtrip_ok": parse → dump → parse gives the same data, "losses": what saving from the form would drop}."""
    report = workshop.validate_yaml(text, dictionaries).to_dict()
    out: dict[str, Any] = {**report, "data": None, "roundtrip_ok": False, "losses": []}
    try:
        _, raw = workshop.load_yaml_limited(text) if isinstance(text, str) and text.strip() else (None, None)
    except (yaml.YAMLError, workshop.YamlLimitError):
        return out  # the syntax / limit error is already in report["errors"]
    if not isinstance(raw, dict):
        return out
    data = _to_json(raw)
    out["data"] = data
    losses: list[str] = []
    try:
        again = yaml.safe_load(dump_class(data))
        out["roundtrip_ok"] = again == raw
    except (ValueError, yaml.YAMLError):
        again = None
    if again != raw:
        losses.append("wartości, których nie da się zapisać w formularzu (np. daty bez cudzysłowu) zmienią się w tekst")
    if _has_comments(text):
        losses.append("komentarze (# …)")
    if _has_anchors(text):
        losses.append("kotwice i aliasy (&nazwa / *nazwa) — wspólne fragmenty zostaną rozpisane w każdym miejscu")
    out["losses"] = losses
    return out


# --------------------------------------------------------------------------- meta for the form

FORM_TEMPLATE: dict[str, Any] = {
    "id": "nowa_klasa",
    "name": "Nowa klasa dokumentu",
    "description": "Krótki opis — czym jest dokument i kto go wystawia.",
    "version": 1,
    "enabled": True,
    "classification": {
        "min_score": 3,
        "signals": [
            {"pattern": "tytuł dokumentu", "weight": 3, "scope": "head"},
            {"pattern": "charakterystyczne sformułowanie", "weight": 1},
        ],
        "negative_signals": [],
    },
    "extraction": {
        "mode": "llm",
        "instructions": "Przepisuj wartości dokładnie tak, jak w dokumencie; brak danych → null.\n",
    },
    "output": {
        "numer_dokumentu": {"type": "string", "description": "Numer lub sygnatura dokumentu."},
        "data_dokumentu": {"type": "date", "description": "Data wystawienia dokumentu."},
    },
}


def _first_doc_line(obj: Any) -> str:
    doc = inspect.getdoc(obj) if callable(obj) else None
    return doc.strip().splitlines()[0] if doc and doc.strip() else ""


def form_meta(dictionaries: dict[str, Dictionary]) -> dict[str, Any]:
    """Lists the form needs: field types, normalizer / validator / dictionary names, extraction modes, template."""
    from odwolania2json.normalize import NORMALIZERS
    from odwolania2json.validate import VALIDATORS

    mode_field = ExtractionSpec.model_fields.get("mode")
    modes = [a for a in get_args(mode_field.annotation) if isinstance(a, str)] if mode_field else []
    if not modes and mode_field is not None:  # Literal[...] | None → look one level deeper
        for arg in get_args(mode_field.annotation):
            modes += [a for a in get_args(arg) if isinstance(a, str)]
    template = json.loads(json.dumps(FORM_TEMPLATE))
    if not modes:  # this version of spec.py has no extraction.mode → a template with it would not validate
        template["extraction"].pop("mode", None)
    return {
        "field_types": list(get_args(FieldType)),
        "scopes": list(get_args(Scope)),
        "grounds": list(get_args(Ground)),
        "extraction_modes": modes,
        "normalizers": [{"name": n, "doc": _first_doc_line(NORMALIZERS[n])} for n in sorted(NORMALIZERS)],
        "validators": [{"name": n, "doc": _first_doc_line(VALIDATORS[n])} for n in sorted(VALIDATORS)],
        "dictionaries": [{"id": d.id, "description": d.description} for d in dictionaries.values()],
        "id_pattern": workshop.ID_RE.pattern.removesuffix(r"\Z"),
        "template": template,
    }
