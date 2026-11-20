---
title: Flight Interface Node (velocity target → drone, drone state → /flight/state)
type: architecture
status: draft
date: 2026-11-16
updated: 2026-11-19
project: SEA autonomous drone
tags:
  - gestures
  - ros2
  - flight-interface
  - safety
  - docker
aliases:
  - Flight Interface Node
  - flight_interface
---

# 📡 Flight_Interface: the ground ↔ drone link

`flight_interface` is the last ground node of the [[00 Gesture Pipeline|Gesture Pipeline]]. It sends setpoints to the drone and publishes the drone's real state on `/flight/state`, which `mission_node` reads ([[04 Mission Node]] §3.2). The same ROS 2 package also holds `fc_telemetry` (§9) and the MSP library `msp.py` (§10).

> [!WARNING] Status: DRAFT
> - Backends: `loopback` (no drone) and `esp` (the gateway firmware). The PX4 backend for the simulator is planned ([[06 Final Connections and Missing Parts|06]] §2.2).
> - Every setpoint is **neutral** until the controller exists ([[06 Final Connections and Missing Parts|06]] §2.1), so no non-zero setpoint passes the gate yet. `override_active` comes from the ESP (#176, PR #198; §2.4 there): true only when the ESP reports the pilot's override on.
> - Its rules are accepted decisions: [[ADR-025 flight_interface Ground Node|ADR-025]].

---

## 📍 1. Where it sits, and why it exists

```mermaid
flowchart LR
  M[mission_node] -->|"/mission/velocity_target"| FI["<b>flight_interface</b><br/>lifecycle node"]
  FI -->|"/flight/state<br/>JSON, 10 Hz"| M
  FI <-->|"SETPOINT 50 Hz / STATE, FC_HEALTH<br/>UDP + TCP 5759"| ESP([ESP gateway])
  ESP -->|"MSP over UART"| FC([Betaflight FC])
  FI -->|"/fc/attitude, /fc/battery"| OUT([logs, rosbag])
```

The gesture nodes know nothing about the radio link. `flight_interface` is the one place that talks to the drone, so every rule about when the ground may command it (link, E-stop, ARM, failsafe) lives in one node, under the ESP's own failsafe and the safety pilot ([[Architecture Document]] §1, authority order).

---

## 📦 2. The package

`drone/ground/src/flight_interface/flight_interface/`, image `rosrangers/flight_interface:jazzy`:

| Piece | What it does |
| :--- | :--- |
| `guard.py` | every rule of §5, no ROS imports |
| `link_node.py` | the lifecycle node: timers, topics, services |
| `backends.py`, `esp_backend.py` | `loopback` and `esp` (sessions over `sd_protocol.ground`) |
| `telemetry.py` | MSP and `STATE` values → `/fc/attitude`, `/fc/battery` (shared with `fc_telemetry`) |
| `fc_telemetry.py`, `msp.py` | §9 and §10 |
| `launch/flight_interface.launch.py` | the node outside Docker, no respawn on purpose |

---

## 📥 3. Input

- `/mission/velocity_target` (`TwistStamped`, body FLU) from `mission_node`. Only a target that arrives after the activation counts; one older than 0.25 s gives zero.
- From the drone: `STATE`, `FC_HEALTH`, events and acks over [[Protocol Spec v0]].
- The PSK, from `~/.config/seame-drone/secrets.json` (never a ROS parameter, never in the image). In Docker, only `psk.json` (the PSK alone) is mounted there ([[ADR-025 flight_interface Ground Node|ADR-025]] E3.2, §7.2).
- Parameters, declared at the top of `link_node.py`: `backend`, `mask` (8 / 11 / 15, default 8), `esp_host`, `esp_port`, `rate_hz`, `state_rate_hz`, the timeouts, `publish_fc`. A bad value fails `configure`.

---

## ⚙️ 4. Processing

The node starts **unconfigured**. The operator runs `configure`, then `activate`. While active, a timer at `rate_hz` (50 Hz) checks the link and the drone state, decides the setpoint through the guard and hands it to the backend. Inactive, it sends nothing. `deactivate` sends one last zero.

---

## 🛡️ 5. Rules

| Rule | In short |
| :--- | :--- |
| Watchdog | no valid drone state for 0.5 s → zero setpoints and `link_ok` false, so `mission_node` goes `IDLE`; no latch |
| Gate | a non-zero setpoint needs `STREAMING`, override on, a known mask and the FC link alive; axes outside the mask are zero |
| ARM | only the operator's `~/arm_control`, in `SESSION_IDLE`, with a fresh link and the FC alive. Never by itself, also not after a reconnect (REQ-SAF-023) |
| RESUME | only the operator's `~/resume`, after the ESP's link failsafe. Never by itself |
| Held requests | ARM and RESUME go out after 1.5 s of neutral setpoints; dropped after 3 s or when their conditions go |
| E-stop | `~/estop` (or an unexpected error): one neutral setpoint, then `BYE`, then nothing. Latched until `~/reset_estop` |
| Sessions | the `esp` backend reconnects by itself, never with ARM |
| No restart | a dead node stays dead; the ESP's failsafe takes over |
| ROS stays on this PC | the real mode's domain from `env/real.env` and `LOCALHOST` discovery, so another laptop on the drone's Wi-Fi can't call its services. A native `ros2 launch` on the host defaults to `ROS_DOMAIN_ID=42` (ADR-025 E1.4) |

Every rule with its reason: [[ADR-025 flight_interface Ground Node|ADR-025]].

---

## 📤 6. Output

- `/flight/state`: JSON, 10 Hz. `mission_node`'s fields plus `active`, `estop`, `arm_pending`, `fc_link_ok`, `armed` and others ([[Architecture Document]] §2).
- `/fc/attitude`, `/fc/battery` from the FC values in `STATE`, only while they are fresh (`publish_fc`). Don't run `fc_telemetry` at the same time: both publish these topics.
- Services (`std_srvs/Trigger`): `~/estop`, `~/reset_estop`, `~/arm_control`, `~/resume`. They answer when the request is accepted; the ESP's answer appears in the log.
- Logs in `logs/flight_interface/latest.log`.

---

## ▶️ 7. Run (Docker)

The service belongs to the **real** mode only: profile `real`, `drone/ground/env/real.env` (its ROS domain and `LOCALHOST`; copy it from `env.example/real.env.example` and fill in the values the safety pilot gives you, [[01 ROS2 Nodes Installation|01]] §4.2) and `ground_bringup`'s `config/real.yaml` (backend `esp`). Without a filled-in `env/real.env`, compose refuses to start. `make` (demo) and `make sim` never start it, and refuse to start while it runs. The demo's `fake_flight_state` and the sim's bridge never run next to it, so `/flight/state` has one publisher. Every command below passes `--env-file env/real.env`, `echo` included (the `ros2` CLI, [[01 ROS2 Nodes Installation|01]] §5). Without it, `echo` is on the demo's domain and never sees the node.

### 7.1 Without the drone: `make real-dry`

From `drone/ground/`. The real mode with `flight_interface` on the loopback backend: no drone, no PSK, nothing leaves the PC. Use it to rehearse the steps of §7.2 at a desk.

```bash
make real-dry
```

It starts gesture, intent, mission and `flight_interface` (unconfigured), prints the operator steps and shows the live table. Run the steps in another terminal: after `configure` and `activate`, `/flight/state` shows `link_ok` true and `esp_state` `SESSION_IDLE`, and `mission_node` stays `IDLE`. Stop with `make real-down`.

### 7.2 With the drone: `make real`

Follow [[Safety Rules]]: props off on the bench, the safety pilot holds the radio.

```bash
make real
```

1. **Checks, and refuses on any failure:** `env/real.env` is filled in, the PSK file (§ below) exists with mode 600 and holds a valid key, and the ESP (`esp_host` in `ground_bringup`'s `config/real.yaml`) answers ping. It never opens a TCP connection to the gateway.
2. **Starts** the chain and `flight_interface` with `real.yaml` (backend `esp`), **unconfigured**. It never configures, activates or ARMs it (REQ-SAF-023).
3. **Prints the operator steps** (again any time: `make real-steps`) and shows the live table. Ctrl+C closes only the table: the stack keeps running.

The operator steps, in another terminal in `drone/ground/`:

```bash
R='docker compose --env-file env/real.env run --rm echo'
L='ros2 lifecycle set --no-daemon --spin-time 5'   # waits for discovery: each run is a fresh container
$R $L /flight_interface configure
$R $L /flight_interface activate
$R ros2 service call /flight_interface/arm_control std_srvs/srv/Trigger   # ARM: by hand only
$R ros2 service call /flight_interface/estop std_srvs/srv/Trigger         # E-stop
```

**Stop:** `make real-down`. It cuts the drone's link on purpose (the ESP's failsafe takes over), so it asks you to type `stop` first, and the safety pilot must be ready. It stops `flight_interface` first (one last zero setpoint, then BYE), then the chain.

The container mounts only the PSK, never the whole `secrets.json` (it also holds the Wi-Fi and OTA passwords, [[ADR-025 flight_interface Ground Node|ADR-025]] E3.2). Create that file once, and again after the PSK changes:

```bash
make psk-file   # ~/.config/seame-drone/secrets.json -> psk.json (PSK only, mode 600)
```

- A missing `psk.json` stops the container from starting. `SEAME_SECRETS_FILE=<path>` mounts another file.
- The container keeps the file it started with. After the PSK changes (`make psk-file` says so), stop the container and start it again; otherwise `configure` fails with no `HELLO_ACK`.
- While `flight_interface` runs, `make`, `make sim`, `make sim-smoke`, `make down` and `make clean` refuse: each would add a second `/flight/state` publisher or stop its chain. Stop the real mode with `make real-down`.

---

## 🧪 8. Test

`make smoke` runs the package tests inside the image and checks that the loopback node's `/flight/state` reaches `mission_node`; CI runs them in `ground-ros2` ([[01 ROS2 Nodes Installation|01]] §7). Test doubles: `test/fake_esp.py` (an ESP on localhost with fault controls) and `test/fake_fc.py`. What each test file checks is in its docstring.

---

## 📈 9. `fc_telemetry`

A second node in the package: polls the FC over MSP, read-only, through the FC USB or the ESP's transparent bridge (UDP 5760), and publishes `/fc/attitude` (20 Hz) and `/fc/battery` (5 Hz). It is the bench and USB path; with the gateway firmware, `flight_interface` publishes the same topics from `STATE` (§6).

It has no compose service, so today it runs only from a sourced Jazzy workspace with the package built:

```bash
ros2 run flight_interface fc_telemetry --ros-args -p transport:=udp   # or transport:=usb
```

Topic contract, signs, exit codes and bench results: [[Architecture Document]] §2. Tests: [[Testing Strategy]] §5.

---

## 🔌 10. `msp.py`

The MSP v1 client. It lives in this package so `fc_telemetry` can import it; the bench tools in `scripts/` (`msp_check.py`, `esp_at.py`) and `drone/tests/hil/bench.py` add its folder to `sys.path` and still run without ROS ([[Repository Layout]]).

---

## 🧭 11. Design decisions

The decisions are in [[ADR-025 flight_interface Ground Node|ADR-025]]. The ones that shape how you run it:

| Decision | Why |
| :--- | :--- |
| Lifecycle node, starts unconfigured | Nothing goes to the drone until the operator says so |
| ARM and RESUME only from the operator | The ground never takes control by itself |
| The container runs the node itself, not the launch file | `ros2 launch` ignores `docker stop`'s SIGTERM, so the last zero setpoint would be lost |
| PSK mounted read-only, never in the environment or the image | The key stays a file only the operator can read |
| One `/flight/state` publisher | `fake_flight_state` (demo), `px4_offboard_bridge` (sim) and `flight_interface` (real) are in different modes and never run together |

---

## 🔖 12. Requirement IDs

| ID | Requirement | Checked in |
|---|---|---|
| REQ-SAF-023 | The ground never sends ARM on its own. | `test_guard*.py`, `test_link_node*.py` |
| REQ-SAF-017 | Zero within 0.25 s of losing the target. | `test_guard.py`, `test_link_node_ros.py` |

---

## ❓ 13. Open points

- [ ] **Controller and PX4 backend** ([[06 Final Connections and Missing Parts|06]] §2.1–2.2).
- [x] **`override_active` from the ESP** ([[06 Final Connections and Missing Parts|06]] §2.4; #176, PR #198).
- [ ] **E-stop time** measured end to end on the bench (ADR-025 C3).
- [ ] **A compose service for `fc_telemetry`**, if the bench needs it outside a native workspace.

---

## 🔗 14. Related pages

[[00 Gesture Pipeline]] · [[01 ROS2 Nodes Installation]] · [[04 Mission Node]] · [[06 Final Connections and Missing Parts]] · [[ADR-025 flight_interface Ground Node|ADR-025]] · [[Architecture Document]] · [[Protocol Spec v0]] · [[Safety Rules]]

---

## Glossary

| Term | Meaning |
| --- | --- |
| **ARM** | Here `ARM_CONTROL_REQUEST`: asks the ESP to start sending the ground's setpoints to the FC. Not the FC's motor arm, which stays with the pilot. |
| **Backend** | The part of the node that talks to one kind of drone: `loopback`, `esp`, later `px4`. |
| **E-stop** | Emergency stop: the node sends neutral and `BYE`, then nothing until reset. |
| **FLU** | Body frame with x forward, y left, z up (REP-103). |
| **Lifecycle node** | A ROS 2 node with managed states (unconfigured, inactive, active), changed with `ros2 lifecycle set`. |
| **Loopback** | A backend with no drone: it reports an idle link, so the node can be tried alone. |
| **MSP** | MultiWii Serial Protocol: how the ground and the ESP talk to the Betaflight FC. |
| **PSK** | Pre-shared key: the secret the ground and the ESP use to authenticate the session. |
| **Setpoint** | One command frame to the drone (sticks or attitude), sent at 50 Hz. |
| **Watchdog** | A timer that zeroes the output when the drone state stops arriving. |
