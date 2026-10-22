---
title: ADR-023 — ELRS Firmware Version and Regulatory Domain
type: adr
status: accepted
date: 2026-10-22
project: SEA autonomous drone
tags:
  - adr
  - radio
  - elrs
  - safety
---

# ADR-023 — ELRS Firmware Version and Regulatory Domain

> [!success] Status: ACCEPTED (team, 2026-10-21). Applied on 2026-10-21 (GitHub #22).
> The update was authorised and applied on the bench (props off) on 2026-10-21 to get the safety pilot's radio link working. This ADR records the problem, the options, the decision and the method.

## 1. Context

The RadioMaster Pocket was taken out of its sealed box on 2026-10-20 and set up for the drone: model `MODEL06`, Internal RF = CRSF, 250 Hz. It never kept a link with the drone's BETAFPV ELRS Nano receiver:

- After a bind (receiver put in bind mode by 3 power cycles, then [Bind] in the ExpressLRS Lua), the receiver LED went solid once.
- After the Pocket was power-cycled, the receiver went back to a slow blink (searching) and never reconnected. A second bind did not connect.

Both devices were then read directly over their own ExpressLRS Wi-Fi pages (`http://10.0.0.1`, `/target` and `/config`):

| Item | Pocket (internal TX module) | Nano RX (drone) |
|---|---|---|
| ELRS version | **4.1.0** (`a9d4a9`) | **3.3.0** (`ae9df3`) |
| Regulatory domain | **ISM2G4** (non-EU) | **CE_LBT** (EU) |
| Binding UID | 179,19,74,136,33,254 (`Overridden`) | 0,1,0,1,0,83 (`Traditional`) |
| Hardware target flashed | RadioMaster **Zorro** Internal 2.4GHz TX | `BETAFPV_NANO_2400_RX` |
| MCU / RF chip | ESP32 / SX128x | ESP8285 / SX1281 |

Sources on compatibility:

- ExpressLRS binding guide: "The first number in the Version string should match between a TX Module and a Receiver."
- ExpressLRS 4.0.0 release notes: "Not compatible with ExpressLRS 3.x.x, 2.x.x, or 1.x.x."
- BETAFPV ELRS Nano Receiver product page: "the module and receiver have to be in the same ELRS version". The page also states that the receiver ships with ELRS 3.3.0.

The radio link is a safety requirement: the pilot must always be able to take over ([[Safety Rules]] #4, REQ-SAF-001, REQ-SAF-002). Without it, no override, failsafe or flight test can run.

## 2. Root cause

1. **Different major versions** (4 on the Pocket, 3 on the receiver). Confirmed by the sources above.
2. **Different regulatory domains** (ISM2G4 and CE_LBT). The sources consulted do not say whether these interoperate. Both ends were set to the same domain to remove the doubt.
3. **Different binding UIDs.** Solved by the same binding phrase on both ends once the versions match.

## 3. Options considered

| Option | Pocket | Nano RX | What is flashed | Notes |
|---|---|---|---|---|
| Change only the Pocket domain to LBT | 4.1.0 LBT | 3.3.0 LBT | Pocket | ❌ Does not link: major versions still differ |
| A. Pocket down to 3.x LBT | 3.x LBT | 3.3.0 LBT (unchanged) | Pocket only | Drone untouched; older ELRS on a new radio |
| **B. Both on 4.1.0 LBT** | **4.1.0 LBT** | **4.1.0 LBT** | Both | Latest stable release (2026-07-17); EU variant on both |
| B'. Both on 4.1.0 ISM | 4.1.0 ISM (unchanged) | 4.1.0 ISM | Nano RX only | Simplest, but not the EU variant |

ELRS 4.1.0 still supports the ESP8285 receivers: the BETAFPV Nano RX is in the official ExpressLRS target list, and the 4.1.0 release notes include ESP8285 fixes.

## 4. Decision

**Option B: both devices on ExpressLRS 4.1.0, regulatory domain CE_LBT, with the team's binding phrase.**

- The binding phrase is **not** written in this repository. It is kept by the team outside GitHub. Anyone with the phrase can bind a radio to the drone.
- The Pocket uses the official target **"RadioMaster Pocket Internal 2.4GHz TX"**. It replaces the factory "Zorro" target. Both targets use the same hardware layout file (`Radiomaster Zorro.json`) in the ExpressLRS target list, so only the name changes.

## 5. Method (applied 2026-10-21)

1. ExpressLRS **Web Flasher** (`expresslrs.github.io/web-flasher`, no install), version 4.1.0, region **LBT**, binding phrase set, flashing method **Local Download**. "Flash RX as TX" off; Wi-Fi auto-on 60 s, UART 420000 baud and "Lock on first connection" left at their defaults.
2. Files produced:
   - `ELRS-v4.1.0-betafpv.rx_2400.nano-LBT-bindphrase.bin.gz` (uploaded as-is, not decompressed)
   - `ELRS-v4.1.0-radiomaster.tx_2400.pocket-LBT-bindphrase.bin`
   Both were checked before upload: complete, image magic byte `0xE9`, and the strings `4.1.0`, `CE_LBT` and the expected product name inside.
3. **Pocket:** ExpressLRS Lua → WiFi Connectivity → Enable WiFi → join `ExpressLRS TX` → `http://10.0.0.1` → Update → upload. Result in the Lua: `4.1.0 CE_LBT`.
4. **Nano RX:** Pocket off, drone powered (props off) for about 60 s until the receiver LED blinks fast (Wi-Fi mode) → join `ExpressLRS RX` → `http://10.0.0.1` → Update → upload. The page warned "Target mismatch" (current name `BETAFPV_NANO_2400_RX`). This is expected: the official target list gives that name as the receiver's prior target name in the 4.x unified firmware. "Flash Anyway" was used.
5. A first upload attempt was rejected with `Magic byte is not 0xE9` because the wrong file (`elrs.lua`, the Lua script) was selected. Nothing was written: the receiver checks the image header before flashing.

## 6. Risks considered

| Risk | Likelihood | Consequence | Mitigation |
|---|---|---|---|
| Interrupted upload (battery or Wi-Fi drop) | Low | Receiver or TX module without valid firmware | Charged battery, PC next to the device. Recovery: re-flash; receiver also through Betaflight passthrough or its BOOT pad |
| Wrong target or file | Low | Device does not boot or misbehaves | Target read from the device itself; file contents checked before upload |
| Options reset (4.x flash layout change) | Certain | Receiver options back to defaults | No impact: the receiver options were already at defaults |
| Receiver outside the vendor's recommended version | — | No vendor support for 4.x | 4.1.0 officially supports the target |
| Effect on the FC | None | The upload only writes the receiver's ESP8285 flash. The FC only powers it | — |

## 7. Consequences

- The Pocket and the receiver link on power-up with no bind step, and the link survives receiver power cycles (verified by the team on 2026-10-21, [[Radio Link Tests]]).
- Any other radio needs ELRS 4.x, CE_LBT and the same binding phrase to fly this drone.
- Radio tests still open before the link counts as fully working: [[Radio Link Tests]] §3.

## 8. Related pages

- [[Radio Link Tests]] · [[Hardware Inventory]] · [[Betaflight Setup]] · [[Safety Rules]] · [[Decision Register]]

## 9. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Binding phrase** | Secret text set on both radio and receiver; both derive the same UID from it, so they link on every power-up. |
| **CE_LBT** | EU regulatory domain of ELRS 2.4 GHz: listens before it transmits (Listen Before Talk). |
| **CRSF** | Crossfire serial protocol: the wire format between the EdgeTX radio and its ELRS module, and between the receiver and the FC. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **ESP8285** | The microcontroller inside the BETAFPV Nano receiver that runs the ELRS firmware (not the drone's ESP-12F bridge). |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **ISM2G4** | Non-EU regulatory domain of ELRS 2.4 GHz. |
| **Lua (ExpressLRS Lua)** | The ExpressLRS menu script on the radio (SYS → TOOLS → ExpressLRS). |
| **Target** | The hardware definition an ELRS firmware is built for (pins, power levels, product name). |
| **UID** | Binding identifier derived from the binding phrase (or set by a bind); TX and RX link only when it matches. |
