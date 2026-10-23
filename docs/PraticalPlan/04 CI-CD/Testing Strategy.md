---
title: Testing Strategy
type: test-plan
status: draft
date: 2026-10-21
updated: 2026-10-23
project: SEA autonomous drone
aliases:
  - Communication Test Plan
tags:
  - testing
  - ci
---

# Testing Strategy

> [!danger] Hardware work follows [[Safety Rules]]: props off on the bench, the radio and the safety pilot win over code, no LiPo on the HIL bench.

> [!info] Builds on [[Dev_Strategy]] §12–§13 (test levels and pyramid)
> It also covers the test requirements in [[PC_to_ESP]] §9, [[ESP_to_FC]] §11 and [[ThoneFlow]] §9.

---

## 1. Test levels

| Level | What | Where | Example tests |
|---|---|---|---|
| 1 Unit (C++) | framing, MAC, sequence window, failsafe timers, AETR mapping, MSP encode/decode, ThoneFlow parser | GitHub runner, GoogleTest | stale seq rejected; 251 ms without control → failsafe; tag flip → reject; false `0xFE` header resync |
| 1 Unit (Python) | same spec in `sd_protocol_py`, controller, gesture filter | GitHub runner, pytest | |
| 1 Unit + 2 Integration (ROS 2) | `gesture_node`, `intent_node`: unit, node, process and pipeline tests | GitHub runner in `ros:jazzy-ros-base` (job `ground-ros2`, `colcon test` + pytest, coverage gate 90 %) | gesture → intent over real topics |
| 2 Docker images | the ground images build; gesture container → intent container; clean stops | GitHub runner (job `ground-docker`, `drone/ground/docker/smoke_test.sh`) | `INDEX_RIGHT` → `MOVE_RIGHT`; stop gesture → `HOVER_LAND_REQUEST` |
| 2 Cross-language | golden vectors (`drone/tests/vectors/*.json`) | both | C++ encoder and Python decoder agree byte for byte |
| 2 MSP integration | our MSP code ↔ **the real Betaflight parser** | Betaflight 2026.6.2 SITL, TCP 5761 | API handshake, v2 CRC, malformed frames ignored |
| 2 Impaired link | loss, delay, duplication, reordering, corruption between a simulated sender and receiver | GitHub runner | [[PC_to_ESP]] §9.1 |
| 3 Simulation | controller + flight interface | PX4 SITL + Gazebo | square path, link-loss injection |
| 4 HIL | real spare ESP, no FC (FC checks through Betaflight SITL or the flight unit, #61), no battery; Wi-Fi once the Pi has a USB dongle | Pi runner ([[HIL Runner]]) | passthrough, failsafe timing, latency |
| 4 Bench (manual) | ESP alone, then with the FC on temporary wires | lab | [[ESP Bench Test Plan]] Stages 1–2 |
| 4 Bench (manual) | props off, battery on | lab | [[ESP_to_FC]] §11 Phases 1–6 |
| 5 Flight | cage, safety pilot | lab | staged masks ([[ADR-010 MSP Override\|ADR-010]]) |

## 2. Fault-injection list (Module 1 E-stop demo)

Kill `flight_interface` · kill the ground app · Wi-Fi off · ESP reset · bad MAC · replayed packet · out-of-range values · UART unplugged · ELRS TX off. These map onto **F1–F9** in [[ESP_to_FC]] §9.
Each one has an **expected state** and a **measured time-to-safe-state**.

## 3. Metrics to publish (the program wants numbers)

UDP RTT (median/p99/max), loss %, ESP loop jitter, ESP CPU cost of the MAC ([[ADR-002 Message Integrity|ADR-002]]), Betaflight task timings, gesture → setpoint latency, ThoneFlow frame-loss rate (threshold agreed **before** testing, TF-10), flow velocity error, coverage %.

## 4. MSP integration tests against Betaflight SITL

Level 2 above. Code in `drone/tests/sitl/`, CI job `betaflight-sitl` ([[ADR-019 Sim Backends|ADR-019]], [[ADR-014 Betaflight Version|ADR-014]]). Added on 2026-10-23 (issue #85).

| File | What |
|---|---|
| `msp_frames.py` | Minimal MSP v1/v2 encoder and decoder. Moves into `sd_protocol` once it has MSP code. |
| `test_msp_frames.py` | Unit tests for the encoder/decoder. No SITL needed. |
| `test_msp_handshake.py` | `MSP_API_VERSION` over v1 and v2. A v2 frame with a bad CRC, a v1 frame with a bad checksum and line noise get no reply, and SITL still answers the next valid frame. |
| `conftest.py` | Waits for TCP 5761 (UART1) and an MSP answer instead of a fixed `sleep`. |

Run locally (the same steps as the CI job):

```sh
git clone --depth 1 --branch 2026.6.2 https://github.com/betaflight/betaflight.git bf
git -C bf submodule update --init --recursive --depth 1
mkdir -p shim
printf '#!/bin/sh\n[ "$1" = "-dumpversion" ] && echo 13.3.1 && exit 0\nexit 1\n' > shim/arm-none-eabi-gcc
chmod +x shim/arm-none-eabi-gcc
PATH="$PWD/shim:$PATH" make -C bf TARGET=SITL -j"$(nproc)"
(cd bf && ./obj/main/betaflight_SITL.elf > ../sitl.log 2>&1 &)
SITL_REQUIRED=1 pytest drone/tests/sitl -v
pkill -f betaflight_SITL.elf
```

| Variable | Default | Effect |
|---|---|---|
| `SITL_REQUIRED` | unset | `1` turns "SITL not reachable" from a skip into a failure. CI sets it. |
| `SITL_TIMEOUT_S` | 60 s with `SITL_REQUIRED=1`, else 5 s | How long to wait for the port and an MSP answer |
| `SITL_HOST`, `SITL_MSP_PORT` | `127.0.0.1`, `5761` | Where SITL listens |

> [!warning] Gotchas
> - Fetch the submodules before `make -j`. Otherwise the parallel jobs race for `bf/.git/config` ("could not lock config file") and the build fails.
> - Outside an MSP frame Betaflight acts on `#` (enters the CLI) and `R` (default `reboot_character`, reboot to bootloader). Test noise must contain neither.
> - When the job fails, CI uploads `sitl.log` as the `betaflight-sitl-log` artifact.

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **CRC** | Cyclic Redundancy Check: a checksum that detects accidental corruption (not tampering). |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
| **Gazebo** | The 3D physics simulator used with PX4 (Harmonic release). |
| **RTT** | Round-trip time: how long a packet takes to go out and come back. |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **colcon** | The build tool for ROS 2 workspaces; `colcon test` runs each package's tests. |
| **Node** | A single ROS 2 process that does one job and talks to others through topics. |
| **Topic** | A named channel on which ROS 2 nodes publish and subscribe to messages. |
| **Docker** | Tool that runs software in containers: isolated environments built from an image (Dockerfile). |
| **Smoke test** | A quick end-to-end check that the built thing starts and does its basic job. |
| **Submodule** | A Git repository nested inside another one at a fixed commit; Betaflight pulls its config and libraries in this way. |
| **Artifact (CI)** | A file a CI job saves so it can be downloaded after the run, such as a log. |
| **CLI (Betaflight)** | Betaflight's text command line on a serial port, entered with `#`. |
| **E-stop** | Emergency stop: when something fails, the drone is brought to a safe stop, in the air or on the ground. The faults it must cover are in [[Testing Strategy]] §2. |
| **Self-hosted runner** | A machine of ours (the Pi 4 `hil-drone`) that runs GitHub Actions jobs, used for the HIL job. |
