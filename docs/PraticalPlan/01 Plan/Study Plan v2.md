---
title: Study Plan v2 — Sprint 0 and Module 1
type: plan
date: 2026-10-15
updated: 2026-10-26
status: active
version: 2.1
project: SEA autonomous drone
covers: Sprint 0 (7–18 Oct 2026) + Module 1 (19 Oct – 11 Dec 2026)
replaces: "v1 plan (single learner, 8 weeks; archived in Outdated/)"
tags: [plan]
---

# Study Plan v2 — Sprint 0 and Module 1

> [!summary] What changed from v1 (single learner, 8 weeks; archived in `Outdated/`)
> - Written for **5 people in parallel tracks** ([[Team Tracks]]) with a shared core, instead of one learner. *Changed 2026-10-26: the team is now **4 people**; tracks stay the same, B, C and E share two people ([[Team Tracks]] §1).*
> - Aligned to the **Module 1 deliverables**: architecture document, green CI (>80% coverage + HIL), real-time profile, sim-to-real report, E-stop under fault injection, sprint demos every 2 weeks.
> - **Gestures are part of the plan.** The webcam prototype already exists, now the ROS 2 node `gesture_node` ([[02 Gesture Node|Gesture Node]]). Drone-camera perception (UVC, ArUco) moves to Module 2.
> - **Betaflight upgrade to 2026.6.2** in week 2 (may slip to week 3), before any MSP Override flight ([[ADR-014 Betaflight Version|ADR-014]], update 2026-10-23).
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
| Hardware | 2 flying units shared by the class, ELRS (BETAFPV Nano) + RadioMaster Pocket, ESP, ThoneFlow-3901U (not installed), MS210x USB video grabber |
| Flight | Netted cage only, safety pilot on the radio ([[Safety Rules]]) |

### Sprints

| Sprint | Dates | Demo | Weeks of this plan |
|---|---|---|---|
| 0 | 2026-10-07 to 2026-10-18 | n/a | §2 |
| 1 | 2026-10-19 to 2026-11-01 | 2026-10-30 | weeks 1–2 |
| 2 | 2026-11-02 to 2026-11-15 | 2026-11-13 | weeks 3–4 |
| 3 | 2026-11-16 to 2026-11-29 | 2026-11-27 | weeks 5–6 |
| 4 | 2026-11-30 to 2026-12-13 | 2026-12-11 | weeks 7–8 |
| Module 2 | from 2026-12-14 | n/a | [[Roadmap to April 2027]] |

### Tracks (details in [[Team Tracks]])

- **A · Flight & Safety**: Betaflight, ELRS, failsafe, upgrade, flight tests, safety pilot.
- **B · Embedded Link**: ESP8266 firmware, MSP, C++ protocol library.
- **C · Ground & ROS 2**: flight-interface lifecycle node, Python protocol, controller.
- **D · CI/CD & Test**: pipelines, coverage, SITL, HIL runner.
- **E · Simulation, Estimation & Gestures**: PX4 SITL + Gazebo, optical flow, MediaPipe.

---

## 2. Sprint 0 — status (7–18 Oct)

### 2.1 Done in week 1 (7–9 Oct) and so far

- [x] Hardware identified from photos and the Betaflight CLI: FC `ODDITYRCF7AIO`, Betaflight **4.5.1**, MSP API **1.46**, VTX VT5804, MS210x grabber ([[2.5Drone]])
- [x] Port functions read: UART2 = Serial RX, UART3 = Tramp VTX, UART1/4/6 unassigned ([[ESP_to_FC]] §2)
- [x] Exposed pads listed: T1, R1, R2, R3, T4, R4, CAM, SCL, SDA, 5V, 3.3V, GND, DV5V
- [x] Development process and Git workflow documented ([[Dev_Strategy]], Git Learning notes)
- [x] Communication drafts written ([[PC_to_ESP]], [[ESP_to_FC]], [[ThoneFlow]])
- [x] Static-gesture webcam prototype working ([[02 Gesture Node|Gesture Node]])
- [x] Decisions accepted ([[Decision Register]])

### 2.2 To do in Sprint 0, week 2 (2026-10-12 to 2026-10-18)

- [x] Safety induction done ([[Safety Rules]]); props-off rule agreed by all (GitHub #13, closed 2026-10-20)
- [x] Save `diff all` and `status` output in the software repo under `drone/config/betaflight/4.5.1/`
- [x] Re-check the **barometer**: DPS310 detected (`status` 2026-10-14: `I2C:1`, 0 errors). Sensors tab altitude follows the board when raised/lowered (GitHub #15).
- [x] Confirm the ELRS wires are on R2/T2 with a multimeter (2026-10-16, GitHub #16). T4/R4 continuity also OK; CAM/DV5V identified ([[Wiring and Pinout]] §1)
- [x] Create the **software repo** with `main` + `dev` and branch protection ([[Dev_Strategy]], [[Repository Layout]])
- [x] Back up the ESP **AT firmware** before any flashing (ESP-S1-01 in [[ESP Bench Test Plan]], GitHub #17)
- [x] Ask instructors the items in [[Open Questions]]: reflash allowed, no lab HIL runners (build our own), no mandated ROS 2 distro (2026-10-14). ToF/MCU answered on 2026-10-14: neither ([[ADR-016 Flow Sensor Path|ADR-016]]).
- [x] XT30 → XT60 adapter received; battery checked (2026-10-16, [[Battery Log]], GitHub #19)

---

## 3. Week 1 · 19–23 Oct · Shared core: drone, radio, safety

**Everyone** (pairs rotate on the hardware): quad anatomy, LiPo handling, the Betaflight tabs (Ports, Receiver, Modes, Motors, Failsafe, CLI), ELRS basics/CRSF, Stage 1/Stage 2 failsafe.

| Track | Practice |
|---|---|
| A | Bind ELRS, ARM + kill switches, props-off motor test, failsafe test with the TX off (props off), first short Angle-mode hover in the cage on **4.5.1** (baseline). |
| B | Read `MSP_API_VERSION`, `MSP_STATUS`, `MSP_ATTITUDE` over the FC's USB in Python (pyserial). **ESP alone: Stage 1 of the [[ESP Bench Test Plan]]** (backup, blink, SoftAP, UDP latency, current draw, range, watchdog). |
| C | Install Ubuntu 24.04 + ROS 2 Jazzy, do the CLI/rclpy tutorials. Create the `flight_interface` package skeleton (lifecycle node). |
| D | Software repo skeleton, pre-commit, first workflow green on `dev`: lint + empty C++ test + pytest ([[GitHub Actions Starter]]). |
| E | Install PX4 SITL + Gazebo, run the default quad, fly offboard from the ROS 2 example. |

**Exit criteria:** the drone hovers in Angle mode, and cutting the radio cuts the motors on the bench. `diff all` is in Git. CI is green.

> [!info] Status 2026-10-23
> - Done: ELRS bound (ELRS 4.1.0 CE_LBT, [[ADR-023 ELRS Firmware Version and Regulatory Domain|ADR-023]]), ARM and kill switch on SA, props-off motor test, and the **failsafe with the TX off, with a video** (2026-10-22, GitHub #22). `diff all` is in Git. CI is green.
> - Moves to week 2: the first 4.5.1 hover in the cage (GitHub #23).

## 4. Week 2 · 26–30 Oct · Upgrade + UART link · *Sprint 1 demo Fri*

| Track | Practice |
|---|---|
| A | **Failsafe first, on 4.5.1:** TX-off test with a video (#22, #41; *done 2026-10-22*), then the 4.5.1 cage hover (#23). Only after both pass: upgrade to **2026.6.2** ([[Betaflight Setup]]), re-enter the configuration by hand (don't paste the 4.5 diff), try a cloud build with ALTHOLD if the baro works and it fits, re-test failsafe, re-fly, save the new `diff all`. If time runs out, the upgrade moves to week 3. MSP Override stays bench-only until then ([[Safety Rules]] #14). |
| B | *Changed 2026-10-20: the ESP is already soldered (incident #69), and the soldered link loses 0.7–3.7 % against 0 % before (#30). 2026-10-23: these figures came from runs with hardware problems and are being measured again (#30).* **Find the cause of the loss, cheapest check first** (10 min each, near): FC on USB with the radio off → radio on and bound → ESP antenna turned away from the frame. Finish S2-02, S2-06 and S1-08 (ESP current) on the soldered link. Post the [[ESP_to_FC]] §10 joint evidence (#69). Agree the pass threshold (OD-22, #63). Props off ([[Safety Rules]] #13). |
| C | Python MSP + UDP client. Publish `/fc/attitude` and `/fc/battery` from bridge telemetry. Record a rosbag. Decide the telemetry set (FC-12, #63) by 2026-10-23. Develop against the FC USB link or a recorded log, so C does not wait for B's fix. |
| D | C++ protocol library skeleton + GoogleTest + gcovr gate at **80%** (matches `ci.yaml`; target 100%). PlatformIO build job for `esp`. **Bring the Betaflight SITL job forward from week 3**: with no spare FC it partly replaces HIL. Run the HIL/SITL spike (#61) and ask the instructors what counts as HIL. |
| E | Measure Wi-Fi ↔ ELRS coexistence **with Track B, as part of the #30 runs**: Wi-Fi channels 1/6/11, log ELRS LQ from the OSD. Keep the PX4 SITL work going in parallel. |
| All | **One drone for A, B and D.** Book it with the daily drone schedule in [[Weekly Rituals]]. |

**Demo (changed 2026-10-20):** failsafe video and the 4.5.1 hover. FC attitude in ROS 2, over Wi-Fi or USB, with measured latency. Results of the link-loss investigation (#30) and the ESP bench results committed. The demo no longer depends on the upgrade or on the link fix.

## 5. Week 3 · 2–6 Nov · Protocol v0 and the architecture document

- **All:** review and freeze [[Protocol Spec v0]] in a 1-hour design review. Write the architecture document (layer diagram, node graph, message contracts, timing budget). This is a Module 1 deliverable.
- **B:** implement the C++ protocol lib (encode/decode, HMAC, sequence window) with tests. Replace the transparent bridge with the **allow-listed gateway** firmware.
- **C:** Python implementation of the same spec. Shared **golden test vectors** pass in both languages.
- **D:** extend the Betaflight SITL tests (the job itself is done, week 2). Extend the registered Pi 4 HIL runner ([[HIL Runner]] §3.1, #48): a USB Wi-Fi dongle for the Wi-Fi tests, since the Pi has only its built-in Wi-Fi. *Changed 2026-10-26: no dongle. The Pi time-slices its Wi-Fi ([[ADR-020 HIL Runner|ADR-020]], #48).*
- **E:** ThoneFlow **sensor-alone characterisation** ([[ThoneFlow]] §9.2): serial framing, frame rate (66 Hz datasheet vs 25 Hz ArduPilot), byte order, checksum. Write the parser.

## 6. Week 4 · 9–13 Nov · Failsafe and sessions · *Sprint 2 demo Fri*

- **B:** [[ESP Link State Machine]]: session over TCP, UDP control, 250 ms link failsafe, explicit resume. Start the time-boxed **ThoneFlow SoftwareSerial experiment** with the threshold agreed beforehand ([[ADR-016 Flow Sensor Path|ADR-016]]).
- **C:** `flight_interface` lifecycle node: activate/deactivate, watchdog, an E-stop transition that cuts setpoints. Unit tests.
- **A + B (props off):** run [[ESP_to_FC]] §11 Phases 1–5. MSP Override with mask **8** (yaw). Readback via `MSP_RC`. Pilot takeover. F1–F9 results recorded.
- **D:** **HIL job on the runner** (runner registered 2026-10-22, first smoke test in `drone/tests/hil`): flash the ESP from CI, run the props-off passthrough and failsafe-timing tests.
- **E:** wrap the existing `gestures.py` into the recogniser process + `intent_node` ([[00 Gesture Pipeline|Gesture Pipeline]]). No drone yet. *Done 2026-10-21: `gesture_node` and `intent_node` are in `dev` with tests and Docker images (#78); the first webcam run is still to be recorded.*

**Demo:** fault injection on the bench: kill the ground node, drop Wi-Fi, send garbage. Each must end in the defined safe state.

## 7. Week 5 · 16–20 Nov · Control in simulation and first override flight

- **C + E:** cascade controller (velocity → attitude setpoints) in PX4 SITL behind the same flight interface ([[ADR-019 Sim Backends|ADR-019]]).
- **E:** flow + IMU + baro height velocity estimate on the bench ([[ADR-016 Flow Sensor Path|ADR-016]]). Error measured against a tape on the floor; also record baro noise and drift at known heights.
- **A + B (cage):** first override **flight**, only if FC-15 passed and the upgrade to 2026.6.2 is done, with the failsafe re-tested and a new `diff all` committed ([[ADR-014 Betaflight Version|ADR-014]], [[Safety Rules]] #14): yaw only, pilot on throttle and roll/pitch. Then roll/pitch/yaw (mask 11).
- **D:** real-time profile: Betaflight `tasks` output plus ESP loop jitter, plotted automatically from logs.

## 8. Week 6 · 23–27 Nov · Position hold · *Sprint 3 demo Fri*

- **All:** velocity setpoints from the ground controller hold position in the cage using the flow estimate. Throttle stays with the pilot (or ALTHOLD). Same commanded motion in PX4 SITL → **sim-to-real measurement**.
- **E:** gestures drive the flight interface **in simulation** (hover, move, land request).

## 9. Week 7 · 30 Nov – 4 Dec · Fault injection and hardening

- E-stop demo rehearsal: kill a node, drop the link, feed bad data (MAC failure, stale seq, out-of-range values) in sim, HIL and the cage.
- Coverage ≥ 80 % on the protocol lib and `sd_protocol_py`, ≥ 90 % on the ROS 2 nodes ([[ADR-022 CI Gates and Lint Rules|ADR-022]], REQ-SW-003). clang-tidy and cppcheck clean.
- **Stretch:** gestures → real drone in the cage at low speed limits, pilot on the kill switch.

## 10. Week 8 · 7–11 Dec · Module 1 delivery · *Sprint 4 demo*

- Final architecture document, real-time profile, sim-to-real report, E-stop live demo, green pipeline with HIL job.
- Promote `dev` → `main` with a merge commit and tag **`v0.1.0`** ([[Dev_Strategy]] §20).
- Retrospective. Update [[Roadmap to April 2027]] and this vault.

## 11. Exit criteria for Module 1 (from the program README)

- [ ] Streams velocity/attitude setpoints through the flight interface and **holds position** in PX4 SITL and in the cage (REQ-NAV-001: 0.25 m for 60 s in SITL; REQ-NAV-002: 0.5 m radius for 30 s in the cage; REQ-CTL-001 limits the gesture speeds).
- [ ] Link loss → the drone levels with neutral sticks (it can drift; no position hold) and the pilot takes over with the radio ([[ADR-004 ESP Failsafe|ADR-004]], 2026-10-20 and 2026-10-23; REQ-SAF-009, REQ-SAF-013, REQ-SAF-014, REQ-SAF-017). Betaflight's own RX failsafe (DROP on radio loss) is a separate failsafe.
- [ ] Green CI: build, unit tests, static analysis, coverage > 80 % (90 % on the ROS 2 nodes), HIL job (REQ-SW-003, REQ-SW-004).
- [ ] Real-time profile plotted (REQ-SW-005). Sim-to-real report (REQ-SYS-002). E-stop under fault injection (REQ-SAF-002, REQ-SAF-005, REQ-SAF-021).

### 11.1 Module 1 deliverables

Tracks B, C and E rotate between people ([[Team Tracks]] §1), so each deliverable is owned by a track, not a name.

| Deliverable | Track | Due | Requirement |
|---|---|---|---|
| Architecture document (layer diagram, node graph, message contracts, timing budget) | all | draft 2026-11-06 (week 3), final 2026-12-11 | REQ-SYS-001 |
| Green CI with coverage gates | D | 2026-12-11 | REQ-SW-003 |
| HIL job on the self-hosted runner | D | first run 2026-11-13 (week 4) | REQ-SW-004 |
| Real-time profile (Betaflight `tasks` + ESP loop jitter, plotted from logs) | D | 2026-11-20 (week 5) | REQ-SW-005 |
| Sim-to-real report | E | 2026-12-11 | REQ-SYS-002 |
| E-stop under fault injection (sim, HIL, cage) | A + C | rehearsal 2026-12-04, live 2026-12-11 | REQ-SAF-021 |
| Kill switch and pilot takeover | A | bench done 2026-10-22; cage before the first override flight | REQ-SAF-022, REQ-SAF-002 |

Requirement IDs are listed in [[Requirements]]. See also [[Weekly Rituals]].

## 12. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **Branch** | An independent line of development in version control. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **CRSF** | Crossfire serial protocol: the serial wire format between the ELRS receiver and the FC (UART2), and between the radio and its ELRS module. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **HMAC** | Hash-based Message Authentication Code: a keyed tag that proves a message came from someone holding the key (ADR-002 uses a truncated HMAC-SHA256). |
| **LiPo** | Lithium-polymer battery: the 4S flight pack. Handle it by the rules in Safety Rules. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
| **Gazebo** | The 3D physics simulator used with PX4 (Harmonic release). |
| **CLI** | Command-line interface: here the Betaflight CLI in the Configurator, used for `diff all` and `status`. |
| **RTT** | Round-trip time: how long a packet takes to go out and come back. |
| **PlatformIO** | The build system used for the ESP8266 firmware (envs `esp`, `esp_ota`). |
| **XT60** | The battery connector on the pack. An XT30 to XT60 adapter is used on the drone. |
| **IMU** | Inertial Measurement Unit: the gyro and accelerometer on the FC. |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
| **AT** | The Hayes-style AT command set of the stock ESP8266 firmware (1.7.4.0), replaced by custom firmware (ADR-007). |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
| **ALTHOLD** | Betaflight altitude-hold mode that uses the barometer. Only in 2026.6.x builds, and not default on 512 KB F7 targets (ADR-014). |
| **Angle mode** | Betaflight mode where the sticks command a tilt angle; centred sticks level the drone. It does not hold position: the drone can drift. |
| **Betaflight** | The open-source FC firmware on the drone: 4.5.1 installed, 2026.6.2 planned (ADR-014). |
| **diff all** | Betaflight CLI command that prints every setting changed from default. Baselines are in `drone/config/betaflight/`. |
| **ESP** | The ESP-12F module (ESP8266) on the drone: the Wi-Fi bridge between the ground PC and the FC, wired to FC UART4. |
| **MSP Override** | Betaflight feature that lets MSP supply the values of selected RC channels, while ELRS stays the primary receiver (ADR-010). |
| **Mask** | `msp_override_channels_mask`: which stick channels the ground controls (8 = yaw, 11 = roll+pitch+yaw, 15 = all four). |
| **Props off** | Propellers physically removed. Required for every bench test ([[Safety Rules]] #1). |
| **Safety pilot** | The trained person at the cage with the RadioMaster and line of sight. The radio always wins over code. |
| **SoftAP** | Software access point: the ESP creates its own Wi-Fi network that the ground PC joins (ADR-006). |
| **Sprint** | A 2-week iteration of the board. Sprint 0 = 2026-10-07 to 2026-10-18; Sprint 1 = 2026-10-19 to 2026-11-01. |
| **Track (A–E)** | The five ownership areas: A flight-safety, B embedded-link, C ground-ros2, D ci-test, E sim-gestures ([[Team Tracks]]). |
| **E-stop** | Emergency stop: when something fails, the drone is brought to a safe stop, in the air or on the ground. The faults it must cover are in [[Testing Strategy]] §2. |
| **Kill switch** | SA on the RadioMaster, which is also the ARM switch: releasing it disarms and stops the motors ([[Radio Link Tests]] RL-12). |
| **Takeover switch** | The MSP Override switch on the radio: turning it off ends ground control, and from then on the drone takes commands only from the radio (ADR-010). Its channel is not mapped yet. |
