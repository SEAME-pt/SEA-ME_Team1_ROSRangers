---
title: ADR-006 — Wi-Fi Network Topology and Security
type: adr
status: accepted
date: 2026-10-12
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

## 3. Consequences

- The [[HIL Runner]] Pi joins the ESP SoftAP on Wi-Fi and uses Ethernet for GitHub.
- The ground laptop loses internet while connected to the drone network unless it has a second interface.

## 4. Related pages

- [[PC_to_ESP]] §8 · [[Decision Register]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F, Wemos D1 mini class). It bridges the ground PC to the FC over UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
