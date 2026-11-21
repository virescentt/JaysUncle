"""Field-level precision / recall / F1 between predicted and gold JSON files.

Our approximation of the organizers' F1 (exact rules unknown — ask the mentors!):
  * every leaf field is compared after normalization (whitespace; numbers 200 == 200.0),
  * gold value & equal prediction → TP;  gold value & null → FN;  null gold & predicted value → FP;
    gold value & different prediction → FP + FN;  both null → ignored,
  * list items (Odwolujacy, mocodawca, pelnomocnicy, …) are aligned by similarity, not by position,
  * documents inside a case are matched by type (top-level keys), so a misclassified document counts as
    a missing document (all FN) + an extra document (all FP).
  --lenient : also ignore case, quotes and punctuation;  --fuzzy 0.9 : accept strings with similarity ≥ 90%.

Usage:
  python -m evaluation.score --pred runs/<run>/output --gold data/uzp/dane [--lenient] [--fuzzy 0.9] [--show 5]
"""

from __future__ import annotations

import argparse
import json
import re
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rapidfuzz import fuzz

from odwolania2json.paths import flatten, generic

KNOWN_TYPES = {"OdwolanieKIO": "odwolanie", "mocodawca": "pelnomocnictwo", "przelew_bankowy": "przelew"}


def doc_type(data: Any) -> str:
    if not isinstance(data, dict) or not data:
        return "?"
    for k in data:
        if k in KNOWN_TYPES:
            return KNOWN_TYPES[k]
    return "|".join(sorted(data))


def norm(v: Any, lenient: bool = False) -> str | None:
    if v is None:
        return None
    if isinstance(v, bool):
        s = "true" if v else "false"
    elif isinstance(v, int | float):
        f = float(v)
        s = str(int(f)) if f.is_integer() else f"{f:.2f}".rstrip("0").rstrip(".")
    else:
        s = re.sub(r"\s+", " ", unicodedata.normalize("NFC", str(v))).strip()
    if not s:
        return None
    if lenient:
        s = s.casefold()
        s = re.sub(r"[\"'„”“«»`]", "", s)
        s = re.sub(r"[\s.,;:\-–—/()]+", " ", s).strip()
    return s or None


@dataclass
class Counts:
    tp: int = 0
    fp: int = 0
    fn: int = 0

    def add(self, other: Counts) -> None:
        self.tp, self.fp, self.fn = self.tp + other.tp, self.fp + other.fp, self.fn + other.fn

    @property
    def precision(self) -> float:
        return self.tp / (self.tp + self.fp) if self.tp + self.fp else 0.0

    @property
    def recall(self) -> float:
        return self.tp / (self.tp + self.fn) if self.tp + self.fn else 0.0

    @property
    def f1(self) -> float:
        p, r = self.precision, self.recall
        return 2 * p * r / (p + r) if p + r else 0.0

    def as_dict(self) -> dict:
        return {"tp": self.tp, "fp": self.fp, "fn": self.fn, "precision": round(self.precision, 4),
                "recall": round(self.recall, 4), "f1": round(self.f1, 4)}  # fmt: skip


@dataclass
class Scorer:
    lenient: bool = False
    fuzzy: float | None = None
    total: Counts = field(default_factory=Counts)
    by_type: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    by_field: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    by_case: dict[str, Counts] = field(default_factory=lambda: defaultdict(Counts))
    errors: dict[str, list[dict]] = field(default_factory=lambda: defaultdict(list))
    doc_stats: dict[str, int] = field(default_factory=lambda: defaultdict(int))

    # ------------------------------------------------------------------ comparison
    def equal(self, g: str, p: str) -> bool:
        if g == p:
            return True
        return bool(self.fuzzy) and fuzz.ratio(g, p) >= self.fuzzy * 100

    def similarity(self, a: Any, b: Any) -> int:
        fa, fb = flatten(a), flatten(b)
        return sum(
            1
            for k, v in fb.items()
            if (g := norm(v, self.lenient)) is not None
            and (p := norm(fa.get(k), self.lenient)) is not None
            and self.equal(g, p)
        )

    def align(self, pred: Any, gold: Any) -> Any:
        """Reorder list items in pred to best match gold (greedy by similarity)."""
        if isinstance(pred, dict) and isinstance(gold, dict):
            return {k: (self.align(v, gold[k]) if k in gold else v) for k, v in pred.items()}
        if isinstance(pred, list) and isinstance(gold, list) and pred and gold:
            if all(isinstance(x, dict) for x in pred + gold):
                sims = sorted(
                    ((self.similarity(p, g), -i, -j, i, j) for i, p in enumerate(pred) for j, g in enumerate(gold)),
                    reverse=True,
                )
                mapping, used = {}, set()
                for *_, i, j in sims:
                    if i not in used and j not in mapping:
                        mapping[j] = i
                        used.add(i)
                out = [self.align(pred[mapping[j]], gold[j]) if j in mapping else nullify(gold[j])
                       for j in range(len(gold))]  # fmt: skip
                return out + [pred[i] for i in range(len(pred)) if i not in used]
        return pred

    def compare(self, pred: Any, gold: Any, dtype: str, case: str, name: str) -> Counts:
        c = Counts()
        fp_, fg = flatten(self.align(pred, gold)), flatten(gold)
        for path in list(fg) + [k for k in fp_ if k not in fg]:
            g, p = norm(fg.get(path), self.lenient), norm(fp_.get(path), self.lenient)
            if g is None and p is None:
                continue
            key = f"{dtype}:{generic(path)}"
            fc = Counts()
            if g is None:
                fc.fp = 1
            elif p is None:
                fc.fn = 1
            elif self.equal(g, p):
                fc.tp = 1
            else:
                fc.fp = fc.fn = 1
            if not fc.tp:
                self.errors[key].append({"case": case, "file": name, "gold": fg.get(path), "pred": fp_.get(path)})
            self.by_field[key].add(fc)
            c.add(fc)
        self.by_type[dtype].add(c)
        self.by_case[case].add(c)
        self.total.add(c)
        return c

    # ------------------------------------------------------------------ cases
    def score_case(self, pred_dir: Path, gold_dir: Path, case: str) -> None:
        # empty file / {} = "document not recognised" (negative) → not a document to compare
        gold = {n: d for n, d in load_jsons(gold_dir).items() if d}
        pred = {n: d for n, d in (load_jsons(pred_dir) if pred_dir.exists() else {}).items() if d}
        by_type_g, by_type_p = defaultdict(list), defaultdict(list)
        for n, d in gold.items():
            by_type_g[doc_type(d)].append((n, d))
        for n, d in pred.items():
            by_type_p[doc_type(d)].append((n, d))
        for t in set(by_type_g) | set(by_type_p):
            gs, ps = by_type_g.get(t, []), list(by_type_p.get(t, []))
            for gname, gdata in gs:
                if ps:
                    best = max(range(len(ps)), key=lambda i: self.similarity(ps[i][1], gdata))
                    pname, pdata = ps.pop(best)
                    self.doc_stats["matched"] += 1
                    self.compare(pdata, gdata, t, case, pname)
                else:
                    self.doc_stats["missing"] += 1
                    self.compare(nullify(gdata), gdata, t, case, f"(brak) {gname}")
            for pname, pdata in ps:
                self.doc_stats["extra"] += 1
                self.compare(pdata, nullify(pdata), t, case, pname)

    def report(self, show: int = 3) -> dict:
        fields = sorted(self.by_field.items(), key=lambda kv: kv[1].fp + kv[1].fn, reverse=True)
        return {
            "mode": {"lenient": self.lenient, "fuzzy": self.fuzzy},
            "total": self.total.as_dict(),
            "documents": dict(self.doc_stats),
            "by_type": {k: v.as_dict() for k, v in sorted(self.by_type.items())},
            "by_field": {k: v.as_dict() for k, v in fields},
            "by_case": {k: round(v.f1, 4) for k, v in sorted(self.by_case.items())},
            "errors": {k: self.errors[k][:show] for k, _ in fields if self.errors.get(k)},
        }


def nullify(data: Any) -> Any:
    if isinstance(data, dict):
        return {k: nullify(v) for k, v in data.items()}
    if isinstance(data, list):
        return [nullify(v) for v in data]
    return None


def load_jsons(folder: Path) -> dict[str, Any]:
    out = {}
    for f in sorted(Path(folder).glob("*.json")):
        try:
            data = json.loads(f.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {}
        if isinstance(data, dict):
            out[f.name] = data
    return out


def find_cases(gold_root: Path) -> list[str]:
    """Case = folder containing gold JSON files or PDFs (relative path).
    A folder with PDFs but no gold JSON (e.g. dane/negatywne) = only negative documents: any non-empty
    prediction there counts as false positives."""
    root = Path(gold_root)
    dirs = {f.parent for f in root.rglob("*") if f.is_file() and f.suffix.lower() in (".json", ".pdf")}
    return sorted(str(d.relative_to(root)) for d in dirs)


def score_dirs(pred_root: Path, gold_root: Path, cases: list[str] | None = None, lenient: bool = False,
               fuzzy: float | None = None, show: int = 3) -> dict:  # fmt: skip
    scorer = Scorer(lenient=lenient, fuzzy=fuzzy)
    for case in cases or find_cases(gold_root):
        scorer.score_case(Path(pred_root) / case, Path(gold_root) / case, case)
    return scorer.report(show)


def print_report(rep: dict, top_fields: int = 15) -> None:
    from rich.console import Console
    from rich.table import Table

    con = Console()
    t = rep["total"]
    con.print(f"\n[bold]F1 = {t['f1']:.4f}[/]  (P={t['precision']:.4f}  R={t['recall']:.4f}  "
              f"TP={t['tp']} FP={t['fp']} FN={t['fn']})  dokumenty: {rep['documents']}")  # fmt: skip
    tt = Table("typ", "F1", "P", "R", "TP", "FP", "FN", title="Według typu dokumentu")
    for k, v in rep["by_type"].items():
        tt.add_row(k, f"{v['f1']:.3f}", f"{v['precision']:.3f}", f"{v['recall']:.3f}",
                   str(v["tp"]), str(v["fp"]), str(v["fn"]))  # fmt: skip
    con.print(tt)
    tf = Table("pole", "F1", "TP", "FP", "FN", "przykład błędu (gold → pred)", title="Najgorsze pola")
    for k, v in list(rep["by_field"].items())[:top_fields]:
        ex = rep["errors"].get(k, [{}])[0]
        sample = f"{ex.get('gold')!r} → {ex.get('pred')!r}" if ex else ""
        tf.add_row(k, f"{v['f1']:.3f}", str(v["tp"]), str(v["fp"]), str(v["fn"]), sample[:70])
    con.print(tf)


def main() -> None:
    ap = argparse.ArgumentParser(description="Field-level F1: predicted JSON vs gold JSON")
    ap.add_argument("--pred", type=Path, required=True)
    ap.add_argument("--gold", type=Path, default=Path("data/uzp/dane"))
    ap.add_argument("--lenient", action="store_true")
    ap.add_argument("--fuzzy", type=float, default=None)
    ap.add_argument("--show", type=int, default=5, help="error examples per field in the JSON report")
    ap.add_argument("--report", type=Path, default=None, help="save the JSON report here")
    args = ap.parse_args()
    rep = score_dirs(args.pred, args.gold, lenient=args.lenient, fuzzy=args.fuzzy, show=args.show)
    print_report(rep)
    if args.report:
        args.report.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
