---
title: Requirements
type: plan
status: active
date: 2026-10-21
project: SEA autonomous drone
tags:
  - plan
  - requirements
---

# Requirements

Single register of every requirement written in the docs. IDs follow `REQ-<SYS|NAV|SEN|COM|CTL|SAF|PWR|SW|HW>-NNN` ([[Dev_Strategy]] §15), numbered per category from 001. IDs are stable: never reuse or renumber one. Issues, PRs and tests reference them (`Implements REQ-COM-001`).

Only requirements already written in a note are registered here. The full wording stays in the **Source** note. Where the text still has a TBD or an open decision, Status is `draft`.

## 1. How to add a requirement

1. Write it in the note where it belongs, with the next free number of its category: `**REQ-COM-024**: The receiver shall ...`.
2. Add a row below with the same ID, a one-line summary, the source note, the verification method and the status.
3. Verification is one or more of `test`, `analysis`, `inspection`, `demo` (§1.1), and Level says where it runs. Status is `draft`, `accepted` or `verified` (link the test or report when verified).
4. Never change an ID. To retire a requirement, set Status to `withdrawn` and keep the row.
5. Reference the ID in the issue, the PR and the test.

### 1.1 Verification methods and levels

| Method | Use it when | Evidence |
|---|---|---|
| **test** | The result can be measured against a pass/fail number. Default for anything in code or on the link. | pytest / GoogleTest run in CI, or a bench / cage log with the numbers |
| **analysis** | Testing every case is impractical: timing budgets, fuzzing results, static analysis, worst-case reasoning. | Short report or notebook linked from the row |
| **inspection** | It is a property of wiring, configuration or code that you can look at. | Photo, `diff all` compared with the baseline, review comment |
| **demo** | It must be shown working, but a precise number adds little (pilot takeover in the cage, E-stop). | Video plus a one-line result in the flight-test report |

Safety requirements (`REQ-SAF`) use **test** wherever a number exists. A demo alone is not enough for them.

Levels, from cheapest to most expensive: **unit** (C++ / Python, golden vectors) → **SITL** (Betaflight SITL or PX4 Gazebo in CI) → **HIL** (bench, props off) → **bench** (props off, battery allowed per [[Safety Rules]] #13) → **cage** (flight). Verify at the cheapest level that can show the requirement, and repeat in the cage only what needs real flight.

### 1.2 Categories

`REQ-COM` covers what makes a message correct: validation, sequence, freshness, TCP, reconnection. `REQ-SAF` covers what keeps the drone and people safe when something goes wrong: pilot takeover, loss detection, failsafe action, start-up state. Loss detection is in SAF, not COM, because it triggers the failsafe. `REQ-NAV` covers position and estimation, `REQ-CTL` the limits on commands, `REQ-SW` software timing and structure, and `REQ-PWR` power and batteries.

## 2. Register

### Communication (`REQ-COM`)

| ID | Requirement | Source | Verification | Level | Status |
|---|---|---|---|---|---|
| REQ-COM-001 | Receiver checks every datagram for structural validity (length vs type, version, message type) before interpreting it. | [[PC_to_ESP]] §5 (validation) | test | unit (C++ + Python, golden vectors) | accepted |
| REQ-COM-002 | Receiver checks field-level validity (allowed range) before any value goes toward the FC. Range limits TBD. | [[PC_to_ESP]] §5 (validation) | test | unit (C++ + Python, golden vectors) | draft |
| REQ-COM-003 | Receiver verifies an integrity check on each datagram. Mechanism was open; see [[ADR-002 Message Integrity|ADR-002]]. | [[PC_to_ESP]] §5 (validation) | test | unit (C++ + Python, golden vectors) | accepted |
| REQ-COM-004 | Datagrams failing any check are discarded without side effects on control state and counted in a diagnostic counter. | [[PC_to_ESP]] §5 (validation) | test | unit (C++ + Python, golden vectors) | accepted |
| REQ-COM-005 | Malformed input never causes a crash, hang, buffer overrun or undefined behaviour on the ESP8266. | [[PC_to_ESP]] §5 (validation) | test + analysis | unit + fuzz; static analysis | accepted |
| REQ-COM-006 | Each control setpoint carries a sender-assigned sequence number that advances per new setpoint. | [[PC_to_ESP]] §6 (sequence) · [[ADR-003 Sequence Acceptance|ADR-003]] | test | unit (C++ + Python, golden vectors) | accepted |
| REQ-COM-007 | Receiver tracks the sequence number of the most recently accepted control message. | [[PC_to_ESP]] §6 (sequence) · [[ADR-003 Sequence Acceptance|ADR-003]] | test | unit (C++ + Python, golden vectors) | accepted |
| REQ-COM-008 | Sequence-number width and wrap-around are defined (32-bit; `seq >= 2^31` forces a new session). | [[PC_to_ESP]] §6 (sequence) · [[ADR-003 Sequence Acceptance|ADR-003]] | inspection + test | spec review; unit (wrap-around) | accepted |
| REQ-COM-009 | Sequence state is tied to a session so a restarted sender is not confused with replayed traffic. | [[PC_to_ESP]] §6 (sequence) · [[ADR-003 Sequence Acceptance|ADR-003]] | test | unit (C++ + Python, golden vectors) | accepted |
| REQ-COM-010 | Receiver accepts a control message only if it is newer than the latest accepted one. | [[PC_to_ESP]] §6 (sequence) · [[ADR-003 Sequence Acceptance|ADR-003]] | test | unit (C++ + Python, golden vectors) | accepted |
| REQ-COM-011 | A duplicate control message is discarded and counted. | [[PC_to_ESP]] §6 (sequence) · [[ADR-003 Sequence Acceptance|ADR-003]] | test | unit (C++ + Python, golden vectors) | accepted |
| REQ-COM-012 | A reordered (older) message after a newer one is discarded and counted; old values are not replayed. | [[PC_to_ESP]] §6 (sequence) · [[ADR-003 Sequence Acceptance|ADR-003]] | test | unit (C++ + Python, golden vectors) | accepted |
| REQ-COM-013 | Gaps in sequence numbers are detected and counted; a gap alone is not an error. | [[PC_to_ESP]] §6 (sequence) · [[ADR-003 Sequence Acceptance|ADR-003]] | test | unit (C++ + Python, golden vectors) | accepted |
| REQ-COM-014 | A setpoint is usable only while fresh: stale after `T_fresh` (TBD) since accepted, never forwarded indefinitely. | [[PC_to_ESP]] (Freshness) | test | unit + HIL | draft |
| REQ-COM-015 | Freshness uses a time source the ESP8266 controls (own receive-time clock). Sender timestamps are an open decision. | [[PC_to_ESP]] (Freshness) | test | unit + HIL | draft |
| REQ-COM-016 | Timing evaluation does not depend on the TCP connection being alive. | [[PC_to_ESP]] (Freshness) | test | unit + HIL | accepted |
| REQ-COM-017 | TCP messages use explicit framing and are validated for structure, version and type before use. | [[PC_to_ESP]] (TCP) | test | unit | accepted |
| REQ-COM-018 | Configuration requests are acknowledged, with success or failure reported to the ground side. | [[PC_to_ESP]] (TCP) | test | unit | accepted |
| REQ-COM-019 | Malformed TCP input is rejected without corrupting state; handling of repeated malformed input TBD. | [[PC_to_ESP]] (TCP) | test | unit | draft |
| REQ-COM-020 | After loss, control does not resume just because packets arrive again; a defined, validated handshake is required (exact handshake TBD). | [[PC_to_ESP]] (Reconnection) | test | HIL (Wi-Fi / TCP drop on the bench) | draft |
| REQ-COM-021 | On reconnection the receiver is not fooled by replayed or old traffic. | [[PC_to_ESP]] (Reconnection) | test | unit + HIL | accepted |
| REQ-COM-022 | Ground side can re-establish the TCP session and re-synchronise state after a Wi-Fi or TCP interruption. | [[PC_to_ESP]] (Reconnection) | test | HIL (Wi-Fi / TCP drop on the bench) | accepted |
| REQ-COM-023 | Wi-Fi re-association behaviour (retry, backoff) is defined and does not block loss detection. | [[PC_to_ESP]] (Reconnection) | test | HIL (Wi-Fi / TCP drop on the bench) | draft |

### Safety (`REQ-SAF`)

| ID | Requirement | Source | Verification | Level | Status |
|---|---|---|---|---|---|
| REQ-SAF-001 | Independent receiver path: ELRS receiver to FC stays independent of the ESP8266; the pilot never needs the ESP8266 link. | [[ESP_to_FC]] §8 | inspection | bench: wiring + `diff all` | accepted |
| REQ-SAF-002 | Pilot retains takeover: switching MSP override off with an ELRS switch takes over at any time. | [[ESP_to_FC]] §8 | test + demo | bench props off (`MSP_RC` readback), then cage | accepted |
| REQ-SAF-003 | Independent ARM: ARM is a receiver-originated channel; the ESP8266/MSP path cannot arm the FC (to be confirmed by test). | [[ESP_to_FC]] §8 | test | bench props off | accepted |
| REQ-SAF-004 | Takeover channel not overridable: ARM and override-switch channels are not among those overridable via MSP (enforcement TBD). | [[ESP_to_FC]] §8 | inspection + test | `diff all` + bench props off | draft |
| REQ-SAF-005 | Takeover works when the ESP8266 misbehaves (still sending, crashed or flooding the UART); to be demonstrated. | [[ESP_to_FC]] §8 | test | bench props off: fault injection F1–F9 ([[ESP_to_FC]] §11) | accepted |
| REQ-SAF-006 | Takeover works across link events (ESP8266 reboot, UART disconnect, receiver loss); behaviour must be known. | [[ESP_to_FC]] §8 | test | bench props off | accepted |
| REQ-SAF-007 | Pilot awareness: pilot or safety observer can tell whether override is active (mechanism TBD). | [[ESP_to_FC]] §8 | inspection + demo | OSD / LED config, then cage | draft |
| REQ-SAF-008 | Unchanged ELRS configuration: the ESP8266 link requires no disabled or weakened receiver failsafe or arming safeguard. | [[ESP_to_FC]] §8 | inspection | `diff all` compared with the baseline | accepted |
| REQ-SAF-009 | ESP8266 detects when valid control updates stop, using `T_loss` (TBD) from the last accepted message. | [[PC_to_ESP]] (loss and failsafe) | test | unit + HIL | accepted |
| REQ-SAF-010 | Loss detection does not rely only on Wi-Fi association or TCP state. | [[PC_to_ESP]] (loss and failsafe) | test | unit + HIL | accepted |
| REQ-SAF-011 | Invalid, stale, duplicate or out-of-order packets do not reset the loss timer. | [[PC_to_ESP]] (loss and failsafe) | test | unit + HIL | accepted |
| REQ-SAF-012 | Ground side independently detects link loss and informs mission logic. | [[PC_to_ESP]] (loss and failsafe) | test | unit (ground) + SITL | accepted |
| REQ-SAF-013 | On loss the ESP8266 enters a defined failsafe state and stops forwarding the last setpoint as current. | [[PC_to_ESP]] (loss and failsafe) | test | unit + HIL | accepted |
| REQ-SAF-014 | Failsafe action toward the FC follows [[ADR-004 ESP Failsafe|ADR-004]] (2026-10-20 entry): neutral sticks on the overridden axes, throttle stays with the pilot, no automatic landing or return; the drone holds and the pilot takes over with the radio. | [[PC_to_ESP]] (loss and failsafe) · [[ADR-004 ESP Failsafe|ADR-004]] | test | HIL, then cage | accepted |
| REQ-SAF-015 | Failsafe state is observable by the ground side when the link allows. | [[PC_to_ESP]] (loss and failsafe) | test | HIL | accepted |
| REQ-SAF-016 | ESP8266 is safe and deterministic at power-up, reset and watchdog; no control forwarding until the start-up conditions are met. | [[PC_to_ESP]] (loss and failsafe) | test | HIL (power cycle, reset, watchdog) | accepted |
| REQ-SAF-017 | Mission node outputs zero velocity within 0.25 s of losing `/gesture/intent`. | [[04 Mission Node]] (acceptance) | test | unit + SITL | draft |
| REQ-SAF-018 | Mission node never commands an axis outside the active override mask. | [[04 Mission Node]] (acceptance) | test | unit + SITL | draft |
| REQ-SAF-019 | `intent_node` never relays a stale gesture: hand gone > 0.3 s gives `NEUTRAL`; hand or packets gone > 2 s, or no packet yet, gives `HOVER_LAND_REQUEST`; no replay after a gap. | [[03 Intent Node]] §10 | test | unit + ROS node | draft |
| REQ-SAF-020 | `intent_node` changes the intent only for a known gesture seen in 5 consecutive packets; no gesture maps to arm, disarm or the override switch. | [[03 Intent Node]] §10 | test + inspection | unit + ROS node | draft |

### Navigation (`REQ-NAV`)

Values proposed 2026-10-20 ([[ADR-016 Flow Sensor Path|ADR-016]] update). The team confirms them after the week-5 baro and flow measurements.

| ID | Requirement | Source | Verification | Level | Status |
|---|---|---|---|---|---|
| REQ-NAV-001 | Position hold in PX4 SITL: at 1.0 m, with zero velocity setpoints from the flight interface, the drone stays within **0.25 m** horizontally and **±0.15 m** vertically of the start point for **60 s**. | [[Study Plan v2]] §11 · [[ADR-016 Flow Sensor Path|ADR-016]] | test | SITL (Gazebo ground truth, in CI) | draft |
| REQ-NAV-002 | Position hold in the cage: at about 1.0 m, with the ground controller holding position from the flow estimate and the pilot hands-off on roll/pitch, the drone stays inside a **0.5 m radius** for **30 s**, in **3 of 3** runs. When height is automatic (ALTHOLD or the ground controller), height stays within **±0.3 m**. If the pilot has the throttle, height is not judged. | [[Study Plan v2]] §11 · [[ADR-016 Flow Sensor Path|ADR-016]] | test | cage (floor tape grid + overhead video) | draft |

### Control (`REQ-CTL`)

| ID | Requirement | Source | Verification | Level | Status |
|---|---|---|---|---|---|
| REQ-CTL-001 | Gesture speeds limited to 0.3 m/s lateral, 0.2 m/s vertical, 20 °/s yaw (parameters, with a hard ceiling). | [[04 Mission Node]] (acceptance) | test | unit + SITL | draft |

### Software (`REQ-SW`)

| ID | Requirement | Source | Verification | Level | Status |
|---|---|---|---|---|---|
| REQ-SW-001 | `/mission/velocity_target` is published at 20 Hz in every mode. | [[04 Mission Node]] (acceptance) | test | SITL | draft |
| REQ-SW-002 | `/gesture/intent` is published at 20 Hz as a JSON string, whatever the input. | [[03 Intent Node]] §10 | test | ROS node | draft |

### Power (`REQ-PWR`)

| ID | Requirement | Source | Verification | Level | Status |
|---|---|---|---|---|---|
| REQ-PWR-001 | Before any bench or cage session the flight battery is inspected, XT30/XT60 polarity checked, cell voltages logged; damage or a cell outside 3.5-4.20 V means no use. | [[Battery Log]] §1 | inspection | every session (Battery Log) | accepted |

Categories `REQ-SYS`, `REQ-SEN` and `REQ-HW` have no requirement yet. The IDs in [[Dev_Strategy]] §15 (`REQ-NAV-001` and similar) are illustrative examples, not entries.

## 3. Legacy ID mapping

Old IDs came from [[PC_to_ESP]], [[ESP_to_FC]] and [[04 Mission Node]], and from the notes that cite them. Those notes keep the old ID in brackets on first use. `REQ-PWR-001` in [[Battery Log]] already fits the scheme and is unchanged.

| Old ID | New ID |
|---|---|
| REQ-VAL-01 | REQ-COM-001 |
| REQ-VAL-02 | REQ-COM-002 |
| REQ-VAL-03 | REQ-COM-003 |
| REQ-VAL-04 | REQ-COM-004 |
| REQ-VAL-05 | REQ-COM-005 |
| REQ-SEQ-01 | REQ-COM-006 |
| REQ-SEQ-02 | REQ-COM-007 |
| REQ-SEQ-03 | REQ-COM-008 |
| REQ-SEQ-04 | REQ-COM-009 |
| REQ-ORD-01 | REQ-COM-010 |
| REQ-ORD-02 | REQ-COM-011 |
| REQ-ORD-03 | REQ-COM-012 |
| REQ-ORD-04 | REQ-COM-013 |
| REQ-FRESH-01 | REQ-COM-014 |
| REQ-FRESH-02 | REQ-COM-015 |
| REQ-FRESH-03 | REQ-COM-016 |
| REQ-TCP-01 | REQ-COM-017 |
| REQ-TCP-02 | REQ-COM-018 |
| REQ-TCP-03 | REQ-COM-019 |
| REQ-RECON-01 | REQ-COM-020 |
| REQ-RECON-02 | REQ-COM-021 |
| REQ-RECON-03 | REQ-COM-022 |
| REQ-RECON-04 | REQ-COM-023 |
| SAF-01 | REQ-SAF-001 |
| SAF-02 | REQ-SAF-002 |
| SAF-03 | REQ-SAF-003 |
| SAF-04 | REQ-SAF-004 |
| SAF-05 | REQ-SAF-005 |
| SAF-06 | REQ-SAF-006 |
| SAF-07 | REQ-SAF-007 |
| SAF-08 | REQ-SAF-008 |
| REQ-LOSS-01 | REQ-SAF-009 |
| REQ-LOSS-02 | REQ-SAF-010 |
| REQ-LOSS-03 | REQ-SAF-011 |
| REQ-LOSS-04 | REQ-SAF-012 |
| REQ-FS-01 | REQ-SAF-013 |
| REQ-FS-02 | REQ-SAF-014 |
| REQ-FS-03 | REQ-SAF-015 |
| REQ-FS-04 | REQ-SAF-016 |
| REQ-SAF-0xx (placeholder) | REQ-SAF-017 |
| REQ-SAF-0xx (placeholder) | REQ-SAF-018 |
| REQ-CTL-0xx (placeholder) | REQ-CTL-001 |
| REQ-SW-0xx (placeholder) | REQ-SW-001 |

## 4. Glossary

| Term | Meaning |
|---|---|
| **REQ** | Requirement ID of the form `REQ-<SYS, NAV, SEN, COM, CTL, SAF, PWR, SW or HW>-NNN`, used for traceability. |
| **Verification** | How a requirement is shown to hold: test, analysis, inspection or demo. |
| **Level** | Where a requirement is verified: unit, SITL, HIL, bench or cage. |
| **SITL** | Software-in-the-loop: the flight software runs in simulation (Betaflight SITL or PX4 + Gazebo). |
| **Position hold** | The drone stays over one point with no stick input, measured as the drift radius over a fixed time. |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ELRS** | ExpressLRS, the radio link the safety pilot uses. |
| **MSP** | MultiWii Serial Protocol, used by the ESP8266 to talk to the flight controller. |
| **FC** | Flight controller. |
