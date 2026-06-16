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
import shutil
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths / config
# ---------------------------------------------------------------------------

SCRIPT_DIR = Path(__file__).resolve().parent
HW_DIR = SCRIPT_DIR / "hardware"

SCH_FILE = HW_DIR / "e-foc.kicad_sch"
PCB_FILE = HW_DIR / "e-foc.kicad_pcb"
PRO_FILE = HW_DIR / "e-foc.kicad_pro"

OUT_DIR = SCRIPT_DIR / "review_exports"
GERBER_DIR = OUT_DIR / "gerbers"

# Copper + fab layers to plot, one Gerber each (4-layer board).
COPPER_LAYERS = ["F.Cu", "In1.Cu", "In2.Cu", "B.Cu"]
TECH_LAYERS = [
    "F.Paste", "B.Paste",
    "F.Silkscreen", "B.Silkscreen",
    "F.Mask", "B.Mask",
    "Edge.Cuts",
    "F.Fab", "B.Fab",
]
GERBER_LAYERS = COPPER_LAYERS + TECH_LAYERS

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

    run(cli, ["pcb", "export", "gerbers",
              "-o", str(GERBER_DIR) + os.sep,
              "--layers", ",".join(GERBER_LAYERS),
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
    dru = HW_DIR / "e-foc.kicad_dru"
    if dru.exists():
        lines.append("Custom .kicad_dru:")
        lines.append(dru.read_text(encoding="utf-8"))

    out.write_text("\n".join(lines), encoding="utf-8")
    print("\n>>> Design rules\n    [OK] -> design_rules.txt")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    ap = argparse.ArgumentParser(description="Export KiCad review package for e-foc.")
    ap.add_argument("--kicad-cli", help="Path to kicad-cli.exe")
    ap.add_argument("--skip-sch", action="store_true", help="Skip schematic exports")
    ap.add_argument("--skip-pcb", action="store_true", help="Skip PCB exports")
    args = ap.parse_args()

    for f in (SCH_FILE, PCB_FILE, PRO_FILE):
        if not f.exists():
            sys.exit(f"Missing project file: {f}")

    cli = find_cli(args.kicad_cli)
    OUT_DIR.mkdir(parents=True, exist_ok=True)

    print(f"kicad-cli : {cli}")
    print(f"project   : {HW_DIR}")
    print(f"output    : {OUT_DIR}")

    if not args.skip_sch:
        export_schematic(cli)
    if not args.skip_pcb:
        export_pcb(cli)
        dump_design_rules()

    print(f"\nDone. Review files in: {OUT_DIR}")


if __name__ == "__main__":
    main()
