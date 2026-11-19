---
title: ADR-025 — flight_interface Ground Node
type: adr
status: accepted
date: 2026-11-16
project: SEA autonomous drone
tags:
  - adr
  - ros2
  - ground
  - safety
---

# ADR-025 — flight_interface Ground Node

> [!success] Status: ACCEPTED (#103, PR #187)
> A1–A6, K1–K6 and B1–B3 were decided by the safety pilot (BrunoBMFT) on 2026-11-09 and 2026-11-10. C1–C3, D1–D4 and E1–E6 were put to the team on draft PR #187 and decided there on 2026-11-12. The rule for that round (BrunoBMFT): a colleague's answer on PR #187 is used, and where nobody answered, the proposal is used. K6 was changed by the safety pilot on PR #191 (2026-11-13). E6.2 and E1.2 were added on 2026-11-16 (PR #187). E3.1 was added on 2026-11-16 (#186). E3.2 was added on 2026-11-18 (#186, PR B, from ThiagoTEP's review of PR #211). E1.3 was added on 2026-11-18 (#186, PR B). A7 was decided by the safety pilot on the PR #187 review (2026-11-16).
> This ADR replaces the `DECISIONS.md` file that PR #187 carried at the repository root.

## 1. Context

- `flight_interface` (#103) is the ROS 2 lifecycle node between `mission_node` and the drone: `/mission/velocity_target` in, `SETPOINT` to the ESP gateway (#102) out, drone state back on `/flight/state`. Code: `drone/ground/src/flight_interface/flight_interface/` (`guard.py` safety logic without ROS, `link_node.py` the node, `backends.py` / `esp_backend.py` the drone side, session code in `sd_protocol.ground`).
- It is the innermost ground layer of the safety chain ([[ADR-004 ESP Failsafe|ADR-004]] §3): the radio and the safety pilot, Betaflight's failsafe and the ESP's link failsafe all act without it.
- Fixed rules every decision respects:
  - The node never sends ARM or RESUME on its own (REQ-SAF-023). ARM only hands the masked stick channels to the ground; it never arms the motors (only the pilot's switch does).
  - The guard repeats `mission_node`'s limits: a stale target becomes zero within 0.25 s (REQ-SAF-017); no drone `STATE` for 0.5 s means the link is lost (REQ-SAF-012); axes outside the mask are zero (REQ-SAF-018); horizontal speed ≤ 0.5 m/s as a vector, vertical ≤ 0.5 m/s, yaw rate ≤ 45 °/s.
  - The gateway accepts only mask 8 today, and `override_active` is 0 until #176, so no non-zero setpoint passes the gate yet.
  - `px4_offboard_bridge` (simulation) and `flight_interface` both publish `/flight/state`: never run them together.

## 2. Decision

### 2.1 Safety behaviour (safety pilot, 2026-11-09 / 2026-11-10)

| ID | Decision | Why |
|---|---|---|
| A1 | E-stop (`~/estop`): latch first, then one neutral setpoint (only if setpoints were going out), then `BYE`, then nothing until `~/reset_estop`. Afterwards control needs a new session, an operator ARM and a new target. A failed neutral never skips `BYE`. Without TCP `BYE` cannot go out: the ESP then fails over after `T_loss`, and the service answer says which happened (PR #187 review, 2026-11-16). | `BYE` starts the ESP's failsafe output at once instead of after `T_loss`. |
| K4 | E-stop while inactive: `BYE` only. | No setpoint is ever sent while inactive. |
| A2 | Link loss: zero setpoints and `link_ok` false, no latch. | The ESP's link failsafe plus the operator's RESUME already gate the way back. |
| A3 | RESUME: operator-only `~/resume`, accepted only in `LINK_FAILSAFE`. | Control never returns without someone deciding. |
| A4 | ARM: `std_srvs/Trigger` `~/arm_control`, mask from the `mask` parameter (8 / 11 / 15, checked at configure). Accepted only when the node is active, not in E-stop, the link is fresh, the FC link is alive and the ESP reports `SESSION_IDLE`. | No new interface package; a topic could be replayed into an accidental ARM. |
| K6 | ARM and RESUME are **held** until 1.5 s of neutral setpoints went out with no gap of 0.1 s, then sent once; a request still pending after 3 s, or whose conditions go (state, link, FC, E-stop, a new session), is dropped. Changed by the safety pilot on PR #191 (2026-11-13); before: RESUME only, 0.6 s / 2 s. Only setpoints that really went out count, and the request goes out only in the session it was asked in, once the ESP has confirmed that session (E4); the node runs at 20 Hz at least, so the 0.1 s gap can hold (PR #187 review, 2026-11-16). | Since #188 the ESP refuses both until the FC has acknowledged RC for 1 s, and after a failsafe RC only flows while the ground streams ([[ADR-004 ESP Failsafe|ADR-004]] §2.6). 0.6 s was refused with result 4 on the bench. |
| A5 | The ESP backend opens a new session by itself (at most one attempt every 2 s), never with ARM. | Otherwise the ground is blind after every Wi-Fi drop. |
| K2 | Each new session is confirmed at once with a read-only `CONFIG_GET 0x01`. | A new session that arrives while another is current stays pending and is dropped after 2 s without a frame (#156). |
| K5 | "No `STATE` for 3 s" means a lost session only while setpoints are going out. | The ESP sends `STATE` only to the sender of the last setpoint. |
| K3 | `cleanup` and `shutdown` send `BYE`. | Starts the ESP's failsafe at once. |
| K1 | FC link lost: zero setpoints and `link_ok` false; no E-stop. | `mission_node` goes IDLE; a short FC hiccup should not cost a reset and an ARM. |
| A6 | A process restart clears the E-stop latch; the launch file has no respawn (a test checks it). | Saving the latch adds code and failure modes to the safety path. |
| A7 | An unexpected error in the node's tick latches the E-stop with reason `internal error: …`: `BYE` at once, no neutral (the state it would come from may be what broke), and the node stays alive. Cleared like any E-stop, with `~/reset_estop`. Decided on the PR #187 review (2026-11-16). | `BYE` starts the ESP's failsafe output at once; a crashed node leaves the ESP waiting for `T_loss`, and a node that went on after an unknown error might keep sending from a broken state. |
| B1 | `mask` on `/flight/state` = the mask this session sent and the ESP confirmed with `ARM_CONTROL_ACK` OK; reset every session; a refused ARM does not change it. | No wire change; the ground already has the data. |
| B2 | One ground session implementation: `sd_protocol.ground`. | Two copies had already drifted. |
| B3 | The PX4 backend gets its own issue, together with the controller. | #103 would otherwise wait on C1. |

### 2.2 Decided on PR #187 (2026-11-12)

| ID | Decision | Decided by |
|---|---|---|
| C1.1 | The controller's output: JSON on `std_msgs/String`, topic `/control/attitude_setpoint`, fields `roll`, `pitch` (deg), `yaw_rate` (°/s), `throttle` (0–1), `active`; body frame FLU. | ThiagoTEP, BrunoBMFT |
| C1.2 | Track E builds the controller, yaw only (mask 8) first, tested in SITL. Roll / pitch only after a velocity estimate (flow + height, [[ADR-016 Flow Sensor Path\|ADR-016]]). | ThiagoTEP, BrunoBMFT |
| C1.3 | Yaw-only pass-through for mask 8 until the full controller exists. The yaw rate → stick mapping uses the FC's rate curve from the Betaflight baseline (`drone/config/betaflight`), with a unit test that 45 °/s stays inside the stick limit. First used on the props-off bench (#104). | ThiagoTEP, BrunoBMFT |
| C1.4 | The guard checks limits in attitude units: yaw rate ≤ 45 °/s; roll / pitch from Betaflight `angle_limit` plus a ground cap (value chosen when roll / pitch starts); throttle only under mask 15. `mission_node` keeps its velocity ceilings as a second, independent limit. | ThiagoTEP, BrunoBMFT |
| C1.5 | The velocity input moves from `flight_interface` to the controller. `px4_offboard_bridge` stays velocity-based and simulation-only until the PX4 backend replaces it. | ThiagoTEP, BrunoBMFT |
| C1.6 | The controller comes after #109, which needs only neutral sticks. | ThiagoTEP, BrunoBMFT |
| C2 | `SETPOINT` mode 0 (neutral) when there is no command; mode 1 when the controller commands, including an active zero. Until there is a velocity estimate, "no command" on x / y is neutral. Until the controller exists every setpoint is mode 0. | ThiagoTEP, BrunoBMFT |
| C3 | The E-stop time (#109) is measured ground side and end to end; end to end is the pass / fail number: from the injected fault to the first neutral `MSP_SET_RAW_RC` on the FC UART, both stamped on the HIL Pi's clock. | ptorrao-git, Dot99, ThiagoTEP, BrunoBMFT |
| D1 | `/flight/state` gets `armed` (true / false / null, from `FC_HEALTH`); `airborne` = armed for now. | ThiagoTEP, BrunoBMFT |
| D2 | The ESP backend publishes `/fc/attitude` and `/fc/battery` from `STATE` with the `fc_telemetry` conversion; the MSP client stays the USB / bench path. The yaw is zeroed at each activate. | Dot99, ThiagoTEP, BrunoBMFT |
| D2.1 | `STATE` carries no age for its FC values (0 before the FC's first reply, frozen up to 10 s after it goes silent), so each topic is published only while `FC_HEALTH` says its value is fresh: attitude under 250 ms, battery under 1 s. | Claude under BrunoBMFT's rule; ages since 2026-11-16 |
| D3 | #103's Simulation box moves to the PX4 backend issue. | Dot99, BrunoBMFT |
| D4 | Track B moves `scripts/ground_link.py` onto `sd_protocol.ground` once PR #187 merges (it brings that module). The bench-only keys stay in the tool. | ptorrao-git, Dot99 |
| E1 | ROS stays on this PC: `ROS_DOMAIN_ID = 42` (outside the test range 81–98) and `ROS_AUTOMATIC_DISCOVERY_RANGE=LOCALHOST` as defaults in the launch file and `docker-compose.yml`, kept when the caller sets them. SROS2 only if the ground ever spans more than one PC. | Dot99, BrunoBMFT |
| E1.2 | `make sim` and `make sim-smoke` use `SUBNET` discovery: with `LOCALHOST` the PX4 image (uid 1000, UDP only) and the other containers never see each other. The simulation runs no `flight_interface` and no drone. Added 2026-11-16. | Claude under BrunoBMFT's go, 2026-11-16 |
| E1.3 | Each compose mode (demo, sim, real) has its own ROS domain, so two modes never hear each other. A mode's profile, parameter file, domain and discovery range are set together in its env file, `drone/ground/env/<mode>.env`. The values are never in git: `env/` and `.env` are ignored, their templates (`env.example/<mode>.env.example`, `.env.example`) leave the domain and discovery range empty, and the safety pilot gives the values. Until they are set, compose refuses to start (`${VAR:?}`), so a fresh clone runs nothing, and `make` checks the file first (`tools/env_check.sh`: a whole number 0–101, not the tests' 81–98, a different domain per mode). The containers take the values from `GROUND_DOMAIN_ID` / `GROUND_DISCOVERY_RANGE`, not from `ROS_DOMAIN_ID`, so a terminal's `export ROS_DOMAIN_ID=...` cannot move a mode. E1.2's `SUBNET` is now a value of the sim's env file. Added 2026-11-18. | BrunoBMFT, on #186 PR B |
| E1.4 | E1's domain 42 stays only as the default of the native launch files, run on the host without Docker (`ros2 launch flight_interface flight_interface.launch.py`, `ros2 launch ground_bringup real.launch.py`), kept when the caller sets `ROS_DOMAIN_ID`. The compose modes never use it: their domain comes from `env/<mode>.env` (E1.3). Added 2026-11-19. | BrunoBMFT, on #186 PR B |
| E2 | Every `ARM_CONTROL_ACK` / `RESUME_ACK` is logged, a refusal as a warning with its reason; `last_ack` on `/flight/state` comes with the docs of #98. | ThiagoTEP, BrunoBMFT |
| E3 | `load_psk` warns when the secrets file is readable by the group or others; Docker will mount it read-only (never `SEAME_PSK` in the environment) once `flight_interface` has a compose service. | ptorrao-git, BrunoBMFT |
| E3.1 | The compose service exists (`flight_interface`, its own profile, `restart: "no"`): the secrets file is mounted read-only and a missing file is an error; no `SEAME_PSK` in its environment, no PSK in the image. The container runs the node itself, not the launch file: `ros2 launch` ignores the SIGTERM of `docker stop`, so the node would be killed without its last zero setpoint. E1's defaults then come from `docker-compose.yml`. Added 2026-11-16. | Claude in #186 (PR A), for BrunoBMFT's review |
| E3.2 | The container mounts only the PSK: `~/.config/seame-drone/psk.json` (`{"psk": …}`, mode 600), which `make psk-file` writes from `secrets.json`, at the path the node reads. Never the whole `secrets.json`, which also holds the Wi-Fi and OTA passwords. `make psk-file` always rewrites it from `secrets.json` (the one source) and never prints the key; a missing `psk.json` is an error, with no fallback to `secrets.json`. The mount binds the file's inode, so after the PSK changes the container must be recreated. Together with this, `make`, `make sim` and `make sim-smoke` refuse to start while a `flight_interface` container runs (one `/flight/state` publisher, §1), and `make smoke` checks that `/flight/state` has exactly one publisher. Needed before `backend:=esp` is used from the container. Added 2026-11-18. | BrunoBMFT (safety pilot), on ThiagoTEP's review of PR #211 |
| E4 | A new session counts only once a frame under its keys comes back (the `CONFIG_ACK` or a `STATE`); none within 1 s: drop it and redo the handshake, never with ARM ([[ADR-005 TCP Session\|ADR-005]]). | ptorrao-git, BrunoBMFT |
| E5 | Each test file has its own `TEST_DOMAIN_ID`; a check fails when two share one or use 42. Track D, own PR. | Dot99 |
| E6 | After a reconnect the ground takes the FC state from `FC_HEALTH` (5 Hz), since the events `0x0201` / `0x0202` report only changes. | BrunoBMFT, ptorrao-git |
| E6.1 | Unknown FC state = not OK for ARM, RESUME, the gate and `link_ok`. The loopback backend reports the FC alive, so dry runs still work. | The proposal, under BrunoBMFT's rule |
| E6.2 | `fc_link_ok` is false at once on `EVENT 0x0201` or any `FC_HEALTH` with `fc_alive` 0; true on `EVENT 0x0202` or after 250 ms of `FC_HEALTH` all saying `fc_alive` 1; unknown at the start of a session and after 1 s without `FC_HEALTH`. Added 2026-11-16. | BrunoBMFT's go on Claude's proposal, 2026-11-16 |
| TCP | A short TCP `send()` closes TCP, with the reason in the backend's last error. | BrunoBMFT |

**Why E6.2:** `fc_alive` follows the ESP's raw 250 ms rule without the hysteresis that #188 gave `FC_LINK_RESTORED`, so taking it directly would flip more often than the events. Lost from either source wins at once; alive again needs the same 250 ms on the ground. A lost event (TCP down) is still covered by `FC_HEALTH` over UDP.

### 2.3 What `/flight/state` carries

`mission_node`'s fields (`link_ok`, `esp_state`, `override_active`, `mask`, `airborne`) plus `active`, `estop`, `estop_reason`, `resume_pending`, `arm_pending`, `failsafe_output`, `fc_link_ok`, `armed` and `esp_rx_rejected` (the ESP's `STATE.rx_rejected` with its wraps counted, reset every session, #149). Unknown values are `null`; `mission_node` ignores fields it does not know. The JSON stays until an interface package exists (Module 2).

## 3. Consequences

- **Positive:** every way to ARM or RESUME goes through one operator service, one held request and one backend call, checked by a static test; a reconnect or a session switch can never carry a request over. The FC state is known about 0.4 s after the node starts streaming, also when no event came.
- **Negative:** ARM and RESUME answer 1.5 s later than before. Until `override_active` (#176) and the controller (C1) exist, the node only ever sends neutral sticks.
- **Open:** the C1.4 roll / pitch cap value, E1's SROS2, E2's `last_ack`, and the bench checks (#104, #109, blocked by #184).

## 4. Related pages

- [[ADR-004 ESP Failsafe]] (§2.6: what the ESP asks of the ground), [[ADR-005 TCP Session]], [[ADR-010 MSP Override]]
- [[Protocol Spec v0]] (§3: `STATE`, `FC_HEALTH`, result 4), [[ESP Link State Machine]]
- [[Architecture Document]] §2 (the node's contract), [[Testing Strategy]] §5 (its tests)
- [[04 Mission Node]] (the consumer of `/flight/state`)

## 5. Glossary

| Term | Meaning |
|---|---|
| **ARM (`ARM_CONTROL_REQUEST`)** | The ground asks the ESP to pass its sticks to the FC on the masked axes. It does not arm the motors. |
| **E-stop** | The operator's `~/estop`: latched on the node until `~/reset_estop`. |
| **`FC_HEALTH`** | ESP → ground message 0x81 at 5 Hz: FC alive, armed flag, value ages, counters (Protocol Spec v1.4, #179). |
| **Gate** | The guard's condition for a non-zero setpoint: `STREAMING`, override on, a known mask, FC link alive. |
| **Held request** | An operator ARM or RESUME that the node keeps until the neutral window is full (K6). |
| **Lifecycle node** | A ROS 2 node with explicit states (unconfigured, inactive, active) changed by `ros2 lifecycle set`. |
| **Mask** | Which stick channels the ground controls: 8 = yaw, 11 = roll + pitch + yaw, 15 = all four ([[ADR-010 MSP Override\|ADR-010]]). |
| **mode 0 / mode 1** | `SETPOINT` mode: 0 = neutral ("no command"), 1 = apply these sticks. |
| **PSK** | The pre-shared key ([[ADR-002 Message Integrity\|ADR-002]]) from which every session key is derived. |
| **ROS domain** | `ROS_DOMAIN_ID`: nodes only see nodes with the same number. Not a security boundary. |
| **SROS2** | ROS 2's security layer: signed and encrypted DDS traffic with per-topic permissions. |
