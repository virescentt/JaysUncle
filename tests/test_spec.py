"""Configuration (config/doc_types/*.yaml) ↔ structure expected by the organizers."""

import json
from pathlib import Path

import pytest

from odwolania2json import PROJECT_ROOT
from odwolania2json.paths import flatten
from odwolania2json.spec import load_registry

DATA = PROJECT_ROOT / "data" / "uzp" / "dane" / "przyklad_42"
GOLD = {"odwolanie": "odwolanie.json", "pelnomocnictwo": "pelnomocnictwo.json", "przelew": "przelew.json"}


@pytest.fixture(scope="module")
def registry():
    return load_registry()


def test_config_loads_and_is_valid(registry):
    assert set(registry.doc_types) >= {"odwolanie", "pelnomocnictwo", "przelew"}
    assert registry.check() == []


def _keys(data, prefix=""):
    """Set of key paths without indices (structure only)."""
    out = set()
    if isinstance(data, dict):
        for k, v in data.items():
            p = f"{prefix}.{k}" if prefix else k
            out.add(p)
            out |= _keys(v, p)
    elif isinstance(data, list):
        for v in data:
            out |= _keys(v, prefix + "[]")
    return out


@pytest.mark.skipif(not DATA.exists(), reason="run: python scripts/fetch_data.py")
@pytest.mark.parametrize("doc_type", list(GOLD))
def test_output_structure_matches_gold(registry, doc_type):
    gold = json.loads((DATA / GOLD[doc_type]).read_text(encoding="utf-8"))
    ours = registry.doc_types[doc_type].empty_output()
    assert _keys(ours) == _keys(gold)


def test_build_output_creates_list_items(registry):
    spec = registry.doc_types["odwolanie"]
    data = spec.build_output({"Zamawiajacy[1].nip": "123", "Odwolujacy[3].nip": "x"})
    assert len(data["Zamawiajacy"]) == 2 and data["Zamawiajacy"][1]["nip"] == "123"
    assert len(data["Odwolujacy"]) == 1  # max_items: 1 → extra items dropped


def test_llm_schema_skips_derived_fields(registry):
    schema = registry.doc_types["odwolanie"].json_schema(registry.dictionaries)
    props = schema["properties"]["OdwolanieKIO"]["properties"]
    assert "czynnosc_opis" not in props  # derived from czynnosc_powod_id
    assert 9 in props["czynnosc_powod_id"]["enum"]


def test_flatten_paths():
    assert flatten({"a": [{"b": 1}, {"b": None}], "c": {"d": "x"}}) == {
        "a[0].b": 1,
        "a[1].b": None,
        "c.d": "x",
    }


def test_project_root():
    assert (Path(PROJECT_ROOT) / "config" / "settings.yaml").exists()
