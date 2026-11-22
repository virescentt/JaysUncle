"""Form editor of the class workshop: YAML text ↔ JSON for the friendly "Formularz" mode of the panel.

The form edits the plain data of a class (what yaml.safe_load gives) and sends it back as JSON; this module turns
it into YAML again. A YAML file can carry things JSON cannot: comments, anchors / aliases (&kotwica / *alias),
quoting and flow style. When the panel sends the YAML text the form was opened from (`base`), the changes are
merged INTO that text (ruamel.yaml round-trip): only the edited values change, comments, anchors, quotes and
`{ … }` rows stay where they were. Without `base` (a new class) the YAML is written from scratch.
`parse_class_yaml` reports what a save from the form would still lose, so the panel can warn.
Validation itself is `workshop.validate_yaml` (the same as the YAML mode).
"""

from __future__ import annotations

import copy
import difflib
import inspect
import io
import json
import re
from typing import Any, get_args

import yaml
from ruamel.yaml import YAML
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.emitter import Emitter
from ruamel.yaml.scalarstring import ScalarString

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


def dump_class(data: Any, base: str | None = None) -> str:
    """JSON data of a class → YAML text (key order kept, Polish characters as they are).

    base: the YAML text the form was opened from — the changes are merged into it, so comments, anchors,
    quotes and flow rows survive. Falls back to a fresh dump when base cannot be used."""
    if not isinstance(data, dict):
        raise ValueError("oczekiwano obiektu z danymi klasy (id, name, classification, output…)")
    plain = yaml.dump(data, Dumper=_Dumper, allow_unicode=True, sort_keys=False, default_flow_style=False, width=10_000)
    text = _dump_roundtrip(data, base, plain) if isinstance(base, str) and base.strip() else None
    text = text or plain
    if len(text) > workshop.MAX_YAML_CHARS:
        raise ValueError(f"wynikowy YAML jest za duży (> {workshop.MAX_YAML_CHARS} znaków)")
    return text


def _to_json(value: Any) -> Any:
    """safe_load data → JSON-compatible data (dates and other scalars → str, keys → str)."""
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


# --------------------------------------------------------------------------- round-trip save (comments kept)


class _PaddedEmitter(Emitter):
    """Flow mappings as "{ a: 1 }" (the style of the shipped class files), not ruamel's "{a: 1}"."""

    def write_indicator(self, indicator: Any, need_whitespace: Any, whitespace: bool = False, indention: bool = False):
        if indicator.strip() == "{":
            indicator += " "
        elif indicator == "}":
            indicator = " }"
        return super().write_indicator(indicator, need_whitespace, whitespace=whitespace, indention=indention)


def _rt_yaml(mapping: int, sequence: int, offset: int) -> YAML:
    y = YAML(typ="rt")
    y.preserve_quotes = True
    y.width = 10_000  # never re-wrap long regexes
    y.Emitter = _PaddedEmitter
    y.indent(mapping=mapping, sequence=sequence, offset=offset)
    return y


# indentation layouts: the shipped class files ("  key:\n    - item") and PyYAML's / the form's ("key:\n- item")
_LAYOUTS = [(2, 4, 2), (2, 2, 0), (4, 4, 2), (4, 6, 4)]


def _rt_for(base: str) -> YAML:
    """The layout that writes the unchanged `base` back byte for byte (else the shipped-files layout)."""
    for layout in _LAYOUTS:
        y = _rt_yaml(*layout)
        buf = io.StringIO()
        y.dump(y.load(base), buf)
        if buf.getvalue() == base:
            return y
    return _rt_yaml(*_LAYOUTS[0])


def _shared_ids(root: Any) -> set[int]:
    """ids of containers reachable more than once (anchor + aliases point to the same object)."""
    seen: set[int] = set()
    shared: set[int] = set()

    def walk(node: Any) -> None:
        if not isinstance(node, (dict, list)):
            return
        if id(node) in seen:
            shared.add(id(node))
            return
        seen.add(id(node))
        for child in node.values() if isinstance(node, dict) else node:
            walk(child)

    walk(root)
    return shared


def _fresh(value: Any, like: Any = None) -> Any:
    """A new value, styled like a sibling: a "{ … }" row next to "{ … }" rows, quotes like the neighbour."""
    if isinstance(value, dict) and isinstance(like, CommentedMap) and like.fa.flow_style():
        cm = CommentedMap(value)
        cm.fa.set_flow_style()
        return cm
    if isinstance(value, str) and isinstance(like, ScalarString) and "\n" not in value:
        try:
            return type(like)(value)
        except TypeError:
            return value
    return value


def _merge(old: Any, new: Any, shared: set[int]) -> Any:
    """The node for `new`, reusing `old` (its comments, anchor, quotes, flow style) wherever the value allows."""
    if _to_json(old) == new and type(_to_json(old)) is type(new):
        return old  # unchanged — keep the node exactly as it was
    if isinstance(old, (CommentedMap, CommentedSeq)) and id(old) in shared:
        # an anchored block used in several places: edit a copy here, the other places keep the original
        old = copy.deepcopy(old)
        old.anchor.value = None
    if isinstance(old, CommentedMap) and isinstance(new, dict):
        for key in [k for k in old if k not in new]:
            del old[key]
        for pos, (key, value) in enumerate(new.items()):
            if key in old:
                merged = _merge(old[key], value, shared)
                if merged is not old[key]:
                    old[key] = merged
            else:
                old.insert(pos, key, value)
        if list(old) != list(new):
            for key in new:
                old.move_to_end(key)
        return old
    if isinstance(old, CommentedSeq) and isinstance(new, list):
        _merge_seq(old, new, shared)
        return old
    if isinstance(old, ScalarString) and isinstance(new, str):
        return _fresh(new, like=old)
    return new


def _merge_seq(old: CommentedSeq, new: list, shared: set[int]) -> None:
    """Edit the list in place, item by item, so a comment stays with its item (difflib over the JSON of items)."""
    a = [json.dumps(_to_json(x), ensure_ascii=False, sort_keys=True) for x in old]
    b = [json.dumps(x, ensure_ascii=False, sort_keys=True) for x in new]
    ops = difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes()
    for tag, i1, i2, j1, j2 in reversed(ops):  # from the end: earlier indexes stay valid
        if tag == "equal":
            continue
        common = min(i2 - i1, j2 - j1) if tag == "replace" else 0
        for t in range(common):
            merged = _merge(old[i1 + t], new[j1 + t], shared)
            if merged is not old[i1 + t]:
                old[i1 + t] = merged
        for idx in range(i2 - 1, i1 + common - 1, -1):
            del old[idx]
        like = old[i1 - 1] if i1 > 0 else (old[0] if len(old) else None)
        for t, value in enumerate(new[j1 + common : j2]):
            old.insert(i1 + common + t, _fresh(value, like=like))


def _dump_roundtrip(data: dict, base: str, plain: str) -> str | None:
    """Merge `data` into the YAML text `base`. None when that is not possible or the result would not load
    exactly like the fresh dump `plain` (then the caller writes `plain` — correct data, comments lost)."""
    try:
        y = _rt_for(base)
        tree = y.load(base)
        if not isinstance(tree, CommentedMap):
            return None
        merged = _merge(tree, data, _shared_ids(tree))
        buf = io.StringIO()
        y.dump(merged, buf)
        text = buf.getvalue()
        return text if yaml.safe_load(text) == yaml.safe_load(plain) else None
    except Exception:  # noqa: BLE001 — any ruamel problem → the plain dump, never a failed save
        return None


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
    # the form saves by merging into this text — comments / anchors are lost only if that merge is impossible
    keeps_layout = again == raw and _dump_roundtrip(data, text, dump_class(data)) is not None
    if not keeps_layout and _has_comments(text):
        losses.append("komentarze (# …)")
    if not keeps_layout and _has_anchors(text):
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
