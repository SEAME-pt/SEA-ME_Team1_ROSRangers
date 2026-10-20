---
title: ADR-004 — ESP Link Failsafe
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D04
  - D04 ESP Failsafe
tags:
  - adr
  - safety
  - esp8266
---

# ADR-004 — ESP Link Failsafe

> [!success] Status: ACCEPTED (team, 2026-10-12). The numbers are *initial* values, to be confirmed by measurement.
> Gives initial values for **OD-06/OD-07**, partly resolves **OD-12**, and resolves **OD-13** in [[PC_to_ESP]]. Related to **FC-11/FC-13** in [[ESP_to_FC]].

## 1. Context

- The Module 1 rule says the real-time side must reach a safe state **without Linux being alive**.
- On Betaflight **4.5.1**, MSP Override values are **held indefinitely** if the sender stops. This is confirmed by the Betaflight guide quoted in [[ESP_to_FC]] §3.1 and in the 4.5.1 source ([[Verified Facts and Sources]]).
- **Pilot takeover remains the only guaranteed mitigation** ([[ESP_to_FC]] FC-11). This ADR is defence in depth, not a replacement for it.

## 2. Decision

**Trigger:** no *accepted* control message for **`T_loss` = 250 ms** (initial value, about 12 missed packets at 50 Hz). Confirm with the OD-07 method: just above the worst gap seen in normal operation.

**Action in `LINK_FAILSAFE`:**

| Betaflight version | ESP action | Result |
|---|---|---|
| 4.5.1 | Keep streaming **neutral** values (roll/pitch/yaw 1500). Throttle is not overridden (pilot owns it, [[ADR-010 MSP Override|ADR-010]]). | Drone levels in Angle mode. The pilot holds altitude and takes over. |
| 2026.6.2 | Neutral for 1 s, then **stop sending** | After 300 ms, Betaflight's freshness guard falls back to the ELRS sticks |

**Resume:** never automatic. The ground sends `RESUME_REQUEST` inside a valid session, then streams neutral setpoints for ≥ 500 ms before control returns ([[ESP Link State Machine]]).

## 3. Safety layers (inner → outer)

1. Betaflight ELRS failsafe (radio loss → Stage 1 → Stage 2).
2. Betaflight MSP freshness guard (2026.6.x only).
3. This ESP link failsafe.
4. Ground `flight_interface` watchdog (lifecycle E-stop).
5. **Safety pilot + ELRS switch: always wins.**

## 4. Consequences

- On 4.5.1, if the **ESP itself** crashes, layer 3 is gone and the FC holds the last values. That is why [[ADR-014 Betaflight Version|ADR-014]] upgrades Betaflight, and why override flights wait for the upgrade.
- What counts as "safe" stays a flight-safety judgement for the team (OD-12).

## 5. Related pages

- [[ESP_to_FC]] §9 (F1–F9) · [[PC_to_ESP]] §7 · [[ESP Link State Machine]] · [[Safety Rules]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
