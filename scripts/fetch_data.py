"""Download the UZP training data (github.com/Urzad-Zamowien-Publicznych/hacknation-2026) into data/uzp/.

    python scripts/fetch_data.py                   # → data/uzp/{dane, oczekiwane-json, szablon-aplikacja}
    python scripts/fetch_data.py --sample-input 3  # + copy 3 cases into ./input (quick demo of ./start.sh)
    python scripts/fetch_data.py --force           # re-download

Stdlib only — works on Windows / macOS / Linux without git.
"""

from __future__ import annotations

import argparse
import io
import shutil
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
URL = "https://github.com/Urzad-Zamowien-Publicznych/hacknation-2026/archive/refs/heads/main.zip"
DEST = ROOT / "data" / "uzp"


def download(force: bool) -> None:
    if (DEST / "dane").exists() and not force:
        print(f"Dane już są w {DEST} (użyj --force, aby pobrać ponownie)")
        return
    print(f"Pobieranie {URL} …")
    with urllib.request.urlopen(URL, timeout=120) as r:
        payload = r.read()
    if DEST.exists():
        shutil.rmtree(DEST)
    DEST.mkdir(parents=True)
    with zipfile.ZipFile(io.BytesIO(payload)) as zf:
        for member in zf.infolist():
            parts = Path(member.filename).parts[1:]  # strip "hacknation-2026-main/"
            if not parts or ".." in parts:
                continue
            target = DEST.joinpath(*parts)
            if member.is_dir():
                target.mkdir(parents=True, exist_ok=True)
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(zf.read(member))
    n_cases = len([d for d in (DEST / "dane").iterdir() if d.is_dir()])
    print(f"OK: {n_cases} spraw treningowych w {DEST / 'dane'}")


def sample_input(n: int) -> None:
    cases = sorted(d for d in (DEST / "dane").iterdir() if d.is_dir())[:n]
    for case in cases:
        target = ROOT / "input" / case.name
        target.mkdir(parents=True, exist_ok=True)
        for pdf in case.glob("*.pdf"):
            shutil.copy2(pdf, target / pdf.name)
    tpl = DEST / "szablon-aplikacja" / "input" / "case1"
    if tpl.exists():  # real scanned appeal + negative example from the organizers' template
        shutil.copytree(tpl, ROOT / "input" / "szablon_case1", dirs_exist_ok=True)
    print(f"Skopiowano {len(cases)} spraw (+ szablon_case1) do {ROOT / 'input'}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--sample-input", type=int, default=0, metavar="N")
    args = ap.parse_args()
    download(args.force)
    if args.sample_input:
        sample_input(args.sample_input)
