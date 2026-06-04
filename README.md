# e-foc — Hardware

[![kicad-bot](https://github.com/embedded-pro/e-foc-hw/actions/workflows/kicad-bot.yml/badge.svg)](https://github.com/embedded-pro/e-foc-hw/actions/workflows/kicad-bot.yml)
[![KiCad](https://img.shields.io/badge/KiCad-10-blue.svg)](https://www.kicad.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

A three-phase brushless (BLDC / PMSM) motor driver board designed to run the
[**e-foc** Field-Oriented Control firmware](https://github.com/embedded-pro/e-foc).

This repository contains the **KiCad** electrical design — schematics, project
configuration and (eventually) the PCB layout — for the power and signal-chain
hardware that the e-foc control software targets. The microcontroller itself
lives on a pluggable module; this board provides the power stage, gate drive,
current sensing, protection and peripheral connectivity around it.

> **Scope:** this repo is the *hardware*. For the control algorithms (Clarke/Park
> transforms, Space Vector Modulation, current/velocity PID loops, auto-tuning),
> see the [firmware repository](https://github.com/embedded-pro/e-foc).

---

## Highlights

- **Three-phase inverter** built from six TI `CSD19506KTT` 100 V NexFET™ MOSFETs
  (three half-bridges) — high current capability with low R<sub>DS(on)</sub>.
- **`FAN7888` 3-phase gate driver** with bootstrap high-side supply for clean,
  cross-conduction-protected switching.
- **Low-side shunt current sensing** (0.02 Ω) on each phase, amplified and
  filtered through a dedicated `TLV9062` / `TLV9064` analog conditioning stage
  for the controller's ADC.
- **Wide-input switching power supply** — `LM5164` 100 V synchronous buck plus
  `AMS1117` linear regulators generating the +15 V gate-drive rail, +5 V and
  +3.3 V logic rails, with reverse-polarity (P-MOSFET) and TVS protection.
- **CAN connectivity** via an `SN65HVD230` transceiver for multi-axis /
  networked control.
- **Non-volatile storage** — `24LC04` I²C EEPROM (calibration / parameters) and
  `W25Q32` 32-Mbit SPI flash (logs / firmware assets).
- **MCU-agnostic carrier** — the controller plugs in over a 24-pin (2×12)
  header, matching the firmware's `PlatformFactory` abstraction (e.g. STM32F407
  or TI Tiva TM4C1294 modules).

---

## Board architecture

The design is organised as hierarchical schematic sheets, each a self-contained
functional block:

| Sheet | Function | Key parts |
|-------|----------|-----------|
| `e-foc.kicad_sch` | Top-level sheet wiring all blocks together | — |
| `power-supply` | Wide-input supply: gate-drive + logic rails, protection | `LM5164`, `AMS1117-5.0`, `AMS1117-3.3`, `IRF9540N`, `SMAJ82A` |
| `driver` | Three-phase inverter, gate drive, phase shunts, motor terminals | `FAN7888`, 6× `CSD19506KTT`, 0.02 Ω shunts |
| `adc-conditioner` | Current/voltage sense amplification & anti-alias filtering | `TLV9062`, `TLV9064` |
| `microcontroller-connector` | 2×12 header to the MCU module + signal break-out | `Conn_02x12` |
| `can-bus` | CAN field-bus interface | `SN65HVD230` |
| `serial-com` | Serial / debug communications | — |
| `eeprom-mem` | I²C EEPROM for calibration & parameters | `24LC04` |
| `flash-mem` | SPI NOR flash for logging / assets | `W25Q32JVSS` |

---

## Specifications (target)

| Parameter | Value |
|-----------|-------|
| Motor types | 3-phase BLDC / PMSM |
| Power topology | 3× half-bridge, low-side shunt sensing |
| Switching devices | TI `CSD19506KTT`, 100 V N-channel NexFET™ |
| Bus voltage | Wide input, components rated to 100 V (TVS clamp ~82 V) |
| Gate driver | `FAN7888`, 3-phase, bootstrap high-side |
| Current sense | 0.02 Ω low-side shunt per phase + op-amp gain stage |
| Logic rails | +3.3 V / +5 V / +15 V (gate drive) |
| Protection | Reverse-polarity P-MOSFET, TVS, zener clamps |
| Comms | CAN (`SN65HVD230`), serial/debug |
| Storage | I²C EEPROM (`24LC04`), SPI flash (`W25Q32`, 32 Mbit) |
| Controller | External 24-pin MCU module (STM32 / TI Tiva, via firmware abstraction) |

> Specifications reflect the rated capability of the selected components and are
> finalised once the PCB layout and bring-up are complete.

---

## Repository layout

```
e-foc/
├── hardware/                        # KiCad project (kept together — KiCad requirement)
│   ├── e-foc.kicad_pro              #   project
│   ├── e-foc.kicad_sch              #   top-level (root) schematic
│   ├── e-foc.kicad_pcb              #   PCB layout
│   └── schematic/                   #   hierarchical sub-sheets ↓
│       ├── untitled.kicad_sch       #     power-supply sheet
│       ├── driver.kicad_sch
│       ├── adc-conditioner.kicad_sch
│       ├── microcontroller-connector.kicad_sch
│       ├── can-bus.kicad_sch
│       ├── serial-com.kicad_sch
│       ├── eeprom-mem.kicad_sch
│       └── flash-mem.kicad_sch
├── .github/workflows/kicad-bot.yml  # CI quality gate
└── .github/workflows/release.yml    # release + fabrication package
```

> The project (`.kicad_pro`), root schematic and board must share a folder —
> KiCad locates them by the project's name and path — so they live together in
> `hardware/`. The hierarchical sub-sheets are referenced by relative path and
> are grouped under `hardware/schematic/`.

---

## Getting started

This is a [**KiCad 10**](https://www.kicad.org/) project.

1. Clone the repository:
   ```bash
   git clone https://github.com/embedded-pro/e-foc-hw.git
   cd e-foc-hw
   ```
2. Open `hardware/e-foc.kicad_pro` in KiCad.
3. Open the schematic editor to browse the hierarchical sheets, or the PCB
   editor for the board layout.

### Headless checks (the same ones CI runs)

With KiCad 8+ installed you can run electrical / design-rule checks locally:

```bash
kicad-cli sch erc  --output erc.json --format json hardware/e-foc.kicad_sch
kicad-cli pcb drc  --output drc.json --format json hardware/e-foc.kicad_pcb
```

---

## Continuous integration

Every push and pull request that touches a schematic or board file is checked by
[**kicad-bot**](https://github.com/embedded-pro/kicad-bot) — a CI quality gate
that wraps `kicad-cli`, KiBot, KiCost and KiDiff to run:

- **ERC / DRC verification** — electrical and design-rule checks.
- **BOM availability** — distributor stock / lifecycle (end-of-life) flags.
- **Visual diffs** — schematic & PCB change previews on pull requests.

The workflow lives in [`.github/workflows/kicad-bot.yml`](.github/workflows/kicad-bot.yml)
and uploads a `kicad-bot-output/` artifact (report, metrics, raw violation data).

> kicad-bot is a CI guardrail, **not** a substitute for design review or sign-off.

To enable distributor BOM pricing/availability, add the relevant API keys as
repository secrets (`MOUSER_KEY`, `DIGIKEY_CLIENT_ID`, `DIGIKEY_CLIENT_SECRET`).

All third-party actions are **pinned to a full commit SHA** (with the version in a
trailing comment) for supply-chain hardening.

## Releases

Releases are automated with [release-please](https://github.com/googleapis/release-please)
and driven by [Conventional Commits](https://www.conventionalcommits.org/)
(`feat:`, `fix:`, …). The flow ([`.github/workflows/release.yml`](.github/workflows/release.yml)):

1. Commits to `main` keep an open **release PR** with the next version bump and
   `CHANGELOG.md` entries.
2. Merging that PR cuts a **GitHub Release** and tag.
3. On release, kicad-bot re-runs **ERC/DRC** (gating the release on a clean
   design), resolves the **BOM**, and produces **Gerbers / drill / position**
   files. These are zipped into `e-foc-hw-<version>.zip` and attached to the
   release as a downloadable fabrication package.

Version state is tracked in [`.release-please-manifest.json`](.release-please-manifest.json);
behaviour is configured in [`release-please-config.json`](release-please-config.json).

---

## License

Released under the [MIT License](LICENSE).
