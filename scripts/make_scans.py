"""Synthetic "scans" of the training PDFs — to measure the OCR path before we see real test data.

The hidden test set contains REAL cases (often scans, sometimes anonymised with black boxes). This script renders
every training PDF to images, degrades them like a scanner would, and saves image-only PDFs + copies the gold
JSONs:

    python scripts/make_scans.py --src data/uzp/dane --dst data/scans/dane --limit 20
    python scripts/make_scans.py --strength 1.5 --redact 0.3 --seed 7      # harsher, 30% pages with black boxes
    python -m evaluation.run_eval --data data/scans/dane

degrade(): grey paper, faint bleed-through of the mirrored page, slight rotation (±1.5°), blur, gaussian noise,
salt-and-pepper specks, JPEG artefacts, optionally black redaction boxes and 1-bit (fax-like) output.
"""

from __future__ import annotations

import argparse
import io
import random
import shutil
from pathlib import Path

import numpy as np
import pypdfium2 as pdfium
from PIL import Image, ImageFilter, ImageOps


def degrade(
    img: Image.Image, rng: random.Random | None = None, strength: float = 1.0, redact: float = 0.0
) -> Image.Image:
    """Make a rendered page look scanned. strength 0 = grayscale only; redact = probability of black boxes."""
    rng = rng or random.Random(0)
    g = img.convert("L")
    if strength <= 0:
        return g
    w, h = g.size
    nrng = np.random.default_rng(rng.randrange(1 << 30))
    page = np.asarray(g, dtype=np.float32)

    # ink not pure black, paper not pure white; faint bleed-through of the mirrored page
    paper = rng.uniform(228, 248)
    ink = rng.uniform(10, 45)
    arr = ink + (page / 255.0) * (paper - ink)
    if rng.random() < 0.5 * strength:
        ghost = np.asarray(ImageOps.mirror(g).filter(ImageFilter.GaussianBlur(1.5)), dtype=np.float32) / 255.0
        arr = np.minimum(arr, paper - (1 - ghost) * rng.uniform(25, 60) * min(strength, 1.5))

    # black redaction boxes (anonymisation)
    if rng.random() < redact:
        for _ in range(rng.randint(1, 4)):
            bw, bh = rng.uniform(0.15, 0.4) * w, rng.uniform(0.012, 0.03) * h
            x0, y0 = rng.uniform(0.08, 0.9 - bw / w) * w, rng.uniform(0.05, 0.9) * h
            arr[int(y0) : int(y0 + bh), int(x0) : int(x0 + bw)] = rng.uniform(0, 25)

    # sensor noise + specks
    arr = arr + nrng.normal(0, rng.uniform(2, 8) * strength, arr.shape)
    specks = nrng.random(arr.shape) < 0.0004 * strength
    arr[specks] = 0
    out = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))

    # slight rotation and blur
    angle = rng.uniform(-1.5, 1.5) * min(strength, 1.5)
    out = out.rotate(angle, resample=Image.BICUBIC, fillcolor=int(paper), expand=False)
    out = out.filter(ImageFilter.GaussianBlur(rng.uniform(0.2, 0.7) * strength))

    # 1-bit fax-like copy or JPEG artefacts
    if rng.random() < 0.2 * strength:
        thr = rng.uniform(120, 170)
        return out.point(lambda v: 255 if v > thr else 0).convert("1")
    buf = io.BytesIO()
    out.save(buf, "JPEG", quality=rng.randint(35, 75))
    return Image.open(io.BytesIO(buf.getvalue())).convert("L")


def scan_pdf(
    src: Path, dst: Path, dpi: int = 200, rng: random.Random | None = None, strength: float = 1.0, redact: float = 0.0
) -> None:
    pdf = pdfium.PdfDocument(str(src))
    pages = [degrade(page.render(scale=dpi / 72).to_pil(), rng, strength, redact) for page in pdf]
    pdf.close()
    dst.parent.mkdir(parents=True, exist_ok=True)
    pages[0].save(dst, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", type=Path, default=Path("data/uzp/dane"))
    ap.add_argument("--dst", type=Path, default=Path("data/scans/dane"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--strength", type=float, default=1.0, help="0 = grayscale only, 1 = typical scan, 2 = bad copy")
    ap.add_argument("--redact", type=float, default=0.15, help="probability of black redaction boxes on a page")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()
    rng = random.Random(args.seed)
    # synthetic cases (with gold) + negatives; dane/skany are real scans already
    cases = sorted(d for d in args.src.iterdir() if d.is_dir() and d.name != "skany")[: args.limit]
    for case in cases:
        for pdf in case.glob("*.pdf"):
            scan_pdf(pdf, args.dst / case.name / pdf.name, args.dpi, rng, args.strength, args.redact)
        for gold in case.glob("*.json"):
            shutil.copy2(gold, args.dst / case.name / gold.name)
        print(f"✓ {case.name}")


if __name__ == "__main__":
    main()
