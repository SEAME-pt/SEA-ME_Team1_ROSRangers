---
title: Software Repository Layout
type: ci-cd
status: active
date: 2026-10-21
updated: 2026-11-24
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

Each component gets its own folder because the repo mixes C++, firmware and ROS 2 Python. The bench tools moved from `scripts/` to `drone/tools/bench/` and their tests from `tests/scripts/` to `drone/tests/tools/` on 2026-11-20 (#60).

```text
SEA-ME_Team1_ROSRangers/
├── drone/
│   ├── config/
│   │   └── betaflight/        # `diff all` and `status` baselines per Betaflight version
│   ├── firmware/
│   │   └── esp8266/           # PlatformIO, Arduino core, esp (thin glue only)
│   │       └── backup/        # factory AT firmware backup notes (flash images are kept out of the repo)
│   ├── lib/
│   │   └── protocol/          # portable C++17 library (ADR-007): framing, HMAC, seq window, receive rules,
│   │       ├── include/sd/    #   message codecs, MSP v1 codec + FC link scheduler (done); failsafe timers, ThoneFlow parser (planned)
│   │       ├── src/
│   │       ├── tests/         # GoogleTest unit tests (run by the protocol-cpp CI job)
│   │       ├── library.json   # lets PlatformIO use it
│   │       └── CMakeLists.txt
│   ├── ground/                # ROS 2 Jazzy ground nodes (colcon packages)
│   │   ├── Makefile           # make: Docker (+ native venv/workspace if Jazzy is installed); make real / real-dry;
│   │   │                      #   make test (every suite) / unit / test-sitl / smoke;
│   │   │                      #   make sim / sim-smoke / sim-hold / sim-down: the chain against PX4 SITL (PX4 SITL Setup §7)
│   │   ├── docker-compose.yml # one profile per mode: demo (gesture, intent, mission, echo, fake_flight_state),
│   │   │                      #   real (... + controller, flight_interface), sim (... + controller, flight_interface_sim, px4; SIM ONLY),
│   │   │                      #   sim-smoke and sim-hold (the sim tests)
│   │   ├── env.example/       # demo|real|sim.env.example: each mode's env file, values empty (copy to env/, ignored by Git,
│   │   │                      #   and fill in the safety pilot's values); .env.example likewise for .env
│   │   ├── docker/            # what goes into the images: base/Dockerfile (the base every ground image builds FROM:
│   │   │                      #   Jazzy, non-root user ros with the host's uid), entrypoint.sh (node logs)
│   │   ├── tools/             # host helpers: smoke_test.sh (make smoke, CI ground-docker), pkg_dirs.sh (finds each
│   │   │                      #   ROS package's folder for the Makefile and CI ground-ros2), run_tests.sh (make test's
│   │   │                      #   suites), env_check.sh / real_check.sh (checks before a mode starts)
│   │   ├── build/             # make test's C++ build and Betaflight SITL checkout (ignored by Git)
│   │   ├── logs/              # node and test logs (ignored by Git, only .gitignore committed)
│   │   └── src/               # one folder per component: its ROS 2 package and its Dockerfile
│   │       ├── gesture/          # gesture_node: webcam + MediaPipe recogniser -> /gesture/raw (see 06 Gestures)
│   │       ├── intent/           # intent_node: /gesture/raw -> safety filter -> /gesture/intent
│   │       ├── mission/          # mission_node: /gesture/intent + /flight/state -> /mission/velocity_target (+ pretend inputs)
│   │       ├── controller/       # controller_node: velocity target + /estimate/velocity -> /control/attitude_setpoint
│   │       ├── flight_interface/ # flight_interface: the lifecycle node ground <-> drone (ADR-025), fc_telemetry,
│   │       │                     #   msp.py (MSP client, shared with the bench tools); backends loopback, esp,
│   │       │                     #   px4 (sim only: Dockerfile stage sim adds px4_msgs)
│   │       ├── echo/             # monitor.py: the live table of docker compose up (not a ROS package)
│   │       ├── bringup/          # ground_bringup: the modes, no code. launch/demo|real|sim.launch.py (the mode
│   │       │                     #   without Docker), config/demo|real|sim.yaml (its parameters; the containers
│   │       │                     #   mount config/ and pass --params-file /config/<mode>.yaml)
│   │       ├── simulation/       # SIM ONLY: PX4 SITL + Gazebo Dockerfile (stage px4 + ground nodes), README,
│   │       │   │                 #   sim_start.sh (make sim: configures flight_interface, arms, takes off)
│   │       │   └── sim_smoke/    # the scenarios of make sim-smoke and make sim-hold, and their checks
│   │       │                     # (smoke_checks.py, hold_checks.py: no ROS, CI job sim-smoke-python).
│   │       │                     # Not in ground-ros2 (needs px4_msgs): a plain colcon build over
│   │       │                     # drone/ground/src fails on it; select packages by name
│   │       │                     # (--packages-select), as CI and make do
│   │       └── sd_protocol_py/   # Python twin of lib/protocol
│   ├── tools/
│   │   └── bench/             # bench tools (no ROS): seame, esp_at, msp_check, fc_cli, ground_link ...
│   └── tests/
│       ├── vectors/           # golden vectors (generate.py + *.json) shared by C++ and Python
│       ├── sitl/              # MSP integration tests against Betaflight SITL
│       ├── hil/               # pytest suites run on the self-hosted runner
│       ├── ros/               # gesture_node, intent_node, mission_node together: contract, pipeline, launch tests; echo table, entrypoint
│       └── tools/             # unit tests of the bench tools (CI job scripts-python)
├── docs/                      # Obsidian vault (plan, ADRs, architecture, hardware, process)
├── scripts/                   # repo tooling: sync-labels.sh
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

> [!note] Bench tools and the MSP library
> The bench tools sit in `drone/tools/bench/` and their tests in `drone/tests/tools/`. Their MSP library, `msp.py`, moved into the `flight_interface` package with #96 (`drone/ground/src/flight_interface/flight_interface/flight_interface/msp.py`), so the ROS 2 node can import it. `msp_check.py` and `esp_at.py` add that folder to `sys.path` and still run without ROS. The path is computed from the repo root (`parents[3]`), so it keeps working from the new folder (#60).

> [!note] Generated, not committed
> `make` in `drone/ground/` creates `gesture-venv/` and `ros2_ws/` (`build/`, `install/`, `log/`) there when ROS 2 Jazzy is installed. Both are in `.gitignore` and `make fclean` removes them ([[01 ROS2 Nodes Installation]] §4).

## 2. Rule for testability

Anything in `drone/firmware/esp8266/src` that contains an `if` probably belongs in `drone/lib/protocol/`, where it gets unit tests and coverage.

## 3. Related pages

- [[CI-CD Overview]] · [[GitHub Actions Starter]] · [[PX4 SITL Setup]] · [[Testing Strategy]]

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Betaflight** | Open-source flight-controller firmware on the drone's FC. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Docker** | Tool that runs software in containers: isolated environments built from an image (Dockerfile). |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F). It bridges the ground PC to the FC over UART4. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **GoogleTest** | C++ unit-test framework used for `drone/lib/protocol` on the PC and in CI. |
| **HMAC** | Hash-based Message Authentication Code: a keyed tag that proves a message came from someone holding the key (ADR-002 uses a truncated HMAC-SHA256). |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **PlatformIO** | The build system used for the ESP8266 firmware (envs `esp`, `esp_ota`). |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **ruff** | Python linter and formatter (`ruff` and `ruff-format` hooks). |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **colcon** | The build tool for ROS 2 workspaces; `colcon test` runs each package's tests. |
| **Gazebo** | The 3D physics simulator used with PX4 (Harmonic release). |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
| **px4_msgs** | The ROS 2 message package of PX4's topics; only the simulation image of `flight_interface` has it. |
