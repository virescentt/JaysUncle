"""Run the pipeline on the training set and score it (field-level F1).

    python -m evaluation.run_eval                        # all 60 cases, mode from settings/.env
    python -m evaluation.run_eval --limit 10 --mode rules
    python -m evaluation.run_eval --cases przyklad_01,przyklad_42
    python -m evaluation.run_eval --data data/scans/dane # synthetic scans (scripts/make_scans.py)

Results: runs/<timestamp>/{output/, traces/, report.json}; the latest report is also copied to
runs/latest_report.json. Put "F1 before → after" in every PR description.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from datetime import datetime
from pathlib import Path

from evaluation.score import find_cases, print_report, score_dirs
from odwolania2json.output import output_path_for, write_json
from odwolania2json.pipeline import Pipeline, find_pdfs
from odwolania2json.settings import Settings

BASELINE = Path("runs/baseline_report.json")


def main() -> None:
    ap = argparse.ArgumentParser(description="Pipeline on the training set + F1")
    ap.add_argument(
        "--data", type=Path, default=Path("data/uzp/dane"), help="cases: <data>/<case>/*.pdf + gold *.json"
    )
    ap.add_argument("--out", type=Path, default=None, help="run folder (default runs/<timestamp>)")
    ap.add_argument("--limit", type=int, default=None, help="only the first N cases")
    ap.add_argument("--cases", type=str, default=None, help="comma-separated case names")
    ap.add_argument("--mode", choices=["rules", "llm", "hybrid"], default=None)
    ap.add_argument("--no-run", action="store_true", help="only score an existing --out")
    ap.add_argument("--lenient", action="store_true")
    ap.add_argument("--fuzzy", type=float, default=None)
    ap.add_argument(
        "--save-baseline", action="store_true", help=f"save this result as the reference ({BASELINE}) — run on master"
    )
    args = ap.parse_args()

    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    if not args.data.exists():
        raise SystemExit(f"Brak danych w {args.data} — uruchom: python scripts/fetch_data.py")

    cases = find_cases(args.data)
    if args.cases:
        wanted = set(args.cases.split(","))
        cases = [c for c in cases if c in wanted]
    cases = cases[: args.limit]
    out = args.out or Path("runs") / datetime.now().strftime("%Y%m%d-%H%M%S")
    pred_root = out / "output"

    if not args.no_run:
        settings = Settings.load()
        if args.mode:
            settings.extraction.mode = args.mode
        pipe = Pipeline(settings)
        print(f"Tryb: {settings.extraction.mode} · LLM: {settings.llm.model if pipe.llm else 'brak'} · "
              f"spraw: {len(cases)} → {out}")  # fmt: skip
        t0 = time.perf_counter()
        n = 0
        for case in cases:
            for pdf in find_pdfs(args.data / case):
                res = pipe.process_file(pdf)
                if res.data is not None:
                    write_json(output_path_for(pdf, args.data, pred_root), res.data)
                write_json(output_path_for(pdf, args.data, out / "traces", ".trace.json"), res.to_trace())
                n += 1
        print(f"Przetworzono {n} PDF w {time.perf_counter() - t0:.1f}s")

    rep = score_dirs(pred_root, args.data, cases=cases, lenient=args.lenient, fuzzy=args.fuzzy)
    print_report(rep)
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.json").write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
    Path("runs").mkdir(exist_ok=True)
    Path("runs/latest_report.json").write_text(
        json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Raport: {out / 'report.json'}")
    compare_with_baseline(rep, save=args.save_baseline)


def compare_with_baseline(rep: dict, save: bool = False) -> None:
    """Print 'F1 before → after' (for the PR description) against runs/baseline_report.json."""
    f1 = rep["total"]["f1"]
    if save:
        BASELINE.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"Zapisano baseline: F1 = {f1:.4f} → {BASELINE}")
        return
    if not BASELINE.exists():
        print("Brak baseline — na master uruchom: python -m evaluation.run_eval --save-baseline")
        return
    base = json.loads(BASELINE.read_text(encoding="utf-8"))
    b = base["total"]["f1"]
    print(f"\nF1: {b:.4f} → {f1:.4f}  (Δ {f1 - b:+.4f})   ← wklej do opisu PR")
    changed = []
    for key, v in rep["by_field"].items():
        old = base["by_field"].get(key, {}).get("f1")
        if old is not None and abs(v["f1"] - old) >= 0.005:
            changed.append((v["f1"] - old, key, old, v["f1"]))
    for delta, key, old, new in sorted(changed, reverse=True)[:10]:
        print(f"   {key}: {old:.3f} → {new:.3f} ({delta:+.3f})")


if __name__ == "__main__":
    main()
