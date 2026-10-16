---
title: Software Repository Layout
type: ci-cd
status: draft
date: 2026-10-12
project: SEA autonomous drone
tags:
  - ci
  - repository
---

# Software Repository Layout

> [!info] Two repositories, as defined in [[Dev_Strategy]] §2–§3
> - **Software repo** (`sea-me-drone`): code, tests, build and CI configuration, scripts, simulation, Betaflight configuration. Layout below.
> - **Documentation repo** (this Obsidian vault): requirements, architecture, ADRs, test plans and reports. It does **not** contain source code.

---

## 1. Layout

The top level follows the [[Dev_Strategy]] §2 example. Each component gets its own folder because the repo mixes C++, firmware and ROS 2 Python.

```text
sea-me-drone/
├── lib/
│   └── protocol/            # portable C++17 library (ADR-007): framing, MAC, seq window,
│       ├── include/sd/      #   failsafe timers, MSP encode/decode, ThoneFlow parser
│       ├── src/
│       ├── library.json     # lets PlatformIO use it
│       └── CMakeLists.txt
├── firmware/
│   └── esp8266/             # PlatformIO, Arduino core, d1_mini (thin glue only)
├── ground/                  # ROS 2 Jazzy workspace (colcon)
│   └── src/
│       ├── sd_protocol_py/  # Python twin of lib/protocol
│       ├── flight_interface/# lifecycle node (real + PX4 backends)
│       ├── estimator/
│       └── gesture_node/    # talks to the MediaPipe recogniser process
├── tools/gesture/           # MediaPipe recogniser in its own venv (from Gesture Recognition Program)
├── simulation/              # PX4/Gazebo worlds, launch files, Betaflight SITL scripts
├── tests/
│   ├── vectors/             # golden vectors shared by C++ and Python
│   ├── sitl/                # MSP integration tests against Betaflight SITL
│   └── hil/                 # pytest suites run on the self-hosted runner
├── config/
│   └── betaflight/          # diff all per firmware version
├── scripts/
├── .github/
│   ├── workflows/ci.yml, hil.yml
│   ├── pull_request_template.md
│   └── CODEOWNERS
└── README.md
```

## 2. Rule for testability

Anything in `firmware/esp8266/src` that contains an `if` probably belongs in `lib/protocol/`, where it gets unit tests and coverage.

## 3. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
