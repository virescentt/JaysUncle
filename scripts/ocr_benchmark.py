"""OCR benchmark on the real scans with gold JSON (data/uzp/dane/skany/<type>/<id>/<id>.pdf + <id>.json).

Metric — "gold value recall": the share of non-null gold leaf values (strings and numbers) that can be found
in the OCR text. Values that are not text by nature (fields with derived_from / enum / dictionary in the YAML
spec, booleans) are skipped. Matching:
  * text values: whitespace-collapsed, case-insensitive substring (quotes/dashes unified),
  * digit-like values (NIP, KRS, accounts, amounts, BZP numbers…): the same alphanumeric sequence, allowing
    separators (space . - / , :) between characters, not glued to other digits,
  * ISO dates (YYYY-MM-DD): also DD.MM.YYYY / D.M.YYYY and "D <miesiąc> YYYY" (Polish month names),
  * numbers: integer part ("7500") or with grosze ("7500,00").
"strict" keeps Polish diacritics, "folded" compares after folding them (ą→a, ł→l…) — the difference shows
how many values were lost only because of diacritics.

The OCR runs through the product code path (odwolania2json.ingest.ocr.get_ocr_engine + engine.ocr_page),
so a configuration is just a set of IngestSettings overrides:

    python scripts/ocr_benchmark.py                                   # default settings
    python scripts/ocr_benchmark.py --config v6-small-300 --config v5-latin-200
    python scripts/ocr_benchmark.py --config "my:ocr_dpi=200,ocr_preprocess=[grayscale,deskew]"
    python scripts/ocr_benchmark.py --list                            # presets
    python scripts/ocr_benchmark.py --types potwierdzenia --limit 3 --dump-text runs/ocr-texts --show-missed

Timings: wall seconds per page (render + preprocessing + OCR) and CPU seconds per page (all threads of this
process; more stable than wall time when the machine is busy).
"""

from __future__ import annotations

import argparse
import json
import re
import sys
import time
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium
import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from odwolania2json.models import Page  # noqa: E402
from odwolania2json.paths import flatten, generic  # noqa: E402
from odwolania2json.settings import IngestSettings, Settings  # noqa: E402
from odwolania2json.spec import DocTypeSpec, FieldSpec, load_registry  # noqa: E402

# --- configurations: name -> IngestSettings overrides --------------------------------------------------------
# "raw" model comparison: no retry pass, no Polish-letter fix (only the model / resolution differ)
_RAW = {"ocr_engine": "rapidocr", "ocr_preprocess": [], "ocr_retry_score": 0, "ocr_polish_letters": False}
# the full pipeline as configured by default (PP-OCRv6 small, 300 dpi, letter fix, retry of low-confidence lines)
_V6 = {"ocr_engine": "rapidocr", "ocr_model": "PP-OCRv6-small", "ocr_dpi": 300, "ocr_preprocess": []}

PRESETS: dict[str, dict[str, Any]] = {
    "default": {},  # config/settings.yaml as is
    # the pre-benchmark behaviour: PP-OCRv5 latin mobile, page rendered at 200 dpi and downscaled by RapidOCR
    # to 2000 px (longest side) before detection AND recognition
    "v5-latin-200": {
        **_RAW,
        "ocr_model": "PP-OCRv5-latin",
        "ocr_dpi": 200,
        "ocr_max_side": 2000,
        "ocr_det_side": 0,
        "ocr_rec_batch": 6,
    },
    "v5-latin-300": {**_RAW, "ocr_model": "PP-OCRv5-latin", "ocr_dpi": 300},
    "v6-tiny-300": {**_RAW, "ocr_model": "PP-OCRv6-tiny", "ocr_dpi": 300},
    "v6-small-200": {**_RAW, "ocr_model": "PP-OCRv6-small", "ocr_dpi": 200},
    "v6-small-300": {**_RAW, "ocr_model": "PP-OCRv6-small", "ocr_dpi": 300},
    "v6-medium-300": {**_RAW, "ocr_model": "PP-OCRv6-medium", "ocr_dpi": 300},
    # pipeline steps on top of PP-OCRv6 small @300 dpi
    "v6s-letters": {**_V6, "ocr_retry_score": 0, "ocr_polish_letters": True},
    "v6s-letters-retry": {**_V6, "ocr_retry_score": 0.9, "ocr_polish_letters": True},
    # RapidOCR's default batch of 6 lines (each padded to the longest one) instead of one line at a time
    "v6s-batch6": {**_V6, "ocr_retry_score": 0.9, "ocr_polish_letters": True, "ocr_rec_batch": 6},
    "v6s-200-letters-retry": {**_V6, "ocr_dpi": 200, "ocr_retry_score": 0.9, "ocr_polish_letters": True},
    "v6s-nocls": {**_V6, "ocr_retry_score": 0.9, "ocr_polish_letters": True, "ocr_use_cls": False},
    "v6s-gray": {**_V6, "ocr_retry_score": 0.9, "ocr_polish_letters": True, "ocr_preprocess": ["grayscale"]},
    "v6s-contrast": {**_V6, "ocr_retry_score": 0.9, "ocr_polish_letters": True, "ocr_preprocess": ["autocontrast"]},
    "v6s-binarize": {**_V6, "ocr_retry_score": 0.9, "ocr_polish_letters": True, "ocr_preprocess": ["binarize"]},
    "v6s-deskew": {**_V6, "ocr_retry_score": 0.9, "ocr_polish_letters": True, "ocr_preprocess": ["deskew"]},
    "v6s-denoise": {**_V6, "ocr_retry_score": 0.9, "ocr_polish_letters": True, "ocr_preprocess": ["denoise"]},
    # other engines (EasyOCR: pip install -r requirements-ocr.txt; Tesseract: system binary + 'pol')
    "easyocr-300": {"ocr_engine": "easyocr", "ocr_dpi": 300, "ocr_preprocess": [], "ocr_polish_letters": False},
    "tesseract-300": {"ocr_engine": "tesseract", "ocr_dpi": 300, "ocr_preprocess": [], "ocr_polish_letters": False},
}

TYPES = ("odwolania", "pelnomocnictwa", "potwierdzenia")

# --- matching -------------------------------------------------------------------------------------------------
_QUOTES = str.maketrans({c: '"' for c in "„”“«»‟″"} | {c: "'" for c in "‘’‚`´"} | {c: "-" for c in "‐‑‒–—―−"})
_FOLD = str.maketrans({"ł": "l", "Ł": "L", "đ": "d", "Đ": "D", "ø": "o", "Ø": "O"})
_SEP = r"[\s.\-/,:]*"
MONTHS = [
    "stycznia",
    "lutego",
    "marca",
    "kwietnia",
    "maja",
    "czerwca",
    "lipca",
    "sierpnia",
    "września",
    "października",
    "listopada",
    "grudnia",
]


def fold_diacritics(s: str) -> str:
    s = unicodedata.normalize("NFKD", s.translate(_FOLD))
    return "".join(c for c in s if not unicodedata.combining(c))


def norm_text(s: str, fold: bool = False) -> str:
    s = unicodedata.normalize("NFC", s).translate(_QUOTES).casefold()
    if fold:
        s = fold_diacritics(s)
    return re.sub(r"\s+", " ", s).strip()


def _alnum_pattern(value: str) -> str:
    chars = [c for c in value if c.isalnum()]
    pat = _SEP.join(re.escape(c) for c in chars)
    if chars and chars[0].isdigit():
        pat = r"(?<![0-9])" + pat
    if chars and chars[-1].isdigit():
        pat += r"(?![0-9])"
    return pat


def _is_digit_like(s: str) -> bool:
    alnum = [c for c in s if c.isalnum()]
    digits = sum(c.isdigit() for c in alnum)
    return digits >= 4 and digits >= 0.6 * len(alnum)


def value_variants(value: Any) -> tuple[list[str], list[str]]:
    """(regex patterns over normalized text, plain substrings) that count as "found"."""
    patterns: list[str] = []
    plain: list[str] = []
    if isinstance(value, int | float):
        f = float(value)
        whole = int(f)
        if f.is_integer():
            patterns += [_alnum_pattern(str(whole)), _alnum_pattern(f"{whole}00")]
        else:
            patterns.append(_alnum_pattern(f"{f:.2f}"))
        return patterns, plain
    s = norm_text(str(value))
    if m := re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", s):
        y, mo, d = m.groups()
        patterns += [_alnum_pattern(f"{d}{mo}{y}"), _alnum_pattern(f"{y}{mo}{d}")]
        patterns.append(rf"(?<![0-9])0?{int(d)}[.\-/ ]+0?{int(mo)}[.\-/ ]+{y}(?![0-9])")
        if 1 <= int(mo) <= 12:
            plain.append(f"{int(d)} {MONTHS[int(mo) - 1]} {y}")
            plain.append(f"{d} {MONTHS[int(mo) - 1]} {y}")
        return patterns, plain
    if _is_digit_like(s):
        patterns.append(_alnum_pattern(s))
        return patterns, plain
    plain.append(s)
    return patterns, plain


def is_found(value: Any, text_norm: str, fold: bool) -> bool:
    patterns, plain = value_variants(value)
    if fold:
        patterns = [fold_diacritics(p) for p in patterns]
        plain = [fold_diacritics(p) for p in plain]
    return any(p in text_norm for p in plain) or any(re.search(p, text_norm) for p in patterns)


# --- gold -----------------------------------------------------------------------------------------------------
def spec_for(gold: dict, specs: dict[str, DocTypeSpec]) -> DocTypeSpec | None:
    for spec in specs.values():
        if set(spec.output) & set(gold):
            return spec
    return None


def textual(fs: FieldSpec | None) -> bool:
    if fs is None:
        return True
    return not (fs.derived_from or fs.enum or fs.dictionary or fs.type == "boolean")


def gold_values(gold: dict, spec: DocTypeSpec | None) -> list[tuple[str, Any]]:
    leaf = spec.leaf_specs() if spec else {}
    out = []
    for path, v in flatten(gold).items():
        if v is None or isinstance(v, bool) or not isinstance(v, str | int | float):
            continue
        if isinstance(v, str) and not v.strip():
            continue
        if textual(leaf.get(generic(path))):
            out.append((path, v))
    return out


# --- running --------------------------------------------------------------------------------------------------
@dataclass
class DocResult:
    doc: str
    type: str
    pages: int
    wall_s: float
    cpu_s: float
    values: list[tuple[str, Any, bool, bool]] = field(default_factory=list)  # path, value, strict, folded
    engine: str | None = None
    confidence: float | None = None


def parse_config(spec: str) -> tuple[str, dict[str, Any]]:
    name, _, rest = spec.partition(":")
    if name not in PRESETS and not rest:
        raise SystemExit(f"Unknown preset {name!r} (see --list); ad-hoc: 'name:key=value,key=value'")
    overrides = dict(PRESETS.get(name, {}))
    # split on commas that are not inside [...]
    for part in re.split(r",(?![^\[]*\])", rest) if rest else []:
        key, _, val = part.partition("=")
        overrides[key.strip()] = yaml.safe_load(val)
    return name, overrides


def find_docs(data: Path, types: list[str], limit: int | None) -> list[tuple[str, Path, Path]]:
    docs = []
    for t in types:
        dirs = sorted(d for d in (data / t).iterdir() if d.is_dir()) if (data / t).exists() else []
        for d in dirs[:limit]:
            pdfs = sorted(d.glob("*.pdf"))
            golds = sorted(d.glob("*.json"))
            if pdfs and golds:
                docs.append((t, pdfs[0], golds[0]))
    return docs


def make_engine(name: str, overrides: dict[str, Any]) -> tuple[Any, IngestSettings] | None:
    from odwolania2json.ingest.ocr import get_ocr_engine

    base = Settings.load().ingest.model_dump()
    settings = IngestSettings.model_validate({**base, **overrides, "cache_dir": None})
    engine = get_ocr_engine(settings)
    if engine is None:
        print(f"[{name}] OCR engine not available — skipped", file=sys.stderr)
        return None
    return engine, settings


def run_doc(
    name: str,
    engine: Any,
    settings: IngestSettings,
    doc: tuple[str, Path, Path],
    specs: dict[str, DocTypeSpec],
    max_pages: int | None,
    dump_dir: Path | None,
) -> DocResult:
    t, pdf_path, gold_path = doc
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    pdf = pdfium.PdfDocument(str(pdf_path))
    n = min(len(pdf), max_pages or settings.max_pages, settings.max_pages)
    sizes = [(pdf[i].get_width(), pdf[i].get_height()) for i in range(n)]
    pdf.close()
    texts, confs = [], []
    wall0, cpu0 = time.perf_counter(), time.process_time()
    used = None
    for i in range(n):
        page = Page(number=i + 1, text="", width=float(sizes[i][0]), height=float(sizes[i][1]))
        page = engine.ocr_page(pdf_path, i, page, settings)
        texts.append(page.text)
        used = page.ocr_engine or used
        if getattr(page, "ocr_confidence", None) is not None:
            confs.append(page.ocr_confidence)
    wall, cpu = time.perf_counter() - wall0, time.process_time() - cpu0
    text = "\n\n".join(texts)
    strict_norm, folded_norm = norm_text(text), norm_text(text, fold=True)
    res = DocResult(
        doc=f"{t}/{pdf_path.parent.name}",
        type=t,
        pages=n,
        wall_s=wall,
        cpu_s=cpu,
        engine=used,
        confidence=sum(confs) / len(confs) if confs else None,
    )
    for path, v in gold_values(gold, spec_for(gold, specs)):
        res.values.append((path, v, is_found(v, strict_norm, False), is_found(v, folded_norm, True)))
    if dump_dir is not None:
        out = dump_dir / name / t / f"{pdf_path.stem}.txt"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(text, encoding="utf-8")
    found = sum(v[2] for v in res.values)
    print(
        f"  [{name}] {res.doc}: {found}/{len(res.values)} values, {n} p, {wall / n:.2f} s/p wall, "
        f"{cpu / n:.2f} s/p cpu",
        file=sys.stderr,
    )
    return res


def run_all(
    configs: list[tuple[str, dict[str, Any]]],
    docs: list[tuple[str, Path, Path]],
    specs: dict[str, DocTypeSpec],
    max_pages: int | None,
    dump_dir: Path | None,
    interleave: bool,
) -> dict[str, list[DocResult]]:
    """Config by config, or (interleave) document by document with every config in turn — then all configs
    see the same machine load, which makes their timings comparable on a busy machine."""
    results: dict[str, list[DocResult]] = {}
    if not interleave:
        for name, overrides in configs:
            print(f"== {name}: {overrides}", file=sys.stderr)
            built = make_engine(name, overrides)
            if built:
                results[name] = [run_doc(name, *built, d, specs, max_pages, dump_dir) for d in docs]
        return results
    engines = {}
    for name, overrides in configs:
        print(f"== {name}: {overrides}", file=sys.stderr)
        if built := make_engine(name, overrides):
            engines[name] = built
            results[name] = []
    for d in docs:
        for name, built in engines.items():
            results[name].append(run_doc(name, *built, d, specs, max_pages, dump_dir))
    return results


def summarize(results: list[DocResult]) -> dict[str, Any]:
    def agg(rs: list[DocResult]) -> dict[str, Any]:
        vals = [v for r in rs for v in r.values]
        pages = sum(r.pages for r in rs) or 1
        return {
            "values": len(vals),
            "strict": sum(v[2] for v in vals) / len(vals) if vals else 0.0,
            "folded": sum(v[3] for v in vals) / len(vals) if vals else 0.0,
            "pages": pages,
            "wall_per_page": sum(r.wall_s for r in rs) / pages,
            "cpu_per_page": sum(r.cpu_s for r in rs) / pages,
        }

    by_type = defaultdict(list)
    for r in results:
        by_type[r.type].append(r)
    by_field: dict[str, list[int]] = defaultdict(lambda: [0, 0])
    for r in results:
        for path, _, strict, _ in r.values:
            key = f"{r.type}:{generic(path)}"
            by_field[key][0] += strict
            by_field[key][1] += 1
    return {
        "all": agg(results),
        "by_type": {t: agg(rs) for t, rs in sorted(by_type.items())},
        "by_field": {k: {"found": f, "total": n} for k, (f, n) in sorted(by_field.items())},
        "engine": next((r.engine for r in results if r.engine), None),
    }


def print_table(summaries: dict[str, dict[str, Any]]) -> None:
    types = sorted({t for s in summaries.values() for t in s["by_type"]})
    head = f"{'config':<26} {'engine':<26} {'ALL strict/fold':>16}"
    for t in types:
        head += f" {t[:14]:>16}"
    head += f" {'s/page':>7} {'cpu/p':>7}"
    print("\nGold value recall (strict / diacritics-folded), seconds per page")
    print(head)
    print("-" * len(head))
    for name, s in summaries.items():
        a = s["all"]
        row = f"{name:<26} {str(s['engine'])[:26]:<26} {a['strict']:>8.3f}/{a['folded']:.3f}"
        for t in types:
            b = s["by_type"].get(t)
            row += f" {b['strict']:>8.3f}/{b['folded']:.3f}" if b else f" {'-':>16}"
        row += f" {a['wall_per_page']:>7.2f} {a['cpu_per_page']:>7.2f}"
        print(row)
    n = next(iter(summaries.values()))["all"]["values"] if summaries else 0
    print(f"(values per config: {n})")


def print_missed(name: str, results: list[DocResult], max_rows: int = 200) -> None:
    print(f"\nMissed values (strict) — {name}")
    rows = 0
    for r in results:
        for path, v, strict, folded in r.values:
            if not strict:
                print(f"  {r.doc:<22} {generic(path):<45} {v!r}{'  (found when folded)' if folded else ''}")
                rows += 1
                if rows >= max_rows:
                    return


def main() -> None:
    ap = argparse.ArgumentParser(description="OCR benchmark: gold value recall on real scans")
    ap.add_argument("--data", type=Path, default=Path("data/uzp/dane/skany"))
    ap.add_argument("--config", action="append", default=None, help="preset or 'name:key=value,…' (repeatable)")
    ap.add_argument("--types", default=",".join(TYPES))
    ap.add_argument("--limit", type=int, default=None, help="documents per type")
    ap.add_argument("--max-pages", type=int, default=None, help="pages per document")
    ap.add_argument("--json", type=Path, default=None, help="save the summary (and per-document results)")
    ap.add_argument("--dump-text", type=Path, default=None, help="write OCR text: <dir>/<config>/<type>/<id>.txt")
    ap.add_argument("--show-missed", action="store_true")
    ap.add_argument("--interleave", action="store_true", help="document by document, all configs in turn (timing)")
    ap.add_argument("--list", action="store_true", help="list presets and exit")
    args = ap.parse_args()

    if args.list:
        for name, ov in PRESETS.items():
            print(f"{name:<24} {ov}")
        return
    if not args.data.exists():
        raise SystemExit(f"No data in {args.data} — run: python scripts/fetch_data.py")

    registry = load_registry()
    docs = find_docs(args.data, [t for t in args.types.split(",") if t], args.limit)
    configs = [parse_config(c) for c in (args.config or ["default"])]
    summaries: dict[str, dict[str, Any]] = {}
    all_results = run_all(configs, docs, registry.doc_types, args.max_pages, args.dump_text, args.interleave)
    for name, overrides in configs:
        if all_results.get(name):
            summaries[name] = summarize(all_results[name])
            summaries[name]["overrides"] = overrides
    print_table(summaries)
    if args.show_missed:
        for name, results in all_results.items():
            print_missed(name, results)
    if args.json:
        args.json.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summaries": summaries,
            "documents": {
                name: [
                    {
                        "doc": r.doc,
                        "pages": r.pages,
                        "wall_s": round(r.wall_s, 3),
                        "cpu_s": round(r.cpu_s, 3),
                        "found_strict": sum(v[2] for v in r.values),
                        "found_folded": sum(v[3] for v in r.values),
                        "values": len(r.values),
                        "missed": [[p, v] for p, v, s, _ in r.values if not s],
                    }
                    for r in rs
                ]
                for name, rs in all_results.items()
            },
        }
        args.json.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        print(f"\nSaved: {args.json}")


if __name__ == "__main__":
    main()
