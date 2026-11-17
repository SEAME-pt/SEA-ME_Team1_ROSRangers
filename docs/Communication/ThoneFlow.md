---
title: ThoneFlow-3901U Optical-Flow Sensor Integration (Planned)
type: sensor-integration
status: draft
date: 2026-10-12
updated: 2026-11-16
project: SEA autonomous drone
tags:
  - sea-drone
  - sensor
  - optical-flow
  - thoneflow-3901u
  - pmw3901
  - uart
  - esp8266
  - betaflight
  - ros2
  - state-estimation
  - draft
---

# ThoneFlow-3901U Optical-Flow Sensor Integration (Planned)

> [!tip] Current state (2026-10-23): the accepted decisions are in [[Decision Register]]. Where this draft and an ADR disagree, the ADR wins. The rest is kept as history.

> [!success] Update 2026-11-16: bench characterisation done (#107)
> The sensor was characterised **alone, on a bench Raspberry Pi Pico, off the drone** (2026-11-10 and 2026-11-16, 40 captures, 66,297 frames). The datasheet is wrong on two points that matter for the parser: the rate is **~95 Hz, not 66 Hz**, and X/Y come **low byte first**. The checksum is the sum of bytes 2–5. On the carpet the motion is correct and repeatable; on a plain white table and on a black EVA mat it is not, and on the mat it was once **reversed with good quality**. Results in §2.4, parser in §3.3, test log in §9.2, decisions TF-03 to TF-07 in §10.2. Still not on the drone.

> [!warning] Status: DRAFT / PLANNED. Sensor NOT installed.
> The ThoneFlow-3901U is an unsoldered, separate module and is **not installed** on the drone. No wiring exists. It is **not confirmed** that Betaflight 4.5.1 supports this sensor. Everything under "Proposed" below is an architectural option, not a validated design. Datasheet facts are marked **[DS]**; proposals are marked **[PROPOSED]**; items the datasheet does not state are marked **[NOT STATED]**.

> [!success] Update 2026-10-12: team decisions recorded as ADRs
> The team accepted the proposals on this page. Rows marked **Accepted (ADR-0xx)** below are decided. See the linked ADR for the reasoning, and the [[Decision Register]] for the full list. Where an ADR differs from the earlier "Recommended" text, **the ADR wins** (it supersedes it).

## Contents

1. [Purpose and current installation status](#1-purpose-and-current-installation-status)
2. [Verified electrical and serial facts (datasheet)](#2-verified-electrical-and-serial-facts-datasheet)
3. [Frame format and parsing requirements](#3-frame-format-and-parsing-requirements)
4. [Optical flow vs. video camera vs. position estimation](#4-optical-flow-vs-video-camera-vs-position-estimation)
5. [Potential integration architectures](#5-potential-integration-architectures)
6. [ESP8266 serial-port limitations](#6-esp8266-serial-port-limitations)
7. [Height information and state estimation](#7-height-information-and-state-estimation)
8. [Data validity and fault handling](#8-data-validity-and-fault-handling)
9. [Testing plan](#9-testing-plan)
10. [Acceptance criteria and open decisions](#10-acceptance-criteria-and-open-decisions)
11. [Current recommendation and remaining decisions](#11-current-recommendation-and-remaining-decisions)
12. [Sources](#sources)

---

## 1. Purpose and current installation status

### Purpose

Record what is known about the ThoneFlow-3901U's communication interface and describe how it *could* fit the project's software architecture, so the team can choose an integration path deliberately and test it before relying on it.

### Current status

| Item | Status |
|---|---|
| Sensor availability | Available as a separate, unsoldered module |
| Installed on the drone | **No** |
| Wired to anything | Only to a bench Raspberry Pi Pico (USB to the PC), never to the drone |
| Tested | **Bench only** (2026-11-10, 2026-11-16, #107): serial format, rate, checksum, byte order, axes, scale and surfaces (§2.4, §9.2). Not tested on the drone or in flight |
| Betaflight support | **None** for this sensor (TF-01) |
| Integration path chosen | **No**; see §5 and §11 |

### Scope

This page covers the sensor's **communication interface and integration options** only. It does not cover mounting, the whole drone architecture, flight control design, or flight testing. Related pages:

- [[PC_to_ESP|ground-esp8266-communication]]: Wi-Fi link between the ground computer and the ESP8266.
- [[ESP_to_FC|esp8266-fc-serial-interface]]: proposed ESP8266 ↔ flight-controller UART link (provisional).

### Not the FPV camera

The drone's existing **CADDXFPV analog FPV camera is a different device** and is unrelated to this sensor. This page does not describe the camera; how (or whether) its video reaches the ground computer is out of scope.

---

## 2. Verified electrical and serial facts (datasheet)

Source: the Seeed Studio/DigiKey datasheet for SKU 101990480 (see [Sources](#sources)), read on 2026-10-12. The retrieved text is brief; figures in the original (e.g. the "Flow Frame" and "Test Chart" images) were not available as text and are not reflected here.

### 2.1 Stated in the datasheet **[DS]**

| Parameter | Datasheet value |
|---|---|
| Product | ThoneFlow-3901U, UART version of a PMW3901 optical-flow module |
| Interface | UART |
| Baud rate | 19200 |
| Output rate | "FPS: 66 Hz" (treated here as the nominal frame output rate) |
| Supply voltage | 3.3 to 3.6 V |
| Current | 10 mA |
| Dimensions | 14 × 11 × 5 mm |
| Weight | 0.6 g |
| Frame length / structure | 9 bytes; see §3 |
| Compatibility claim | Data format said to be compatible with the discontinued CX-OF module, which the datasheet says ArduCopter already supports |
| Mechanical note | Module replaces the original PMW3901 lens (datasheet cites dust and vibration protection) |

Notes on interpretation:

- The compatibility claim is the manufacturer's statement. It says nothing about Betaflight. It does **not** mean Betaflight 4.5.1 supports the sensor. The documented byte layout also differs from ArduPilot's CX-OF driver description (see §3.2).
- The datasheet frame has **no range/distance field**, which is consistent with the team's understanding that the module does **not** include a rangefinder.

### 2.2 **Not stated** in the retrieved datasheet text **[NOT STATED]**

> [!note] Several of these items were measured on the bench on 2026-11-10 and 2026-11-16. The measured values are in §2.4; the list below is the datasheet gap as found on 2026-10-12.

These must be determined (from further documentation, a reference driver, or measurement), not assumed:

- Data bits, parity and stop bits (8N1 is a common default but is **not** confirmed).
- Whether the sensor's TX output swings to 3.3 V, and whether the module has any usable RX input.
- Pin names, pin order and connector details. **No pinout is recorded on this page, deliberately.** Use the physical module markings and the manufacturer's product page, and verify by inspection (see §9).
- Checksum algorithm (see §3).
- Whether X/Y values are signed, and their units/scaling.
- Whether X/Y values are per-frame deltas or accumulated counts.
- Meaning, range and thresholds of the surface-quality field.
- Valid operating conditions (height range, lighting, surface types, field of view/lens parameters).
- Start-up behaviour, time to first frame, and behaviour when no surface is tracked.
- Whether the 66 Hz rate is exact, fixed, or varies.
- Recommended power-supply conditioning.

### 2.3 Derived timing (arithmetic, assumptions flagged)

If the serial format is 8N1 (**assumed, not stated**), then:

- one bit ≈ 52 µs at 19200 baud;
- one 9-byte frame = 90 bit times ≈ 4.7 ms;
- at the datasheet's ~66 frames/s the frame period would be ≈ 15 ms, so the line would be busy roughly 31% of the time (measured: ~95 Hz and ~45 %, below).

These numbers matter for the software-serial discussion in §6, but must be confirmed by measurement.

**Measured (2026-11-16, §2.4):** the frames come every **~10.5 ms (~95.4 Hz)**, not 15 ms. With 8N1 a frame takes ~4.7 ms of line time, so the line is busy **~45 %** of the time, not 31 %. This makes software serial on the ESP8266 (§6.3) harder than first estimated.

### 2.4 Measured on the bench **[MEASURED]**

Setup: sensor alone, off the drone, read by a Raspberry Pi Pico (MicroPython 1.29.0, hardware UART0 RX) that forwards each frame with a microsecond timestamp to the PC over USB. Supply from the Pico 3V3 pin, **3.29 V measured at the sensor pads** (the datasheet asks for 3.3–3.6 V; it worked, but the margin is small). For the movement tests the sensor was fixed on a base on top of a box (~15 cm or ~30 cm to the floor) and slid 30 cm between two tape marks placed **beside** the path, so the sensor never saw the marks. 40 captures, 66,297 frames. Four of the captures (forward on the carpet, lens covered, white table, black mat) are in `drone/ground/src/sd_protocol_py/tests/data/thoneflow/`, and the parser tests replay them; the rest and the capture scripts are kept by the author (#107).

| Item | Measured | Datasheet | Evidence |
|---|---|---|---|
| Serial format | 19200 baud, 8 data bits, no parity: **0 checksum errors** in 66,297 frames | 19200, rest not stated | All captures. One vs. two stop bits cannot be told apart by receiving only |
| Frame rate | **~95.4 Hz**, median gap 10.5 ms, max ~16 ms on a clean wiring | 66 Hz | All captures |
| Frame layout | `FE 04 X X Y Y CS Q AA`, as §3.1 | Same | All captures |
| X/Y byte order | **Low byte first**, signed 16-bit (`01 00` = +1, `fe ff` = −2) | High byte first | 2026-11-10; with high byte first the sums run away at rest |
| X/Y meaning | **Per-frame deltas**: 0 at rest, and the sum over a move is proportional to the distance | Not stated | Rest and 30 cm moves |
| Checksum | **(byte2 + byte3 + byte4 + byte5) & 0xFF** (the four motion bytes) | Not stated | 0 mismatches in 66,297 frames |
| Axes (sensor on our base) | Forward = **+Y**, left = **+X**, right = **−X** | Not stated | 3–4 runs each. The direction relative to the module body is still to be recorded with a photo (TF-16) |
| Scale | Carpet, 30 cm move: **~1015 counts at 15 cm** (~34 counts/cm), **~533 at 30 cm** (half). About **510–535 counts per radian** of flow (distance ÷ height) | Not stated | Hand-moved, height to the box top: rough (±10 %) |
| At rest | ≤ 3 counts drift in 30 s, quality ~163 | — | 3 × 30 s on the carpet |
| Lens covered | **X = Y = 0, quality 5–17** | Not stated | 2 × 10 s. Zero motion and "no image" look the same in X/Y; only quality differs |

**Surfaces** (same 30 cm forward move at 15 cm; correct answer ≈ +1000 in Y):

| Surface | Result | Quality | Verdict |
|---|---|---|---|
| Striped carpet, moving along or across the stripes | +906 to +1086, repeatable | 125–180 | **Good**. The stripes do not confuse it |
| Plain white table | ~0: noise only, the move is not seen | 52–82 | **Bad** |
| Black EVA mat (tatami type), first set | **−3600 to −4040**: reversed and ~4× too large, but repeatable | 78–135 | **Bad, and dangerous**: wrong motion with medium quality |
| Same mat, second set | +358, +526 (right sign, ⅓–½ of the move) | 57–85 | **Bad** |
| Same mat with random pieces of tape under the path | +433, +289 | 75–114 | **Bad** |

What follows:

- **No single quality threshold separates good from bad data.** On the carpet at 30 cm the quality was ~135 with correct motion, and on the mat it reached ~135 with reversed motion. At the flight height (~1 m) it will be lower still. The threshold (TF-06) stays a per-surface, per-height setting, and quality alone is not a safety check.
- **The floor decides.** Before flow is used in the cage, the cage floor must be tested the same way; a plain or fine, regular floor may need a textured mat.
- **Wiring:** in two runs bytes were lost for 2–4 s while the setup was being handled (55 and 180 bytes dropped, gaps up to 1.2 s), then the stream was clean again. Likely an intermittent contact. Check the solder joints before the sensor goes on the drone.

---

## 3. Frame format and parsing requirements

### 3.1 Frame format **[DS]**

| Byte | Content | Datasheet value / note |
|---|---|---|
| 0 | Header | `0xFE` |
| 1 | Number of data bytes | `0x04` |
| 2 | X-motion high byte | |
| 3 | X-motion low byte | |
| 4 | Y-motion high byte | |
| 5 | Y-motion low byte | |
| 6 | Checksum | Algorithm **not stated** |
| 7 | Surface quality | Range/meaning **not stated** |
| 8 | Footer | `0xAA` |

Observations that follow directly from the table:

- The checksum is at byte 6, **before** the surface-quality byte at byte 7.
- The length byte (`0x04`) counts the four motion bytes. It does not count the whole frame.
- X and Y are each assembled from a high byte followed by a low byte.

> [!warning] Measured 2026-11-10: the byte order is the opposite
> On the bench, X and Y are **low byte first** (signed 16-bit), and byte 6 is a checksum of bytes 2–5. See §2.4. The table above is kept as the datasheet says it.

### 3.2 Items to establish before the parser is trusted

> [!warning] Discrepancy found 2026-10-12 against ArduPilot's CX-OF driver
> The datasheet says the format is compatible with CX-OF, but ArduPilot's CX-OF driver describes the frame differently: X/Y low byte first (datasheet: high byte first), byte 1 reserved (datasheet: length `0x04`), byte 6 time motion (datasheet: checksum), and 25 Hz output (datasheet: 66 Hz). That driver reads X/Y as signed 16-bit values and checks only header, footer and length, with no checksum. The compatibility claim should therefore not be relied on. Byte order, checksum and signedness must be determined from bench captures (§9.2).

| Item | How to establish |
|---|---|
| Checksum algorithm and which bytes it covers | Not resolved by the ArduPilot CX-OF driver (it does not validate a checksum). Determine from captured frames (§9.2) and any further manufacturer documentation. Not asserted here. |
| X/Y signedness | Bench observation (move the sensor in both directions), cross-checked with the reference. |
| Units and scaling | Calibration measurement (§7); not available from the datasheet text. |
| Meaning/range of surface quality | Bench observation across surfaces, cross-checked with the reference. |
| Serial framing (data/parity/stop bits) | Logic-analyser/oscilloscope or serial-capture check (§9.2). |

> [!success] Measured 2026-11-16 (§2.4): checksum = sum of bytes 2–5 & 0xFF; X/Y signed 16-bit, low byte first, per-frame deltas; ~510–535 counts per radian (rough); quality 5–17 with the lens covered, 125–180 on the carpet, and not a reliable validity signal on its own; 19200 8N1 decodes with no errors.

### 3.3 Parsing requirements [PROPOSED]

- **PAR-01**: Parsing shall operate on a **byte stream**, not assume that reads align with frame boundaries.
- **PAR-02**: A frame is accepted only if **all** of these hold: header byte matches, length byte matches the expected value, footer byte matches at the expected position, and the checksum validates (once the algorithm is confirmed).
- **PAR-03**: The parser shall **not** resynchronise on a header byte alone. `0xFE` and `0xAA` could also occur as motion, checksum or quality values, so alignment must be confirmed by structure (length, footer, checksum).
- **PAR-04**: After corrupted or partial input, the parser shall recover and resynchronise to the next valid frame without manual intervention.
- **PAR-05**: The parser shall be a **portable, hardware-independent library** (no dependence on a specific UART driver or board) so the same logic can run wherever the sensor is read and be tested off-target.
- **PAR-06**: Each parse result shall expose: X, Y, surface quality, a validity/status indicator (§8), a receive timestamp from a clock the reader controls, and a local sequence counter. The field layout for forwarding data elsewhere is an open decision.
- **PAR-07**: Parser counters (frames accepted, header/length/footer/checksum rejects, resync events) shall be kept and exposable for diagnostics.
- **PAR-08**: The parser shall never emit "zero motion" to represent "no valid data" (see §8).
- **PAR-09**: No silent clamping or sign assumptions; signedness and scaling follow §3.2 once established.

> [!success] Implemented 2026-11-16 (#107): a Python parser, `sd_protocol.thoneflow` (`drone/ground/src/sd_protocol_py/`), with the status outputs of §8.1 (`OK`, `LOW_QUALITY`, `STALE`, `NO_DATA`; a bad frame is dropped and counted, never emitted) and the PAR-07/VAL-03 counters (accepted, header/length/footer/checksum rejects, skipped bytes, resyncs, stale events). The quality threshold and the stale interval are required settings with no default, because the bench found no safe value (§2.4). The threshold cannot be set below 18, so the covered-lens frames (X = Y = 0, quality 5–17) are never `OK` (PAR-08). Tests cover the §9.1 table, including false headers, resync, fuzzing and a replay of the bench captures. A C++ version for the ESP is only needed if Option B goes ahead (#108).

---

## 4. Optical flow vs. video camera vs. position estimation

These three are easily conflated. They are different things with different outputs.

| | **Optical-flow sensor (ThoneFlow-3901U)** | **Video camera (e.g. the CADDXFPV analog FPV camera)** | **Position estimate** |
|---|---|---|---|
| What it is | A module that tracks surface motion and outputs motion values over UART | An image source | A derived quantity (where the drone is) |
| Output | Small frames: X/Y motion values + surface-quality field | Video images | Position (and often velocity) with an uncertainty |
| Provides images to perception? | **No**; it outputs no image data | Yes (video) | n/a |
| Absolute position? | **No**; relative motion only | No by itself | Requires a reference and/or fusion |
| Height/range? | **No rangefinder**, no range field in the frame | No | Needs height information from elsewhere |
| In this project | Planned, not installed | Existing, separate device | Not provided by any single sensor here |

Key consequences:

- The sensor reports **relative motion measurements**. They are **not** a position and **not** a physical velocity until combined with height and attitude information (§7).
- Integrating motion over time accumulates drift; with no absolute reference, **long-term position cannot be obtained from this sensor alone**.
- The sensor is **not** a substitute for video-based perception on the ground computer; they serve different functions and neither replaces the other.

---

## 5. Potential integration architectures

> [!note]
> All options below are **proposals**. None is selected, built or validated. Pin assignments are intentionally absent.

### 5.1 Overview diagram

```mermaid
flowchart LR
    SENS["ThoneFlow-3901U<br/>UART 19200 baud · ~95 Hz measured · 3.3–3.6 V<br/>(NOT INSTALLED)"]

    subgraph DRONE["Drone"]
        FC["Flight controller<br/>Betaflight 4.5.1<br/>(sensor support UNCONFIRMED)"]
        ESP["ESP8266 bridge<br/>(custom firmware needed for any option that reads the sensor)"]
        OTHER["Other processor<br/>with spare hardware UART<br/>(option C, not selected)"]
        HGT["Height source<br/>(not selected; sensor has no rangefinder)"]
    end

    subgraph GND["Ground computer (ROS 2)"]
        EST["State estimation<br/>flow + height + attitude"]
        PERC["Perception / mission logic"]
    end

    SENS -. "Option A: FC UART<br/>(only if firmware support confirmed)" .-> FC
    SENS -. "Option B: ESP RX<br/>(hardware-UART conflict or software serial)" .-> ESP
    SENS -. "Option C" .-> OTHER
    OTHER -.-> ESP
    FC <-. "UART, provisional<br/>see esp8266-fc-serial-interface" .-> ESP
    ESP <-- "Wi-Fi<br/>see ground-esp8266-communication" --> GND
    HGT -.-> EST
    EST --> PERC
```

### 5.2 Option A: sensor connected to a supported FC interface

**Idea [PROPOSED]:** connect the sensor to a spare FC UART and let Betaflight consume it; flow-derived data reaches the ground computer, if at all, via FC telemetry.

| Aspect | Notes |
|---|---|
| Precondition | **Betaflight 4.5.1 (or the ODDITYRCF7AIO target build) must be confirmed to support this sensor/format on a UART.** Unconfirmed; do not assume. |
| What to check | Official Betaflight 4.5.x documentation and release notes for optical-flow / rangefinder sensor support and supported frame formats; the target's build options and serial-port function list. Whether any supported optical-flow input is by UART frame, by MSP message, or not at all is **unknown**. |
| FC UART availability | Earlier configuration inspection showed UART1, UART4 and UART6 appearing unassigned, with UART4 the leading candidate for the ESP link (see [[ESP_to_FC\|esp8266-fc-serial-interface]]). Physical pad availability is **unconfirmed**, so any sensor UART would need its own confirmation. |
| Pros | Hardware UART on the FC; no ESP8266 serial-port constraint; parsing handled by firmware if supported. |
| Cons / unknowns | May simply be unsupported; even if flow input is supported, it is unknown what Betaflight does with it and what is exposed to the ground computer; changes to FC configuration need explicit approval; no way for ground-side estimation to see raw flow values unless exposed through MSP. |
| Validation needed | Confirm support from documentation first; then props-off bench check that the FC recognises frames and exposes the data. |

### 5.3 Option B: sensor read by custom ESP8266 firmware

**Idea [PROPOSED]:** the ESP8266 receives the sensor's UART stream, validates frames with the shared parser library (§3.3), and forwards timestamped, validated measurements over Wi-Fi to the ground computer (and/or onward to the FC if ever useful).

| Aspect | Notes |
|---|---|
| Precondition | **Custom ESP8266 firmware.** The current AT firmware (v1.7.4.0, SDK 3.0.5) is not a platform for a sensor parser; whether it could be used at all is not assessed here. This ties into the AT vs. custom C++ decision on [[PC_to_ESP\|ground-esp8266-communication]] (OD-01). |
| Serial constraint | See §6: the ESP8266 has **one** full-duplex hardware UART (UART0), which is already proposed for the FC link; UART1 is transmit-only. A second receive path would be **software serial**, which needs validation. |
| Pros | No extra processor; data enters the same Wi-Fi path the ground computer already uses; ground-side estimator sees raw flow values. |
| Cons / risks | Software-serial reliability under Wi-Fi load is **unproven**; ESP8266 CPU/timing budget is shared with Wi-Fi, FC link and command validation; any ESP8266 fault affects both the flow data and the FC link. |
| Validation needed | §9.3 serial reception under load; measure frame error and loss rates, not assumed. |

### 5.4 Option C: sensor read by another suitable processor

> [!warning] Rejected on 2026-10-14 (TF-11, [[ADR-016 Flow Sensor Path|ADR-016]]). Kept for the record.

**Idea [PROPOSED]:** a processor with a free hardware UART receives and validates frames, then passes measurements onward (e.g. to the ESP8266 over a link with spare capacity, or by replacing the ESP8266 bridge with a part that has more hardware UARTs). No specific part is selected or evaluated here.

| Aspect | Notes |
|---|---|
| Pros | Hardware UART reception, isolating timing-sensitive serial reading from Wi-Fi activity; parser library reusable. |
| Cons / unknowns | Extra hardware, wiring, power, weight and firmware to maintain; another hop (latency, failure point); small team and six-month schedule; the link from this processor onward needs its own design. |
| Validation needed | Selection criteria, power budget, and a test plan; none defined yet. |

### 5.5 Qualitative comparison (not a decision)

| Criterion | A: FC UART | B: ESP8266 | C: Other processor |
|---|---|---|---|
| Depends on unconfirmed firmware support | **Yes** | No | No |
| Needs custom ESP8266 firmware | Not for the sensor | **Yes** | Likely, for forwarding |
| Hardware UART available for the sensor | Probably, once pads confirmed | **No** hardware RX free in current proposal | Yes by selection |
| Raw flow data reaches the ground computer | Only if FC exposes it | Yes (by design) | Yes (by design) |
| Added hardware | None | None | **Yes** |
| Main risk | Unsupported | Software-serial reliability | Complexity |

---

## 6. ESP8266 serial-port limitations

### 6.1 Facts used

- ESP8266 **UART0** supports full-duplex serial (GPIO1 = TX, GPIO3 = RX).
- ESP8266 **UART1 is transmit-only**.
- UART0 is the **proposed** port for the FC link (see [[ESP_to_FC|esp8266-fc-serial-interface]], provisional).
- The sensor is a **data source**: the host must be able to **receive** its UART stream.

### 6.2 What follows

- The ESP8266 effectively has **one** hardware serial receive path. If UART0 serves the FC link (which needs both TX and RX for MSP), then **no hardware RX remains** for the sensor.
- Options on the ESP8266 are therefore limited to: (i) a **software-serial** receiver on a spare GPIO, (ii) sharing UART0 (conflicts with the proposed FC link, **not proposed**), or (iii) moving the sensor to a different processor or the FC (§5.2, §5.4). Remapping UART0's pins would not create a second hardware receiver.
- The USB-to-serial adapter on an ESP8266 dev board shares UART0 pins (see the FC-interface page); that issue is separate and unchanged by the sensor.

### 6.3 Software-serial risk under Wi-Fi load

> [!danger] Treat as an option requiring validation, not a proven design
> Receiving a UART stream by software on the ESP8266 while the Wi-Fi stack is active is **not demonstrated** for this project. This page makes no claim that it works.

Why it is a risk:

- Software serial depends on precise bit timing. Wi-Fi activity and other interrupts can delay sampling, risking corrupted or missed bytes.
- The ESP8266 is also handling Wi-Fi traffic, command validation and, if proposed, the FC link; CPU contention is plausible.
- Failures would look like garbled frames, checksum errors and dropped frames, exactly the kind of data corruption the sensor-handling in §8 must catch.

Points that may help but guarantee nothing:

- The sensor's baud rate (19200, ≈52 µs per bit) is comparatively slow.
- The sensor transmits only a short frame at a modest rate (§2.3).
- Checksum validation and resynchronisation (§3.3) bound the damage from errors, but they cannot recover bytes that were never sampled.

What would justify using it: measured results (§9.3) showing acceptable frame-error and frame-loss rates **with the Wi-Fi link and FC link running at realistic load**, with the acceptance threshold agreed in advance.

---

## 7. Height information and state estimation

### 7.1 Why flow alone is not enough

Optical flow reports apparent motion of the surface in the sensor's image plane. To turn that into physical motion:

- **Height above the surface** is required to convert image-plane motion into metres per second. **This sensor has no rangefinder and its frame has no range field**, so height must come from another source.
- **Rotation compensation** is required, because tilting or rotating the drone produces apparent flow that is not translation. Attitude/angular-rate information from the FC must be time-aligned with the flow samples.
- **Scaling/units** of the X/Y values are not stated in the datasheet text and must be calibrated (§3.2).
- **Position** requires integrating velocity, which **drifts** and has no absolute reference without something else (e.g. perception, mission-level correction).

### 7.2 Needs [PROPOSED]

- **EST-01**: A height source must be selected before flow data can produce physical velocity. Candidates are **not evaluated** here and none is chosen.
- **EST-02**: Flow, height and attitude/rate data must carry timestamps from known clocks, with a defined way to align them. Requirements for time alignment are TBD.
- **EST-03**: The state estimator must treat flow as a measurement with an uncertainty and a validity flag (§8), not as truth.
- **EST-04**: Where the estimator runs (ground computer in ROS 2, FC, other) is **open**. Running it on the ground computer implies Wi-Fi latency and jitter in the loop, which has not been quantified.
- **EST-05**: The estimator's output must not be used for control until its behaviour is tested; this page does not define that testing.

> [!note]
> Betaflight's ability, if any, to fuse flow with height is **unconfirmed** and is not assumed here.

---

## 8. Data validity and fault handling

All requirements are **proposed**. Thresholds and timeouts are **not defined**; they depend on measured behaviour (§9) and open decisions (§10).

### 8.1 Suggested status outputs

Each measurement should carry a status, so consumers never guess:

| Status | Meaning |
|---|---|
| `OK` | Frame valid, quality acceptable, fresh |
| `LOW_QUALITY` | Frame structurally valid, but surface quality is below the accepted threshold (threshold TBD) |
| `INVALID_FRAME` | Header, length, footer or checksum failed |
| `STALE` | No new valid frame within the allowed interval (interval TBD) |
| `NO_DATA` | No bytes received at all (sensor absent or disconnected) |

### 8.2 Conditions and handling

| Condition | Detection | Required handling |
|---|---|---|
| **Checksum failure** | Checksum mismatch (algorithm to be confirmed, §3.2) | Discard frame; count it; do not use values; do not reset freshness timer |
| **Malformed frame** | Header/length/footer mismatch | Discard; count; resynchronise (PAR-03/04) |
| **Low surface quality** | Quality field below threshold (threshold and semantics TBD) | Mark `LOW_QUALITY`; consumer must not treat motion values as reliable |
| **Stale frames** | No valid frame within a staleness interval (TBD, chosen from the measured rate, not assumed) | Mark `STALE`; stop publishing last values as current |
| **Duplicate / repeated frames** | Same content could be genuine (no motion) or a repeat; datasheet gives no frame counter | Do not infer staleness from identical values; use arrival timing and sequence counters on the reader's side |
| **Sensor disconnection** | No bytes (`NO_DATA`), or only invalid bytes for a period (TBD) | Report fault; do not output motion; require valid frames again before resuming |
| **Frame-rate drop** | Measured rate falls well below the measured ~95 Hz (§2.4; limit TBD, TF-07). A limit set against the datasheet's 66 Hz would miss a ~30 % drop | Report degraded data; log |
| **Reader overload** (e.g. dropped bytes under load) | Rising reject counters, rate drop | Surface in diagnostics; may indicate unsuitable reception method (§6.3) |
| **Power fault / brown-out** | Sensor stops or restarts | Treat as disconnection; do not use data until valid frames resume |
| **Reader restart** | Reader reboots | Start with `NO_DATA`/`STALE`; no carried-over values |

### 8.3 Cross-cutting rules

- **VAL-01**: "No valid measurement" is **never** represented as zero motion.
- **VAL-02**: Stale or invalid data must not be forwarded as if current.
- **VAL-03**: Counters (accepted, rejected by cause, resyncs, stale events) shall be available for diagnostics on the ground side.
- **VAL-04**: The estimator (§7) must handle missing flow gracefully; its behaviour when flow is unavailable is a design item.

---

## 9. Testing plan

> [!important]
> Done so far (#107): the §9.1 parser tests are written and pass (`sd_protocol.thoneflow`), and the §9.2 bench characterisation was run on 2026-11-10 and 2026-11-16. Everything from §9.3 on is still a plan. All tests are **bench** tests; no flight testing is proposed. Do not connect anything to the FC until the team has approved wiring (see the FC-interface page).

### 9.1 Hardware-independent tests (parser and logic)

| Area | What to verify |
|---|---|
| Valid frames | Correct decoding of X, Y and quality for known byte sequences |
| Bad frames | Wrong header, length, footer, checksum → rejected and counted |
| Stream handling | Frames split across reads; several frames in one read; leading garbage; truncated frame |
| False headers | `0xFE`/`0xAA` appearing inside payload/checksum/quality does not cause mis-alignment (PAR-03) |
| Resync | Recovery after corruption without manual action (PAR-04) |
| Endianness / signedness | High/low byte composition per datasheet; signedness per what is established (§3.2) |
| Status logic | `OK`, `LOW_QUALITY`, `STALE`, `NO_DATA` transitions with a mock clock |
| Robustness | Fuzzed random byte streams never crash, hang or over-read |
| Replay | Parser output from recorded raw captures (§9.2) matches expectations |

Gate: these pass before any hardware-specific work relies on the parser.

### 9.2 Sensor bench characterisation (sensor alone, off the drone)

Purpose: establish the facts the datasheet text does not state, and record real data for replay.

Done on 2026-11-10 and 2026-11-16 (#107) with a Raspberry Pi Pico as the reader. Results in §2.4.

- [x] Power the sensor within **3.3–3.6 V** from a source whose voltage has been measured. Pico 3V3, **3.29 V** measured at the sensor pads with a multimeter; wiring checked for shorts. Pads marked G, V, T on the module; **no pinout is recorded here**.
- [ ] Confirm the serial parameters (baud, data/parity/stop bits) and the TX logic level with a scope or logic analyser. *Partly: 19200 8N1 decodes 66,297 frames with no checksum error; no scope or logic analyser was used, and the TX level was not measured.*
- [x] Capture raw output on a computer using a serial adapter whose logic level is compatible with the sensor; store the raw bytes. *Pico (3.3 V logic) forwarding every frame with a timestamp; 40 CSV captures.*
- [x] Verify frame structure against §3.1 and measure the actual frame rate and its variation against the nominal ~66 Hz. *Structure as §3.1; **~95.4 Hz**, median gap 10.5 ms.*
- [x] Determine the checksum algorithm from the captures and the reference documentation (§3.2); confirm every captured frame validates. *Sum of bytes 2–5 & 0xFF; every frame validates.*
- [x] Determine X/Y signedness and sign conventions by moving the sensor in known directions. *Signed, low byte first; forward +Y, left +X on our base.*
- [x] Characterise the surface-quality field over different surfaces and conditions, and note behaviour when no surface is tracked. *Carpet, white table, black EVA mat, lens covered; heights 15 and 30 cm. Not yet at flight height (~1 m) or on the cage floor.*
- [ ] Observe start-up behaviour and behaviour on power interruption.
- [x] Record datasets for offline replay in tests §9.1 and §9.5.

### 9.3 Serial reception and timing tests (on the candidate reader)

Depending on the selected option:

- **Option B (ESP8266 software serial)**: if pursued, measure frame-accept rate, framing/checksum rejects, missed frames and timing jitter with (a) Wi-Fi idle, (b) realistic Wi-Fi traffic, (c) Wi-Fi traffic plus the FC UART link active, (d) a stress case. Compare to a hardware-UART baseline from the same sensor.
- **Option C (another processor)**: same measurements on a hardware UART as a reference.
- Record timestamp quality (resolution, jitter) at the reader, since estimation depends on it (§7).

### 9.4 Fault-handling tests

- [ ] Inject checksum errors, truncated frames and noise; verify rejection and counters.
- [ ] Unplug/power-cycle the sensor; verify `NO_DATA`/`STALE` and clean recovery.
- [ ] Cover the sensor / present a poor surface; verify `LOW_QUALITY` behaviour.
- [ ] Restart the reader; verify no stale carry-over.

### 9.5 Integration tests

| Stage | What to verify | Preconditions |
|---|---|---|
| Reader → ground computer | Flow measurements arrive with timestamps, sequence counters and status; ROS 2 side represents invalid/stale data correctly | Reader option chosen; Wi-Fi message definition agreed |
| Latency/jitter | End-to-end timing characterised, not assumed | Previous stage working |
| Estimator offline | Flow + height + attitude replayed through the estimator (recorded data) | Height source chosen (EST-01) |
| Option A only | FC recognises frames and exposes data; props-off bench | Documentation confirms support; wiring and configuration changes **explicitly approved** |
| Coexistence | Flow reception does not disturb the FC link, command validation or loss detection described in the companion pages | Both links implemented |

---

## 10. Acceptance criteria and open decisions

### 10.1 Acceptance criteria (for this integration to be considered ready for further development)

Evidence (captures, logs, test records) is required for each.

**Sensor facts**

- [ ] Serial parameters, TX logic level, checksum algorithm, signedness and quality-field behaviour are established and written into §2–§3.
- [ ] Pin assignments are recorded from verified sources (physical inspection and manufacturer documentation), on a separate wiring page.

**Parsing**

- [ ] §9.1 tests pass, including false-header, resync and fuzz cases.
- [ ] Parser reproduces expected results on recorded real captures.

**Reception**

- [ ] The chosen reception method meets an agreed frame-loss/error threshold at realistic load (threshold to be defined **before** testing).
- [ ] If software serial is used, the above is demonstrated with Wi-Fi and FC link running, not just in isolation.

**Fault handling**

- [ ] Checksum failures, stale frames, low quality and disconnection each produce the defined status and never zero-motion output (VAL-01/02).

**Architecture**

- [ ] The integration option is chosen and recorded with the reason.
- [ ] If Option A: Betaflight 4.5.1 support confirmed from official documentation **and** on the bench.
- [ ] A height source and the state-estimation approach are selected before flow data is used for physical velocity.
- [ ] Interaction with the ESP8266↔FC link and the ground link is tested and shown not to degrade them.

### 10.2 Open decisions

> [!note] Status key (updated 2026-10-12)
> **Open** = unresolved. **Recommended** = proposed resolution from the 2026-10-12 review; *not yet approved by the team*, so treat it as the working assumption until approved or changed. **Method recommended** = how to choose the value is proposed, but the value itself must come from measurement. **Partly resolved** = documentation checked or approach proposed, verification or detail still needed. Nothing here has been implemented or tested.

| # | Decision | Blocks | Owner | Status | Proposed resolution |
|---|---|---|---|---|---|
| TF-01 | Does Betaflight 4.5.1 / the ODDITYRCF7AIO target support this sensor, and in what form (UART frame, MSP, none)? | Option A |  | Resolved | **Unsupported.** Betaflight 4.5.1 has no optical-flow driver at all; 2026.6.2 supports MT and UPT1 only (not CX-OF); INAV has CX-OF but no `ODDITYRCF7AIO` target (source-verified). Option A dropped. [[ADR-016 Flow Sensor Path\|ADR-016]] |
| TF-02 | Which integration option (A, B, C) | Any wiring |  | Accepted | Characterise first → time-boxed Option B experiment with a threshold agreed beforehand → if it fails, ADR-016 is re-opened (Option C, an extra MCU, was rejected on 2026-10-14, TF-11). [[ADR-016 Flow Sensor Path\|ADR-016]] |
| TF-03 | Serial framing (data/parity/stop bits), TX logic level, whether the sensor RX pin is usable | Bench setup |  | Partly resolved | 2026-11-16 (§2.4): 19200, 8 data bits, no parity decode 66,297 frames with no error. Not measured: the TX logic level and stop-bit count (no scope), and the RX pin (not needed: the sensor only sends). |
| TF-04 | Checksum algorithm and covered bytes | Parser acceptance |  | Resolved | 2026-11-16 (§2.4): **(byte2 + byte3 + byte4 + byte5) & 0xFF**, the four motion bytes. 0 mismatches in 66,297 frames. |
| TF-05 | X/Y signedness, units, scaling; delta vs. accumulated | Estimation |  | Resolved (scale rough) | 2026-11-16 (§2.4): signed 16-bit, **low byte first** (as ArduPilot's CX-OF driver, not the datasheet); per-frame deltas; ~510–535 counts per radian of flow, from hand-moved 30 cm runs at 15 and 30 cm. Calibrate properly in #200. |
| TF-06 | Surface-quality meaning and threshold | Fault handling |  | Partly resolved | 2026-11-16 (§2.4): 5–17 with the lens covered, 125–180 on the carpet, 52–135 on surfaces where the motion is wrong. **No single threshold separates good from bad**; it is a required, per-surface setting of the parser, and quality is not a safety check on its own. Measure again at flight height and on the cage floor. |
| TF-07 | Staleness interval, disconnection timeout, frame-rate-drop limit (from measurement) | Fault handling |  | Method recommended | Measured rate ~95.4 Hz (gap 10.5 ms, max ~16 ms). The stale interval is a required parser setting; choose it from this rate (for example a few frame periods) with the consumer (#200). |
| TF-08 | Pin assignments and physical wiring (verified separately) | Any wiring |  | Open | — |
| TF-09 | Sensor power source and supply conditioning within 3.3–3.6 V | Any wiring |  | Open | — |
| TF-10 | Acceptable software-serial error/loss threshold, if Option B is pursued | Option B |  | Method recommended | Agree the acceptable error/loss threshold before testing, not after. |
| TF-11 | Selection of another processor, if Option C is pursued | Option C |  | Rejected | No second processor on the drone (companion-computer rule), 2026-10-14. [[ADR-016 Flow Sensor Path\|ADR-016]] |
| TF-12 | Height source selection | Physical velocity |  | Accepted | FC DPS310 baro, relative to takeoff, fused with the accelerometer (2026-10-14). No rangefinder in the lab. [[ADR-016 Flow Sensor Path\|ADR-016]] |
| TF-13 | Where state estimation runs (ground computer, FC, other) | Estimation |  | Accepted | Ground computer first, as recommended; measure latency ([[System Overview]]). |
| TF-14 | Time-alignment method for flow, height and attitude data | Estimation |  | Open | — |
| TF-15 | Message format for forwarding flow data to the ground computer | Integration |  | Open | — |
| TF-16 | Sensor mounting, orientation and field-of-view requirements (outside this page's scope) | Installation |  | Open | — |
| TF-17 | Whether the ESP8266 firmware path (AT vs. custom C++) is decided in time for Option B | Option B |  | Open | — |

### Suggested related pages *(do not exist unless stated)*

- [[PC_to_ESP|ground-esp8266-communication]] *(exists)*
- [[ESP_to_FC|esp8266-fc-serial-interface]] *(exists)*
- `[[ThoneFlow Raw Capture Log]]` *(suggested; results of §9.2)*
- `[[Height Sensing Options]]` *(suggested; resolves TF-12)*
- `[[State Estimation Design]]` *(suggested; resolves TF-14; TF-13 is decided)*
- `[[ThoneFlow Wiring Notes]]` *(suggested; resolves TF-08)*

---

## 11. Current recommendation and remaining decisions

> [!warning] Superseded by [[ADR-016 Flow Sensor Path|ADR-016]] (2026-10-14)
> The recommendation and the open list below are the 2026-10-12 state. ADR-016 supersedes them: no extra processor (Option C rejected, TF-11), the ESP SoftwareSerial experiment with a threshold agreed beforehand, and height from the FC DPS310 baro (TF-12). Read ADR-016 and the TF table in §10 first.

### Current recommendation (proposed, not decided)

1. **Do not design around ESP8266 software serial.** Keep it as an experiment that must pass §9.3 before anyone depends on it.
2. **Characterise the sensor first, on a bench, with no drone integration** (§9.2): confirm serial parameters, logic level, checksum, signedness and quality behaviour, and record raw data. This costs little and removes the main unknowns.
3. **Build and test the parser as a portable, hardware-independent library** (§3.3, §9.1) so it works whichever option is chosen.
4. **Check Betaflight 4.5.1 support from official documentation before spending effort on Option A.** If support is absent or unclear, drop Option A.
5. **Lean, tentatively, toward a hardware-UART reader that forwards validated, timestamped flow data to the ground computer**, which would be the FC (if supported) or another processor (Option C), rather than software serial on the ESP8266. This is a leaning based on the serial-port limits in §6 and the schedule/complexity trade-offs, **not** a validated conclusion; measurement may change it.
6. **Defer using flow for physical velocity until a height source and a state-estimation approach are chosen** (§7). Without height, the sensor does not provide metric velocity.

### Decisions still open (most important first)

1. Does Betaflight 4.5.1 support this sensor at all, and how? (TF-01)
2. Which reader: FC, ESP8266, or another processor? (TF-02; recommendation, pending approval: not ESP8266 software serial first, prefer a hardware-UART reader)
3. What do the unstated sensor parameters turn out to be (serial framing, logic level, checksum, signedness, units, quality field)? (TF-03 to TF-06)
4. What height source will be used? (TF-12)
5. Where does state estimation run, and how are flow, height and attitude time-aligned? (TF-13, TF-14)
6. If the ESP8266 reads the sensor, is software serial reliable enough under Wi-Fi load, and what threshold counts as acceptable? (TF-10)

---

## Sources

- [ThoneFlow-3901U UART Serial Version PMW3901 Optical Flow Sensor, datasheet (SKU 101990480), via DigiKey/Seeed Studio](https://media.digikey.com/pdf/Data%20Sheets/Seeed%20Technology/101990480_Web.pdf)
- Manufacturer product page referenced in the datasheet: Seeed Studio, ThoneFlow-3901U (not retrieved for this page)

## 12. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **Mermaid** | A text-based diagram syntax rendered by compatible Markdown tools. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **Counts per radian** | Flow scale: sensor counts for a movement equal to the height (distance ÷ height = 1 rad). It does not depend on the height, so one number calibrates the sensor. |
| **Little-endian / low byte first** | A 16-bit number sent with its low byte before its high byte. |
| **Optical flow** | How fast the image of the floor moves under the sensor. It is roughly speed ÷ height, so it needs a height to give a speed. |
| **Raspberry Pi Pico** | A small microcontroller board, used here only as a bench reader for the sensor. It does not fly (ADR-016). |
| **Surface quality** | Byte 7 of the frame: how much detail the sensor sees. Low with no image; not a reliable validity check on its own (§2.4). |
| **AT** | The Hayes-style AT command set of the stock ESP8266 firmware (1.7.4.0), replaced by custom firmware (ADR-007). |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
