---
title: Study Plan v2 — Sprint 0 and Module 1
status: active
version: 2.1
project: SEA autonomous drone
covers: Sprint 0 (7–16 Oct 2026) + Module 1 (19 Oct – 11 Dec 2026)
replaces: "[[plano_de_estudo]] (v1, single learner, 8 weeks)"
tags: [plan]
---

# Study Plan v2 — Sprint 0 and Module 1

> [!summary] What changed from v1 ([[plano_de_estudo]])
> - Written for **5 people in parallel tracks** ([[Team Tracks]]) with a shared core, instead of one learner.
> - Aligned to the **Module 1 deliverables**: architecture document, green CI (>80% coverage + HIL), real-time profile, sim-to-real report, E-stop under fault injection, sprint demos every 2 weeks.
> - **Gestures are part of the plan.** The webcam prototype already exists ([[Gesture Recognition Program]]). Drone-camera perception (UVC, ArUco) moves to Module 2.
> - **Betaflight upgrade to 2026.6.2** in week 2, before any MSP Override flight ([[ADR-014 Betaflight Version|ADR-014]]).
> - ESP on **UART4**. ELRS stays on **UART2** ([[ADR-011 FC UART Allocation|ADR-011]]). **Custom ESP firmware** replaces AT 1.7.4.0 ([[ADR-007 Portable Protocol Library and Custom ESP Firmware|ADR-007]]).
> - ThoneFlow: **characterise first**, then a time-boxed SoftwareSerial experiment ([[ADR-016 Flow Sensor Path|ADR-016]]).
> - Git and CI follow [[Dev_Strategy]] (`main` + `dev`).
> - Cut order if late: stretch goals and gesture polish first. **Never cut safety, failsafe or CI.**

---

## 1. Assumptions

| Item | Value |
|---|---|
| Rhythm | 5 days/week, about 8 h/day per person |
| Daily split | 1 h review + goal, 2 h study, 4 h practice, 1 h documentation |
| Records | English (code, notes, this vault) |
| Hardware | 2 flying units shared by the class, ELRS (BETAFPV Nano) + RadioMaster Pocket, D1 mini, ThoneFlow-3901U (not installed), MS210x USB video grabber |
| Flight | Netted cage only, safety pilot on the radio ([[Safety Rules]]) |

### Tracks (details in [[Team Tracks]])

- **A · Flight & Safety**: Betaflight, ELRS, failsafe, upgrade, flight tests, safety pilot.
- **B · Embedded Link**: ESP8266 firmware, MSP, C++ protocol library.
- **C · Ground & ROS 2**: flight-interface lifecycle node, Python protocol, controller.
- **D · CI/CD & Test**: pipelines, coverage, SITL, HIL runner.
- **E · Simulation, Estimation & Gestures**: PX4 SITL + Gazebo, optical flow, MediaPipe.

---

## 2. Sprint 0 — status (7–16 Oct)

### 2.1 Done in week 1 (7–9 Oct) and so far

- [x] Hardware identified from photos and the Betaflight CLI: FC `ODDITYRCF7AIO`, Betaflight **4.5.1**, MSP API **1.46**, VTX VT5804, MS210x grabber ([[2.5Drone]])
- [x] Port functions read: UART2 = Serial RX, UART3 = Tramp VTX, UART1/4/6 unassigned ([[ESP_to_FC]] §2)
- [x] Exposed pads listed: T1, R1, R2, R3, T4, R4, CAM, SCL, SDA, 5V, 3.3V, GND, DV5V
- [x] Development process and Git workflow documented ([[Dev_Strategy]], Git Learning notes)
- [x] Communication drafts written ([[PC_to_ESP]], [[ESP_to_FC]], [[ThoneFlow]])
- [x] Static-gesture webcam prototype working ([[Gesture Recognition Program]])
- [x] Decisions accepted ([[Decision Register]])

### 2.2 To do this week (12–16 Oct)

- [ ] Safety induction done ([[Safety Rules]]); props-off rule agreed by all
- [x] Save `diff all` and `status` output in the software repo under `drone/config/betaflight/4.5.1/`
- [x] Re-check the **barometer**: DPS310 detected (`status` 2026-10-14: `I2C:1`, 0 errors). Sensors tab altitude follows the board when raised/lowered (GitHub #15).
- [x] Confirm the ELRS wires are on R2/T2 with a multimeter (2026-10-16, GitHub #16). T4/R4 continuity also OK; CAM/DV5V identified ([[Wiring and Pinout]] §1)
- [x] Create the **software repo** with `main` + `dev` and branch protection ([[Dev_Strategy]], [[Repository Layout]])
- [ ] Back up the D1 mini **AT firmware** before any flashing (ESP-S1-01 in [[ESP Bench Test Plan]])
- [x] Ask instructors the items in [[Open Questions]]: reflash allowed, no lab HIL runners (build our own), no mandated ROS 2 distro (2026-10-14). ToF/MCU still open.
- [x] XT30 → XT60 adapter received; battery checked (2026-10-16, [[Battery Log]], GitHub #19)

---

## 3. Week 1 · 19–23 Oct · Shared core: drone, radio, safety

**Everyone** (pairs rotate on the hardware): quad anatomy, LiPo handling, the Betaflight tabs (Ports, Receiver, Modes, Motors, Failsafe, CLI), ELRS basics/CRSF, Stage 1/Stage 2 failsafe.

| Track | Practice |
|---|---|
| A | Bind ELRS, ARM + kill switches, props-off motor test, failsafe test with the TX off (props off), first short Angle-mode hover in the cage on **4.5.1** (baseline). |
| B | Read `MSP_API_VERSION`, `MSP_STATUS`, `MSP_ATTITUDE` over the FC's USB in Python (pyserial). **D1 mini alone: Stage 1 of the [[ESP Bench Test Plan]]** (backup, blink, SoftAP, UDP latency, current draw, range, watchdog). |
| C | Install Ubuntu 24.04 + ROS 2 Jazzy, do the CLI/rclpy tutorials. Create the `flight_interface` package skeleton (lifecycle node). |
| D | Software repo skeleton, pre-commit, first workflow green on `dev`: lint + empty C++ test + pytest ([[GitHub Actions Starter]]). |
| E | Install PX4 SITL + Gazebo, run the default quad, fly offboard from the ROS 2 example. |

**Exit criteria:** the drone hovers in Angle mode, and cutting the radio cuts the motors on the bench. `diff all` is in Git. CI is green.

## 4. Week 2 · 26–30 Oct · Upgrade + UART link · *Sprint 1 demo Fri*

| Track | Practice |
|---|---|
| A | Upgrade to **2026.6.2** ([[Betaflight Setup]]). Re-enter the configuration by hand (don't paste the 4.5 diff). Try a cloud build with ALTHOLD if the baro works and it fits. Re-test failsafe, re-fly hover. Save the new `diff all`. |
| B | **Stage 2 of the [[ESP Bench Test Plan]]**: D1 mini ↔ FC with **temporary wires** (no soldering), FC on USB only. Enable MSP 115200. **Transparent UART↔UDP bridge spike** (props off, isolated network only). Measure RTT, max and loss. **Solder only after Stage 2 passes** and the [[ESP_to_FC]] §10 checklist is done ([[Wiring and Pinout]]). |
| C | Python MSP + UDP client. Publish `/fc/attitude` and `/fc/battery` from bridge telemetry. Record a rosbag. |
| D | C++ protocol library skeleton + GoogleTest + gcovr gate (start at 50%, rise to 80%). PlatformIO build job for `d1_mini`. |
| E | Measure Wi-Fi ↔ ELRS coexistence with Track A: Wi-Fi channels 1/6/11, watch ELRS LQ in the OSD. |

**Demo:** FC attitude visible in ROS 2 over Wi-Fi with measured latency. The upgraded drone flies. ESP bench results (Stages 1–2) committed.

## 5. Week 3 · 2–6 Nov · Protocol v0 and the architecture document

- **All:** review and freeze [[Protocol Spec v0]] in a 1-hour design review. Write the architecture document (layer diagram, node graph, message contracts, timing budget). This is a Module 1 deliverable.
- **B:** implement the C++ protocol lib (encode/decode, HMAC, sequence window) with tests. Replace the transparent bridge with the **allow-listed gateway** firmware.
- **C:** Python implementation of the same spec. Shared **golden test vectors** pass in both languages.
- **D:** Betaflight 2026.6.2 SITL integration job in CI (MSP over TCP 5761). Get a dedicated Raspberry Pi for the HIL runner ([[HIL Runner]]).
- **E:** ThoneFlow **sensor-alone characterisation** ([[ThoneFlow]] §9.2): serial framing, frame rate (66 Hz datasheet vs 25 Hz ArduPilot), byte order, checksum. Write the parser.

## 6. Week 4 · 9–13 Nov · Failsafe and sessions · *Sprint 2 demo Fri*

- **B:** [[ESP Link State Machine]]: session over TCP, UDP control, 250 ms link failsafe, explicit resume. Start the time-boxed **ThoneFlow SoftwareSerial experiment** with the threshold agreed beforehand ([[ADR-016 Flow Sensor Path|ADR-016]]).
- **C:** `flight_interface` lifecycle node: activate/deactivate, watchdog, an E-stop transition that cuts setpoints. Unit tests.
- **A + B (props off):** run [[ESP_to_FC]] §11 Phases 1–5. MSP Override with mask **8** (yaw). Readback via `MSP_RC`. Pilot takeover. F1–F9 results recorded.
- **D:** **HIL runner online**: flash the ESP from CI, run the props-off passthrough and failsafe-timing tests.
- **E:** wrap the existing `gestures.py` into the recogniser process + `gesture_node` ([[Gesture Pipeline]]). No drone yet.

**Demo:** fault injection on the bench: kill the ground node, drop Wi-Fi, send garbage. Each must end in the defined safe state.

## 7. Week 5 · 16–20 Nov · Control in simulation and first override flight

- **C + E:** cascade controller (velocity → attitude setpoints) in PX4 SITL behind the same flight interface ([[ADR-019 Sim Backends|ADR-019]]).
- **E:** flow + IMU + baro height velocity estimate on the bench ([[ADR-016 Flow Sensor Path|ADR-016]]). Error measured against a tape on the floor; also record baro noise and drift at known heights.
- **A + B (cage):** first override **flight**, only if FC-15 passed: yaw only, pilot on throttle and roll/pitch. Then roll/pitch/yaw (mask 11).
- **D:** real-time profile: Betaflight `tasks` output plus ESP loop jitter, plotted automatically from logs.

## 8. Week 6 · 23–27 Nov · Position hold · *Sprint 3 demo Fri*

- **All:** velocity setpoints from the ground controller hold position in the cage using the flow estimate. Throttle stays with the pilot (or ALTHOLD). Same commanded motion in PX4 SITL → **sim-to-real measurement**.
- **E:** gestures drive the flight interface **in simulation** (hover, move, land request).

## 9. Week 7 · 30 Nov – 4 Dec · Fault injection and hardening

- E-stop demo rehearsal: kill a node, drop the link, feed bad data (MAC failure, stale seq, out-of-range values) in sim, HIL and the cage.
- Coverage ≥ 80% on the protocol lib and ground packages. clang-tidy and cppcheck clean.
- **Stretch:** gestures → real drone in the cage at low speed limits, pilot on the kill switch.

## 10. Week 8 · 7–11 Dec · Module 1 delivery · *Sprint 4 demo*

- Final architecture document, real-time profile, sim-to-real report, E-stop live demo, green pipeline with HIL job.
- Promote `dev` → `main` with a merge commit and tag **`v0.1.0`** ([[Dev_Strategy]] §20).
- Retrospective. Update [[Roadmap to April 2027]] and this vault.

## 11. Exit criteria for Module 1 (from the program README)

- [ ] Streams velocity/attitude setpoints through the flight interface and **holds position** in PX4 SITL and in the cage.
- [ ] Link loss → hover or controlled landing.
- [ ] Green CI: build, unit tests, static analysis, coverage > 80%, HIL job.
- [ ] Real-time profile plotted. Sim-to-real report. E-stop under fault injection.

See also [[Weekly Rituals]].

## 12. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **Branch** | An independent line of development in version control. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **CRSF** | Crossfire Serial Protocol, a serial protocol used by radio-control equipment. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
