#!/usr/bin/env python3
"""
Visual diff of a KiCad board between two checkouts (old vs new), per board.

Renders each document with kicad-cli (works on the actual file format, no GUI /
KiAuto automation), rasterizes with PyMuPDF, and produces a red/green overlay
with Pillow:

    red   = present in OLD, gone in NEW   (removed)
    green = present in NEW, not in OLD     (added)
    grey  = unchanged

Outputs PNGs under <out>/<board>/ :
    pcb_front_p1.png   pcb_back_p1.png    (copper + silk + edge, per side)
    sch_p1.png ...                        (one per schematic page)

Usage (CI passes two checkout roots; e.g. a base git worktree and "."):
    python visual_diff.py --name e-foc --old-root ../base --new-root . --out diff
    python visual_diff.py --name tiva-80pin-adapter --old-root ../base --new-root .

Each root is a repo checkout; the board file is located under it automatically
(hardware/<name>/<name>.* preferred, falling back to hardware/<name>.*).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import fitz  # PyMuPDF
from PIL import Image
import numpy as np

CLI_CANDIDATES = [
    r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
]

# PCB views: (label, [layers]) — one composited page each.
PCB_VIEWS = [
    ("front", ["F.Cu", "F.Silkscreen", "Edge.Cuts"]),
    ("back",  ["B.Cu", "B.Silkscreen", "Edge.Cuts"]),
]

INK = 200        # luminance below this counts as "ink" (plotted dark on white)
DELTA = 24       # per-channel difference that counts as a change


def find_cli(override: str | None) -> str:
    if override:
        if not Path(override).exists():
            sys.exit(f"kicad-cli not found at: {override}")
        return override
    found = shutil.which("kicad-cli")
    if found:
        return found
    for c in CLI_CANDIDATES:
        if Path(c).exists():
            return c
    sys.exit("kicad-cli not found. Pass --kicad-cli <path>.")


def resolve(root: Path, name: str, ext: str) -> Path | None:
    """Find a board document under a checkout root, tolerating either layout."""
    for cand in (root / "hardware" / name / f"{name}.{ext}",
                 root / "hardware" / f"{name}.{ext}"):
        if cand.exists():
            return cand
    return None


def render_pdf(cli: str, kind: str, src: Path, out_pdf: Path,
               layers: list[str] | None) -> bool:
    if kind == "pcb":
        args = ["pcb", "export", "pdf", "-o", str(out_pdf),
                "--layers", ",".join(layers or []),
                "--mode-single", "--scale", "1", str(src)]
    else:
        args = ["sch", "export", "pdf", "-o", str(out_pdf), str(src)]
    res = subprocess.run([cli] + args, capture_output=True, text=True)
    if res.returncode != 0:
        print(f"    [render FAIL] {src.name}: {res.stderr.strip()[:200]}")
        return False
    return True


def pdf_to_images(pdf: Path, dpi: int) -> list[Image.Image]:
    if not pdf.exists():
        return []
    doc = fitz.open(pdf)
    imgs = []
    for page in doc:
        pix = page.get_pixmap(dpi=dpi)
        imgs.append(Image.frombytes("RGB", (pix.width, pix.height), pix.samples))
    doc.close()
    return imgs


def _pad(arr: np.ndarray, h: int, w: int) -> np.ndarray:
    out = np.full((h, w, 3), 255, np.uint8)
    out[:arr.shape[0], :arr.shape[1]] = arr
    return out


def diff_image(old: Image.Image | None, new: Image.Image | None) -> tuple[Image.Image, int]:
    """Red/green overlay. Returns (image, changed_pixel_count)."""
    if old is None and new is None:
        return Image.new("RGB", (8, 8), "white"), 0
    ref = new or old
    blank = np.full((ref.height, ref.width, 3), 255, np.uint8)
    o = np.asarray(old.convert("RGB")) if old else blank.copy()
    n = np.asarray(new.convert("RGB")) if new else blank.copy()
    h = max(o.shape[0], n.shape[0]); w = max(o.shape[1], n.shape[1])
    o = _pad(o, h, w); n = _pad(n, h, w)

    o_lum = o.mean(2); n_lum = n.mean(2)
    ink_o = o_lum < INK; ink_n = n_lum < INK
    changed = (np.abs(o.astype(int) - n.astype(int)).max(2) > DELTA)
    removed = ink_o & ~ink_n & changed
    added = ink_n & ~ink_o & changed

    out = np.full((h, w, 3), 255, np.uint8)
    # unchanged ink -> light grey context
    keep = (ink_o | ink_n) & ~(added | removed)
    out[keep] = (200, 200, 200)
    out[removed] = (220, 30, 30)
    out[added] = (30, 170, 30)
    return Image.fromarray(out, "RGB"), int(added.sum() + removed.sum())


def diff_doc(cli: str, kind: str, name: str, old_src: Path | None,
             new_src: Path | None, out_dir: Path, dpi: int) -> None:
    views = PCB_VIEWS if kind == "pcb" else [("", None)]
    with tempfile.TemporaryDirectory() as td:
        tdp = Path(td)
        for label, layers in views:
            old_imgs = new_imgs = []
            if old_src:
                p = tdp / f"old_{kind}_{label}.pdf"
                if render_pdf(cli, kind, old_src, p, layers):
                    old_imgs = pdf_to_images(p, dpi)
            if new_src:
                p = tdp / f"new_{kind}_{label}.pdf"
                if render_pdf(cli, kind, new_src, p, layers):
                    new_imgs = pdf_to_images(p, dpi)
            pages = max(len(old_imgs), len(new_imgs))
            for i in range(pages):
                o = old_imgs[i] if i < len(old_imgs) else None
                n = new_imgs[i] if i < len(new_imgs) else None
                img, changed = diff_image(o, n)
                stem = f"{kind}_{label}" if label else kind
                fname = f"{stem}_p{i + 1}.png"
                img.save(out_dir / fname)
                flag = f"{changed} px changed" if changed else "no change"
                print(f"    {name}/{fname}: {flag}")


def main() -> None:
    ap = argparse.ArgumentParser(description="Visual diff of a board: old vs new.")
    ap.add_argument("--name", default="e-foc", help="Board basename (default: e-foc)")
    ap.add_argument("--old-root", required=True, help="Old checkout root (e.g. base worktree)")
    ap.add_argument("--new-root", default=".", help="New checkout root (default: .)")
    ap.add_argument("--out", default="diff", help="Output dir (default: ./diff)")
    ap.add_argument("--dpi", type=int, default=150)
    ap.add_argument("--kicad-cli")
    args = ap.parse_args()

    cli = find_cli(args.kicad_cli)
    old_root = Path(args.old_root); new_root = Path(args.new_root)
    out_dir = Path(args.out) / args.name
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"kicad-cli : {cli}")
    print(f"diff      : {args.name}  (old={old_root}  new={new_root})")

    for kind, ext in (("pcb", "kicad_pcb"), ("sch", "kicad_sch")):
        old_src = resolve(old_root, args.name, ext)
        new_src = resolve(new_root, args.name, ext)
        if not old_src and not new_src:
            print(f"    [skip] no {ext} for {args.name}")
            continue
        diff_doc(cli, kind, args.name, old_src, new_src, out_dir, args.dpi)

    print(f"\nDone. Diff images in: {out_dir}")


if __name__ == "__main__":
    main()
