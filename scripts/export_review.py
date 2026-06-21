#!/usr/bin/env python3
"""
Export a complete review package for the e-foc KiCad project.

Produces, under ./review_exports/ :

  Schematic
    - schematic.pdf          full schematic, all sheets
    - bom.csv                bill of materials (grouped by value)
    - erc.rpt / erc.json     Electrical Rules Check report

  PCB
    - gerbers/               one Gerber file per layer + drill files
    - drc.rpt / drc.json     Design Rules Check report (with schematic parity = "compare")
    - design_rules.txt       net classes / clearances / track widths from the project
    - board_stats.txt        board statistics

Usage:
    python export_review.py
    python export_review.py --kicad-cli "C:/Program Files/KiCad/10.0/bin/kicad-cli.exe"

Everything in review_exports/ can be loaded into Claude for a board review.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = SCRIPT_DIR.parent  # script lives in <root>/scripts/
HW_DIR = PROJECT_ROOT / "hardware"

SCH_FILE = HW_DIR / "e-foc" / "e-foc.kicad_sch"
PCB_FILE = HW_DIR / "e-foc" / "e-foc.kicad_pcb"
PRO_FILE = HW_DIR / "e-foc" / "e-foc.kicad_pro"

OUT_DIR = PROJECT_ROOT / "review_exports" / "e-foc"
GERBER_DIR = OUT_DIR / "gerbers"
IMAGE_DIR = OUT_DIR / "pcb_views"

# Non-copper (technical) layers to plot as Gerbers. Copper layers are detected
# from the board at runtime — see detect_copper_layers().
TECH_LAYERS = [
    "F.Paste", "B.Paste",
    "F.Silkscreen", "B.Silkscreen",
    "F.Mask", "B.Mask",
    "Edge.Cuts",
    "F.Fab", "B.Fab",
]

# Human-readable PCB "views" rendered to PDF for visual inspection. Each entry
# is (filename, [layers plotted together]). Edge.Cuts is added to every view
# for board-outline context. {COPPER} is expanded to the detected copper layers.
PCB_VIEWS = [
    ("copper_top.pdf",    ["F.Cu", "F.Mask", "Edge.Cuts"]),
    ("copper_bottom.pdf", ["B.Cu", "B.Mask", "Edge.Cuts"]),
    ("silk_top.pdf",      ["F.Silkscreen", "Edge.Cuts"]),
    ("silk_bottom.pdf",   ["B.Silkscreen", "Edge.Cuts"]),
    ("assembly_top.pdf",  ["F.Fab", "F.Silkscreen", "Edge.Cuts"]),
    ("assembly_bottom.pdf", ["B.Fab", "B.Silkscreen", "Edge.Cuts"]),
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

    # Grab the top-level (layers ... ) block: from "(layers" to its matching
    # close paren. The block is shallow, so balance parens from the opener.
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

    # Lines look like:  (0 "F.Cu" signal)  /  (1 "In1.Cu" power)
    found = re.findall(r'\(\s*(\d+)\s+"([^"]+\.Cu)"\s+\w+', block)
    if not found:
        return fallback
    # Order by the layer ordinal so the stack reads top -> bottom.
    layers = [name for _, name in sorted(found, key=lambda t: int(t[0]))]
    # Move B.Cu to the end (KiCad numbers it 2, before inner layers).
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


# ---------------------------------------------------------------------------
# Exports
# ---------------------------------------------------------------------------

def export_schematic(cli: str) -> None:
    run(cli, ["sch", "export", "pdf",
              "-o", str(OUT_DIR / "schematic.pdf"),
              str(SCH_FILE)],
        "Schematic PDF")

    run(cli, ["sch", "export", "bom",
              "-o", str(OUT_DIR / "bom.csv"),
              "--group-by", "Value",
              "--fields", "Reference,Value,Footprint,${QUANTITY},${DNP},Manufacturer,MPN",
              "--labels", "Refs,Value,Footprint,Qty,DNP,Manufacturer,MPN",
              str(SCH_FILE)],
        "Bill of Materials (CSV)")

    run(cli, ["sch", "erc",
              "-o", str(OUT_DIR / "erc.rpt"),
              "--severity-all", "--units", "mm",
              str(SCH_FILE)],
        "ERC report (text)")

    run(cli, ["sch", "erc",
              "-o", str(OUT_DIR / "erc.json"),
              "--format", "json", "--severity-all",
              str(SCH_FILE)],
        "ERC report (json)")


def export_pcb(cli: str) -> None:
    GERBER_DIR.mkdir(parents=True, exist_ok=True)

    # Wipe stale plot outputs so a previous run's layers (e.g. inner layers from
    # a 4-layer board later cut to 2) can't linger and mislead a fabricator.
    for f in GERBER_DIR.glob("*"):
        if f.suffix.lower() in (".gbr", ".gbrjob", ".drl"):
            f.unlink()

    copper = detect_copper_layers(PCB_FILE)
    gerber_layers = copper + TECH_LAYERS
    print(f"\n    detected copper layers: {', '.join(copper)}")

    run(cli, ["pcb", "export", "gerbers",
              "-o", str(GERBER_DIR) + os.sep,
              "--layers", ",".join(gerber_layers),
              "--no-protel-ext",
              str(PCB_FILE)],
        "Gerbers (layer by layer)")

    run(cli, ["pcb", "export", "drill",
              "-o", str(GERBER_DIR) + os.sep,
              "--format", "excellon",
              "--generate-map", "--map-format", "gerberx2",
              str(PCB_FILE)],
        "Drill files")

    # DRC with schematic parity = the schematic/PCB "compare".
    run(cli, ["pcb", "drc",
              "-o", str(OUT_DIR / "drc.rpt"),
              "--schematic-parity", "--all-track-errors",
              "--severity-all", "--units", "mm",
              str(PCB_FILE)],
        "DRC report + schematic parity (text)")

    run(cli, ["pcb", "drc",
              "-o", str(OUT_DIR / "drc.json"),
              "--format", "json", "--schematic-parity",
              "--all-track-errors", "--severity-all",
              str(PCB_FILE)],
        "DRC report + schematic parity (json)")

    run(cli, ["pcb", "export", "stats",
              "-o", str(OUT_DIR / "board_stats.txt"),
              str(PCB_FILE)],
        "Board statistics")


def _pdf_to_png(pdf: Path, dpi: int = 300) -> bool:
    """Rasterize a single-page PDF to PNG (same stem) using PyMuPDF. Returns
    False (and leaves the PDF) if PyMuPDF isn't installed."""
    try:
        import fitz  # PyMuPDF — self-contained, no external DLLs
    except ImportError:
        return False
    doc = fitz.open(pdf)
    doc[0].get_pixmap(dpi=dpi).save(pdf.with_suffix(".png"))
    doc.close()
    return True


def export_pcb_images(cli: str) -> None:
    """Render human-readable per-view images of the board (copper top/bottom,
    silk, assembly) so a reviewer can visually inspect the layout. Each view is
    plotted to PDF by kicad-cli, then rasterized to PNG so an LLM reviewer can
    actually see it (PNG is viewable; vector PDF is not)."""
    IMAGE_DIR.mkdir(parents=True, exist_ok=True)

    # Wipe stale views so a previous run's layers can't linger.
    for f in IMAGE_DIR.glob("*"):
        if f.suffix.lower() in (".pdf", ".png"):
            f.unlink()

    views = list(PCB_VIEWS)

    # Add a copper view for any inner layers (only present on >2-layer boards).
    copper = detect_copper_layers(PCB_FILE)
    inner = [l for l in copper if l not in ("F.Cu", "B.Cu")]
    for layer in inner:
        fname = "copper_" + layer.replace(".", "_").lower() + ".pdf"
        views.append((fname, [layer, "Edge.Cuts"]))

    png_ok = True
    for fname, layers in views:
        # --mode-single: output path is the full file; LAYER_LIST = all layers
        # plotted onto that one sheet. --scale 0 autoscales to fill the page.
        if not run(cli, ["pcb", "export", "pdf",
                         "-o", str(IMAGE_DIR / fname),
                         "--layers", ",".join(layers),
                         "--mode-single", "--scale", "0",
                         str(PCB_FILE)],
                   f"PCB view: {fname}"):
            continue
        if png_ok and not _pdf_to_png(IMAGE_DIR / fname):
            png_ok = False
            print("    [warn] PyMuPDF not installed -> PNGs skipped, PDFs kept."
                  " Install with: pip install pymupdf")

    if png_ok:
        # PNGs generated: drop the intermediate PDFs to keep the folder clean.
        for f in IMAGE_DIR.glob("*.pdf"):
            f.unlink()


def dump_design_rules() -> None:
    """Extract net classes / clearances / track widths from the .kicad_pro."""
    out = OUT_DIR / "design_rules.txt"
    try:
        pro = json.loads(PRO_FILE.read_text(encoding="utf-8"))
    except Exception as e:  # noqa: BLE001
        print(f"\n>>> Design rules\n    [FAIL] cannot read {PRO_FILE.name}: {e}")
        return

    lines: list[str] = ["E-FOC DESIGN RULES (from project file)", "=" * 40, ""]

    ds = pro.get("board", {}).get("design_settings", {})
    rules = ds.get("rules", {})
    if rules:
        lines.append("Global rules:")
        for k, v in rules.items():
            lines.append(f"  {k}: {v}")
        lines.append("")

    netclasses = pro.get("net_settings", {}).get("classes", [])
    if netclasses:
        lines.append("Net classes:")
        for nc in netclasses:
            lines.append(f"  - {nc.get('name', '?')}:")
            for k in ("clearance", "track_width", "via_diameter",
                      "via_drill", "microvia_diameter", "microvia_drill",
                      "diff_pair_width", "diff_pair_gap"):
                if k in nc:
                    lines.append(f"      {k}: {nc[k]}")
        lines.append("")

    # Also copy any custom design-rules file if present.
    dru = PRO_FILE.with_suffix(".kicad_dru")
    if dru.exists():
        lines.append("Custom .kicad_dru:")
        lines.append(dru.read_text(encoding="utf-8"))

    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n>>> Design rules\n    [OK] -> design_rules.txt")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def _resolve_project(name: str | None, pcb: str | None) -> None:
    """Point the module-level path globals at the chosen project. Every board
    lives under hardware/<name>/<name>.* and exports to review_exports/<name>/.
    Default board is e-foc (`python export_review.py` with no args -> hardware/
    e-foc/). Pass --name <basename> for another board, or --pcb <path> to point
    at an explicit .kicad_pcb (sibling .sch/.pro assumed)."""
    global SCH_FILE, PCB_FILE, PRO_FILE, OUT_DIR, GERBER_DIR, IMAGE_DIR

    if pcb:
        stem = Path(pcb).with_suffix("")
        PCB_FILE = Path(pcb)
        SCH_FILE = stem.with_suffix(".kicad_sch")
        PRO_FILE = stem.with_suffix(".kicad_pro")
        label = name or stem.name
        OUT_DIR = PROJECT_ROOT / "review_exports" / label
    else:
        name = name or "e-foc"
        base = HW_DIR / name / name
        SCH_FILE = base.with_suffix(".kicad_sch")
        PCB_FILE = base.with_suffix(".kicad_pcb")
        PRO_FILE = base.with_suffix(".kicad_pro")
        OUT_DIR = PROJECT_ROOT / "review_exports" / name

    GERBER_DIR = OUT_DIR / "gerbers"
    IMAGE_DIR = OUT_DIR / "pcb_views"


def main() -> None:
    ap = argparse.ArgumentParser(description="Export a KiCad review package.")
    ap.add_argument("--kicad-cli", help="Path to kicad-cli.exe")
    ap.add_argument("--name", help="Project basename under hardware/<name>/ "
                    "(default: e-foc top-level board)")
    ap.add_argument("--pcb", help="Explicit path to a .kicad_pcb (overrides --name)")
    ap.add_argument("--skip-sch", action="store_true", help="Skip schematic exports")
    ap.add_argument("--skip-pcb", action="store_true", help="Skip PCB exports")
    args = ap.parse_args()

    _resolve_project(args.name, args.pcb)

    needed = (PCB_FILE,) if args.skip_sch else (SCH_FILE, PCB_FILE, PRO_FILE)
    for f in needed:
        if not f.exists():
            sys.exit(f"Missing project file: {f}")

    cli = find_cli(args.kicad_cli)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"kicad-cli : {cli}")
    print(f"project   : {PCB_FILE.parent}")
    print(f"output    : {OUT_DIR}")

    if not args.skip_sch:
        export_schematic(cli)
    if not args.skip_pcb:
        export_pcb(cli)
        export_pcb_images(cli)
        dump_design_rules()

    print(f"\nDone. Review files in: {OUT_DIR}")


if __name__ == "__main__":
    main()
