"""Web API (FastAPI): processing, page previews, highlight boxes, class workshop.

Every test uses a TEMPORARY copy of config/ (create_app(config_dir=...)), so saving classes never touches the repo.
"""

import hashlib
import io
import json
import difflib
import re
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
import yaml
from fastapi.testclient import TestClient

from odwolania2json import CONFIG_DIR, PROJECT_ROOT
from odwolania2json.api.app import create_app
from odwolania2json.api.highlight import PageIndex, field_boxes, search_targets
from odwolania2json.api.workshop import TEMPLATE_YAML
from odwolania2json.models import Evidence, FieldResult, Word
from odwolania2json.spec import DocTypeSpec

DATA = PROJECT_ROOT / "data" / "uzp" / "dane"
SYNTH = DATA / "przyklad_01" / "pelnomocnictwo.pdf"
NEGATIVE = DATA / "negatywne" / "2025-OJS107-00364525-pl-ts.pdf"
TED_CLASS = PROJECT_ROOT / "docs" / "examples" / "ogloszenie_ted.yaml"
needs_data = pytest.mark.skipif(not (SYNTH.exists() and NEGATIVE.exists()), reason="run: python scripts/fetch_data.py")


@pytest.fixture()
def config_dir(tmp_path):
    d = tmp_path / "config"
    shutil.copytree(CONFIG_DIR, d)
    return d


@pytest.fixture()
def client(config_dir, tmp_path):
    app = create_app(config_dir, mode="rules", store_dir=tmp_path / "store")
    with TestClient(app) as c:
        yield c


def _upload(client, path: Path, name: str | None = None):
    with path.open("rb") as fh:
        return client.post("/api/process", files=[("files", (name or path.name, fh, "application/pdf"))])


def _tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for f in sorted(root.rglob("*")):
        if f.is_file():
            h.update(str(f.relative_to(root)).encode() + f.read_bytes())
    return h.hexdigest()


# --------------------------------------------------------------------------- status


def test_health_and_doc_types(client):
    h = client.get("/api/health").json()
    assert h["status"] == "ok" and h["mode"] == "rules" and h["llm_model"] is None
    assert {"odwolanie", "pelnomocnictwo", "przelew"} <= set(h["doc_types"])
    types = {t["id"]: t for t in client.get("/api/doc-types").json()}
    assert types["przelew"]["file"] == "przelew.yaml"
    assert "przelew_bankowy.kwota" in types["przelew"]["fields"]
    files = client.get("/api/doc-types", params={"all": "true"}).json()
    assert all(f["valid"] and f["active"] for f in files)


def test_ui_is_served_without_cdn(client):
    html = client.get("/").text
    assert "/static/app.js" in html
    for asset in ["/static/app.js", "/static/util.js", "/static/workshop.js", "/static/style.css"]:
        r = client.get(asset)
        assert r.status_code == 200
        assert not re.search(r"https?://", r.text), f"{asset}: external URL (the panel must work offline)"
    assert not re.search(r"(src|href)=\"https?://", html)


# --------------------------------------------------------------------------- documents


@needs_data
def test_process_synthetic_pdf(client):
    r = _upload(client, SYNTH)
    assert r.status_code == 200
    (doc,) = r.json()
    assert re.fullmatch(r"[a-f0-9]{16}", doc["id"])
    assert doc["filename"] == "pelnomocnictwo.pdf" and doc["json_filename"] == "pelnomocnictwo.json"
    assert doc["doc_type"] == "pelnomocnictwo" and doc["confidence"] >= 0.9
    assert doc["pages"] == len(doc["page_sizes"]) == 1
    page = doc["page_sizes"][0]
    assert page["width"] == pytest.approx(595.3, abs=1) and page["height"] == pytest.approx(841.9, abs=1)
    assert page["source"] == "text" and page["words"] > 50
    assert json.loads(doc["json_text"]) == doc["data"]  # exactly what the CLI writes
    assert doc["fields"] and all(isinstance(f["boxes"], list) for f in doc["fields"])
    for f in doc["fields"]:
        for b in f["boxes"]:
            x0, top, x1, bottom = b["bbox"]
            assert 0 <= x0 < x1 <= page["width"] and 0 <= top < bottom <= page["height"]
    assert client.get(f"/api/doc/{doc['id']}").json()["data"] == doc["data"]
    dl = client.get(f"/api/doc/{doc['id']}/json")
    assert dl.status_code == 200 and 'filename="pelnomocnictwo.json"' in dl.headers["content-disposition"]
    assert dl.text == doc["json_text"]


@needs_data
def test_process_negative_pdf_gives_empty_json(client):
    (doc,) = _upload(client, NEGATIVE, "inny dokument.pdf").json()
    assert doc["doc_type"] is None and doc["data"] is None and doc["fields"] == []
    assert doc["json_text"] == ""
    dl = client.get(f"/api/doc/{doc['id']}/json")
    assert dl.status_code == 200 and dl.content == b""
    assert "inny" in dl.headers["content-disposition"] and ".json" in dl.headers["content-disposition"]


@needs_data
def test_page_png(client):
    from PIL import Image

    (doc,) = _upload(client, SYNTH).json()
    r = client.get(f"/api/doc/{doc['id']}/page/1.png")
    assert r.status_code == 200 and r.headers["content-type"] == "image/png"
    img = Image.open(io.BytesIO(r.content))
    assert img.width == pytest.approx(595.3 * 110 / 72, abs=2)  # ~110 dpi
    assert client.get(f"/api/doc/{doc['id']}/page/1.png", params={"dpi": 72}).status_code == 200
    assert client.get(f"/api/doc/{doc['id']}/page/2.png").status_code == 404
    assert client.get("/api/doc/0123456789abcdef/page/1.png").status_code == 404
    assert client.delete(f"/api/doc/{doc['id']}").status_code == 204
    assert client.get(f"/api/doc/{doc['id']}/page/1.png").status_code == 404


def test_process_rejects_non_pdf(client):
    r = client.post("/api/process", files=[("files", ("notatka.txt", b"hello", "text/plain"))])
    assert r.status_code == 400 and "PDF" in r.json()["detail"]
    r = client.post("/api/process", files=[("files", ("fake.pdf", b"not a pdf at all", "application/pdf"))])
    assert r.status_code == 400


# --------------------------------------------------------------------------- class workshop: validation


def _validate(client, text: str) -> dict:
    r = client.post("/api/doc-types/validate", json={"yaml": text})
    assert r.status_code == 200
    return r.json()


def test_validate_good_yaml(client, config_dir):
    rep = _validate(client, (config_dir / "doc_types" / "pelnomocnictwo.yaml").read_text(encoding="utf-8"))
    assert rep["ok"] and rep["errors"] == [] and rep["id"] == "pelnomocnictwo"
    assert rep["summary"]["fields"] == 8
    assert _validate(client, TEMPLATE_YAML)["ok"]  # the "Nowa klasa" template is valid as is
    raw = client.post("/api/doc-types/validate", content=TEMPLATE_YAML, headers={"content-type": "text/plain"})
    assert raw.json()["ok"]  # raw YAML body works too (curl)


def test_validate_bad_yaml(client, config_dir):
    good = (config_dir / "doc_types" / "pelnomocnictwo.yaml").read_text(encoding="utf-8")

    rep = _validate(client, "id: x\n  name: [")
    assert not rep["ok"] and "składni YAML" in rep["errors"][0]["message"] and rep["errors"][0]["line"] == 2

    typo = good.replace("  min_score: 4", "  min_scor: 4", 1)
    rep = _validate(client, typo)
    (err,) = rep["errors"]
    assert "min_scor" in err["message"] and "nieznany klucz" in err["message"]
    assert typo.splitlines()[err["line"] - 1].strip().startswith("min_scor")

    rep = _validate(client, good.replace("normalize: [digits_only]", "normalize: [digits_onlyy]", 1))
    assert any("nieznany normalizator" in e["message"] and "digits_onlyy" in e["message"] for e in rep["errors"])

    rep = _validate(client, good.replace("'\\bmocodaw\\w*'", "'(mocodaw'", 1))
    assert any("wyrażenie regularne" in e["message"] for e in rep["errors"])

    rep = _validate(client, good.replace("id: pelnomocnictwo", "id: Zła-Klasa", 1))
    assert any("id" in e["message"] for e in rep["errors"])

    rep = _validate(client, good.replace("enabled: true", "enabled: false", 1))
    assert rep["ok"] and any("wyłączona" in w["message"] for w in rep["warnings"])


# --------------------------------------------------------------------------- class workshop: test + save


@needs_data
def test_doc_type_test_endpoint_uses_temporary_registry(client, config_dir):
    before = _tree_hash(config_dir)
    with NEGATIVE.open("rb") as fh:
        r = client.post(
            "/api/doc-types/test",
            data={"yaml": TED_CLASS.read_text(encoding="utf-8")},
            files={"file": ("ted.pdf", fh, "application/pdf")},
        )
    assert r.status_code == 200
    doc = r.json()
    assert doc["doc_type"] == "ogloszenie_ted" and doc["test"]["recognised"] and not doc["test"]["replaced"]
    assert doc["data"]["ogloszenie"]["numer_ted"] == "364525-2025"
    assert doc["test_doc_type"] == "ogloszenie_ted"
    assert client.get(f"/api/doc/{doc['id']}/page/1.png").status_code == 200  # viewable like any document
    # nothing saved, active classes unchanged
    assert _tree_hash(config_dir) == before
    assert "ogloszenie_ted" not in client.get("/api/health").json()["doc_types"]
    (again,) = _upload(client, NEGATIVE).json()
    assert again["doc_type"] is None

    with NEGATIVE.open("rb") as fh:
        bad = client.post(
            "/api/doc-types/test", data={"yaml": "id: x"}, files={"file": ("t.pdf", fh, "application/pdf")}
        )
    assert bad.status_code == 422 and bad.json()["errors"]


@needs_data
def test_put_saves_into_temp_config_and_reloads(client, config_dir):
    real_before = _tree_hash(CONFIG_DIR)
    ted = TED_CLASS.read_text(encoding="utf-8")

    r = client.put("/api/doc-types/ogloszenie_ted/yaml", json={"yaml": ted})
    assert r.status_code == 200, r.text
    assert r.json()["created"] and r.json()["file"] == "ogloszenie_ted.yaml"
    assert (config_dir / "doc_types" / "ogloszenie_ted.yaml").read_text(encoding="utf-8") == ted
    assert "ogloszenie_ted" in client.get("/api/health").json()["doc_types"]
    (doc,) = _upload(client, NEGATIVE).json()
    assert doc["doc_type"] == "ogloszenie_ted"  # pipeline reloaded: the new class is used right away
    assert client.get("/api/doc-types/ogloszenie_ted/yaml").text == ted

    # edit an existing class
    path = config_dir / "doc_types" / "pelnomocnictwo.yaml"
    edited = path.read_text(encoding="utf-8").replace("name: Pełnomocnictwo", "name: Pełnomocnictwo (edycja)", 1)
    r = client.put("/api/doc-types/pelnomocnictwo/yaml", json={"yaml": edited})
    assert r.status_code == 200 and not r.json()["created"]
    names = {t["id"]: t["name"] for t in client.get("/api/doc-types").json()}
    assert names["pelnomocnictwo"] == "Pełnomocnictwo (edycja)"

    # invalid YAML / id mismatch / bad id are refused and leave the file untouched
    r = client.put("/api/doc-types/pelnomocnictwo/yaml", json={"yaml": edited.replace("min_score", "min_scor")})
    assert r.status_code == 422 and r.json()["errors"]
    assert client.put("/api/doc-types/inna_klasa/yaml", json={"yaml": edited}).status_code == 422
    assert client.put("/api/doc-types/Zla-Klasa/yaml", json={"yaml": edited}).status_code == 400
    assert client.get("/api/doc-types/Zla-Klasa/yaml").status_code == 400
    assert client.get("/api/doc-types/nie_ma_takiej/yaml").status_code == 404
    assert path.read_text(encoding="utf-8") == edited

    assert _tree_hash(CONFIG_DIR) == real_before  # the repository config was never touched


def test_put_refused_in_readonly_mode(config_dir, tmp_path):
    before = _tree_hash(config_dir)  # (config/doc_types may already contain a nowa_klasa.yaml)
    app = create_app(config_dir, mode="rules", store_dir=tmp_path / "s", readonly=True)
    with TestClient(app) as c:
        r = c.put("/api/doc-types/nowa_klasa/yaml", json={"yaml": TEMPLATE_YAML.replace("Nowa klasa", "Inna", 1)})
        assert r.status_code == 403
        assert c.put("/api/doc-types/przelew/enabled", json={"enabled": False}).status_code == 403
    assert _tree_hash(config_dir) == before


def test_enabled_switch_flips_only_that_line(client, config_dir):
    real_before = _tree_hash(CONFIG_DIR)
    f = config_dir / "doc_types" / "przelew.yaml"
    original = f.read_text(encoding="utf-8")
    r = client.put("/api/doc-types/przelew/enabled", json={"enabled": False})
    assert r.status_code == 200, r.text
    assert r.json()["active"] is False and "przelew" not in client.get("/api/health").json()["doc_types"]
    changed = f.read_text(encoding="utf-8")
    assert changed == original.replace("enabled: true", "enabled: false", 1)  # comments and layout untouched
    assert client.put("/api/doc-types/przelew/enabled", json={"enabled": True}).status_code == 200
    assert f.read_text(encoding="utf-8") == original
    assert client.put("/api/doc-types/przelew/enabled", json={"enabled": "no"}).status_code == 400
    assert client.put("/api/doc-types/nie_ma/enabled", json={"enabled": True}).status_code == 404
    assert client.put("/api/doc-types/Zle-ID/enabled", json={"enabled": True}).status_code == 400
    assert _tree_hash(CONFIG_DIR) == real_before


# --------------------------------------------------------------------------- highlight boxes (unit)


def _line(y: float, texts: list[str], x: float = 50.0) -> list[Word]:
    """Words of one text line, 6 pt per character, 4 pt spaces."""
    out = []
    for t in texts:
        w = 6.0 * len(t)
        out.append(Word(t, (x, y, x + w, y + 10)))
        x += w + 4
    return out


def _page(*lines: list[Word]) -> dict[int, PageIndex]:
    return {1: PageIndex.build(1, [w for line in lines for w in line])}


def test_highlight_digits_ignore_separators():
    pages = _page(_line(100, ["KRS:", "0000123456,", "NIP:", "554-024-05-49,", "REGON:", "123456789"]))
    (box,) = field_boxes(FieldResult("x.nip", "5540240549", evidence=Evidence(1, "")), pages)
    nip_word = pages[1].words[3]
    assert box["kind"] == "value" and box["bbox"] == [round(c, 1) for c in nip_word.bbox]


def test_highlight_fuzzy_text_spanning_two_lines():
    pages = _page(_line(100, ["pn.", "„Zakup", "i", "dostawa"]), _line(114, ["sprzętu", "komputerowego”", "nr"]))
    fr = FieldResult("t", "Zakup i dostawa sprzetu komputerowego", evidence=Evidence(1, "pn. Zakup i dostawa"))
    boxes = field_boxes(fr, pages)
    assert [b["bbox"][1] for b in boxes] == [100, 114]  # one box per line, diacritics/quotes ignored
    assert boxes[1]["bbox"][2] < pages[1].words[-1].bbox[0]  # "nr" not included


def test_highlight_date_requires_exact_day():
    pages = _page(
        _line(100, ["Łódź,", "11", "marca", "2026"]), _line(130, ["czynność", "z", "18", "marca", "2026", "r."])
    )
    (box,) = field_boxes(FieldResult("d", "2026-03-18", evidence=Evidence(1, "")), pages)
    assert box["bbox"][1] == 130
    assert field_boxes(FieldResult("d", "2026-03-08", evidence=Evidence(1, "")), pages) == []


def test_highlight_prefers_occurrence_inside_snippet():
    pages = _page(_line(100, ["Jan", "Kowalski", "(prezes)"]), _line(200, ["pełnomocnik:", "Jan", "Kowalski"]))
    fr = FieldResult("p", "Jan Kowalski", evidence=Evidence(1, "pełnomocnik: Jan Kowalski"))
    (box,) = field_boxes(fr, pages)
    assert box["bbox"][1] == 200


def test_highlight_snippet_fallback_and_bbox_passthrough():
    pages = _page(_line(100, ["Zamawiający", "unieważnił", "postępowanie"]))
    fr = FieldResult("id", 3, raw_value=3, evidence=Evidence(1, "Zamawiający unieważnił postępowanie"))
    boxes = field_boxes(fr, pages)
    assert boxes and all(b["kind"] == "snippet" for b in boxes)
    fr = FieldResult("x", "abc", evidence=Evidence(2, "abc", (1.0, 2.0, 3.0, 4.0)))
    assert field_boxes(fr, pages) == [{"page": 2, "bbox": [1.0, 2.0, 3.0, 4.0], "kind": "bbox"}]


def test_highlight_without_words_or_value():
    assert field_boxes(FieldResult("x", "Warszawa", evidence=Evidence(1, "Warszawa")), {}) == []
    pages = _page(_line(100, ["Warszawa"]))
    assert field_boxes(FieldResult("x", None), pages) == []
    assert field_boxes(FieldResult("x", "opis", method="derived"), pages) == []
    # short values are matched only as whole words
    pages = _page(_line(100, ["Janusz", "Nowak"]))
    assert field_boxes(FieldResult("x", "Jan", evidence=Evidence(1, "")), pages) == []


def test_search_targets_forms():
    assert search_targets(15000.0, None)[:2] == ["15000.00", "15000"]
    assert "18 marca 2026" in search_targets("2026-03-18", None)
    assert search_targets(True, None) == []
    assert search_targets("554-024-05-49", "5540240549") == ["5540240549"]  # same normalized form once


# --------------------------------------------------------------------------- review regressions


def _bomb_yaml(levels: int) -> str:
    """A class whose output tree expands to 10**levels fields through YAML aliases (~120 bytes per level)."""
    keys = "abcdefghij"
    lines = ["id: bomb", "name: Bomb", "classification:", "  signals: [{pattern: x, weight: 5}]", "output:"]
    lines.append("  l0: &l0 {type: object, properties: {" + ", ".join(f"{k}: {{type: string}}" for k in keys) + "}}")
    for i in range(1, levels + 1):
        refs = ", ".join(f"{k}: *l{i - 1}" for k in keys)
        lines.append(f"  l{i}: &l{i} {{type: object, properties: {{{refs}}}}}")
    return "\n".join(lines) + "\n"


def test_validate_refuses_yaml_alias_bomb(client):
    # ~1 KB of YAML = 10**7 fields: before the fix pydantic walked all of them on the event loop (server frozen,
    # GBs of RAM; 4 levels already took 7 s)
    t = time.perf_counter()
    rep = _validate(client, _bomb_yaml(7))
    assert time.perf_counter() - t < 5
    assert not rep["ok"] and "elementów" in rep["errors"][0]["message"]
    rep = _validate(client, "id: x\nname: &a [*a]\n")
    assert not rep["ok"] and "cykliczne" in rep["errors"][0]["message"]
    rep = _validate(client, "id: x\nname: " + "[" * 3000 + "]" * 3000 + "\n")  # O(depth²) in PyYAML's scanner
    assert not rep["ok"] and "zagnieżdżenie" in rep["errors"][0]["message"]
    deep_block = "id: x\nname:\n" + "".join("  " * (i + 1) + f"k{i}:\n" for i in range(600))  # RecursionError
    assert "zagnieżdżenie" in _validate(client, deep_block)["errors"][0]["message"]
    assert _validate(client, _bomb_yaml(1))["ok"]  # anchors / aliases as such are fine
    assert client.get("/api/health").json()["status"] == "ok"


def test_class_id_must_be_a_safe_file_name(client, config_dir):
    before = sorted(p.name for p in (config_dir / "doc_types").iterdir())
    text = TEMPLATE_YAML.replace("id: nowa_klasa", 'id: "abc\\n"')
    # "^[a-z0-9_]+$" accepted "abc\n" ($ matches before a trailing newline) → file "abc\n.yaml" was created
    assert client.put("/api/doc-types/abc%0A/yaml", json={"yaml": text}).status_code == 400
    assert any(e["message"].startswith("id") for e in _validate(client, text)["errors"])
    con = TEMPLATE_YAML.replace("id: nowa_klasa", "id: con")  # Windows device name ("con.yaml" = console)
    assert client.put("/api/doc-types/con/yaml", json={"yaml": con}).status_code == 400
    assert not _validate(client, con)["ok"]
    assert sorted(p.name for p in (config_dir / "doc_types").iterdir()) == before
    assert client.get("/api/doc/0123456789abcdef%0A").status_code == 404


def test_request_body_limit(config_dir, tmp_path, monkeypatch):
    from odwolania2json.api import app as app_module

    monkeypatch.setattr(app_module, "MAX_REQUEST_BYTES", 4096)
    app = create_app(config_dir, mode="rules", store_dir=tmp_path / "store")
    with TestClient(app) as c:
        big = b"%PDF-1.4\n" + b"0" * 10_000
        r = c.post("/api/process", files=[("files", ("duzy.pdf", big, "application/pdf"))])
        assert r.status_code == 413 and "za duże" in r.json()["detail"]  # refused from Content-Length

        def chunks():  # no Content-Length (chunked): counted while streaming
            for _ in range(10):
                yield b"x" * 1000

        r = c.post("/api/doc-types/validate", content=chunks(), headers={"content-type": "text/plain"})
        assert r.status_code == 413
        assert len(app.state.o2j.store) == 0
        assert c.get("/api/health").json()["status"] == "ok"


def test_highlight_amount_cents_must_match():
    # digits alone: 1500.00 → "150000" also matched the unrelated number "150 000"
    pages = _page(_line(100, ["Wartość:", "150", "000", "zł"]), _line(120, ["Wpis:", "1", "500,00", "zł"]))
    (box,) = field_boxes(FieldResult("kwota", 1500.0, evidence=Evidence(1, "")), pages)
    assert box["bbox"][1] == 120
    pages = _page(_line(100, ["Wartość:", "150", "000", "zł"]))
    assert field_boxes(FieldResult("kwota", 1500.0, evidence=Evidence(1, "")), pages) == []
    pages = _page(_line(100, ["kwota", "1500", "zł"]))  # amount written without cents
    assert field_boxes(FieldResult("kwota", 1500.0, evidence=Evidence(1, "")), pages)


def test_highlight_prefers_whole_word_over_longer_word():
    pages = _page(_line(100, ["ul.", "Krakowska", "5,", "30-001", "Kraków"]))
    fr = FieldResult("miasto", "Kraków", evidence=Evidence(1, "ul. Krakowska 5, 30-001 Kraków"))
    (box,) = field_boxes(fr, pages)
    assert box["bbox"][0] == pages[1].words[-1].bbox[0]  # was: the street name "Krakowska"
    pages = _page(_line(100, ["pełnomocnictwa", "dla", "Jana", "Kowalskiego"]))  # inflected form still found
    (box,) = field_boxes(FieldResult("p", "Kowalski", evidence=Evidence(1, "")), pages)
    assert box["bbox"][0] == pages[1].words[-1].bbox[0]


def test_render_caps_huge_pages(tmp_path):
    import pypdfium2 as pdfium
    from PIL import Image

    from odwolania2json.api.store import MAX_RENDER_PIXELS, render_page_png

    pdf = pdfium.PdfDocument.new()
    pdf.new_page(14400, 14400)  # 200 × 200 inches (PDF maximum): 22000 × 22000 px at 110 dpi without the cap
    pdf.save(str(tmp_path / "huge.pdf"))
    pdf.close()
    out = render_page_png(tmp_path / "huge.pdf", 1, 110, tmp_path / "pages" / "1@110.png")
    with Image.open(out) as img:
        assert img.width * img.height <= MAX_RENDER_PIXELS
    assert not list((tmp_path / "pages").glob("*.tmp"))


@needs_data
def test_upload_stored_under_fixed_name(client, tmp_path):
    (doc,) = _upload(client, SYNTH, "CON.pdf").json()  # a Windows device name as the upload name
    stored = tmp_path / "store" / doc["id"]
    assert (stored / "document.pdf").is_file() and not (stored / "CON.pdf").exists()
    assert doc["doc_type"] == "pelnomocnictwo"
    assert doc["filename"] == "CON.pdf" and doc["json_filename"] == "CON.json"
    assert 'filename="CON.pdf"' in client.get(f"/api/doc/{doc['id']}/pdf").headers["content-disposition"]
    assert client.get(f"/api/doc/{doc['id']}/pdf").headers["x-content-type-options"] == "nosniff"


@needs_data
def test_json_text_is_the_exact_output_file(client, monkeypatch):
    from odwolania2json.api import app as app_module

    def write_crlf(path, data, unknown_output, indent):  # what Path.write_text produces on Windows
        path.write_bytes((json.dumps(data, ensure_ascii=False, indent=indent) + "\n").replace("\n", "\r\n").encode())

    monkeypatch.setattr(app_module, "write_result", write_crlf)
    (doc,) = _upload(client, SYNTH).json()
    assert "\r\n" in doc["json_text"]  # read_text() used to turn CRLF into LF → UI download ≠ CLI file
    assert doc["json_text"].encode("utf-8") == client.get(f"/api/doc/{doc['id']}/json").content


def test_save_keeps_line_endings_and_restores_bytes(client, config_dir, monkeypatch):
    from odwolania2json.api.app import AppState

    path = config_dir / "doc_types" / "pelnomocnictwo.yaml"
    crlf = path.read_bytes().replace(b"\r\n", b"\n").replace(b"\n", b"\r\n")  # Windows checkout
    path.write_bytes(crlf)
    edited = crlf.decode("utf-8").replace("\r\n", "\n").replace("name: Pełnomocnictwo", "name: Pełn. (edycja)", 1)
    r = client.put("/api/doc-types/pelnomocnictwo/yaml", json={"yaml": edited})
    assert r.status_code == 200, r.text
    saved = path.read_bytes()
    assert b"(edycja)" in saved and b"\n" not in saved.replace(b"\r\n", b"")  # still CRLF everywhere

    def broken_reload(self):
        raise RuntimeError("boom")

    monkeypatch.setattr(AppState, "reload", broken_reload)
    r = client.put("/api/doc-types/pelnomocnictwo/yaml", json={"yaml": edited.replace("(edycja)", "(2)")})
    assert r.status_code == 500 and "przywrócono" in r.json()["detail"]
    assert path.read_bytes() == saved  # restored byte for byte


def test_validate_checks_keyword_windows_and_few_shot(client):
    def with_extraction(line: str) -> str:
        return TEMPLATE_YAML.replace("extraction:\n", f"extraction:\n  {line}\n", 1)

    rep = _validate(client, with_extraction("keyword_windows: ['(niedomknięty']"))
    assert any("keyword_windows" in e["message"] for e in rep["errors"])
    for bad in ("../../.env", "/etc/passwd", "C:/sekrety.txt", "a/../../b.txt"):
        rep = _validate(client, with_extraction(f"few_shot: ['{bad}']"))
        assert any("few_shot" in e["message"] for e in rep["errors"]), bad
    assert _validate(client, with_extraction("few_shot: ['examples/odwolanie_1.txt']"))["ok"]


@needs_data
def test_previews_do_not_wait_for_document_processing(client):
    (doc,) = _upload(client, SYNTH).json()
    state = client.app.state.o2j
    release = threading.Event()
    blockers = [state.worker.submit(release.wait, 30) for _ in range(state.worker.max_workers)]
    try:  # every processing worker busy (e.g. long OCR) — a preview at a new zoom level must still come back
        t = time.perf_counter()
        r = client.get(f"/api/doc/{doc['id']}/page/1.png", params={"dpi": 150})
        assert r.status_code == 200 and time.perf_counter() - t < 10
    finally:
        release.set()
        for b in blockers:
            b.result(timeout=30)


@needs_data
def test_parallel_processing_matches_sequential(client, config_dir):
    settings = config_dir / "settings.yaml"  # no ingest cache: pdfplumber + pdfium really run in parallel
    settings.write_text(re.sub(r"(?m)^(\s*cache_dir:).*$", r"\1 null", settings.read_text(encoding="utf-8"), count=1))
    pdfs = [DATA / f"przyklad_0{i}" / n for i in (1, 2) for n in ("pelnomocnictwo.pdf", "przelew.pdf", "odwolanie.pdf")]
    sequential = [_upload(client, p).json()[0] for p in pdfs]
    with ThreadPoolExecutor(len(pdfs)) as ex:
        parallel = list(ex.map(lambda p: _upload(client, p).json()[0], pdfs))
    for a, b in zip(sequential, parallel, strict=True):
        assert not a["errors"] and not b["errors"]
        assert (a["doc_type"], a["json_text"], a["page_sizes"]) == (b["doc_type"], b["json_text"], b["page_sizes"])
        assert [f["boxes"] for f in a["fields"]] == [f["boxes"] for f in b["fields"]]


def test_bad_regex_reported_once(client, config_dir):
    # section regexes are checked by the workshop and (since spec._check_rule_hints) by Registry.check() too
    good = (config_dir / "doc_types" / "odwolanie.yaml").read_text(encoding="utf-8")
    section = re.search(r"start: '[^']+'", good).group(0)
    rep = _validate(client, good.replace(section, "start: '(niedomknięty'", 1))
    (err,) = rep["errors"]
    assert "(niedomknięty" in err["message"] and "wyrażenie regularne" in err["message"] and err["line"]


# --------------------------------------------------------------------------- class workshop: form editor (YAML ↔ JSON)


def _class_files(config_dir: Path) -> list[Path]:
    return [*sorted((config_dir / "doc_types").glob("*.y*ml")), TED_CLASS]


def test_parse_dump_roundtrip_keeps_every_class(client, config_dir):
    for f in _class_files(config_dir):
        text = f.read_text(encoding="utf-8")
        parsed = client.post("/api/doc-types/parse", json={"yaml": text}).json()
        assert parsed["ok"] and parsed["data"]["id"] == parsed["id"], f.name
        assert parsed["roundtrip_ok"], f.name
        assert parsed["losses"] == [], f.name  # the form saves by merging into the file: comments / anchors stay
        r = client.post("/api/doc-types/dump", json={"data": parsed["data"]})
        assert r.status_code == 200, r.text
        dumped = r.json()["yaml"]
        assert list(parsed["data"]) == list(yaml.safe_load(dumped)), f.name  # key order kept
        assert DocTypeSpec.model_validate(yaml.safe_load(dumped)) == DocTypeSpec.model_validate(yaml.safe_load(text)), (
            f.name
        )
        assert _validate(client, dumped)["ok"], f.name
        again = client.post("/api/doc-types/parse", json={"yaml": dumped}).json()
        assert again["data"] == parsed["data"] and again["losses"] == [], f.name

    anchored = "a: &x {b: 1}\nc: *x\nd: 'tekst # bez komentarza'\n"
    assert client.post("/api/doc-types/parse", json={"yaml": anchored}).json()["losses"] == []


def test_dump_with_base_keeps_comments_anchors_and_layout(client, config_dir):
    for f in _class_files(config_dir):
        text = f.read_text(encoding="utf-8")
        data = client.post("/api/doc-types/parse", json={"yaml": text}).json()["data"]
        same = client.post("/api/doc-types/dump", json={"data": data, "base": text}).json()["yaml"]
        assert same == text, f.name  # nothing changed in the form → the file byte for byte

        data["name"] += " (zmiana)"
        data["classification"]["signals"][0]["weight"] = 7
        data["classification"]["signals"].append({"pattern": "nowa fraza", "weight": 1})
        out = client.post("/api/doc-types/dump", json={"data": data, "base": text}).json()["yaml"]
        assert yaml.safe_load(out) == data, f.name
        changed = [ln for ln in difflib.ndiff(text.splitlines(), out.splitlines()) if ln[:1] in "+-"]
        assert len(changed) == 5, (f.name, changed)  # name (−/+), first signal (−/+), the new signal (+)
        assert [ln for ln in out.splitlines() if "#" in ln and ln.lstrip().startswith("#")] == [
            ln for ln in text.splitlines() if "#" in ln and ln.lstrip().startswith("#")
        ], f.name

    base = "a: &x { b: 1 }  # wspólne\nc: *x\nd: 2\n"
    out = client.post("/api/doc-types/dump", json={"data": {"a": {"b": 1}, "c": {"b": 1}, "d": 3}, "base": base})
    assert out.json()["yaml"] == base.replace("d: 2", "d: 3")  # anchor, alias and comment kept
    edited = client.post("/api/doc-types/dump", json={"data": {"a": {"b": 1}, "c": {"b": 5}, "d": 2}, "base": base})
    assert yaml.safe_load(edited.json()["yaml"]) == {"a": {"b": 1}, "c": {"b": 5}, "d": 2}  # alias edited alone
    assert client.post("/api/doc-types/dump", json={"data": {"a": 1}, "base": 5}).status_code == 400
    broken = client.post("/api/doc-types/dump", json={"data": {"a": 1}, "base": "a: ["}).json()["yaml"]
    assert yaml.safe_load(broken) == {"a": 1}  # unreadable base → a fresh dump


def test_parse_reports_unreadable_yaml_and_non_json_values(client):
    bad = client.post("/api/doc-types/parse", json={"yaml": "id: x\n  name: ["}).json()
    assert bad["data"] is None and not bad["ok"] and bad["errors"]
    dated = client.post(
        "/api/doc-types/parse", json={"yaml": TEMPLATE_YAML.replace("version: 1", "version: 2024-01-01")}
    )
    body = dated.json()
    assert body["data"]["version"] == "2024-01-01" and not body["roundtrip_ok"] and body["losses"]
    unknown = client.post("/api/doc-types/parse", json={"yaml": "id: a\nname: A\nfoo: [1]\n"}).json()
    assert unknown["data"]["foo"] == [1] and not unknown["ok"]  # unknown keys are kept in the data (and reported)
    assert client.post("/api/doc-types/dump", json={"data": [1, 2]}).status_code == 400
    assert (
        client.post(
            "/api/doc-types/dump", content=b"{not json", headers={"content-type": "application/json"}
        ).status_code
        == 400
    )


def test_doc_type_meta(client):
    meta = client.get("/api/doc-types/meta").json()
    assert meta["field_types"] == ["string", "integer", "number", "boolean", "date", "object", "array"]
    assert {"date_pl", "digits_only"} <= {n["name"] for n in meta["normalizers"]}
    assert {"nip", "iban_pl"} <= {v["name"] for v in meta["validators"]}
    assert "czynnosc_powod" in {d["id"] for d in meta["dictionaries"]}
    assert "head" in meta["scopes"] and "auto" in meta["grounds"]
    assert re.fullmatch(meta["id_pattern"], "nowa_klasa") and not re.fullmatch(meta["id_pattern"], "Zła klasa")
    if meta["extraction_modes"]:
        assert set(meta["extraction_modes"]) == {"rules", "llm", "hybrid"}
        assert meta["template"]["extraction"]["mode"] == "llm"  # a new class works with field descriptions only


def test_new_class_from_form_json_validates_and_saves(client, config_dir):
    data = client.get("/api/doc-types/meta").json()["template"]
    data["id"] = "umowa_najmu"
    data["name"] = "Umowa najmu"
    data["classification"]["signals"] = [{"pattern": re.escape("Umowa najmu (lokalu)"), "weight": 3, "scope": "head"}]
    data["output"]["strony"] = {
        "type": "array",
        "description": "Strony umowy.",
        "items": {"type": "object", "properties": {"nazwa": {"type": "string", "description": "Nazwa strony."}}},
    }
    text = client.post("/api/doc-types/dump", json={"data": data}).json()["yaml"]
    assert "Umowa najmu" in text and "\\u" not in text  # Polish characters written as they are
    assert "instructions: |" in text  # multi-line text as a readable block
    rep = _validate(client, text)
    assert rep["ok"], rep["errors"]
    assert rep["summary"]["fields"] == 3
    r = client.put("/api/doc-types/umowa_najmu/yaml", json={"yaml": text})
    assert r.status_code == 200 and r.json()["created"]
    assert (config_dir / "doc_types" / "umowa_najmu.yaml").read_text(encoding="utf-8") == text
