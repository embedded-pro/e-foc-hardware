#!/usr/bin/env python3
"""
Build a release bundle for a board: ONE zip per project containing everything a
consumer of the GitHub Release needs.

Per board, produces  ./release/<board>-<version>.zip  with:

  - schematic.pdf            full schematic, all sheets
  - pcb_copper_top.pdf       board views (one PDF each)
  - pcb_copper_bottom.pdf
  - pcb_silk_top.pdf
  - pcb_silk_bottom.pdf
  - gerbers/                 Gerber files + Excellon drill (PTH/NPTH separate)
  - bom.csv                  JLCPCB BOM   (Comment, Designator, Footprint, LCSC Part #)
  - cpl.csv                  JLCPCB CPL   (Designator, Val, Package, Mid X/Y, Rotation, Layer)

Every board lives under hardware/<board>/<board>.* (default board: e-foc).

Usage:
    python export_release.py                              # e-foc, version = today
    python export_release.py --name tiva-80pin-adapter
    python export_release.py --version v1.2.3
    python export_release.py --kicad-cli "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
"""

from __future__ import annotations

import argparse
import csv
import datetime as _dt
import os
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent
HW_DIR = PROJECT_ROOT / "hardware"
RELEASE_DIR = PROJECT_ROOT / "release"

# Set per-board by _resolve_project().
PROJECT_NAME = "e-foc"
SCH_FILE = HW_DIR / "e-foc" / "e-foc.kicad_sch"
PCB_FILE = HW_DIR / "e-foc" / "e-foc.kicad_pcb"
STAGE_DIR = RELEASE_DIR / "e-foc"
GERBER_DIR = STAGE_DIR / "gerbers"

LCSC_FIELDS = ["LCSC", "LCSC Part #", "JLCPCB Part #", "JLC", "JLCPCB"]

TECH_LAYERS = [
    "F.Paste", "B.Paste",
    "F.Silkscreen", "B.Silkscreen",
    "F.Mask", "B.Mask",
    "Edge.Cuts",
]

# Board "views" rendered to PDF (kept as PDF, no rasterization). Edge.Cuts is
# added to each for outline context. {COPPER} entries are filled at runtime.
PCB_VIEWS = [
    ("pcb_copper_top.pdf",    ["F.Cu", "Edge.Cuts"]),
    ("pcb_copper_bottom.pdf", ["B.Cu", "Edge.Cuts"]),
    ("pcb_silk_top.pdf",      ["F.Silkscreen", "Edge.Cuts"]),
    ("pcb_silk_bottom.pdf",   ["B.Silkscreen", "Edge.Cuts"]),
]

CLI_CANDIDATES = [
    r"C:\Program Files\KiCad\10.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\9.0\bin\kicad-cli.exe",
    r"C:\Program Files\KiCad\8.0\bin\kicad-cli.exe",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

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


def detect_copper_layers(pcb_path: Path) -> list[str]:
    fallback = ["F.Cu", "B.Cu"]
    try:
        text = pcb_path.read_text(encoding="utf-8")
    except Exception:  # noqa: BLE001
        return fallback
    start = text.find("(layers")
    if start == -1:
        return fallback
    depth, i = 0, start
    while i < len(text):
        if text[i] == "(":
            depth += 1
        elif text[i] == ")":
            depth -= 1
            if depth == 0:
                break
        i += 1
    block = text[start:i + 1]
    found = re.findall(r'\(\s*(\d+)\s+"([^"]+\.Cu)"\s+\w+', block)
    if not found:
        return fallback
    layers = [name for _, name in sorted(found, key=lambda t: int(t[0]))]
    if "B.Cu" in layers:
        layers = [l for l in layers if l != "B.Cu"] + ["B.Cu"]
    return layers


def run(cli: str, args: list[str], label: str) -> bool:
    cmd = [cli] + args
    print(f"\n>>> {label}")
    print("    " + " ".join(f'"{a}"' if " " in a else a for a in cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.stdout.strip():
        print("    " + res.stdout.strip().replace("\n", "\n    "))
    if res.returncode != 0:
        print(f"    [FAIL exit {res.returncode}] {res.stderr.strip()}")
        return False
    print("    [OK]")
    return True


def _stamp() -> str:
    return _dt.date.today().isoformat()


# ---------------------------------------------------------------------------
# Build steps
# ---------------------------------------------------------------------------

def export_schematic_pdf(cli: str) -> None:
    run(cli, ["sch", "export", "pdf",
              "-o", str(STAGE_DIR / "schematic.pdf"),
              str(SCH_FILE)],
        "Schematic PDF")


def export_board_pdfs(cli: str) -> None:
    copper = detect_copper_layers(PCB_FILE)
    inner = [l for l in copper if l not in ("F.Cu", "B.Cu")]
    views = list(PCB_VIEWS)
    for layer in inner:  # inner copper on >2-layer boards
        fname = "pcb_copper_" + layer.replace(".", "_").lower() + ".pdf"
        views.append((fname, [layer, "Edge.Cuts"]))
    for fname, layers in views:
        run(cli, ["pcb", "export", "pdf",
                  "-o", str(STAGE_DIR / fname),
                  "--layers", ",".join(layers),
                  "--mode-single", "--scale", "0",
                  str(PCB_FILE)],
            f"Board PDF: {fname}")


def export_gerbers(cli: str) -> None:
    GERBER_DIR.mkdir(parents=True, exist_ok=True)
    for f in GERBER_DIR.glob("*"):
        if f.is_file():
            f.unlink()
    copper = detect_copper_layers(PCB_FILE)
    gerber_layers = copper + TECH_LAYERS
    print(f"\n    detected copper layers: {', '.join(copper)}")
    run(cli, ["pcb", "export", "gerbers",
              "-o", str(GERBER_DIR) + os.sep,
              "--layers", ",".join(gerber_layers),
              "--subtract-soldermask",
              str(PCB_FILE)],
        "Gerbers")
    run(cli, ["pcb", "export", "drill",
              "-o", str(GERBER_DIR) + os.sep,
              "--format", "excellon",
              "--excellon-separate-th",
              "--generate-map", "--map-format", "gerberx2",
              str(PCB_FILE)],
        "Drill files (Excellon, PTH/NPTH separate)")


def export_bom(cli: str) -> None:
    run(cli, ["sch", "export", "bom",
              "-o", str(STAGE_DIR / "bom.csv"),
              "--group-by", "Value,Footprint,LCSC",
              "--fields", "Value,Reference,Footprint,LCSC",
              "--labels", "Comment,Designator,Footprint,LCSC Part #",
              "--ref-range-delimiter", "",
              "--exclude-dnp",
              str(SCH_FILE)],
        "BOM (JLCPCB columns)")
    _sanitize_lcsc_column()


def _sanitize_lcsc_column() -> None:
    bom = STAGE_DIR / "bom.csv"
    if not bom.exists():
        return
    with bom.open(newline="", encoding="utf-8-sig") as fh:
        rows = list(csv.reader(fh))
    if not rows:
        return
    try:
        col = rows[0].index("LCSC Part #")
    except ValueError:
        return
    for r in rows[1:]:
        if col < len(r) and r[col].strip().startswith("${"):
            r[col] = ""
    with bom.open("w", newline="", encoding="utf-8") as fh:
        csv.writer(fh).writerows(rows)


def export_cpl(cli: str) -> None:
    raw = STAGE_DIR / "_pos_raw.csv"
    ok = run(cli, ["pcb", "export", "pos",
                   "-o", str(raw),
                   "--format", "csv", "--units", "mm",
                   "--side", "both",
                   "--use-drill-file-origin",
                   "--exclude-dnp",
                   str(PCB_FILE)],
             "Placement (raw)")
    if not ok or not raw.exists():
        return
    cpl = STAGE_DIR / "cpl.csv"
    with raw.open(newline="", encoding="utf-8") as fin, \
         cpl.open("w", newline="", encoding="utf-8") as fout:
        reader = csv.DictReader(fin)
        writer = csv.writer(fout)
        writer.writerow(["Designator", "Val", "Package",
                         "Mid X", "Mid Y", "Rotation", "Layer"])
        for r in reader:
            side = (r.get("Side") or "").strip().lower()
            layer = "Top" if side.startswith("t") else "Bottom"
            writer.writerow([
                r.get("Ref", ""), r.get("Val", ""), r.get("Package", ""),
                r.get("PosX", ""), r.get("PosY", ""), r.get("Rot", ""),
                layer,
            ])
    raw.unlink()
    print("\n>>> CPL\n    [OK] -> cpl.csv")


def zip_bundle(version: str) -> Path:
    zip_path = RELEASE_DIR / f"{PROJECT_NAME}-{version}.zip"
    if zip_path.exists():
        zip_path.unlink()
    files = sorted(p for p in STAGE_DIR.rglob("*") if p.is_file())
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            zf.write(f, arcname=str(f.relative_to(STAGE_DIR)))
    print(f"\n>>> Release zip\n    [OK] {zip_path.name}  ({len(files)} files)")
    return zip_path


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _resolve_project(name: str | None, pcb: str | None) -> None:
    global PROJECT_NAME, SCH_FILE, PCB_FILE, STAGE_DIR, GERBER_DIR
    if pcb:
        stem = Path(pcb).with_suffix("")
        PCB_FILE = Path(pcb)
        SCH_FILE = stem.with_suffix(".kicad_sch")
        PROJECT_NAME = name or stem.name
    else:
        name = name or "e-foc"
        base = HW_DIR / name / name
        SCH_FILE = base.with_suffix(".kicad_sch")
        PCB_FILE = base.with_suffix(".kicad_pcb")
        PROJECT_NAME = name
    STAGE_DIR = RELEASE_DIR / PROJECT_NAME
    GERBER_DIR = STAGE_DIR / "gerbers"


def main() -> None:
    ap = argparse.ArgumentParser(description="Build a per-board release bundle.")
    ap.add_argument("--kicad-cli", help="Path to kicad-cli.exe")
    ap.add_argument("--name", help="Board basename under hardware/<name>/ (default: e-foc)")
    ap.add_argument("--pcb", help="Explicit path to a .kicad_pcb (overrides --name)")
    ap.add_argument("--version", help="Version label for the zip name (default: today's date)")
    args = ap.parse_args()

    _resolve_project(args.name, args.pcb)
    version = args.version or _stamp()

    for f in (SCH_FILE, PCB_FILE):
        if not f.exists():
            sys.exit(f"Missing project file: {f}")

    cli = find_cli(args.kicad_cli)

    # Fresh staging dir so a stale file can't leak into the bundle.
    if STAGE_DIR.exists():
        shutil.rmtree(STAGE_DIR)
    STAGE_DIR.mkdir(parents=True, exist_ok=True)

    print(f"kicad-cli : {cli}")
    print(f"project   : {PCB_FILE.parent}")
    print(f"bundle    : {PROJECT_NAME}-{version}.zip")

    export_schematic_pdf(cli)
    export_board_pdfs(cli)
    export_gerbers(cli)
    export_bom(cli)
    export_cpl(cli)
    zip_path = zip_bundle(version)

    print(f"\nDone. Release bundle: {zip_path}")


if __name__ == "__main__":
    main()
