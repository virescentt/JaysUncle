"""Document-class specification: the YAML format of config/doc_types/*.yaml.

One YAML file = one document class. The generic pipeline reads everything it needs from here:
  * classification signals (how to recognise the document),
  * output schema (canonical JSON structure, field types, descriptions),
  * extraction hints (labels / regex patterns / sections for the rules extractor,
    descriptions + instructions for the LLM extractor),
  * normalizers / validators / grounding mode per field (referenced by name).

Adding a new class = adding a new YAML file. No code changes. See docs/NOWA_KLASA_DOKUMENTU.md.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field

from odwolania2json import CONFIG_DIR
from odwolania2json.paths import join

FieldType = Literal["string", "integer", "number", "boolean", "date", "object", "array"]
Scope = Literal["all", "first_page", "head"]
Ground = Literal["auto", "exact", "digits", "fuzzy", "none"]


class _Strict(BaseModel):
    # extra="forbid" → a typo in YAML is reported as an error instead of being silently ignored
    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class Section(_Strict):
    """Text region (regex start … regex end) in which child fields are searched by the rules extractor."""

    start: str
    end: str | None = None


class FieldSpec(_Strict):
    type: FieldType = "string"
    description: str = ""
    nullable: bool = True
    enum: list[Any] | None = None
    dictionary: str | None = None  # name of config/dictionaries/<name>.yaml
    derived_from: str | None = None  # sibling field; value computed from the dictionary (not extracted)
    derived_attr: str = "opis"

    # --- hints for the rules extractor ---
    labels: list[str] = Field(default_factory=list)  # "Label: value" or "Label\nvalue"
    patterns: list[str] = Field(default_factory=list)  # regex; value = group "value" (or group 1)
    case_sensitive: bool = False  # patterns/labels are case-insensitive unless set
    scope: Scope = "all"
    section: Section | None = None  # for object/array: region where children are searched
    item_split: str | None = None  # for array: regex splitting the section into items

    # --- post-processing ---
    normalize: list[str] = Field(default_factory=list)  # names from odwolania2json/normalize
    validators: list[str] = Field(default_factory=list, alias="validate")  # names from odwolania2json/validate
    ground: Ground = "auto"  # how to verify that the value really occurs in the text

    # --- nesting ---
    properties: dict[str, FieldSpec] | None = None  # type: object
    items: FieldSpec | None = None  # type: array
    min_items: int = 0
    max_items: int | None = None

    @property
    def is_leaf(self) -> bool:
        return self.type not in ("object", "array")

    @property
    def grounding(self) -> Ground:
        if self.ground != "auto":
            return self.ground
        if self.derived_from or self.enum or self.dictionary or self.type in ("date", "integer", "boolean"):
            return "none"
        return "fuzzy" if self.type == "string" else "none"


class Signal(_Strict):
    pattern: str
    weight: float = 1.0
    scope: Literal["all", "head"] = "all"
    case_sensitive: bool = False


class ClassificationSpec(_Strict):
    description: str = ""
    min_score: float = 3.0
    signals: list[Signal] = Field(default_factory=list)
    negative_signals: list[Signal] = Field(default_factory=list)


class ExtractionSpec(_Strict):
    instructions: str = ""
    max_pages: int | None = None  # pages passed to the LLM in full
    keyword_windows: list[str] = Field(default_factory=list)  # extra snippets from later pages
    few_shot: list[str] = Field(default_factory=list)  # optional example files (relative to config/)


class DocTypeSpec(_Strict):
    id: str
    name: str
    description: str = ""
    version: int = 1
    enabled: bool = True
    classification: ClassificationSpec
    extraction: ExtractionSpec = ExtractionSpec()
    output: dict[str, FieldSpec]

    # ------------------------------------------------------------------ helpers
    def leaf_specs(self) -> dict[str, FieldSpec]:
        """{generic_path: FieldSpec} for every leaf, e.g. 'Odwolujacy[].nip'."""
        out: dict[str, FieldSpec] = {}

        def walk(spec: FieldSpec, prefix: str) -> None:
            if spec.type == "object":
                for k, child in (spec.properties or {}).items():
                    walk(child, join(prefix, k))
            elif spec.type == "array":
                walk(spec.items, prefix + "[]")
            else:
                out[prefix] = spec

        for key, spec in self.output.items():
            walk(spec, key)
        return out

    def empty_output(self) -> dict[str, Any]:
        return self.build_output({})

    def build_output(self, values: dict[str, Any]) -> dict[str, Any]:
        """Build the canonical JSON from {concrete_path: value}; missing leaves → None.

        Array length = max(min_items, highest index present + 1), capped by max_items.
        Key order follows the YAML (= order of the expected JSON).
        """

        def array_len(prefix: str, spec: FieldSpec) -> int:
            pat = re.compile(re.escape(prefix) + r"\[(\d+)\]")
            idx = [int(m.group(1)) for k in values if (m := pat.match(k))]
            n = max(spec.min_items, max(idx) + 1 if idx else 0)
            return min(n, spec.max_items) if spec.max_items is not None else n

        def build(spec: FieldSpec, prefix: str) -> Any:
            if spec.type == "object":
                return {k: build(child, join(prefix, k)) for k, child in (spec.properties or {}).items()}
            if spec.type == "array":
                return [build(spec.items, f"{prefix}[{i}]") for i in range(array_len(prefix, spec))]
            return values.get(prefix)

        return {key: build(spec, key) for key, spec in self.output.items()}

    def json_schema(self, dictionaries: dict[str, Dictionary] | None = None, for_llm: bool = True) -> dict:
        """JSON Schema of the output (for LLM structured output). Derived fields are skipped for the LLM."""
        dictionaries = dictionaries or {}
        base = {
            "string": "string",
            "integer": "integer",
            "number": "number",
            "boolean": "boolean",
            "date": "string",
        }

        def conv(spec: FieldSpec) -> dict | None:
            if for_llm and spec.derived_from:
                return None
            if spec.type == "object":
                props = {k: s for k, c in (spec.properties or {}).items() if (s := conv(c)) is not None}
                schema: dict[str, Any] = {
                    "type": "object",
                    "properties": props,
                    "required": list(props),
                    "additionalProperties": False,
                }
            elif spec.type == "array":
                schema = {"type": "array", "items": conv(spec.items)}
            else:
                t = base[spec.type]
                schema = {"type": [t, "null"] if spec.nullable else t}
                desc = spec.description.strip()
                if spec.type == "date":
                    desc += " Format: YYYY-MM-DD."
                enum = list(spec.enum) if spec.enum else None
                if spec.dictionary and spec.dictionary in dictionaries:
                    d = dictionaries[spec.dictionary]
                    enum = [e.id for e in d.entries]
                    desc += " Dozwolone wartości: " + "; ".join(f"{e.id} = {e.opis}" for e in d.entries)
                if enum is not None:
                    schema["enum"] = enum + ([None] if spec.nullable else [])
                if desc:
                    schema["description"] = desc
                return schema
            if spec.description:
                schema["description"] = spec.description.strip()
            return schema

        props = {k: s for k, spec in self.output.items() if (s := conv(spec)) is not None}
        return {
            "type": "object",
            "properties": props,
            "required": list(props),
            "additionalProperties": False,
        }


class DictionaryEntry(BaseModel):
    model_config = ConfigDict(extra="allow")
    id: int | str
    opis: str
    hint: str = ""
    keywords: list[str] = Field(default_factory=list)


class Dictionary(_Strict):
    id: str
    description: str = ""
    entries: list[DictionaryEntry]

    def get(self, key: Any) -> DictionaryEntry | None:
        for e in self.entries:
            if str(e.id) == str(key):
                return e
        return None


class Registry(BaseModel):
    """All configuration loaded from config/: document classes, dictionaries, prompts."""

    model_config = ConfigDict(arbitrary_types_allowed=True)
    config_dir: Path
    doc_types: dict[str, DocTypeSpec]
    dictionaries: dict[str, Dictionary]

    def prompt(self, name: str) -> str:
        return (self.config_dir / "prompts" / name).read_text(encoding="utf-8")

    def check(self) -> list[str]:
        """Static checks of the configuration (used by `odwolania2json doc-types`)."""
        from odwolania2json.normalize import NORMALIZERS
        from odwolania2json.validate import VALIDATORS

        problems: list[str] = []
        for dt in self.doc_types.values():
            for sig in dt.classification.signals + dt.classification.negative_signals:
                _check_regex(sig.pattern, f"{dt.id}: classification", problems)
            for path, spec in dt.leaf_specs().items():
                where = f"{dt.id}: {path}"
                for p in spec.patterns:
                    _check_regex(p, where, problems)
                for n in spec.normalize:
                    if n not in NORMALIZERS:
                        problems.append(f"{where}: unknown normalizer '{n}'")
                for v in spec.validators:
                    if v not in VALIDATORS:
                        problems.append(f"{where}: unknown validator '{v}'")
                if spec.dictionary and spec.dictionary not in self.dictionaries:
                    problems.append(f"{where}: unknown dictionary '{spec.dictionary}'")
        return problems


def _check_regex(pattern: str, where: str, problems: list[str]) -> None:
    try:
        re.compile(pattern)
    except re.error as e:
        problems.append(f"{where}: invalid regex {pattern!r}: {e}")


def load_registry(config_dir: Path | None = None) -> Registry:
    config_dir = Path(config_dir or CONFIG_DIR)
    doc_types: dict[str, DocTypeSpec] = {}
    for f in sorted((config_dir / "doc_types").glob("*.y*ml")):
        raw = yaml.safe_load(f.read_text(encoding="utf-8"))
        try:
            spec = DocTypeSpec.model_validate(raw)
        except Exception as e:  # noqa: BLE001 — re-raise with the file name for a readable error
            raise ValueError(f"Invalid document class config {f.name}: {e}") from e
        if spec.enabled:
            doc_types[spec.id] = spec
    dictionaries: dict[str, Dictionary] = {}
    for f in sorted((config_dir / "dictionaries").glob("*.y*ml")):
        d = Dictionary.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
        dictionaries[d.id] = d
    return Registry(config_dir=config_dir, doc_types=doc_types, dictionaries=dictionaries)
