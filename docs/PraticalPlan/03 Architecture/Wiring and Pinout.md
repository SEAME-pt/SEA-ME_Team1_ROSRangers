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

> [!warning] Status: DRAFT. Nothing is wired yet.
> Do not solder anything until Stages 1–2 of the [[ESP Bench Test Plan]] **and** the [[ESP_to_FC]] §10 checklist have passed (continuity, logic level, 5V rail current). During Stage 2, use **temporary** wires only.

---

## 1. FC pads (team inspection, 2026-10-12)

| Pad | Function | Status |
|---|---|---|
| T1 / R1 | UART1 | free (spare) |
| R2 (+ T2 under wires) | UART2, ELRS (CRSF) | in use, Medium certainty ([[2.5Drone]] #22) |
| R3 / T3 | UART3, Tramp VTX | in use |
| **T4 / R4** | **UART4 → D1 mini** | free → [[ADR-011 FC UART Allocation\|ADR-011]] |
| SCL / SDA | I2C1 (baro bus) | leave free |
| CAM, DV5V | camera control, VTX power | in use / do not touch |
| 5V, 3.3V, GND | power | 5V for the D1 mini |

## 2. Connections

| From | To | Notes |
|---|---|---|
| FC **T4** | D1 mini **RX (GPIO3)** | MSP, FC → ESP |
| FC **R4** | D1 mini **TX (GPIO1)** | MSP, ESP → FC |
| FC **5V** | D1 mini **5V** | the D1 mini regulator makes 3.3 V. **Never 5 V on a GPIO.** |
| FC **GND** | D1 mini **G** | common ground |
| ThoneFlow **TX** | D1 mini **D5 (GPIO14)** | only for the [[ADR-016 Flow Sensor Path\|ADR-016]] experiment |
| ThoneFlow **VCC** | D1 mini **3V3** | datasheet: 3.3–3.6 V, 10 mA |
| ThoneFlow **GND** | D1 mini **G** | |

```text
   FC (ODDITYRCF7AIO)                D1 mini (ESP8266)          ThoneFlow-3901U (experiment)
   T4 (PA0) ───────────────────────► RX  (GPIO3)
   R4 (PA1) ◄─────────────────────── TX  (GPIO1)
   5V ─────────────────────────────► 5V
   GND ────────────────────────────► G  ◄──────────────────────── GND
                                     D5 (GPIO14) ◄────────────── TX
                                     3V3 ──────────────────────► VCC
```

### 2.1 Mounting (proposed, [[ADR-021 Removable ESP Connector|ADR-021]])

A 4-pin locking connector between the FC pads and the D1 mini. Pin order to record here once chosen:

| Pin | Signal | FC pad | D1 mini pin |
|---|---|---|---|
| 1 | 5V | 5V | 5V |
| 2 | GND | GND | G |
| 3 | FC TX → ESP RX | T4 | RX (GPIO3) |
| 4 | ESP TX → FC RX | R4 | TX (GPIO1) |

## 3. D1 mini pins to avoid

| Pin | Why |
|---|---|
| D3 (GPIO0), D4 (GPIO2) | boot strapping (must be high at boot). D4 is also the LED and `Serial1` TX. |
| D8 (GPIO15) | boot strapping (must be low at boot) |
| TX / RX | shared with USB: never connect USB and the FC together ([[ADR-012 ESP UART Pins\|ADR-012]]) |

Free for later: D1 (GPIO5) / D2 (GPIO4) as I2C (ToF rangefinder or flow-bridge MCU, [[ADR-016 Flow Sensor Path|ADR-016]]), D6, D7.

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CRSF** | Crossfire Serial Protocol, a serial protocol used by radio-control equipment. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
