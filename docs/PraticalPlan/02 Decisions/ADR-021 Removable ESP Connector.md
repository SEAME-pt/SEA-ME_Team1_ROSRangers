---
title: ADR-021 — ESP Mounting (amended 2026-10-26: direct solder, no connector)
type: adr
status: accepted
date: 2026-10-12
updated: 2026-11-05
project: SEA autonomous drone
tags:
  - adr
  - hardware
  - esp8266
---

# ADR-021 — ESP Mounting: direct solder, no connector (was: removable connector)

> [!success] Status: ACCEPTED (team, 2026-10-20), **AMENDED 2026-10-26: direct soldering, no connector** (see the update below)
> Amends the mounting part of [[ADR-013 Wiring and Power|ADR-013]].

> [!warning] Update 2026-10-26: direct soldering instead of the connector (team decision, recorded 2026-11-04)
> - **Decision:** the ESP stays soldered straight to FC UART4 (T4, R4, 5V, GND) with wires end to end. No connector is fitted. This replaces §3.
> - **Dates:** the ESP was re-soldered on **2026-10-26**, between ESP-S1-07 run 2 and run 3: its solder pins were removed and it was soldered directly to the FC pads ([[ESP Bench Test Plan]] §4). The team accepted direct soldering the same day (the day PR #116 was opened). Recorded here on 2026-11-04 by ThiagoTEP (incident GitHub #69).
> - **Why (team assessment):** the earlier wiring (2026-10-19 to 2026-10-26) had an intermittent contact. ThiagoTEP reported this to the team as the cause of the link problems, and the team accepted that assessment.
> - **Measurements (same test, same firmware, same day):** ESP-S1-07 on 2026-10-26, `esp_diag_tcp`, 50 Hz. Before the re-solder (run 2): the FC stopped answering on UART4 for 117 s while the ESP and Wi-Fi kept working (run 1 on 2026-10-23: 480 s). After the re-solder (runs 3 / 4 / 5): lost 42 / 26 / 28 of 30,000, RTT median 15.2 / 15.4 / 15.9 ms, longest silence 189 / 149 / 203 ms, 0 silences ≥ 250 ms, the FC answered every request it got ([[ESP Bench Test Plan]] §4, #25, #69).
> - **Not wiring:** the lower latency between ESP-S1-05 on 2026-10-19 (median 55 ms, p99 328 ms, 0.69 % loss) and on 2026-10-23 (median 12.2–12.5 ms, p99 58–63 ms) came from the custom ESP firmware (#116). Both runs were on the earlier wiring.
> - **Checks on the new joints (#69):** the [[ESP_to_FC]] §10 checklist was done (continuity, no shorts, TX/RX crossover, T4 idle level). **No photos were taken**, so the photo part of §10 is missing. ESP-S2-02 and ESP-S2-06 were run on the direct solder and reported PASS; **their numbers were not recorded**. The drone was tested again afterwards and works normally.
> - **Trade-off:** flashing the ESP over USB now means desoldering it ([[ADR-012 ESP UART Pins|ADR-012]]: USB never connected while the FC link is wired). OTA updates (ADR-024, proposed in #145) remove that need.
> - **Every new joint** still follows [[Safety Rules]] #11.

> [!info] Update 2026-10-20: accepted (GitHub #55)
> - The team accepted the proposal: use a removable connector, so we stop soldering and desoldering.
> - **Where the install stands:** since 2026-10-19 the ESP-12F (ESP8266) is soldered to FC UART4 with wires that are easy to remove, so the install is not permanent. The connector is **not fitted yet**; the connector type and pin order are still to be chosen and recorded in [[Wiring and Pinout]].
> - The ESP's USB stays disconnected while the FC link is wired ([[ADR-012 ESP UART Pins|ADR-012]]). Until the connector is fitted, this is a procedure, not something the hardware enforces.

## 1. Context

- On the ESP, USB flashing and the FC link share UART0, so they can never be connected together ([[ADR-012 ESP UART Pins|ADR-012]]).
- Desoldering an ESP from a 2.5" AIO risks lifting FC pads. Spares are limited.

## 2. Options considered

| Option | Pros | Cons |
|---|---|---|
| Solder the ESP wires straight to the FC | Lightest, no extra contact | Hard to remove for USB flashing or replacement; repeated soldering on the FC |
| **4-pin connector** (5V, GND, TX, RX) soldered to the FC pads, plug on the ESP | Unplug for USB flashing (removes the UART0/USB conflict by construction), swap a dead ESP in minutes, solder the FC only once | 1–2 g extra; one more contact that can loosen (vibration) |

## 3. Decision (accepted 2026-10-20, replaced 2026-10-26 by the update above)

Use a small locking 4-pin connector (e.g. JST-SH or JST-GH; the exact part is still to be chosen), with the pin order recorded in [[Wiring and Pinout]]. Secure the ESP mechanically (tape or a 3D-printed holder) so the connector carries no load.

## 4. Consequences (of the 2026-10-20 decision; for today see the 2026-10-26 update)

- Until the connector is fitted, the ESP stays on its soldered, removable wires. Re-flashing the ESP over USB means removing those wires first ([[ADR-012 ESP UART Pins|ADR-012]]).
- The connector goes on the pre-flight checklist ([[Safety Rules]]).
- A loose connector looks like the "UART disconnection" failure (F2 in [[ESP_to_FC]]). It is already covered by the readback and failsafe tests ([[ESP Bench Test Plan]]).

## 5. Related pages

- [[ESP Bench Test Plan]] · [[Decision Register]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
| **ESP** | The ESP-12F module (ESP8266) on the drone: the Wi-Fi bridge between the ground PC and the FC, wired to FC UART4. |
| **OTA** | Over-the-air: flashing the ESP firmware over Wi-Fi instead of USB (env `d1_mini_ota`). |
