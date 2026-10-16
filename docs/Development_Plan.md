# SEA:ME — Development Plan

## 1. Project Development Strategy

The development process is defined in [[Dev_Strategy]]. The week-by-week plan is in [[Study Plan v2]], and accepted decisions are in the [[Decision Register]].

The strategy in one line: **build a thin end-to-end path early (ground → Wi-Fi → ESP → FC), make it safe (failsafe, pilot takeover), make it testable (CI + HIL), then add features (position hold, gestures).**

## 2. Six-Month Development Approach

The phases below map onto the program modules:

| Phase | Program period | Plan |
|---|---|---|
| 1 Project Setup | Sprint 0 (7–16 Oct) + Module 1 weeks 1–2 | [[Study Plan v2]] §2–§4 |
| 2 Core Components | Module 1 weeks 2–4 | [[Study Plan v2]] §4–§6 |
| 3 Integration | Module 1 weeks 5–8 | [[Study Plan v2]] §7–§10 |
| 4 Autonomous Behavior | Module 2 (14 Dec – 26 Feb) | [[Roadmap to April 2027]] §4 |
| 5 Hardware Validation | continuous, gated by [[Safety Rules]] | [[Testing Strategy]] |
| 6 Stabilization | Module 3 (1 Mar – 30 Apr) | [[Roadmap to April 2027]] §5 |

## Phase 1 — Project Setup

Establish:

- GitHub repositories
- branch protection
- `main`
- `dev`
- feature/bugfix workflow
- GitHub Actions
- build pipeline
- basic unit tests
- PR template
- Issue templates
- documentation structure

---

## Phase 2 — Core Components

Focus on:

- hardware interfaces
- communication
- sensor interfaces
- basic software architecture
- simulation infrastructure

Begin establishing:

- requirements
- tests
- ADRs
- traceability

---

## Phase 3 — Integration

Integrate:

- sensors
- navigation
- control
- communication
- simulation

Increase integration testing.

`dev` should become the primary integration environment.

---

## Phase 4 — Autonomous Behavior

Implement and validate:

- navigation
- autonomous control
- waypoint behavior
- failure handling
- safety mechanisms

Simulation should be used extensively before hardware testing.

---

## Phase 5 — Hardware Validation

Move progressively through:

```text
Simulation
    ↓
Bench Testing
    ↓
Controlled Hardware Testing
    ↓
Controlled Flight
    ↓
Autonomous Flight
```

Document significant test results.

---

## Phase 6 — Stabilization

Focus on:

- bug fixing
- reliability
- performance
- safety
- integration
- documentation
- final demonstrations

Avoid introducing unnecessary new architecture late in the project.

---

## 3. Definition of Done

A feature is considered complete when the relevant criteria have been satisfied.

Depending on the feature:

- [ ] Requirement identified
- [ ] GitHub Issue created
- [ ] Implementation completed
- [ ] Code reviewed
- [ ] Build succeeds
- [ ] Unit tests added/updated
- [ ] Integration tests added/updated where required
- [ ] Simulation tested where applicable
- [ ] Hardware tested where applicable
- [ ] Flight tested where applicable
- [ ] Documentation updated
- [ ] Relevant ADR created/updated
- [ ] Pull Request approved
- [ ] CI passes
- [ ] Merged into `dev`

Promotion to `main` requires the additional validation appropriate to the release.

## 4. Development Priorities

In order. When time is short, cut from the bottom.

1. **Safety:** pilot takeover, failsafe at every layer ([[ADR-004 ESP Failsafe|ADR-004]], [[ADR-010 MSP Override|ADR-010]]).
2. **CI + HIL:** green pipeline, coverage > 80% (Module 1 deliverable).
3. **End-to-end link:** ground ↔ ESP ↔ FC with measured latency ([[PC_to_ESP]], [[ESP_to_FC]]).
4. **Position hold** in the cage (Module 1 demo).
5. **Gesture control** ([[00 Gesture Pipeline|Gesture Pipeline]]).
6. Stretch goals.

## 5. Project Milestones

| Date | Milestone | Version tag on `main` |
|---|---|---|
| 16 Oct 2026 | Sprint 0 done: docs, decisions, repos, safety induction | – |
| 30 Oct 2026 | Sprint 1 demo: FC telemetry in ROS 2 over Wi-Fi; Betaflight upgraded | – |
| 13 Nov 2026 | Sprint 2 demo: failsafe under fault injection (bench); HIL job green | – |
| 27 Nov 2026 | Sprint 3 demo: position hold in the cage + PX4 SITL | – |
| 11 Dec 2026 | Module 1 delivery | `v0.1.0` |
| 26 Feb 2027 | Module 2 delivery: trained gesture model, detector | `v0.2.0` |
| 30 Apr 2027 | Module 3 delivery: multi-agent scenario, signed OTA | `v0.3.0` |

## 6. What We Are Deliberately Not Doing

See [[Dev_Strategy]] §23. Project-specific additions:

- No autonomous flight on Betaflight 4.5.1 MSP Override ([[ADR-014 Betaflight Version|ADR-014]]).
- No gesture that can arm, disarm or override the pilot.
- No firmware on the FC other than Betaflight (PX4 does not fit; INAV has no target).
- No dependency on ESP8266 SoftwareSerial until the experiment passes ([[ADR-016 Flow Sensor Path|ADR-016]]).
## 7. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Branch** | An independent line of development in version control. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **PR** | Pull Request: a proposed change submitted for review before merging. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
