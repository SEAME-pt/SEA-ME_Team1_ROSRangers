---
title: Software Repository Layout
type: ci-cd
status: active
date: 2026-10-21
project: SEA autonomous drone
tags:
  - ci
  - repository
---

# Software Repository Layout

> [!info] One repository
> Code, tests, CI, simulation, Betaflight configuration **and** this Obsidian vault (`docs/`) live in `SEAME-pt/SEA-ME_Team1_ROSRangers`. Everything that belongs to the drone sits under `drone/`. Rules for branches and PRs are in [[Dev_Strategy]] and [[ADR-017 Branching Model|ADR-017]].

---

## 1. Layout

Each component gets its own folder because the repo mixes C++, firmware and ROS 2 Python. Items marked **(target, #60)** do not exist yet: they are the destination of the bench-tools move tracked in issue #60. It waits until the branches that still change `scripts/` (`feature/transparent-bridge-tools`, `dailys`) are merged.

```text
SEA-ME_Team1_ROSRangers/
├── drone/
│   ├── config/
│   │   └── betaflight/        # `diff all` and `status` baselines per Betaflight version
│   ├── firmware/
│   │   └── esp8266/           # PlatformIO, Arduino core, esp (thin glue only)
│   │       └── backup/        # factory AT firmware backup notes (flash images are kept out of the repo)
│   ├── lib/
│   │   └── protocol/          # portable C++17 library (ADR-007): framing, MAC, seq window,
│   │       ├── include/sd/    #   failsafe timers, MSP encode/decode, ThoneFlow parser
│   │       ├── src/
│   │       ├── library.json   # lets PlatformIO use it
│   │       └── CMakeLists.txt
│   ├── ground/                # ROS 2 Jazzy ground nodes (colcon packages)
│   │   ├── Makefile           # make: Docker (+ native venv/workspace if Jazzy is installed); make unit/test/smoke
│   │   ├── docker-compose.yml # gesture, intent, echo services (no local ROS 2 needed)
│   │   ├── docker/            # one Dockerfile per service, entrypoint.sh, smoke_test.sh
│   │   └── src/
│   │       ├── sd_protocol_py/   # Python twin of lib/protocol
│   │       ├── flight_interface/ # lifecycle node (real + PX4 backends), only info.md so far
│   │       ├── gesture_node/     # webcam + MediaPipe recogniser -> /gesture/raw (see 06 Gestures)
│   │       └── intent_node/      # /gesture/raw -> safety filter -> /gesture/intent
│   ├── simulation/
│   │   └── px4/               # PX4 SITL + Gazebo Dockerfile and README
│   ├── tools/
│   │   └── bench/             # (target, #60) bench tools now in scripts/: esp_at, msp, fc_cli, seame ...
│   └── tests/
│       ├── vectors/           # golden vectors shared by C++ and Python
│       ├── sitl/              # MSP integration tests against Betaflight SITL
│       ├── hil/               # pytest suites run on the self-hosted runner
│       ├── ros/               # gesture_node + intent_node together: contract and pipeline tests
│       └── tools/             # (target, #60) unit tests of the bench tools, now in tests/scripts/
├── docs/                      # Obsidian vault (plan, ADRs, architecture, hardware, process)
├── scripts/                   # repo tooling only: sync-labels.sh
│                              #   (the bench tools there move to drone/tools/bench, #60)
├── .github/
│   ├── workflows/             # ci.yaml, hil.yaml, close-linked-issues.yaml
│   ├── ISSUE_TEMPLATE/        # Task, Bug, Incident, Test session, Spike forms
│   ├── labels.yml             # label set, synced with scripts/sync-labels.sh
│   └── pull_request_template.md
├── .claude/
│   └── agents/                # Claude Code subagents (docs-reviewer)
├── .pre-commit-config.yaml    # lint hooks, run by the CI lint job
├── ruff.toml                  # repo-wide ruff rules (E, W, F, B, I)
└── README.md
```

> [!note] Until #60 lands
> The bench tools sit in `scripts/` and their tests in `tests/scripts/`.

> [!note] Generated, not committed
> `make` in `drone/ground/` creates `gesture-venv/` and `ros2_ws/` (`build/`, `install/`, `log/`) there when ROS 2 Jazzy is installed. Both are in `.gitignore` and `make fclean` removes them ([[01 ROS2 Nodes Installation]] §6).

## 2. Rule for testability

Anything in `drone/firmware/esp8266/src` that contains an `if` probably belongs in `drone/lib/protocol/`, where it gets unit tests and coverage.

## 3. Related pages

- [[CI-CD Overview]] · [[GitHub Actions Starter]] · [[PX4 SITL Setup]] · [[Testing Strategy]]

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Docker** | Tool that runs software in containers: isolated environments built from an image (Dockerfile). |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **ruff** | Python linter and formatter (`ruff` and `ruff-format` hooks). |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
