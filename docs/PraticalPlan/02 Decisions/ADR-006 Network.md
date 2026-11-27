---
title: ADR-006 — Wi-Fi Network Topology and Security
type: adr
status: accepted
date: 2026-10-12
updated: 2026-11-26
project: SEA autonomous drone
aliases:
  - D06
tags:
  - adr
  - communication
  - security
---

# ADR-006 — Wi-Fi Network Topology and Security

> [!success] Status: ACCEPTED (team, 2026-10-12)
> Accepts **P6** and resolves **OD-17** in [[PC_to_ESP]]. It supersedes the earlier "dedicated ground-side access point" recommendation. Partly resolves **OD-04**.

## 1. Context

| Option | Pros | Cons |
|---|---|---|
| **ESP8266 as SoftAP** | One hop, lower and steadier latency. No extra hardware. Isolated by design. | ESP8266 SoftAP supports only a few clients. A small PCB antenna on both ends. |
| Dedicated router, ESP as station | Better ground antenna, many clients | Extra hop through the AP (more airtime), extra hardware |

## 2. Decision

- **ESP8266 SoftAP**, WPA2-PSK with a strong, non-default key (not committed).
- Static IPs. Port numbers are fixed in [[Protocol Spec v0]].
- Wi-Fi sleep disabled on the ESP (`WiFi.setSleepMode(WIFI_NONE_SLEEP)`) to avoid latency spikes.
- The Wi-Fi channel is chosen **after** the ELRS coexistence test ([[Study Plan v2]], week 2).
- Application-level validation stays mandatory ([[ADR-002 Message Integrity|ADR-002]]).
- **Revisit** if more than 4 ground devices need to connect, or if range is insufficient in the cage.

### Update 2026-10-23: port numbers

Port numbers are **not fixed yet**. They are still open in [[Protocol Spec v0]] §7 (OD-04) and are set at the protocol freeze in Study Plan week 3 (2026-11-02 to 2026-11-06). The rest of the decision is unchanged.

### Update 2026-11-03: port numbers set

The decision is unchanged. The protocol review of 2026-11-03 (#97) set the ports in [[Protocol Spec v0]] §7.1: UDP 5759 for control and telemetry, TCP 5759 for the session, 5760 reserved (the transparent bridge's port until the gateway firmware replaces it). OD-04 is resolved.

### Update 2026-11-19: flash clock and SoftAP channel

The decision is unchanged. The #194 TCP stall on the SoftAP went away with the ESP's SPI flash clock at 80 MHz instead of 40 MHz (15 of 15 HIL runs green, PR #212). Likely cause: the 40 MHz clock's harmonic at 2440 MHz is inside channel 6; the 80 MHz harmonics fall at 2400 and 2480 MHz.

- The HIL spare ESP (`env:esp`, flashed over USB) runs at **80 MHz**.
- The drone (`env:esp_ota`) stays at **40 MHz** (lifted 2026-11-26, see below) until its flash chip is checked for 80 MHz (`esptool.py flash_id`, over USB, with the ESP off the FC: its USB shares UART0 with the FC link, [[ADR-012 ESP UART Pins|ADR-012]]). A chip that can't run 80 MHz would leave the ESP unbootable after an OTA update. So HIL tests a different flash clock than the drone flies; the stall was only seen on the HIL bench.
- The SoftAP channel is limited to **1–11**: the 80 MHz harmonic at 2480 MHz is inside channel 13. `secrets.py` stops the build outside 1–11.

### Update 2026-11-26: the drone at 80 MHz too

The decision is unchanged. The drone's ESP now runs its flash at **80 MHz**, like the HIL spare (#218). The 2026-11-19 entry's limit (drone at 40 MHz) is lifted:

- **The chip is rated for it.** `late_log.py` read chip ID `0016405e` (JEDEC `5E 40 16`): a Zbit ZB25VQ32, rated 104 MHz for the fast reads the ESP uses in DIO mode (datasheet, #218). The HIL spare has the same chip. It was read over Wi-Fi, so the ESP stayed on the FC.
- **Every env builds at 80 MHz**, from one line in the shared `[env]` section of `platformio.ini`. Never give an env 40 MHz. After an OTA, `eboot` compares the bootloader sector (the first 4 KB) with the new image's and rewrites it when they differ, a window of a fraction of a second. A clock change (40 ↔ 80) or a size change does that; so does a new `eboot` from a platform update. The flash mode can't: for an uncompressed image (we never upload `.bin.gz`), the OTA code sets the new image's mode byte to the running one. A power loss in that window needs a USB recovery, with the ESP taken off the FC ([[ADR-012 ESP UART Pins|ADR-012]]). An OTA at the same clock, size and platform leaves that sector alone. When the sector has to change (for example after a platform version bump), flash such an image over USB (`env:esp`, ESP off the FC), or OTA it with a charged battery and nobody touching the power until the SoftAP is back, as on 2026-11-26.
- **Guard.** `secrets.py` refuses an OTA env that isn't at 80 MHz or doesn't use the same platform, board and flash layout (`ota_guard.py`). The guard checks the build, not the drone: after a USB restore of a 40 MHz backup, an 80 MHz OTA still rewrites sector 0, so read the running clock first (`bus` in `late_log.py`). Branches cut from `dev` before #218 don't have that check and still build their OTA envs at 40 MHz: merge `dev` into a branch before any OTA from it.
- **HIL rehearsal.** Dot99 ran the 40 → 80 MHz OTA on the HIL spare first: passed (2026-11-26).
- **Drone OTA and power cut** (props off). After the OTA of `esp_late_log_ota`, the SoftAP was back in about 10 s and `bus` read 80 MHz. After a power cut it read 80 MHz again. Then `esp_ota` went back on the same way.
- **Link.** About 100 s of 50 Hz control: 0 refused or dropped frames, 0 link-loss silences. The link failsafe came 268 ms after the last SETPOINT (T_loss 250 ms plus the Wi-Fi trip). Results and CSVs on #218.
- So HIL and the drone now test and fly the same flash clock. The channel limit (1–11) stays.

## 3. Consequences

- The [[HIL Runner]] Pi joins the ESP SoftAP on Wi-Fi and uses Ethernet for GitHub.
- The ground laptop loses internet while connected to the drone network unless it has a second interface.

### Update 2026-10-26: HIL Pi without Ethernet

There is no free Ethernet port at the HIL bench. The Pi uses its one Wi-Fi radio for both: the lab Wi-Fi for GitHub, and short windows (under 2 minutes) on the spare ESP's SoftAP for the tests ([[ADR-020 HIL Runner|ADR-020]] update 2026-10-26). This replaces the "Ethernet for GitHub" line in §3.

## 4. Related pages

- [[PC_to_ESP]] §8 · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
| **SoftAP** | Software access point: the ESP creates its own Wi-Fi network that the ground PC joins (ADR-006). |
| **WPA2-PSK** | Wi-Fi security mode where every client uses the same pre-shared key (the ESP SoftAP, ADR-006). |
| **PSK** | Pre-shared key: a secret agreed in advance (Wi-Fi password, HMAC key). Never committed. |
| **Flash clock** | The SPI bus clock between the ESP8266 and its flash chip (40 or 80 MHz), set in the image header at build time. Its harmonics can fall inside a Wi-Fi channel. |
| **eboot** | The Arduino core's bootloader at the start of the ESP flash. After an OTA it copies the new image into place. |
| **OTA** | Over-the-air update: flashing the ESP over Wi-Fi instead of USB ([[ADR-024 OTA Firmware Updates\|ADR-024]]). |
