#!/usr/bin/env python3
"""
Export a JLCPCB fabrication + assembly package for the e-foc KiCad project.

Produces, under ./manufacturing/ :

  Fabrication
    - gerbers/                 one Gerber file per layer + Excellon drill files
    - e-foc-gerbers-<date>.zip the gerbers/ folder zipped -> upload to JLCPCB
                               "Add gerber file" (fabrication step)

  Assembly (SMT / PCBA)
    - bom.csv                  JLCPCB BOM   (Comment, Designator, Footprint,
                               "LCSC Part #")   -> "Add BOM file"
    - cpl.csv                  JLCPCB placement (Designator, Val, Package,
                               Mid X, Mid Y, Rotation, Layer) -> "Add CPL file"

LCSC part numbers are read from a symbol field named "LCSC" (or "LCSC Part #" /
"JLCPCB Part #") if present; symbols without one get a blank cell to fill in on
the JLCPCB BOM page.

Usage:
    python export_jlcpcb.py
    python export_jlcpcb.py --kicad-cli "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"
    python export_jlcpcb.py --no-assembly      # gerbers + zip only
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
PROJECT_ROOT = SCRIPT_DIR.parent  # script lives in <root>/scripts/
HW_DIR = PROJECT_ROOT / "hardware"

PROJECT_NAME = "e-foc"
SCH_FILE = HW_DIR / "e-foc" / "e-foc.kicad_sch"
PCB_FILE = HW_DIR / "e-foc" / "e-foc.kicad_pcb"

OUT_DIR = PROJECT_ROOT / "manufacturing" / "e-foc"
GERBER_DIR = OUT_DIR / "gerbers"

# Symbol field names that may carry the LCSC part number, in priority order.
LCSC_FIELDS = ["LCSC", "LCSC Part #", "JLCPCB Part #", "JLC", "JLCPCB"]

# Non-copper (technical) layers JLCPCB expects. Copper layers are detected from
# the board at runtime -- see detect_copper_layers().
TECH_LAYERS = [
    "F.Paste", "B.Paste",
    "F.Silkscreen", "B.Silkscreen",
    "F.Mask", "B.Mask",
    "Edge.Cuts",
]

# Common kicad-cli install locations (Windows).
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
    """Read the board's `(layers ...)` block and return the enabled copper
    layers in stack order (F.Cu, In1.Cu, ..., B.Cu). Falls back to a 2-layer
    board if parsing fails."""
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
    """Run a kicad-cli subcommand. Return True on success (exit 0)."""
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
# Fabrication: gerbers + drill + zip
# ---------------------------------------------------------------------------

def export_gerbers(cli: str) -> None:
    GERBER_DIR.mkdir(parents=True, exist_ok=True)

    # Wipe stale plot outputs so a previous run's layers can't linger and
    # mislead the fab.
    for f in GERBER_DIR.glob("*"):
        if f.suffix.lower() in (".gbr", ".gbrjob", ".drl", ".gbl", ".gtl"):
            f.unlink()

    copper = detect_copper_layers(PCB_FILE)
    gerber_layers = copper + TECH_LAYERS
    print(f"\n    detected copper layers: {', '.join(copper)}")

    # JLCPCB accepts standard RS-274X with Protel extensions; --subtract-soldermask
    # keeps mask apertures clean. Use Protel filename extensions (.GTL/.GBL/...)
    # which JLCPCB's parser recognises most reliably.
    run(cli, ["pcb", "export", "gerbers",
              "-o", str(GERBER_DIR) + os.sep,
              "--layers", ",".join(gerber_layers),
              "--subtract-soldermask",
              str(PCB_FILE)],
        "Gerbers (layer by layer)")

    run(cli, ["pcb", "export", "drill",
              "-o", str(GERBER_DIR) + os.sep,
              "--format", "excellon",
              "--excellon-separate-th",
              "--generate-map", "--map-format", "gerberx2",
              str(PCB_FILE)],
        "Drill files (Excellon, PTH/NPTH separate)")


def zip_gerbers() -> Path:
    zip_path = OUT_DIR / f"{PROJECT_NAME}-gerbers-{_stamp()}.zip"
    if zip_path.exists():
        zip_path.unlink()
    files = sorted(p for p in GERBER_DIR.iterdir() if p.is_file())
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in files:
            # Flat archive (no gerbers/ prefix) -- JLCPCB expects files at root.
            zf.write(f, arcname=f.name)
    print(f"\n>>> Gerber zip\n    [OK] {zip_path.name}  ({len(files)} files)")
    return zip_path


# ---------------------------------------------------------------------------
# Assembly: BOM + CPL in JLCPCB column format
# ---------------------------------------------------------------------------

def export_bom(cli: str) -> None:
    """JLCPCB BOM: Comment, Designator, Footprint, LCSC Part #.

    kicad-cli groups identical parts and joins their references, which is the
    grouping JLCPCB's assembly BOM expects. The LCSC column is filled from the
    first matching symbol field in LCSC_FIELDS (blank if none)."""
    # Plain field name (not ${LCSC}) so kicad-cli emits an empty cell -- rather
    # than the literal token -- when no symbol carries the field.
    run(cli, ["sch", "export", "bom",
              "-o", str(OUT_DIR / "bom.csv"),
              "--group-by", "Value,Footprint,LCSC",
              "--fields", "Value,Reference,Footprint,LCSC",
              "--labels", "Comment,Designator,Footprint,LCSC Part #",
              "--ref-range-delimiter", "",      # JLCPCB wants R1,R2,R3 not R1-R3
              "--exclude-dnp",
              str(SCH_FILE)],
        "JLCPCB BOM (Comment, Designator, Footprint, LCSC Part #)")

    _sanitize_lcsc_column()
    _warn_missing_lcsc()


def _sanitize_lcsc_column() -> None:
    """Blank any unresolved ${...} token kicad-cli may leave in the LCSC cell
    when the field is absent on every symbol."""
    bom = OUT_DIR / "bom.csv"
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


def _warn_missing_lcsc() -> None:
    """Report designators whose LCSC Part # cell is blank so the user knows
    exactly what to assign on the JLCPCB BOM page."""
    bom = OUT_DIR / "bom.csv"
    if not bom.exists():
        return
    missing: list[str] = []
    with bom.open(newline="", encoding="utf-8-sig") as fh:
        for row in csv.DictReader(fh):
            lcsc = (row.get("LCSC Part #") or "").strip()
            if not lcsc:
                missing.append((row.get("Designator") or "?").strip())
    if missing:
        print("\n    [warn] no LCSC Part # for: " + "  ".join(missing))
        print("           assign these on the JLCPCB BOM page (or add an "
              "'LCSC' field to the symbols and re-run).")


def export_cpl(cli: str) -> None:
    """JLCPCB CPL (component placement list). kicad-cli emits Ref/Val/Package/
    PosX/PosY/Rot/Side; rewrite the header + Side values to JLCPCB's schema and
    keep the drill-file origin so coordinates match the gerbers."""
    raw = OUT_DIR / "_pos_raw.csv"
    ok = run(cli, ["pcb", "export", "pos",
                   "-o", str(raw),
                   "--format", "csv",
                   "--units", "mm",
                   "--side", "both",
                   "--use-drill-file-origin",
                   "--exclude-dnp",
                   str(PCB_FILE)],
             "Placement (raw, kicad columns)")
    if not ok or not raw.exists():
        return

    cpl = OUT_DIR / "cpl.csv"
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
    print("\n>>> JLCPCB CPL\n    [OK] -> cpl.csv")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _resolve_project(name: str | None, pcb: str | None) -> None:
    """Point path globals at the chosen project. Every board lives under
    hardware/<name>/<name>.* and writes to manufacturing/<name>/. Default board
    is e-foc. --name <basename> selects another board; --pcb gives an explicit
    .kicad_pcb."""
    global PROJECT_NAME, SCH_FILE, PCB_FILE, OUT_DIR, GERBER_DIR

    if pcb:
        stem = Path(pcb).with_suffix("")
        PCB_FILE = Path(pcb)
        SCH_FILE = stem.with_suffix(".kicad_sch")
        PROJECT_NAME = name or stem.name
        OUT_DIR = PROJECT_ROOT / "manufacturing" / PROJECT_NAME
    else:
        name = name or "e-foc"
        base = HW_DIR / name / name
        SCH_FILE = base.with_suffix(".kicad_sch")
        PCB_FILE = base.with_suffix(".kicad_pcb")
        PROJECT_NAME = name
        OUT_DIR = PROJECT_ROOT / "manufacturing" / name

    GERBER_DIR = OUT_DIR / "gerbers"


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Export a JLCPCB fabrication + assembly package.")
    ap.add_argument("--kicad-cli", help="Path to kicad-cli.exe")
    ap.add_argument("--name", help="Project basename under hardware/<name>/ "
                    "(default: e-foc top-level board)")
    ap.add_argument("--pcb", help="Explicit path to a .kicad_pcb (overrides --name)")
    ap.add_argument("--no-assembly", action="store_true",
                    help="Gerbers + zip only; skip BOM/CPL (e.g. hand-soldered boards)")
    args = ap.parse_args()

    _resolve_project(args.name, args.pcb)

    needed = (PCB_FILE,) if args.no_assembly else (SCH_FILE, PCB_FILE)
    for f in needed:
        if not f.exists():
            sys.exit(f"Missing project file: {f}")

    cli = find_cli(args.kicad_cli)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"kicad-cli : {cli}")
    print(f"project   : {PCB_FILE.parent}")
    print(f"output    : {OUT_DIR}")

    export_gerbers(cli)
    zip_path = zip_gerbers()

    if not args.no_assembly:
        export_bom(cli)
        export_cpl(cli)

    print("\nDone.")
    print(f"  Fabrication : upload {zip_path.name}")
    if not args.no_assembly:
        print("  Assembly    : upload bom.csv (BOM) and cpl.csv (CPL)")


if __name__ == "__main__":
    main()
