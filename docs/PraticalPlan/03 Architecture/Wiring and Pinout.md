---
title: Wiring and Pinout
type: hardware
status: draft
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - FC Pad Identification
tags:
  - hardware
  - wiring
---

# Wiring and Pinout

> [!warning] Status: DRAFT. The ESP is wired (state 2026-10-20).
> Since 2026-10-19 the ESP-12F (ESP8266) is **soldered to FC UART4** with wires that are easy to remove, so the install is not permanent. It is not on a connector yet ([[ADR-021 Removable ESP Connector|ADR-021]], accepted 2026-10-20). S2-01 passed on this link ([[ESP Bench Test Plan]] §4). Still open: the [[ESP_to_FC]] §10 evidence on the soldered joints, the ESP current (S1-08), and the ELRS failsafe re-test (GitHub #22, #41).

---

## 1. FC pads (team inspection 2026-10-12, multimeter 2026-10-16)

Functions come from the OddityRC wiring diagram ([vendor diagram](https://www.rotorama.com/cms/assets/docs/6d96909d797b5e734f781ca47138a567/35018-0/6x.jpg)) and the Betaflight target `ODDITYRCF7AIO`. Multimeter checks: GitHub #16.

| Pad | Function | Status |
|---|---|---|
| T1 / R1 | UART1 | free (spare) |
| R2 / T2 | UART2, ELRS (CRSF) | in use. **Confirmed with a multimeter** (2026-10-16, [[2.5Drone]] #22) |
| R3 / T3 | UART3, Tramp VTX (SA/IRC) | in use |
| **T4 / R4** | **UART4 → ESP** (PA0 / PA1) | free. **Continuity to the MCU side OK, no shorts to neighbours** (2026-10-16) → [[ADR-011 FC UART Allocation\|ADR-011]] |
| SCL / SDA | I2C1 (baro bus) | leave free |
| CAM | analog camera **video input** (to the AT7456E OSD) | in use (camera soldered, [[2.5Drone]] #19). Not camera control: the target has no camera-control pin |
| VTX | video output to the analog VTX | in use |
| DV5V | 5 V supply for the **receiver** (the diagram powers TBS / ELRS / SBUS receivers from it) | **ELRS 5 V wire is here** (GitHub #41, reported 2026-10-20). Do not use for the ESP |
| 5V, 3.3V, GND | power | the ELRS red wire was on a 5V pad and is now on DV5V (GitHub #41). The **5V** pad next to T4 feeds the ESP. The ELRS antenna is soldered. The failsafe re-test is still open (GitHub #22, #41) |
| BAT | battery voltage (4–6S) for the analog VTX | in use. **Never** on an ESP pin |

### 1.1 Pad row next to UART4 (vendor diagram)

```text
   top row:     T4   5V   SCL  DV5V  R2
   bottom row:  R4   GND  3V3  SDA   GND  T2
```

<<<<<<< HEAD
- The diagram uses this row for a GPS on UART4 (T4, R4, 5V, GND). The D1 mini takes the same four pads.
- The ELRS receiver in the diagram uses DV5V, GND, R2 and T2. **Our ELRS red wire is on 5V** (2026-10-16). The team moves it to DV5V to free the 5V pad and to keep radio interference low (GitHub #41, [[ADR-011 FC UART Allocation|ADR-011]]).
=======
- The diagram uses this row for a GPS on UART4 (T4, R4, 5V, GND). The ESP takes the same four pads.
- The ELRS receiver in the diagram uses DV5V, GND, R2 and T2. Our ELRS red wire was on 5V (2026-10-16). **The team has moved it to DV5V** (reported 2026-10-20) to free the 5V pad and to keep radio interference low (GitHub #41, [[ADR-011 FC UART Allocation|ADR-011]]). The failsafe has **not** been re-tested since (GitHub #22, #41 stay open).
>>>>>>> adbbf2b014e99e81e0a59c616cca9aeb382a88e3
- The neighbours of T4 are 5V and R4; the neighbours of R4 are T4 and GND. The 2026-10-16 check found no short to any of them.

## 2. Connections

| From | To | Notes |
|---|---|---|
| FC **T4** | ESP **RX (GPIO3)** | MSP, FC → ESP |
| FC **R4** | ESP **TX (GPIO1)** | MSP, ESP → FC |
| FC **5V** | ESP **5V** | the ESP regulator makes 3.3 V. **Never 5 V on a GPIO.** |
| FC **GND** | ESP **G** | common ground |
| ThoneFlow **TX** | ESP **D5 (GPIO14)** | only for the [[ADR-016 Flow Sensor Path\|ADR-016]] experiment |
| ThoneFlow **VCC** | ESP **3V3** | datasheet: 3.3–3.6 V, 10 mA |
| ThoneFlow **GND** | ESP **G** | |

```text
   FC (ODDITYRCF7AIO)                ESP8266          ThoneFlow-3901U (experiment)
   T4 (PA0) ───────────────────────► RX  (GPIO3)
   R4 (PA1) ◄─────────────────────── TX  (GPIO1)
   5V ─────────────────────────────► 5V
   GND ────────────────────────────► G  ◄──────────────────────── GND
                                     D5 (GPIO14) ◄────────────── TX
                                     3V3 ──────────────────────► VCC
```

### 2.1 Mounting ([[ADR-021 Removable ESP Connector|ADR-021]], accepted 2026-10-20)

**Today:** the ESP is soldered to T4, R4, 5V and GND with wires that are easy to remove. **Decided:** a 4-pin locking connector between the FC pads and the ESP, so we stop soldering and desoldering. It is not fitted yet. Pin order to record here once chosen:

| Pin | Signal | FC pad | ESP pin |
|---|---|---|---|
| 1 | 5V | 5V | 5V |
| 2 | GND | GND | G |
| 3 | FC TX → ESP RX | T4 | RX (GPIO3) |
| 4 | ESP TX → FC RX | R4 | TX (GPIO1) |

## 3. ESP pins to avoid

| Pin | Why |
|---|---|
| D3 (GPIO0), D4 (GPIO2) | boot strapping (must be high at boot). D4 is also the LED and `Serial1` TX. |
| D8 (GPIO15) | boot strapping (must be low at boot) |
| TX / RX | shared with USB: never connect USB and the FC together ([[ADR-012 ESP UART Pins\|ADR-012]]) |

Free for later: D1 (GPIO5), D2 (GPIO4), D6, D7. (The ToF rangefinder and the flow-bridge MCU were rejected on 2026-10-14, [[ADR-016 Flow Sensor Path|ADR-016]].)

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CRSF** | Crossfire Serial Protocol, a serial protocol used by radio-control equipment. |
| **DV5V** | 5 V pad on this FC that the vendor diagram uses to power the receiver. |
<<<<<<< HEAD
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
=======
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
>>>>>>> adbbf2b014e99e81e0a59c616cca9aeb382a88e3
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **OSD** | On-Screen Display: the chip that draws flight data over the analog camera video. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **MCU** | Microcontroller unit: the processor chip (STM32F722 on the FC, ESP8266 on the ESP module). |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
| **GND** | Ground: the common 0 V reference wire. |
