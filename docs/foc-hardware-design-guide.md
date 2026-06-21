# FOC Motor-Driver Hardware Design Guide

> A **component-agnostic** body of knowledge for designing and reviewing a
> three-phase BLDC/PMSM Field-Oriented-Control power board. It captures what a
> senior power-electronics / embedded-hardware engineer checks when designing
> gate drives, MOSFET inverters, current sensing, switching supplies, protection
> and the PCB layout for high-di/dt power stages.
>
> Treat this guide as an **interface**, not an implementation. It describes the
> *functional blocks* (the power stage, gate driver, current sense, supply rails,
> protection) and the *parameters* that constrain them — never specific part
> numbers. Any board is one implementation of this interface; its concrete part
> choices and numeric targets live in that board's own **README / spec**, which
> is the binding contract. When this guide and a board's spec disagree, the
> **spec wins**.
>
> **Design parameters** (referenced throughout; a board's spec fixes their
> values): $V_{bus}$ = max DC bus voltage · $I_{ph}$ = max continuous phase
> current · $f_{sw}$ = PWM switching frequency · $V_{drv}$ = gate-drive rail ·
> $N_{layer}$ = PCB copper layers · $V_{(BR)DSS}$ = FET drain-source rating.

---

## Table of contents

1. [FOC power system overview](#1-foc-power-system-overview)
2. [DC bus & input power](#2-dc-bus--input-power)
3. [Power-supply rails (buck + LDO)](#3-power-supply-rails-buck--ldo)
4. [The three-phase inverter (H-bridge power stage)](#4-the-three-phase-inverter-h-bridge-power-stage)
5. [Gate drive](#5-gate-drive)
6. [Current sensing](#6-current-sensing)
7. [Protection: overvoltage, overcurrent, overtemperature](#7-protection-overvoltage-overcurrent-overtemperature)
8. [Thermal design](#8-thermal-design)
9. [PCB layout — the part that makes or breaks it](#9-pcb-layout--the-part-that-makes-or-breaks-it)
10. [KiCad workflow: tips & tricks](#10-kicad-workflow-tips--tricks)
11. [Design-review checklist](#11-design-review-checklist)
12. [References](#12-references)

---

## 1. FOC power system overview

Field-Oriented Control drives a 3-phase motor by synthesising sinusoidal phase
currents. The hardware's job is to (a) make a clean, low-impedance DC bus, (b)
switch that bus onto three motor phases with a MOSFET inverter, (c) measure the
phase currents accurately and in sync with the PWM, and (d) protect everything
when the motor or firmware misbehaves.

```
  Vbus ──┬─ bulk C ─┬───────────────┐  (high-di/dt power loop)
         │          │   ┌──────┐     │
   TVS  ===   buck →│   │  HS  │← gate│
   clamp  │  V_drv  │   ├──────┤ drive│  ← 3-φ gate driver (bootstrap HS supply)
         │  +5/3V3 │   │  LS  │     │
   GND ──┴──────────┴───┴──┬───┴─────┘
                           │ phase A/B/C → motor
                        Rshunt (low-side)
                           │
                        op-amp → ADC  (sampled at PWM centre)
```

The four signal domains — **power** (bus + phases), **gate drive** (fast,
high-dV/dt), **analog sense** (small-signal, noise-sensitive) and **logic/comms**
(MCU, CAN, memory) — must be deliberately partitioned. 90 % of FOC board failures
are not schematic errors; they are **layout** errors where one domain corrupts
another.

**The board's spec fixes the operating point** — the bus ceiling $V_{bus}$, the
continuous phase current $I_{ph}$, the PWM frequency $f_{sw}$, the stackup
$N_{layer}$, and the feedback type (encoder *or* hall). Read those from the
board's README/spec; this guide stays parametric so it applies to any of them.

---

## 2. DC bus & input power

### 2.1 Bus voltage and device margin

Pick switching devices with comfortable headroom over the **worst-case** bus,
not the nominal bus. The worst case is nominal + supply tolerance + **inductive
switching ringing** (the spike across the FET each time it turns off the motor's
inductance through the parasitic loop inductance).

- Example: a $V_{bus}$ of 50 V with a 60 V-rated FET leaves only **10 V (20 %)**
  of margin. That margin is consumed by ringing, so it must be *held by layout*
  (tight loop, bulk + local HF bypass, optional RC snubber) — **not** by the bus
  TVS, which clamps well above $V_{(BR)DSS}$ (see §7.1). A small $V_{(BR)DSS}$ −
  $V_{bus}$ margin is the single most layout-critical constraint on the power
  stage.
- Rule of thumb: target the steady bus at **≤ 80 %** of $V_{(BR)DSS}$, and keep
  the ringing peak below 100 %. If you cannot, raise the FET voltage class.

### 2.2 Bulk capacitance

The inverter draws pulsed current from the bus at the PWM frequency; the bus
caps supply that pulse so the source wiring doesn't have to. Two jobs:

1. **Bulk / electrolytic** — holds the bus stiff over a PWM cycle and absorbs
   energy when the motor regenerates (decelerates). Size for the **RMS ripple
   current**, not just capacitance. Inverter input-cap RMS ripple peaks near
   `Iripple,rms ≈ Iphase,rms × √(modulation factor)`; a practical first cut is
   `≈ 0.5 × Iphase,peak`. For a 5 A phase that is ~2.5 A RMS — choose an
   electrolytic/polymer rated for that ripple at $f_{sw}$ and temperature, or
   parallel several. Under-rated bulk caps run hot and dry out.
2. **High-frequency ceramic** — one or more low-ESL MLCCs (e.g. 100 nF–1 µF)
   placed **directly across each half-bridge** (drain of high-side FET to source
   of low-side FET) to carry the fast di/dt edge locally and kill ringing. This
   cap and the two FETs form the **commutation loop** — keep it physically tiny
   (§9.2). Derate MLCC voltage hard: rate the bypass MLCC at roughly **2×**
   $V_{bus}$ (e.g. ≥ 100 V X7R for a 50 V bus) because class-II ceramics lose
   most of their capacitance near rated voltage (DC-bias derating).

### 2.3 Inrush, reverse polarity, soft-start

- **Inrush:** charging the bulk caps at power-up is a near-short. For bench
  supplies a series NTC or a soft-start (pre-charge resistor bypassed by a
  MOSFET/relay after charge) prevents weld-on inrush and connector arcing.
- **Reverse polarity:** a series P-FET (or ideal-diode controller) protects the
  whole board from a flipped supply with far less drop than a Schottky.
- **UVLO:** make sure the gate-drive rail and logic come up *before* PWM is
  enabled, and that the gate driver's own UVLO holds the FETs off below the
  point where they would only partially enhance (high Rds(on) → thermal runaway).

---

## 3. Power-supply rails (buck + LDO)

A FOC board typically needs: the **gate-drive rail** $V_{drv}$ (commonly
10–15 V), **+5 V** (analog, CAN, sometimes the MCU) and **+3.3 V** (logic,
memories). A common topology derives these from a **wide-input synchronous buck**
feeding one or more **linear regulators (LDOs)**.

### 3.1 Wide-input buck

- **Why a buck rated well above $V_{bus}$?** Headroom for the same ringing/
  load-dump transients the FETs see. The buck's `VIN` pin sees the raw bus, so a
  regulator rated ~2× $V_{bus}$ survives the spike a marginal part would not.
- **Switch node** is the noisiest net on the board — high dV/dt. Keep its copper
  **small** (just big enough for current) to minimise the radiating/coupling
  area, and keep it away from analog sense traces.
- **Inductor** sized for the chosen ripple (typically 20–40 % of load). Keep the
  hot loop (VIN cap → high-side switch → inductor → output cap → GND) tight, just
  like the inverter commutation loop.
- **Feedback** divider tapped at the point of regulation; route FB away from the
  switch node and inductor; respect the datasheet's recommended FB placement.
- **Thermal:** check the buck's dropout, switching loss and junction temperature
  at the full logic + gate-drive load and worst-case ambient.

### 3.2 LDOs and sequencing

- LDOs are simple but **dissipative**: `P = (Vin − Vout) × Iload`. An LDO
  dropping 5 V → 3.3 V at 300 mA burns ~0.5 W — check the package thermal-pad
  copper. Don't feed an LDO directly from the high bus; cascade from the buck.
- **Dropout:** a typical LDO needs ~1–1.3 V of headroom — too much for a 3.3 V
  rail fed from a sagging 3.6 V source. Verify the input rail stays above
  Vout + dropout.
- **Analog rail:** give the op-amp / ADC reference rail its own filtered feed
  (ferrite bead + local cap, or a separate LDO) so PWM noise on the logic 3.3 V
  doesn't ride into the current-sense chain.
- **Sequencing:** logic before PWM enable (§2.3). If the logic rail and the
  gate-drive rail $V_{drv}$ come up in the wrong order, define the safe state
  (FETs off).

---

## 4. The three-phase inverter (H-bridge power stage)

Three half-bridges (one per phase) make the 3-phase inverter. Each half-bridge =
a high-side (HS) and low-side (LS) FET in totem-pole; their midpoint is the motor
phase. A common choice is **one dual-FET half-bridge package per phase** — the HS
and LS FET share one package, which already minimises the internal loop. Discrete
FETs work too, at the cost of a larger commutation loop to manage.

### 4.1 MOSFET selection criteria

| Parameter | What to check | Why it matters |
|-----------|---------------|----------------|
| **Vds / $V_{(BR)DSS}$** | ≥ $V_{bus}$ + ringing margin (§2.1) | a tight margin is layout-critical |
| **Rds(on)** | Conduction loss `Irms² · Rds(on)`; rises ~+0.4 %/°C — use the hot value | sets conduction heat at $I_{ph}$ |
| **Qg / Qgd** | Switching loss & gate-drive current; Qgd sets dV/dt & Miller risk | scales loss with $f_{sw}$ |
| **SOA / avalanche** | Survives the turn-off energy of the motor inductance | check `Eas` |
| **Body diode / Qrr** | Conducts dead-time; Qrr causes reverse-recovery spikes & shoot-through | dead-time & EMI |
| **Thermal (Rθjc/Rθja)** | Junction temp at full load; package thermal pad | pad must reach copper |

> **Footprint reality check.** Power FETs in SON/PowerPAK/DFN packages have a
> large bottom **thermal/source pad** that *is* the heatsink and a primary
> current path. Assigning a generic SOIC-8 land instead of the datasheet's
> recommended land pattern is a **MAJOR** error — it won't solder, the thermal
> path is broken, and Rds(on) effectively rises. Always match the BOM footprint
> to the datasheet package code and recommended land.

### 4.2 Conduction vs switching loss (per FET)

- **Conduction:** `P_cond = Irms² · Rds(on,hot)`. Use the data-sheet Rds(on) at
  the expected junction temperature, not 25 °C.
- **Switching:** `P_sw ≈ 0.5 · Vbus · Iphase · (t_on + t_off) · fsw + Qrr·Vbus·fsw`.
  At a few-tens-of-kHz $f_{sw}$ this is real but usually secondary to conduction;
  it grows fast with $V_{bus}$ and gate-resistor value.
- Sum HS+LS per phase, ×3 phases, and feed §8 (thermal).

### 4.3 Dead-time & shoot-through

If HS and LS conduct together for even an instant, the bus is shorted through
both FETs (**shoot-through**) — instant destruction. Prevent it with:

- **Dead-time:** a gap where both gates are low between HS and LS conduction.
  Many integrated 3-phase gate drivers provide internal dead-time and
  cross-conduction lockout; still verify the value vs the FET's turn-off +
  Miller-plateau time.
- Beware **dV/dt-induced turn-on** (Miller): when one FET switches, the fast
  dV/dt at the other FET's drain couples through Cgd and can momentarily turn it
  on. Mitigate with a low gate-off impedance, a **Miller clamp**, or a small
  gate-to-source resistor/cap (§5.3).

### 4.4 The commutation loop (most important power node)

The loop **bus-cap (+) → HS drain → HS channel → phase → LS channel → LS source
→ bus-cap (−)** carries the full switched di/dt. Its parasitic inductance `L_loop`
turns every turn-off into a voltage spike `V = L_loop · di/dt`. Minimising
`L_loop` is the #1 layout objective (§9.2) and is what protects the
$V_{(BR)DSS}$ − $V_{bus}$ FET margin.

---

## 5. Gate drive

The gate driver turns the FETs on/off fast enough to keep switching loss low, but
not so fast that ringing and EMI explode. The reference topology here is a
**3-phase gate driver with a bootstrap high-side supply**.

### 5.1 High-side bootstrap

The HS FET's source swings from 0 to the bus, so its gate must be driven
*relative to that moving source* — typically `Vgate = Vphase + V_drv`. A
**bootstrap** supply does this cheaply:

- When the LS FET is on (phase ≈ 0 V), the **bootstrap cap** `Cboot` charges from
  the gate rail through the **bootstrap diode** `Dboot`.
- When the HS FET turns on, `Cboot` floats up with the source and supplies the HS
  gate charge.

**Bootstrap cap sizing:** `Cboot ≥ Qtotal / ΔVboot`, where
`Qtotal ≈ Qg(HS) + Qrr(Dboot) + (Iqbs · t_on_max) + Qls_level_shift`, and ΔVboot
is the allowed droop (keep < a few hundred mV so the HS gate stays well above its
plateau). A common practical rule is `Cboot ≥ (10…50) · Qg / ΔVboot`; e.g. for
Qg ≈ 15 nC and ΔVboot ≈ 0.1 V, `Cboot ≥ 150 nF` → choose **1 µF** for margin.
Place `Cboot` right at the driver's VB/VS pins.

**Bootstrap diode:** fast-recovery, reverse voltage ≥ bus, low Qrr; sized for the
peak refresh current. A series resistor can limit the refresh inrush.

**Bootstrap refresh / 100 % duty limit:** the HS rail only recharges while the LS
FET conducts. Near 100 % duty (or a stalled rotor held in one position) the
bootstrap can starve. If the application needs sustained 100 % HS-on, use a
charge pump or isolated HS supply instead.

### 5.2 Gate resistors

The gate resistor `Rg` sets the switching speed and damps the gate-loop ringing
(driver output L + gate-loop L + Ciss form a resonant circuit).

- **Larger Rg:** slower edges → lower dV/dt, di/dt, EMI and overshoot, but more
  switching loss and heat. **Smaller Rg:** faster, more efficient, but more ring
  and Miller-induced turn-on risk.
- Often a **split** drive: a small `Rg(on)` via a diode for controlled turn-on and
  a smaller `Rg(off)` for fast, low-impedance turn-off (turn-off speed fights
  Miller shoot-through). Start from the driver/FET app-note value and tune on the
  bench with a scope on Vds and Vgs.

### 5.3 Miller clamp & negative drive

- **Miller (dV/dt) turn-on:** during the opposite FET's fast edge, Cgd injects
  current into the off FET's gate. A **strong pull-down** (low `Rg(off)`,
  dedicated clamp pin, or active Miller clamp) holds Vgs below threshold.
- On a tight low-voltage design a small gate-to-source resistor and minimal gate
  loop usually suffice; negative gate drive is reserved for higher-voltage SiC.

### 5.4 Layout coupling to the driver

Gate loops (`driver out → Rg → FET gate → FET source → driver return`) must be
**small and matched** between HS and LS, and routed away from the switch node.
Long, loopy gate traces pick up the phase dV/dt and cause false switching. Keep
the driver physically close to its FET block — this is a manufacturer-recommended
layout point you should verify against the gate-driver datasheet's layout section.

---

## 6. Current sensing

FOC needs accurate phase-current feedback, **time-aligned to the PWM**. Three
common topologies:

| Topology | Pros | Cons |
|----------|------|------|
| **Low-side shunt** (in each LS source to GND) | Cheap, ground-referenced amp, no CM voltage | Only valid while LS FET is on → tight sample timing; misses current at high duty |
| **Inline / in-phase shunt** | Measures true phase current any time | Amp must reject the full phase common-mode swing (needs high-CMVR current-sense amp) |
| **Hall / magnetic** | Isolated, no I²R loss | Cost, offset/drift, bandwidth |

### 6.1 Shunt sizing

- **Resistance:** trade signal vs loss. A **20 mΩ** shunt gives `5 A → 100 mV`
  full-scale — a healthy signal for a modest-gain amp. Pick $R_{sh}$ so
  $I_{ph} · R_{sh}$ lands in the amp's comfortable input range.
- **Power:** `P = I² · R`. At 5 A through 20 mΩ: `25 · 0.02 = 0.5 W`; at a 7 A
  peak, ~1 W. Choose a **≥ 1 W, ideally 2 W (2512) low-inductance** shunt and
  give it copper to spread heat. Under-rated shunts drift with temperature and
  add error.
- **Tempco & inductance:** use a low-tempco (≤ 50 ppm/°C) metal-element shunt;
  current-sense resistors with low parasitic inductance avoid corrupting the
  fast LS-on current waveform.

### 6.2 Kelvin (4-wire) sensing

Sense the shunt voltage **across the element itself**, with two dedicated traces
landing on the shunt's inner sense points — *not* tapped off the high-current
copper. Any power-current copper between the tap points adds IR error. This
**Kelvin connection** is mandatory for a milliohm-scale shunt where trace
resistance is a large fraction of the shunt (§9.4).

### 6.3 Amplifier chain

A current-sense op-amp (or dedicated current-sense amplifier) conditions the
shunt signal before the ADC. Check:

- **Gain:** map full-scale current to the ADC range with margin (e.g. 100 mV ×
  gain ≈ 2–3 V into a 3.3 V ADC). For **low-side** shunts the measurement is
  ground-referenced and (per LS-on window) unipolar; the bidirectional phase
  current is reconstructed in firmware. For **inline** sensing the amp must be
  biased to mid-rail for bipolar current.
- **Bandwidth:** the amp + filter must settle within the ADC sampling window.
  Too little BW smears the current; too much passes switching noise. Set the
  anti-alias corner above the control bandwidth but below half the ADC rate.
- **Offset & CMRR:** op-amp `Vos` and gain error appear as current-measurement
  offset → torque ripple. Calibrate zero-current offset in firmware at startup.
- **Protection:** clamp/limit the amp inputs against the shunt's switching spikes;
  keep the filter caps close to the ADC pin.

### 6.4 Sampling in sync with PWM

Low-side sensing only sees phase current while the LS FET conducts, which is
around the **PWM cycle centre** (for centre-aligned PWM). The ADC trigger must
fire in that window. This is firmware + timer work, but the hardware must give
the amp enough bandwidth to settle before the sample and must not have so much
filter delay that the valid window closes. Document the expected sample instant so
firmware and hardware agree.

---

## 7. Protection: overvoltage, overcurrent, overtemperature

### 7.1 Overvoltage / transient (TVS, clamp ratio, snubbers)

Two different overvoltage problems, two different fixes:

1. **Gross bus transients** (load dump, long-lead inductive kick, hot-plug):
   absorbed by a **bus TVS**. Pick its standoff (Vrwm) just above $V_{bus}$ so it
   does not conduct in normal operation. Example: a 50 V bus pairs with a ~51 V
   standoff TVS whose clamp sits near ~82 V. The consequence: that **clamp is
   above a 60 V FET rating** — so the TVS protects the *board* from catastrophic
   transients but does **not** protect the FETs from fast switching ringing. Hold
   the FET margin with layout (below).
   - **Clamp ratio reality:** a silicon TVS clamps at roughly **1.4–1.6×** its
     standoff. You cannot both stand off $V_{bus}$ and clamp under a close
     $V_{(BR)DSS}$ with one TVS. If the FETs needed TVS protection you would need
     a higher-voltage FET class.
2. **Fast switching ringing** (every turn-off): controlled by **(a)** a small
   commutation loop, **(b)** local HF bypass MLCC at each half-bridge, **(c)**
   gate-resistor slowing, and optionally **(d)** an **RC snubber** across each
   half-bridge or the bus. Snubber design: `Rsnub ≈ √(L_loop/C_oss)`,
   `Csnub ≈ (2…4)·C_oss`; tune on the bench to critically damp the ring without
   excessive snubber loss `P = Csnub·Vbus²·fsw`.

### 7.2 Overcurrent (OCP)

Layers of defence, fastest first:

- **Hardware cycle-by-cycle / fault trip:** compare the shunt voltage (or a
  dedicated sense) against a threshold with a fast comparator that pulls the
  driver into shutdown / forces all FETs off within a PWM cycle. This catches
  short-circuit and shoot-through before the FET SOA is exceeded — software is too
  slow for a hard short.
- **Gate-driver fault / desat (if supported):** some drivers offer over-current
  or desaturation detection with a fault flag back to the MCU.
- **Firmware current limit:** the FOC loop limits commanded current
  continuously; this protects the motor and handles soft overloads, but is *not*
  the last line of defence against a dead short.
- **Fuse:** a series bus fuse (or the source's current limit) is the final,
  non-resettable backstop.

Define the trip level above the legitimate peak FOC current (including transient
overshoot) but below the FET/shunt SOA, and verify the response time.

### 7.3 Overtemperature (OTP)

- **NTC placement:** put a thermistor on the copper **next to the FET block /
  shunts** (the hot spots), read by an MCU ADC channel; fold its limit into the
  firmware fault logic (fold-back current, then shutdown).
- **FET/driver thermal shutdown:** rely on it as a backstop, not the primary
  limit — by the time the junction TSD trips, the part is already at its limit.
- On a low-layer-count board the FET package pad + thermal copper + vias *is* the
  heatsink; monitor it (§8).

### 7.4 ESD / EMC on I/O

- **CAN:** ESD array on CANH/CANL (transceivers carry some ruggedness, but cable
  ports want external protection); common-mode choke optional for EMC; bus
  termination per CAN spec; **keep GND present in the connector** so nodes share
  a reference.
- **Encoder/Hall:** series resistors + RC/ESD on the exposed connector pins;
  the cable is an antenna — filter at the connector.
- **Logic/SPI/I²C:** keep memory (EEPROM, SPI flash) decoupled and short; pull-up
  values per bus speed.

### 7.5 UVLO / safe-state

Every driver and rail should define the **FET-off safe state** for: power-up,
brown-out, gate-rail loss, and fault. Verify the FETs cannot be left half-on.

---

## 8. Thermal design

A 2-layer board has no inner planes, so **copper area + thermal vias** are the
heatsink. Budget the loss, then provide the copper.

1. **Loss budget:** sum per-FET conduction + switching (§4.2) across all 6 FET
   positions, plus shunt I²R (§6.1, ~3 × 0.5–1 W), plus buck/LDO loss (§3). For a
   typical low-voltage drive this is a few watts total — manageable in copper if
   spread.
2. **Copper as heatsink:** maximise pour around the FET drain/source pads and
   shunts; the package thermal pad must connect to a large pour, not a thin trace.
3. **Thermal vias:** stitch the FET/shunt thermal pads to the opposite-layer pour
   with an array of vias (e.g. 0.3 mm drill, ~1 mm pitch) to use both copper
   layers and the bottom-side air. More vias = lower Rθ.
4. **Verify junction temp:** `Tj = Ta + P · Rθja(as-laid-out)`. The datasheet
   Rθja assumes a reference copper area — your real number depends on your pour.
   Keep `Tj` below ~ 110–125 °C with margin at worst-case ambient.
5. **Hot-spot sensing:** §7.3 NTC closes the loop.

---

## 9. PCB layout — the part that makes or breaks it

> For a high-di/dt FOC stage, layout *is* the design. The schematic can be perfect
> and the board still fail from a loose commutation loop or a non-Kelvin shunt.
> This section is the priority order a senior engineer places components in.

### 9.1 Placement strategy ("first position" — do this first)

Place in this order, **power first**, before routing anything:

1. **Connectors & board outline fixed** — bus input, motor phase output, MCU
   header, CAN, encoder — at the edges where the harness wants them. These anchor
   everything.
2. **Power stage** — the three half-bridges (one dual-FET block per phase), their
   local HF bypass MLCCs, and the phase shunts. Arrange the three phases in a
   regular row so each commutation loop is identical and tiny. The bulk caps sit
   right at the FET drains.
3. **Gate driver** hugging the FET block, with `Cboot`/`Dboot` and gate resistors
   right at the driver, gate loops short and HS/LS matched.
4. **Current-sense amplifiers** close to their shunts with Kelvin taps; keep the
   analog group away from the switch nodes and gate loops.
5. **Buck regulator** with its own tight hot loop, switch node small, away from
   analog.
6. **Logic & comms** — MCU header, CAN transceiver, EEPROM/flash — in the quiet
   corner, fed by the filtered logic rails.

Group parts by schematic sheet; keep each functional block contiguous. Decide the
**grounding scheme now** (§9.5), not after routing.

### 9.2 Power commutation loop (the #1 rule)

Make the loop **bus-cap(+) → HS → phase → LS → bus-cap(−)** as small as physically
possible:

- Put the **HF bypass MLCC directly across the half-bridge** (HS drain to LS
  source), with the shortest, widest connection — it carries the di/dt edge.
- Keep the high-side drain, the FET package, and the low-side source/shunt return
  tightly clustered. On 2 layers, return the loop on the bottom layer **directly
  under** the top-layer power path so the go and return currents overlap and
  cancel inductance (image-plane effect).
- Every mm of loop is inductance; inductance × di/dt is the spike that eats the
  $V_{(BR)DSS}$ − $V_{bus}$ FET margin. This is where that margin is won or lost.

### 9.3 Gate loop

- Short, **matched** HS and LS gate loops; gate and gate-return run together
  (parallel/over each other) to minimise loop area.
- Route gate traces **away from** the switch node / phase copper; never run a gate
  trace under the phase pour where dV/dt couples in.
- Place `Rg`, `Cboot`, `Dboot` at the driver pins.

### 9.4 Kelvin current sense

- Tap the shunt at its dedicated **sense terminals**, with two thin traces routed
  as a **tight differential pair** straight to the amplifier — no power current
  between the taps.
- Keep these traces short, away from gate/switch nodes, guarded by analog ground.
- The amplifier's reference/return lands at the **single analog-ground point**,
  not on the noisy power ground (§9.5).

### 9.5 Grounding (star / single-point on 2 layers)

- Separate **power ground** (bulk-cap return, FET sources, shunt high-current
  side) from **analog ground** (amp refs, ADC, filter caps) and **digital ground**
  (MCU, memories, CAN logic) in your mind and on the copper.
- Join them at **one point** — the natural choice is at the shunt/ADC-reference
  region so the current-sense return is clean. Avoid loops where power return
  current flows through analog-ground copper.
- On a 2-layer board the bottom layer is your ground strategy: a mostly-solid
  bottom **ground pour** under the power and analog sections gives the return
  path and the image plane (§9.2). Don't let high-di/dt power return and quiet
  analog return share the *same* copper region without a deliberate single tie.

### 9.6 Copper pours / planes

- **Power pours:** wide, solid copper for bus +, phases, and grounds; size for
  current (§10.4). Connect power-FET and shunt pads to pours with **solid**
  copper (no thermal-relief spokes) so you don't choke current or heat.
- **Ground pour:** fill the bottom layer; stitch top and bottom grounds with
  vias, especially around the power loop and along board edges.
- **Clearance & slivers:** set zone clearance to your fab's minimum (§10) and
  enable island/sliver removal so the pour doesn't leave unconnected copper
  fragments or acid traps.
- **Switch-node pour:** keep it just large enough for current; do **not** flood a
  big switch-node pour (it radiates).

### 9.7 High-voltage spacing (creepage & clearance)

- Apply IPC-2221 spacing for the $V_{bus}$ and phase nets — at tens of volts the
  electrical minimum is small, but give generous spacing at connectors and under
  the FETs for manufacturability and transient margin.
- Keep enough **edge clearance** (copper to board edge) for the fab and for HV
  creepage to mounting hardware.

### 9.8 Decoupling & filtering placement

- Every IC's bypass cap **at its supply pin**, smallest value closest.
- Analog rail filtered (ferrite + cap) separately from logic (§3.2).
- Filter cable ports (CAN, encoder) **at the connector** (§7.4).

### 9.9 Mechanical & assembly

- Mounting holes / stack-mate connectors positioned per the mechanical stack —
  when the board mates to another (adapter, carrier, or controller board), the
  connectors and holes must register; see the stack-mate notes in the reviewer
  agent.
- Fiducials for assembly, polarity marks on silk, test points on key nets
  (bus, rails, phase, shunt, faults) for bring-up.

---

## 10. KiCad workflow: tips & tricks

Practical KiCad (v8–v10) steps for getting a clean, fabricable board.

### 10.1 Schematic & ERC first

- **Power symbols & `PWR_FLAG`:** every supply net needs a source. KiCad's ERC
  flags "input power not driven" until you place a `PWR_FLAG` on rails fed from
  connectors/regulals outputs. Don't silence the error by deleting it — place the
  flag where power genuinely enters.
- **Net labels everywhere:** name bus, phase, gate, sense and rail nets
  explicitly; it prevents accidental merges and makes the PCB readable. Watch for
  **aliased/merged sense nets** (e.g. three phase-shunt returns accidentally on
  one net) — a classic FOC ERC/derived bug.
- **No-connect flags** on intentionally unused pins; correct **pin electrical
  types** on hierarchical sheet pins so ERC's input/output/power checks are
  meaningful.
- Run **ERC clean** (or every remaining item justified) before layout. Treat ERC
  as design intent verification, not noise.

### 10.2 Component placement order (mirrors §9.1)

- Lock the board outline and connectors, then place power → gate → sense → buck →
  logic. Use **Place Footprints by sheet** / cross-probe from schematic to keep
  blocks together.
- Set the **drill/aux origin** to a board corner early — the assembly CPL export
  references it (the `scripts/export_jlcpcb.py` CPL must be board-relative, not
  page-space, or JLCPCB has to re-align it).

### 10.3 Net classes (set widths & clearances by function)

- Define net classes in **Board Setup → Net Classes**: e.g. `Power` (bus/phase,
  wide tracks, large clearance), `Gate`, `Analog`, `Default/Logic`.
- Assign track width and clearance per class so the autorouter/DRC enforce your
  current and isolation intent. This is faster and safer than hand-setting each
  trace.

### 10.4 Track width vs current (IPC-2152)

- Rough external-layer guide (1 oz / 35 µm copper, ~10 °C rise): **~0.5 A per
  10 mil (0.25 mm)** of width as a starting point.
  - ~1 A → ~12 mil (0.3 mm)
  - ~3 A → ~40 mil (1.0 mm)
  - **~5 A → ~100–120 mil (2.5–3.0 mm)** (size bus/phase for $I_{ph}$)
- Use **2 oz copper** to roughly halve the required width if the power copper is
  crowded — note the cost/fab implication. Always confirm against an IPC-2152
  calculator for your copper weight, rise and layer (internal traces need more).

### 10.5 Via sizing & current

- A single 0.3 mm-drill via carries roughly **~1 A** at a modest rise — use
  **arrays of vias** for power nets and thermal pads, never one via for a 5 A net.
- Stitch ground pours and FET/shunt thermal pads with via fields (§8).

### 10.6 Copper zones (pours)

- **Connection style:** set power/thermal pads to **solid** zone connection (no
  thermal spokes) so current/heat aren't choked; use thermal relief only where
  hand-soldering ease matters on signal pads.
- **Zone clearance / min width:** set to your fab minimum (§10.8); enable
  **remove islands** to delete orphan copper and avoid slivers/acid traps.
- **Zone priority & two grounds:** give overlapping zones explicit priority;
  keep analog-ground and power-ground zones distinct with the single tie (§9.5).
- **Refill (`B`) and re-run DRC** after every change — KiCad pours are not live by
  default; a stale pour hides shorts/clearance errors.

### 10.7 Teardrops

- Add **teardrops** (Route → ... / Board Setup → Teardrops in v7+, or the
  teardrop dialog) where tracks meet pads/vias. Benefits: mechanical strain
  relief, better drill-to-track registration, fewer acid traps, and a smoother
  current path on power tracks.
- Enable for vias and pads on the power nets especially; verify they don't create
  clearance violations on dense areas, then re-run DRC.

### 10.8 Design rules → match the fab (and DRC)

- Set **Board Setup → Constraints / Design Rules** to your fab's *standard* tier
  to stay cheap. Typical JLCPCB 2-layer standard (verify live — these change):
  min track/space **~0.127 mm (5 mil)** (6 mil is safest/cheapest), min drill
  **~0.2–0.3 mm**, min annular ring **~0.13 mm**, min hole-to-hole, min silk
  width **~0.15 mm**. Seeed Fusion is comparable — check both capability pages.
- Find the **single tightest rule** that would push you into an "advanced" price
  tier and relax it if you can.
- **Run DRC (`Inspect → Design Rules Checker`) to zero** before fab. Categorise:
  - *Clearance / track-width* — real, fix.
  - *Unconnected items* — real, route or intentionally tie.
  - *Schematic parity* — board doesn't match schematic; fix before trusting nets.
  - *Silk over pad / silk clipped* — cosmetic but fix for legibility & mask.
  - *Courtyard overlap* — placement/assembly risk.
- Locate each violation in the layout (KiCad zooms to it) and resolve or annotate.

### 10.9 Solder mask & paste

- **Mask expansion:** keep the default unless a part needs mask-defined pads;
  over-large expansion on fine-pitch parts bridges.
- **Mask slivers / web:** between close pads (fine-pitch IC, the FET pad fingers)
  the mask web can fall below the fab's minimum and peel — DRC/fab check flags
  this; widen pad spacing or accept a mask-defined region.
- **Via tenting:** tent (cover) vias under parts and in pours to stop solder
  wicking and shorts; leave test-point vias open.
- **Paste (stencil):** for the **FET/regulator thermal pads**, use a *windowpane*
  (segmented) paste aperture (~60–80 % coverage), not a single big opening, to
  avoid the part floating on a solder ball and tombstoning — check the assembly
  paste layer (`*.gtp/*.gbp`) reflects this.

### 10.10 Silkscreen & fab outputs

- Keep silk **off pads and mask openings**; meet the fab's min line width/height;
  add polarity marks, pin-1, refdes, and a board version/date.
- Export the **fab package** with `scripts/export_jlcpcb.py` and sanity-check:
  gerbers complete (copper/mask/paste/silk/Edge.Cuts/PTH+NPTH drill), **BOM has
  LCSC part numbers** for every assembled line, and the **CPL origin** is
  board-relative (§10.2). Generate the **review package** with
  `scripts/export_review.py` and look at the rendered PCB images before sign-off.

---

## 11. Design-review checklist

A condensed go/no-go list (the reviewer agent expands on this):

**Spec & devices**
- [ ] FET $V_{(BR)DSS}$ ≥ $V_{bus}$ + ringing margin; a tight margin is held by layout, not the TVS.
- [ ] FET/driver/regulator operating points inside datasheet recommended ranges.
- [ ] **Footprint matches the real package land** (esp. SON/PowerPAK FETs, thermal pad).
- [ ] Shunt power rating ≥ I²R at peak with margin; low tempco/inductance.
- [ ] TVS stands off $V_{bus}$ and is understood to clamp above the FET (board, not FET, protection).
- [ ] Bootstrap cap/diode sized; 100 %-duty/refresh case considered.

**Protection**
- [ ] Overcurrent: fast hardware trip + firmware limit + fuse backstop, level set below SOA.
- [ ] Overtemp: NTC at hot spots into firmware fold-back/shutdown.
- [ ] UVLO / safe-state defines FETs-off on power-up, brown-out, fault.
- [ ] ESD/filtering on CAN, encoder/hall, and cable ports.

**Layout**
- [ ] Commutation loop tiny; local HF bypass across each half-bridge; bottom-layer image return.
- [ ] Gate loops short & HS/LS matched, away from switch node.
- [ ] Kelvin shunt taps; analog ground single-point tie at sense/ADC ref.
- [ ] Power copper sized for $I_{ph}$ (IPC-2152); via arrays on power & thermal pads.
- [ ] Thermal copper + vias for FETs/shunts; junction-temp budget OK.
- [ ] Star/single-point ground; switch-node copper minimised.

**Manufacturing (KiCad/fab)**
- [ ] ERC clean (PWR_FLAGs placed, no merged sense nets).
- [ ] DRC clean and matched to fab standard tier; tightest cost-driving rule known.
- [ ] Pours refilled; islands removed; teardrops added on power nets.
- [ ] Mask webs/slivers OK; thermal-pad paste windowpaned; vias tented as needed.
- [ ] Drill/aux origin set; CPL board-relative; BOM has LCSC numbers; gerbers complete.
- [ ] Stack-mate connectors/holes register with the mating board(s).

---

## 12. References

Verify ratings against the live datasheet/app-note — numbers here are for
orientation and may change.

**Functional blocks** (this guide is component-agnostic — pick parts that meet
each block's interface; a board's README lists its concrete choices)
- **Power stage** — dual-FET half-bridge (one per phase), $V_{(BR)DSS}$ > $V_{bus}$.
- **Gate driver** — 3-phase, bootstrap high-side, internal dead-time.
- **Buck regulator** — wide-input synchronous buck, rated ~2× $V_{bus}$.
- **Current-sense amplifier** — low-offset op-amp / current-sense amp.
- **Bus TVS** — standoff just above $V_{bus}$ (see §7.1 clamp-ratio note).
- **CAN transceiver**, **I²C EEPROM**, **SPI NOR flash** — comms & storage.

**Standards & app-notes (general)**
- **IPC-2152** — conductor current-carrying capacity (track width vs current).
- **IPC-2221** — generic PCB design, spacing/creepage/clearance.
- Gate-driver / power-stage **layout app-notes** from the chosen driver & FET
  vendors (commutation loop, bootstrap, Kelvin sense, decoupling) — pull the
  specific parts' layout sections during review.
- **JLCPCB** capabilities (jlcpcb.com/capabilities) and **Seeed Fusion** PCB
  capabilities — manufacturability limits; verify live, they change.

---

> **For the reviewer agent:** treat §11 as the checklist, §7/§9 as the
> power-electronics rationale, and §10 as the KiCad/manufacturability basis. This
> guide is component-agnostic; the board's own README/spec remains the binding
> contract (specific parts and numeric targets), and this guide explains the
> *why* behind each check.
