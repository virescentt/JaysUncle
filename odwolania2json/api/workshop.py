"""Class workshop: view, validate, test and save document-class YAML files (config/doc_types/*.yaml) from the panel.

Validation = what the pipeline itself requires, reported in Polish with line numbers where possible:
  1. YAML syntax,
  2. `spec.DocTypeSpec` (pydantic, extra="forbid" → typos in keys are errors),
  3. `Registry.check()` semantics (regexes compile, normalizers / validators / dictionaries exist),
  4. a few generic structural checks (id format, regexes in `section` / `item_split` / `keyword_windows`, arrays
     without `items`, `derived_from` pointing to a sibling, `few_shot` paths inside config/, classes that can
     never be recognised → warnings).
YAML from the panel is untrusted: it is parsed once with yaml.SafeLoader and refused when alias expansion
("billion laughs": &anchor / *alias) would exceed MAX_YAML_NODES nodes, when aliases form a cycle, or when it is
nested too deeply — before pydantic walks the structure.
Nothing here is specific to one document class.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from odwolania2json.spec import Dictionary, DocTypeSpec, FieldSpec, Registry

ID_RE = re.compile(r"[a-z0-9_]{1,64}\Z")  # \Z, not $: "$" also accepts a trailing newline ("abc\n.yaml")
# file names Windows reserves for devices (also with an extension: "con.yaml" is the console)
_WINDOWS_RESERVED = {"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))}
MAX_YAML_CHARS = 512_000
MAX_YAML_NODES = 20_000  # after alias expansion; the largest real class (odwolanie.yaml) has < 500 nodes
MAX_YAML_FLOW_DEPTH = 64  # [ / { nesting; real classes use ≤ 3 — PyYAML's scanner is O(depth²) in it


def valid_class_id(value: Any) -> bool:
    """Class id usable as a file name on every OS: a-z, 0-9, _ (1–64 characters), not a Windows device name."""
    return isinstance(value, str) and bool(ID_RE.match(value)) and value not in _WINDOWS_RESERVED


class YamlLimitError(ValueError):
    """YAML refused before construction (too many nodes after alias expansion, alias cycle, too deep)."""


def _children(node: yaml.Node) -> list[yaml.Node]:
    if isinstance(node, yaml.MappingNode):
        return [n for pair in node.value for n in pair]
    if isinstance(node, yaml.SequenceNode):
        return list(node.value)
    return []


def _expanded_size(root: yaml.Node, limit: int) -> int:
    """Number of nodes after alias expansion (aliases are shared node objects). Capped at limit + 1.

    Iterative DFS with memoization — linear in the size of the composed graph, whatever the expansion.
    Raises YamlLimitError for an alias cycle (&a [*a]).
    """
    sizes: dict[int, int] = {}
    on_path: set[int] = set()
    stack: list[tuple[yaml.Node, bool]] = [(root, False)]
    while stack:
        node, done = stack.pop()
        key = id(node)
        if done:
            on_path.discard(key)
            sizes[key] = min(limit + 1, 1 + sum(sizes[id(c)] for c in _children(node)))
            continue
        if key in sizes:
            continue
        on_path.add(key)
        stack.append((node, True))
        for child in _children(node):
            if id(child) in on_path:
                raise YamlLimitError("cykliczne odwołanie aliasu (*alias wskazuje na własnego przodka)")
            if id(child) not in sizes:
                stack.append((child, False))
    return sizes[id(root)]


class _LimitedSafeLoader(yaml.SafeLoader):
    """SafeLoader that refuses deep flow nesting ([[[[… / {{{{…) while scanning.

    PyYAML's scanner keeps one "possible simple key" per flow level and re-checks all of them for every token,
    i.e. O(depth²): 3000 × "[" took ~4 s, a 500 KB file of them would take hours — checked in the scanner
    itself, so quotes / comments cannot hide anything from it.
    """

    _composed = 0

    def fetch_flow_collection_start(self, TokenClass):
        if self.flow_level >= MAX_YAML_FLOW_DEPTH:
            raise YamlLimitError(f"zbyt głębokie zagnieżdżenie nawiasów [ ] / {{ }} (maks. {MAX_YAML_FLOW_DEPTH})")
        super().fetch_flow_collection_start(TokenClass)

    def compose_node(self, parent, index):
        self._composed += 1  # stop composing early instead of building a huge graph first
        if self._composed > MAX_YAML_NODES:
            raise YamlLimitError(_TOO_MANY_NODES)
        return super().compose_node(parent, index)


_TOO_MANY_NODES = f"dokument ma ponad {MAX_YAML_NODES} elementów (licząc rozwinięte aliasy &kotwica / *alias)"


def load_yaml_limited(text: str) -> tuple[yaml.Node | None, Any]:
    """(composed root node, data) — yaml.safe_load with the limits described in the module docstring.

    Raises yaml.YAMLError for syntax errors and YamlLimitError when a limit is exceeded.
    """
    loader = _LimitedSafeLoader(text)
    try:
        try:
            root = loader.get_single_node()
        except RecursionError as e:
            raise YamlLimitError("zbyt głębokie zagnieżdżenie") from e
        if root is None:
            return None, None
        if _expanded_size(root, MAX_YAML_NODES) > MAX_YAML_NODES:
            raise YamlLimitError(_TOO_MANY_NODES)
        try:
            return root, loader.construct_document(root)
        except RecursionError as e:
            raise YamlLimitError("zbyt głębokie zagnieżdżenie") from e
    finally:
        loader.dispose()


_PL_ERRORS = {
    "extra_forbidden": "nieznany klucz (literówka? opis formatu: docs/NOWA_KLASA_DOKUMENTU.md)",
    "missing": "brak wymaganego klucza",
    "string_type": "oczekiwano tekstu",
    "int_type": "oczekiwano liczby całkowitej",
    "int_parsing": "oczekiwano liczby całkowitej",
    "int_from_float": "oczekiwano liczby całkowitej",
    "float_type": "oczekiwano liczby",
    "float_parsing": "oczekiwano liczby",
    "bool_type": "oczekiwano true albo false",
    "bool_parsing": "oczekiwano true albo false",
    "list_type": "oczekiwano listy",
    "dict_type": "oczekiwano mapy (klucz: wartość)",
    "model_type": "oczekiwano mapy (klucz: wartość)",
    "model_attributes_type": "oczekiwano mapy (klucz: wartość)",
}

_CHECK_MESSAGES = [
    (re.compile(r"unknown normalizer '(?P<name>.+)'$"), "nieznany normalizator „{name}”. Dostępne: {normalizers}"),
    (re.compile(r"unknown validator '(?P<name>.+)'$"), "nieznany walidator „{name}”. Dostępne: {validators}"),
    (re.compile(r"unknown dictionary '(?P<name>.+)'$"), "nieznany słownik „{name}”. Dostępne: {dictionaries}"),
    (
        re.compile(r"unknown strategy '(?P<name>[^']+)' \(known: (?P<known>.*)\)$"),
        "nieznana strategia „{name}”. Dostępne: {known}",
    ),
    (re.compile(r"invalid regex (?P<name>.+?): (?P<err>[^:]+)$"), "błędne wyrażenie regularne {name}: {err}"),
]


@dataclass
class Report:
    """Result of validating one YAML text."""

    errors: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[dict[str, Any]] = field(default_factory=list)
    spec: DocTypeSpec | None = None
    bad_regexes: set[str] = field(default_factory=set)  # already reported → not again from Registry.check()

    @property
    def ok(self) -> bool:
        return not self.errors

    def error(self, message: str, line: int | None = None) -> None:
        self.errors.append({"message": message, "line": line})

    def warn(self, message: str, line: int | None = None) -> None:
        self.warnings.append({"message": message, "line": line})

    def to_dict(self) -> dict[str, Any]:
        spec = self.spec
        summary = None
        if spec is not None:
            try:
                n_fields = len(spec.leaf_specs())
            except Exception:  # noqa: BLE001 — broken structure is already reported as an error
                n_fields = 0
            summary = {
                "fields": n_fields,
                "signals": len(spec.classification.signals),
                "negative_signals": len(spec.classification.negative_signals),
                "min_score": spec.classification.min_score,
                "enabled": spec.enabled,
            }
        return {
            "ok": self.ok,
            "id": spec.id if spec else None,
            "name": spec.name if spec else None,
            "errors": self.errors,
            "warnings": self.warnings,
            "summary": summary,
        }


# --------------------------------------------------------------------------- helpers: YAML positions


def _line_of(root: yaml.Node | None, loc: tuple[Any, ...]) -> int | None:
    """1-based line of the YAML node addressed by a pydantic error location (deepest existing node)."""
    node = root
    line = node.start_mark.line + 1 if node is not None else None
    for part in loc:
        if isinstance(node, yaml.MappingNode):
            hit = next(((k, v) for k, v in node.value if isinstance(k, yaml.ScalarNode) and k.value == str(part)), None)
            if hit is None:
                return line
            line, node = hit[0].start_mark.line + 1, hit[1]
        elif isinstance(node, yaml.SequenceNode) and isinstance(part, int) and 0 <= part < len(node.value):
            node = node.value[part]
            line = node.start_mark.line + 1
        else:
            return line
    return line


def _line_containing(text: str, needle: str) -> int | None:
    if not needle:
        return None
    for i, line in enumerate(text.splitlines(), start=1):
        if needle in line:
            return i
    return None


def _loc_str(loc: tuple[Any, ...]) -> str:
    out = ""
    for part in loc:
        out += f"[{part}]" if isinstance(part, int) else (f".{part}" if out else str(part))
    return out or "(cały dokument)"


def _pydantic_message(err: dict[str, Any]) -> str:
    typ = err.get("type", "")
    if typ == "literal_error":
        expected = (err.get("ctx") or {}).get("expected", "")
        return f"niedozwolona wartość — dozwolone: {expected}"
    return _PL_ERRORS.get(typ, err.get("msg", "błąd"))


# --------------------------------------------------------------------------- validation


def load_dictionaries(config_dir: Path) -> dict[str, Dictionary]:
    """Dictionaries of the config dir (broken files skipped — they are not the subject of this check)."""
    out: dict[str, Dictionary] = {}
    for f in sorted((Path(config_dir) / "dictionaries").glob("*.y*ml")):
        try:
            d = Dictionary.model_validate(yaml.safe_load(f.read_text(encoding="utf-8")))
        except Exception:  # noqa: BLE001
            continue
        out[d.id] = d
    return out


def _walk_fields(output: dict[str, FieldSpec]):
    """Yield (path, spec, siblings) for every field (objects and arrays included)."""

    def walk(spec: FieldSpec, path: str, siblings: dict[str, FieldSpec]):
        yield path, spec, siblings
        if spec.type == "object":
            for k, child in (spec.properties or {}).items():
                yield from walk(child, f"{path}.{k}", spec.properties or {})
        elif spec.type == "array" and spec.items is not None:
            yield from walk(spec.items, path + "[]", {})

    for key, spec in output.items():
        yield from walk(spec, key, output)


def _structural_checks(spec: DocTypeSpec, text: str, rep: Report) -> bool:
    """Generic checks the pydantic model does not do. Returns False if leaf_specs() would crash."""
    sound = True
    if not valid_class_id(spec.id):
        rep.error(
            f"id „{spec.id}”: dozwolone są tylko małe litery a-z, cyfry i podkreślenie (_), maks. 64 znaki "
            "(nie: con, prn, aux, nul, com1…, lpt1… — nazwy zarezerwowane w Windows)",
            _line_containing(text, "id:"),
        )
    for pattern in spec.extraction.keyword_windows:
        _check_regex(pattern, "extraction.keyword_windows", text, rep)
    for example in spec.extraction.few_shot:  # files read relative to config/ → must not point outside it
        absolute = example.startswith(("/", "\\")) or re.match(r"[A-Za-z]:", example) is not None
        if not example.strip() or absolute or ".." in re.split(r"[\\/]", example):
            rep.error(
                f"extraction.few_shot: „{example}” — podaj ścieżkę względną wewnątrz folderu config/ (bez „..”)",
                _line_containing(text, example) if example.strip() else None,
            )
    if not spec.output:
        rep.warn(
            "sekcja output jest pusta — wynikowy JSON nie będzie miał żadnych pól", _line_containing(text, "output")
        )
    for path, f, siblings in _walk_fields(spec.output):
        name = path.rsplit(".", 1)[-1].removesuffix("[]")
        line = _line_containing(text, f"{name}:")
        if f.type == "array" and f.items is None:
            rep.error(f"{path}: pole typu array wymaga klucza items (opis elementu listy)", line)
            sound = False
        if f.type == "object" and not f.properties:
            rep.warn(f"{path}: obiekt bez properties — nie wygeneruje żadnych pól", line)
        if f.section is not None:
            for key in ("start", "end"):
                pattern = getattr(f.section, key)
                if pattern:
                    _check_regex(pattern, f"{path}: section.{key}", text, rep)
        if f.item_split:
            _check_regex(f.item_split, f"{path}: item_split", text, rep)
        if f.derived_from:
            if f.derived_from not in siblings:
                rep.warn(f"{path}: derived_from wskazuje na „{f.derived_from}”, którego nie ma na tym poziomie", line)
            if not f.dictionary:
                rep.warn(f"{path}: pole pochodne (derived_from) bez słownika (dictionary) zawsze będzie null", line)
    cls = spec.classification
    positive = sum(s.weight for s in cls.signals if s.weight > 0)
    if not cls.signals:
        rep.warn("brak sygnałów klasyfikacji (classification.signals) — klasa nigdy nie zostanie rozpoznana")
    elif positive < cls.min_score:
        rep.warn(
            f"suma wag sygnałów ({positive:g}) jest mniejsza niż min_score ({cls.min_score:g}) — "
            "klasa nigdy nie zostanie rozpoznana",
            _line_containing(text, "min_score"),
        )
    if not spec.enabled:
        rep.warn("klasa jest wyłączona (enabled: false) — nie będzie używana przy przetwarzaniu dokumentów")
    return sound


def _check_regex(pattern: str, where: str, text: str, rep: Report) -> None:
    try:
        re.compile(pattern)
    except re.error as e:
        rep.bad_regexes.add(pattern)
        rep.error(f"{where}: błędne wyrażenie regularne {pattern!r}: {e}", _line_containing(text, pattern))


def _translate_check(problem: str, text: str, ctx: dict[str, str]) -> tuple[str, int | None]:
    """'pelnomocnictwo: a.b: unknown normalizer 'x'' → Polish message + line of the offending token.
    Unknown message formats are passed through unchanged."""
    # the tail may itself contain ": " (regex errors) → try the known patterns on progressively longer tails
    parts = problem.split(": ")
    for i in range(len(parts) - 1, 0, -1):
        tail = ": ".join(parts[i:])
        for rx, template in _CHECK_MESSAGES:
            m = rx.search(tail)
            if m:
                name = m.groupdict().get("name", "")
                msg = template.format(**ctx, **m.groupdict())
                where = ": ".join(parts[1:i]) if i > 1 else ""
                token = name.strip("'\"")
                line = _line_containing(text, token) or _line_containing(text, token.replace("\\\\", "\\"))
                return (f"{where}: {msg}" if where else msg), line
    return problem, None


def validate_yaml(text: str, dictionaries: dict[str, Dictionary], expected_id: str | None = None) -> Report:
    """Validate the YAML of one document class. `expected_id`: id required by the target (PUT /…/{id}/yaml)."""
    from odwolania2json.normalize import NORMALIZERS
    from odwolania2json.validate import VALIDATORS

    rep = Report()
    if not isinstance(text, str) or not text.strip():
        rep.error("pusty plik YAML")
        return rep
    if len(text) > MAX_YAML_CHARS:
        rep.error(f"plik YAML jest za duży (> {MAX_YAML_CHARS} znaków)")
        return rep
    try:
        root, raw = load_yaml_limited(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        line = mark.line + 1 if mark is not None else None
        col = f", kolumna {mark.column + 1}" if mark is not None else ""
        problem = getattr(e, "problem", None) or str(e)
        rep.error(f"błąd składni YAML{f' (linia {line}{col})' if line else ''}: {problem}", line)
        return rep
    except YamlLimitError as e:
        rep.error(f"YAML odrzucony: {e}")
        return rep
    if not isinstance(raw, dict):
        rep.error("YAML musi być mapą (klucz: wartość) z kluczami id, name, classification, output")
        return rep

    try:
        spec = DocTypeSpec.model_validate(raw)
    except ValidationError as e:
        for err in e.errors():
            loc = tuple(err.get("loc", ()))
            rep.error(f"{_loc_str(loc)}: {_pydantic_message(err)}", _line_of(root, loc))
        return rep
    except RecursionError:
        rep.error("YAML odrzucony: zbyt głębokie zagnieżdżenie pól")
        return rep
    rep.spec = spec

    sound = _structural_checks(spec, text, rep)
    if expected_id is not None and spec.id != expected_id:
        rep.error(
            f"id w YAML („{spec.id}”) różni się od zapisywanej klasy („{expected_id}”)", _line_containing(text, "id:")
        )
    if sound:
        ctx = {
            "normalizers": ", ".join(sorted(NORMALIZERS)) or "—",
            "validators": ", ".join(sorted(VALIDATORS)) or "—",
            "dictionaries": ", ".join(sorted(dictionaries)) or "—",
        }
        registry = Registry(config_dir=Path("."), doc_types={spec.id: spec}, dictionaries=dictionaries)
        try:
            problems = registry.check()
        except Exception as e:  # noqa: BLE001 — unexpected structure: report instead of crashing the request
            problems = [f"{spec.id}: {type(e).__name__}: {e}"]
        for p in problems:
            if "invalid regex" in p and any(repr(bad) in p for bad in rep.bad_regexes):
                continue  # the same regex is also checked by Registry.check()
            msg, line = _translate_check(p, text, ctx)
            rep.error(msg, line)
    return rep


# --------------------------------------------------------------------------- files


def _file_id(path: Path) -> str | None:
    try:
        _, raw = load_yaml_limited(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return None
    value = raw.get("id") if isinstance(raw, dict) else None
    return str(value) if value is not None else None


def doc_types_dir(config_dir: Path) -> Path:
    return Path(config_dir) / "doc_types"


def class_files(config_dir: Path) -> list[dict[str, Any]]:
    """Every YAML in <config_dir>/doc_types (also disabled and broken ones — so they can be fixed in the panel)."""
    out = []
    for f in sorted(doc_types_dir(config_dir).glob("*.y*ml")):
        item: dict[str, Any] = {"id": f.stem, "name": f.stem, "description": "", "file": f.name, "enabled": False}
        try:
            spec = DocTypeSpec.model_validate(load_yaml_limited(f.read_text(encoding="utf-8"))[1])
            item.update(
                id=spec.id,
                name=spec.name,
                description=spec.description,
                enabled=spec.enabled,
                valid=True,
                error=None,
                fields=list(spec.leaf_specs()),
                signals=len(spec.classification.signals),
            )
        except Exception as e:  # noqa: BLE001
            item.update(valid=False, error=str(e).splitlines()[0][:300], fields=[], signals=0)
        out.append(item)
    return out


def find_class_file(config_dir: Path, doc_id: str) -> Path | None:
    """The YAML file defining class `doc_id` (by its `id`), or `<doc_id>.yaml` if that file is unreadable."""
    if not valid_class_id(doc_id):
        return None
    d = doc_types_dir(config_dir)
    files = sorted(d.glob("*.y*ml"))
    for f in files:
        if f.stem == doc_id and _file_id(f) == doc_id:
            return f
    for f in files:
        if _file_id(f) == doc_id:
            return f
    for ext in (".yaml", ".yml"):
        f = d / f"{doc_id}{ext}"
        if f.exists() and _file_id(f) is None:
            return f
    return None


def _newline_style(target: Path, previous: bytes | None) -> str:
    """Keep the line endings of the edited file (or of its siblings for a new one): a save from the panel must
    not turn a whole file into a diff (Windows checkouts: CRLF with core.autocrlf, LF without)."""
    if previous is None:
        sibling = next(iter(sorted(target.parent.glob("*.y*ml"))), None)
        previous = sibling.read_bytes() if sibling is not None else b""
    return "\r\n" if b"\r\n" in previous else "\n"


def _atomic_write(target: Path, data: bytes) -> None:
    tmp = target.with_name(f".{target.name}.tmp")  # not matched by the *.y*ml glob of load_registry
    try:
        tmp.write_bytes(data)
        os.replace(tmp, target)
    finally:
        tmp.unlink(missing_ok=True)


def save_class_yaml(config_dir: Path, doc_id: str, text: str) -> tuple[Path, bytes | None]:
    """Atomically write the YAML of class `doc_id`. Returns (file, previous bytes or None if created).

    Raises FileExistsError if `<doc_id>.yaml` exists but defines another class.
    """
    target = find_class_file(config_dir, doc_id)
    if target is None:
        target = doc_types_dir(config_dir) / f"{doc_id}.yaml"
        if target.exists():
            raise FileExistsError(f"plik {target.name} definiuje inną klasę („{_file_id(target)}”)")
    target.parent.mkdir(parents=True, exist_ok=True)
    previous = target.read_bytes() if target.exists() else None
    content = text.replace("\r\n", "\n").replace("\r", "\n")
    if not content.endswith("\n"):
        content += "\n"
    content = content.replace("\n", _newline_style(target, previous))
    _atomic_write(target, content.encode("utf-8"))
    return target, previous


def restore_class_yaml(target: Path, previous: bytes | None) -> None:
    """Undo save_class_yaml byte for byte (used when the pipeline cannot be reloaded with the new file)."""
    if previous is None:
        target.unlink(missing_ok=True)
    else:
        _atomic_write(target, previous)


TEMPLATE_YAML = r"""# =============================================================================
#  Nowa klasa dokumentu — szablon. Jeden plik YAML = jedna klasa, bez zmian w kodzie.
#  1) zmień id (małe litery, cyfry, _) i nazwę,  2) dopisz sygnały rozpoznawania,
#  3) opisz pola wyniku,  4) „Testuj na PDF”,  5) „Zapisz”.
#  Pełny opis formatu: docs/NOWA_KLASA_DOKUMENTU.md
# =============================================================================
id: nowa_klasa
name: Nowa klasa dokumentu
description: Krótki opis — czym jest dokument i kto go wystawia.
version: 1
enabled: true

# --- 1. Rozpoznawanie: klasa wygrywa, gdy suma wag trafionych sygnałów ≥ min_score ---
classification:
  min_score: 4
  signals:                      # wyrażenia regularne, wielkość liter i polskie znaki bez znaczenia
    - { pattern: 'tytuł dokumentu', weight: 4, scope: head }   # head = początek dokumentu
    - { pattern: 'charakterystyczne sformułowanie', weight: 2 }
  negative_signals:             # odejmują punkty (np. żeby nie mylić z odwołaniem)
    - { pattern: '^\s*ODWOŁANIE\s*$', weight: 4, case_sensitive: true }

# --- 2. Wskazówki dla modelu językowego (opcjonalnie) ---
extraction:
  instructions: |
    - Przepisuj wartości dokładnie tak, jak w dokumencie; brak danych → null.

# --- 3. Struktura wyniku (kanoniczny JSON) ---
output:
  dokument:
    type: object
    properties:
      numer:
        type: string
        description: Numer dokumentu.
        patterns: ['[Nn]r\.?\s*(?P<value>[A-Z0-9][A-Z0-9/.\-]+)']
      data:
        type: date
        description: Data wystawienia dokumentu (YYYY-MM-DD).
        labels: [Data wystawienia, Data]
        normalize: [date_pl]
"""
