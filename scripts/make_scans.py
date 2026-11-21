"""Synthetic "scans" of the training PDFs — to measure the OCR path before we see real test data.

The hidden test set contains REAL cases (the organizers' example is a scanned PDF without a text layer).
This script renders every training PDF to images, degrades them like a scanner would, and saves image-only
PDFs + copies the gold JSONs:

    python scripts/make_scans.py --src data/uzp/dane --dst data/scans/dane --limit 20
    python -m evaluation.run_eval --data data/scans/dane

TODO(ingest): implement `degrade()` — slight rotation (±1.5°), gaussian noise, blur, JPEG artefacts,
              grey background, optional black redaction boxes (like the anonymized example).
"""

from __future__ import annotations

import argparse
import shutil
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image


def degrade(img: Image.Image) -> Image.Image:
    """TODO(ingest): make it look scanned. For now: grayscale only."""
    return img.convert("L")


def scan_pdf(src: Path, dst: Path, dpi: int = 200) -> None:
    pdf = pdfium.PdfDocument(str(src))
    pages = [degrade(page.render(scale=dpi / 72).to_pil()) for page in pdf]
    pdf.close()
    dst.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(dst, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=Path("data/uzp/dane"))
    ap.add_argument("--dst", type=Path, default=Path("data/scans/dane"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dpi", type=int, default=200)
    args = ap.parse_args()
    cases = sorted(d for d in args.src.iterdir() if d.is_dir())[: args.limit]
    for case in cases:
        for pdf in case.glob("*.pdf"):
            scan_pdf(pdf, args.dst / case.name / pdf.name, args.dpi)
        for gold in case.glob("*.json"):
            shutil.copy2(gold, args.dst / case.name / gold.name)
        print(f"✓ {case.name}")


if __name__ == "__main__":
    main()
