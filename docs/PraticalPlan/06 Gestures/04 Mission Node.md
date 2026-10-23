---
title: Mission_Node (gesture intent → velocity target)
type: architecture
status: draft
date: 2026-10-16
updated: 2026-10-23
project: SEA autonomous drone
tags:
  - gestures
  - ros2
  - mission
  - safety
  - docker
aliases:
  - Mission Node
  - Mission Nodes
  - mission_node
---

# 🎯 Mission_Node: gesture intent → velocity target

`mission_node` (v0.1) is the third ROS 2 node of the [[00 Gesture Pipeline|Gesture Pipeline]], after `gesture_node` and `intent_node`. It reads what the operator wants from `/gesture/intent` ([[03 Intent Node]]) and what the drone is doing from `/flight/state`. It publishes a safe velocity target on `/mission/velocity_target`: limited, ramped, and only on the axes the ground owns.

> [!WARNING] Status: DRAFT. In `dev` with its tests and Docker image, numbers not reviewed yet
> - Merged into `dev` on 2026-10-23 (#93, issue #27) at **`drone/ground/src/mission_node/`**. `make ws` builds it natively (§8). The Docker image `rosrangers/mission:jazzy` runs it with no local ROS 2, and `make` starts it next to `gesture_node` and `intent_node` (§9.4).
> - 135 tests (logic, ROS node, process, launch files) plus 19 contract, pipeline and launch tests in `drone/tests/ros/` (§10). CI runs all of them in `ground-ros2` (90 % coverage gate) and inside the image in `ground-docker`.
> - Topic names, `/flight/state` fields and every number are still **proposals**. The team must agree them (§13). Choices made while coding are listed in §11 for review. Nothing here changes an accepted ADR.
> - `/flight/state` does not exist yet (no `flight_interface`). Until it does, the package ships a **pretend** publisher, `fake_flight_state` (§7). The Docker stack runs it as the `flight` service. Never use it with a real drone.
> - Not run yet with the real webcam (§10, Level 3), and not with a controller or a drone.

---

## 📍 1. Where it sits

```mermaid
flowchart LR
  GN[intent_node] -->|"/gesture/intent<br/>std_msgs/String (JSON), 20 Hz"| M["<b>mission_node</b><br/>(this page)"]
  FI -.->|"/flight/state<br/>String (JSON), proposal"| M
  FFS["fake_flight_state<br/>(pretend, for now)"] -->|"/flight/state"| M
  FINT["fake_intent<br/>(pretend, tests only)"] -.->|"/gesture/intent"| M
  M -->|"/mission/velocity_target<br/>TwistStamped, 20 Hz"| C["velocity controller<br/>+ estimator (week 5)"]
  M -->|"/mission/state<br/>String (JSON)"| LOG["ros2 topic echo / ros2 bag<br/>operator display"]
  C -->|"attitude / stick setpoints"| FI["flight_interface<br/>lifecycle node (planned)"]
```

| Node | Answers the question | Knows about |
|---|---|---|
| `intent_node` | "What does the **operator** want?" | hands, cameras, timeouts. Nothing about drones |
| **`mission_node`** | "What should the **drone** do, given what it is doing now?" | flight state, speed limits, which axes the ground controls |
| controller + estimator | "Which attitude gives that velocity?" | flow, IMU, baro height, gains |
| `flight_interface` | "How do I send it safely?" | protocol, session, watchdog, sim or real backend |

Why it is separate from `intent_node`:
- `/gesture/intent` stays drone-independent, so the same intents can drive a car or an arm in Module 2 ([[00 Gesture Pipeline|Gesture Pipeline]] §9).
- All drone-specific limits live in one place, which makes them easy to review and test.

---

## 📦 2. What was built

One ROS 2 Python package, `mission_node`, with **three executables** and **two launch files**:

| Piece | Kind | What it does |
| :--- | :--- | :--- |
| `mission_node` | executable (the real node) | `/gesture/intent` + `/flight/state` → `/mission/velocity_target` + `/mission/state` |
| `fake_flight_state` | executable (pretend input) | publishes a pretend `/flight/state` at 10 Hz. Every field is a parameter you can change while it runs (§7.1) |
| `fake_intent` | executable (pretend input) | publishes pretend `/gesture/intent` messages in `intent_node`'s format: a 29 s scripted demo, or one constant intent you change by hand (§7.2) |
| `mission_demo.launch.py` | launch file | `fake_flight_state` + `mission_node` + `fake_intent`: **the node on its own with pretend values** |
| `mission_with_gestures.launch.py` | launch file | `intent_node` (with `input_source: topic`) + `fake_flight_state` + `mission_node`: **the real gesture chain**. You add `gesture_node` or the fake `/gesture/raw` publisher (§9.2) |

### 2.1 Files

```text
drone/ground/src/mission_node/
├── package.xml                       # rclpy, std_msgs, geometry_msgs, rcl_interfaces; launch, launch_ros, intent_node (for the gestures launch)
├── setup.py, setup.cfg               # three console scripts, installs launch/*.launch.py
├── resource/mission_node
├── launch/
│   ├── mission_demo.launch.py        # on its own, pretend inputs
│   └── mission_with_gestures.launch.py   # with intent_node on /gesture/raw
├── mission_node/
│   ├── __init__.py
│   ├── mission_logic.py              # all the rules of §5. NO ROS imports, injectable clock
│   ├── mission_node.py               # rclpy wrapper: subscriptions, 20 Hz timer, publishers, logs
│   ├── spin.py                       # run(): spin until Ctrl+C / SIGTERM, then a clean stop (§5.6). Used by all three executables
│   ├── fake_inputs.py                # demo script + JSON builders. NO ROS imports
│   ├── fake_flight_state.py          # pretend /flight/state publisher
│   └── fake_intent.py                # pretend /gesture/intent publisher
└── test/                             # 135 tests (§10.2)
    ├── test_mission_logic.py         # unit: every rule of §5, the demo script, random inputs
    ├── test_mission_node_ros.py      # node: the real node and the pretend inputs on real topics
    ├── test_mission_node_process.py  # process: Ctrl+C, SIGTERM, bad parameters, the demo launch
    └── test_launch_files.py          # the launch files start the right nodes
```

Outside the package, in `drone/ground/`:

| File | What |
| :--- | :--- |
| `docker/mission/Dockerfile` | image `rosrangers/mission:jazzy`: `ros:jazzy-ros-base` + `colcon build` of this package. Runs `mission_node`, and `fake_flight_state` for the `flight` service (§9.4) |
| `docker-compose.yml` | services `mission` and `flight` (pretend `/flight/state`, `MASK` and `AIRBORNE` from the shell) |
| `docker/echo/monitor.py` | the live table of `make`: gesture, intent, mission mode and velocity target ([[01 ROS2 Nodes Installation\|01]] §4.2) |
| `Makefile` | `make ws` builds this package, `make unit` / `make test` / `make smoke` run its tests ([[01 ROS2 Nodes Installation\|01]] §4.3) |

Tests that use `mission_node` together with `intent_node` or `gesture_node` live in `drone/tests/ros/` (§10.2).

Same pattern as `intent_node` ([[03 Intent Node]] §8): the decisions live in a ROS-free module (`mission_logic.py`), so they can be unit-tested anywhere. `mission_node.py` only wires them to ROS.

---

## 📥 3. What it reads

### 3.1 `/gesture/intent` (from `intent_node`)

Type `std_msgs/msg/String`, 20 Hz. Format: [[03 Intent Node]] §4.

```json
{"intent":"MOVE_RIGHT","gesture":"INDEX_RIGHT","reason":"ok","t":1791976551.56,"xy":[0.51,0.42]}
```

The node uses `intent` and `reason`. `gesture` is copied to `/mission/state` for people. `t` and `xy` are ignored. Intent names are case-insensitive. Bad JSON or an unknown intent becomes `NEUTRAL` and is counted and logged (§5.6).

### 3.2 `/flight/state` (proposal; `fake_flight_state` for now)

Type `std_msgs/msg/String` holding JSON. The real publisher will be `flight_interface` ([[05 Future Nodes]] §6).

```json
{"link_ok":true,"esp_state":"STREAMING","override_active":true,"mask":11,"airborne":true}
```

| Field | Source (real system) | Used for |
|---|---|---|
| `link_ok` | ESP session + STATE freshness ([[Protocol Spec v0]]) | `false` → `IDLE`, zero |
| `esp_state` | STATE `esp_state` | anything but `"STREAMING"` → `IDLE` ([[ADR-010 MSP Override\|ADR-010]] condition 3) |
| `override_active` | STATE `override_active` | `false` → `IDLE`: the pilot has not handed control over |
| `mask` | expected override mask | 8 / 11 / 15 decide which axes may be commanded (§5.3). Any other value → `IDLE` |
| `airborne` | estimator / baro height above a threshold | land requests are ignored on the ground (§5.4) |

The parsing is strict: only a real JSON `true` counts as true (`"true"` or `1` are false), a missing field counts as false, and `mask` must be an integer. `esp_state` is case-insensitive. A message that is not a JSON object is ignored and counted, so the last good one goes stale. A `/flight/state` older than `flight_state_timeout_sec` (0.5 s) counts as lost → `IDLE`. The node never reads ARM state in order to change it.

### 3.3 Parameters

Read once at start-up. Change them with `--ros-args -p name:=value`:

```bash
ros2 run mission_node mission_node --ros-args -p max_lateral_mps:=0.2 -p intent_lost_sec:=1.5
```

The number parameters also take an integer (`-p intent_lost_sec:=3`). Anything else (`-p max_lateral_mps:=fast`) stops the node at start-up: a `FATAL` log line that names the parameter, exit code 1, no traceback.

| Parameter | Default | Meaning |
|---|---|---|
| `intent_topic` | `/gesture/intent` | input topic |
| `flight_state_topic` | `/flight/state` | input topic |
| `velocity_topic` | `/mission/velocity_target` | output topic |
| `state_topic` | `/mission/state` | output topic |
| `frame_id` | `base_link` | `header.frame_id` of the velocity target |
| `intent_timeout_sec` | `0.25` | no intent for this long → `NEUTRAL` (zero) |
| `intent_lost_sec` | `2.0` | no intent for this long → `HOVER_LAND_REQUEST` (`intent_topic_lost`). Also: `NEUTRAL` for this long while `ENGAGED` → `READY`. Never shorter than `intent_timeout_sec` |
| `flight_state_timeout_sec` | `0.5` | no `/flight/state` for this long → `IDLE` |
| `max_lateral_mps` | `0.3` | speed for `MOVE_LEFT/RIGHT` (hard ceiling 0.5) |
| `max_vertical_mps` | `0.2` | speed for `CLIMB_SLOWLY` / `DESCEND_…` and the mask-15 landing (hard ceiling 0.5) |
| `max_yaw_rate_dps` | `20.0` | yaw rate for `YAW_LEFT/RIGHT` (hard ceiling 45) |
| `max_accel_mps2` | `0.5` | how fast linear speed may **increase** (§5.5) |
| `max_yaw_accel_dps2` | `40.0` | how fast yaw rate may **increase** |
| `land_hover_sec` | `2.0` | mask 15 land request: hover this long, then descend |
| `publish_rate_hz` | `20.0` | output rate, always on (at least 1 Hz) |

> [!WARNING] Safety settings
> The speed limits, timeouts and ramps are safety settings. Agree changes with the team. A value above the hard ceiling is clamped to the ceiling with a warning at start-up. A negative or NaN limit becomes 0. The limits are clamped again downstream (controller, `flight_interface`, ESP), as [[Protocol Spec v0]] §5 requires.

---

## ⚙️ 4. What it does

Subscriptions only store the latest message. One timer runs at 20 Hz and does everything, so there are no threads or locks (same pattern as `intent_node`). On every tick:

1. **Check intent freshness.** Last intent older than `intent_timeout_sec` → `NEUTRAL` (`intent_stale`). Older than `intent_lost_sec` → `HOVER_LAND_REQUEST` (`intent_topic_lost`). This catches a crashed `intent_node`, which can't report itself. An intent that would go stale before the **next** tick already counts as stale, so the zero target is never late, whatever the tick phase (at 20 Hz: zero 0.20–0.25 s after the last message).
2. **Check the flight state** (§3.2). Missing, stale, link lost, not `STREAMING`, override off or bad mask → `IDLE`.
3. **Update the mode** (§5.1).
4. **Map** the intent to a raw velocity target (§5.2). Zero unless `ENGAGED` (or a mask-15 landing).
5. **Mask** the axes the ground doesn't own (§5.3).
6. **Ramp** up at `max_accel_mps2` and **clamp** to the limits (§5.5).
7. **Publish** `/mission/velocity_target` (always, even zero) and `/mission/state` (§6).

---

## 🛡️ 5. Rules

### 5.1 Modes

```mermaid
stateDiagram-v2
  [*] --> IDLE
  IDLE --> READY: flight state OK (link, STREAMING, override, valid mask)
  READY --> ENGAGED: HOLD_POSITION starts (OPEN_HAND_UP shown)
  ENGAGED --> READY: STOP_HOVER, NEUTRAL > 2 s, or land request on the ground
  ENGAGED --> LAND_REQUEST: HOVER_LAND_REQUEST while airborne
  READY --> LAND_REQUEST: HOVER_LAND_REQUEST while airborne
  LAND_REQUEST --> READY: HOLD_POSITION (operator back), or touchdown
  READY --> IDLE: flight state not OK
  ENGAGED --> IDLE: flight state not OK
  LAND_REQUEST --> IDLE: flight state not OK
```

| Mode | Output | Meaning |
|---|---|---|
| `IDLE` | zero | not allowed to command: no or stale flight state, no link, ESP not `STREAMING`, override off, or bad mask |
| `READY` | zero (hover) | override is on, but the operator hasn't engaged |
| `ENGAGED` | from the intent | gestures move the drone |
| `LAND_REQUEST` | hover; with mask 15, descend after `land_hover_sec` (§5.4) | operator gone, or `intent_node` gone |

- **Engaging needs a deliberate `OPEN_HAND_UP`.** `HOLD_POSITION` must **start** while in `READY`. If the hand was already showing it, it must go away and be shown again. This holds after `STOP_HOVER`, after a land request, after any fall back to `IDLE`, when the pilot flips override while the operator already holds an open hand, and when `mission_node` (re)starts while the hand is already open: the first message it ever gets is never a "start". A stray gesture never starts motion.
- **Only real messages count for "start".** A delivery gap (`intent_stale`, made up by this node) never looks like "hand removed and shown again". Without this rule, a 0.3 s network hiccup could engage the drone (found in the first real run, §11).
- **Dead-man rule:** in `ENGAGED`, `NEUTRAL` (hand gone > 0.3 s, from `intent_node`) gives zero at once, but stays engaged, so a short loss doesn't force re-engaging. `NEUTRAL` for more than `intent_lost_sec` → `READY`. Hand gone > 2 s → `intent_node` sends `HOVER_LAND_REQUEST`.
- Flight state not OK → `IDLE` from any mode. The pilot always wins ([[System Overview]] §2).

### 5.2 Intent → velocity target

Body frame, ROS convention (REP-103): x forward, y **left**, z up, yaw positive = counter-clockwise (left). SI units on the topic.

| Intent | `linear.x` | `linear.y` | `linear.z` | `angular.z` |
|---|---|---|---|---|
| `HOLD_POSITION` | 0 | 0 | 0 | 0 |
| `STOP_HOVER` | 0 | 0 | 0 | 0 (and leave `ENGAGED`) |
| `MOVE_LEFT` | 0 | +0.3 m/s | 0 | 0 |
| `MOVE_RIGHT` | 0 | −0.3 m/s | 0 | 0 |
| `CLIMB_SLOWLY` | 0 | 0 | +0.2 m/s | 0 |
| `DESCEND_SLOWLY_LAND_REQUEST` | 0 | 0 | −0.2 m/s | 0 |
| `YAW_LEFT` | 0 | 0 | 0 | +0.349 rad/s (20 °/s) |
| `YAW_RIGHT` | 0 | 0 | 0 | −0.349 rad/s |
| `NEUTRAL`, unknown, bad JSON | 0 | 0 | 0 | 0 |
| `HOVER_LAND_REQUEST` | see §5.4 | | | |

- Vocabulary v0 has **no forward/back gesture**, so `linear.x` is always 0.
- "Left/right" is the **operator's** left/right ([[00 Gesture Pipeline|Gesture Pipeline]] §3). It only matches the drone's left when both face the same way (§13).
- Zero velocity means "hold position" (the controller drives flow to zero), not "let go of the sticks".
- `DESCEND_SLOWLY_LAND_REQUEST` is only a slow descent. It does not enter `LAND_REQUEST`.

### 5.3 Axis masking (who owns which stick)

The ground may only command the axes in the override mask ([[ADR-010 MSP Override|ADR-010]] condition 4). The node zeroes the rest and adds `axis_not_owned:<axis>` to `limited` in `/mission/state`:

| Mask | Ground owns | Mission node may send |
|---|---|---|
| 8 | yaw | `angular.z` only |
| 11 | roll, pitch, yaw | `linear.x`, `linear.y`, `angular.z`. **`linear.z` is forced to 0**: the pilot has throttle |
| 15 | all four | everything, **only** with altitude hold or a validated altitude loop |

So `CLIMB_SLOWLY` / `DESCEND_…` do nothing until mask 15.

### 5.4 Land requests

`HOVER_LAND_REQUEST` comes from `intent_node` (hand or recogniser gone > 2 s) or from this node's own watchdog (`intent_topic_lost`).

| Situation | Action |
|---|---|
| `intent_node` start-up (`HOVER_LAND_REQUEST` / `no_packets_yet`) | treated as `NEUTRAL`: "no recogniser yet", never a land request ([[03 Intent Node]] §5) |
| No intent ever received since `mission_node` started | `NEUTRAL` / `no_intent_yet`, never `intent_topic_lost`: `intent_node` hasn't started, it didn't crash |
| Not `airborne` | ignored. `READY` stays `READY`, `ENGAGED` goes to `READY` (`land_request_on_ground`). Never "land" a drone that is on the ground |
| Airborne, mask 8 or 11 | zero velocity (hover) and `land_request: true` in `/mission/state`. The **pilot** lands, because they own throttle |
| Airborne, mask 15 | hover for `land_hover_sec` (2 s), then descend at `max_vertical_mps` until `airborne` goes false (→ `READY`, `landed`). The pilot can take over at any time |

While in `LAND_REQUEST`:

- Motion intents (`MOVE_…`, `YAW_…`, `CLIMB_SLOWLY`) are ignored. Only `HOLD_POSITION` (→ `READY`, `operator_back`, then a new engage is needed), touchdown (→ `READY`, `landed`) or a flight state that is not OK (→ `IDLE`) leave it.
- With mask 15 the descent goes on whatever the intent, also when `intent_node` dies.
- If the mask drops from 15 to 8 or 11 during the descent, `linear.z` goes to zero at once: throttle is back with the pilot.
- A new land request after `READY` starts the `land_hover_sec` hover again.

The node never disarms. Touchdown and disarm stay with the pilot or the Betaflight failsafe ([[Safety Rules]] rule 5).

### 5.5 Ramp and clamp

- **Speeding up** is limited to `max_accel_mps2` (0.5 m/s²: 0 → 0.3 m/s in 0.6 s) and `max_yaw_accel_dps2` (40 °/s²: 0 → 20 °/s in 0.5 s). So a new gesture never steps the target.
- **Slowing down and stopping are immediate.** Stopping is the safe direction, and REQ-SAF (§12) wants zero within 0.25 s of losing the input. A gradual stop would break that.
- **Reversing** (`MOVE_RIGHT` → `MOVE_LEFT`) drops to zero at once, then ramps up the other way.
- **Clamp** to the limits, which can never exceed the hard ceilings (0.5 m/s, 45 °/s) in `mission_logic.py`.
- While an axis is still ramping, `limited` in `/mission/state` shows `ramping:<axis>`.

### 5.6 Things it never does

- It never arms, disarms or touches any AUX channel or the override switch (REQ-SAF-003/004, [[ADR-015 Gesture Input Source|ADR-015]]).
- It never keeps moving on stale, unknown or bad input.
- It never sends a target above the limits or the hard ceilings, whatever the parameters say.
- It never stops publishing while it runs. On Ctrl+C or SIGTERM (`docker stop`) it publishes one last zero target, then exits with code 0 and no traceback, even while the operator still shows a motion gesture. Signals that arrive while it is stopping (`ros2 launch` forwards a second Ctrl+C) are ignored. The pretend nodes stop the same way (`spin.py`).
- It never starts with a bad configuration: a parameter that is not a number gives a `FATAL` log line and exit code 1 (§3.3).
- It never moves the wrong way on a timing fault: a stalled timer cannot make one large ramp step (the step is capped at 0.2 s), and a clock that jumps backwards does not speed anything up.

---

## 📤 6. What it publishes

### 6.1 `/mission/velocity_target`

Type **`geometry_msgs/msg/TwistStamped`**, 20 Hz, always published (zeros when not engaged), so the controller can treat silence as a fault.

```yaml
header:
  stamp: {sec: 1792420240, nanosec: 560000000}
  frame_id: base_link        # body frame, FLU (x forward, y left, z up)
twist:
  linear:  {x: 0.0, y: -0.3, z: 0.0}   # m/s  (MOVE_RIGHT)
  angular: {x: 0.0, y: 0.0, z: 0.0}    # rad/s (20 °/s = 0.349 rad/s)
```

### 6.2 `/mission/state`

Type `std_msgs/msg/String` (JSON). Published on every change, and at 2 Hz otherwise. For people and logs, not for control.

```json
{"mode":"ENGAGED","mode_reason":"hold_position","intent":"CLIMB_SLOWLY","reason":"ok","gesture":"THUMB_UP","mask":11,"airborne":true,"land_request":false,"limited":["axis_not_owned:z"],"intent_age_ms":42}
```

| Field | Meaning |
| :--- | :--- |
| `mode` | `IDLE`, `READY`, `ENGAGED`, `LAND_REQUEST` (§5.1) |
| `mode_reason` | why the node is in that mode: `no_flight_state`, `flight_state_stale`, `link_lost`, `not_streaming`, `override_off`, `bad_mask`, `override_on`, `hold_position`, `stop_hover`, `neutral_too_long`, `land_request_on_ground`, `no_hand_long`, `no_packets`, `intent_topic_lost`, `operator_back`, `landed` |
| `intent`, `reason` | the intent after the checks of §4, and why: `intent_node`'s reason, or this node's `no_intent_yet`, `intent_stale`, `intent_topic_lost`, `unknown_intent`, `bad_json` |
| `gesture` | the gesture from the last message (information only) |
| `mask`, `airborne` | from `/flight/state` (`null` / `false` while it is not OK) |
| `land_request` | `true` in `LAND_REQUEST`: the pilot should land (masks 8, 11) |
| `limited` | what was held back: `axis_not_owned:<axis>`, `ramping:<axis>` |
| `intent_age_ms` | age of the last intent message |

### 6.3 Logs

The node logs its topics at start-up, every mode change (`LAND_REQUEST` as a warning), every intent change, each new `axis_not_owned`, bad input (warnings, at most one every 2 s), a parameter clamped to its ceiling (warning, at start-up), and a health report every 5 s:

```text
[INFO] [mission_node]: Reading /gesture/intent and /flight/state; publishing /mission/velocity_target at 20 Hz and /mission/state
[INFO] [mission_node]: Mode -> ENGAGED (hold_position)
[INFO] [mission_node]: Intent -> MOVE_RIGHT (reason: ok)
[INFO] [mission_node]: Not commanded, mask 11: ['axis_not_owned:z']
[WARN] [mission_node]: Mode -> LAND_REQUEST (no_hand_long)
[INFO] [mission_node]: Health: 100 intents and 50 flight states in the last 5 s; mode ENGAGED, last intent MOVE_RIGHT; bad: json 0, unknown intents 0, flight states 0
```

The health report is a warning when one of the two inputs got no messages in the last 5 s. Bad input looks like `Bad /gesture/intent (unknown_intent) treated as NEUTRAL: ...` or `Ignored a bad /flight/state message: ...`.

In Docker (§9.4) the same lines go to `drone/ground/logs/mission/latest.log` (and `logs/flight/latest.log` for `fake_flight_state`), each one prefixed with the wall-clock time. Every container start writes a new file, so two runs can be compared with `diff` ([[01 ROS2 Nodes Installation|01]] §4.2, "Node logs").

### 6.4 How to read the outputs

With `make` (Docker), the `echo` table already shows the mission mode and the velocity target in words, next to the gesture and the intent ([[01 ROS2 Nodes Installation|01]] §4.2). For the details, run these in a terminal with both `source` lines, or put `docker compose run --rm echo` in front of them without a local ROS 2 ([[01 ROS2 Nodes Installation|01]] §4.4):

| Command | Shows |
| :--- | :--- |
| `ros2 topic echo /mission/state --field data` | the JSON of §6.2: mode, intent, reason, limits. Start here |
| `ros2 topic echo /mission/velocity_target --field twist` | the numbers only (`linear` in m/s, `angular` in rad/s), without the header |
| `ros2 topic echo /mission/velocity_target --field twist.linear.y` | one axis, one number per message (good for watching a ramp) |
| `ros2 topic echo --once /mission/velocity_target` | one full message, with `header.stamp` and `frame_id` |
| `ros2 topic hz /mission/velocity_target` | ~20 Hz in every mode, zeros included |
| `ros2 topic echo /flight/state --field data` | the pretend drone state from `fake_flight_state` (§7.1) |
| the `mission_node` terminal, or `tail -f logs/mission/latest.log` in Docker | `Mode ->` and `Intent ->` on every change, the health report every 5 s (§6.3) |

**Reading `/mission/velocity_target`.** The axes follow §5.2 (x forward, y **left**, z up, yaw positive = left), so the signs look reversed for "right":

| You see | Means |
| :--- | :--- |
| all zeros | hold position, or not `ENGAGED` (check `mode` on `/mission/state`) |
| `linear.y` negative (down to −0.3) | moving **right** |
| `linear.y` positive (up to +0.3) | moving **left** |
| `linear.z` +0.2 / −0.2 | climbing / descending (only with mask 15) |
| `angular.z` +0.349 / −0.349 | yaw left / right (20 °/s) |
| a value growing by 0.025 per message | the ramp (§5.5), not a bug |

**Reading `/mission/state`.** Look at `mode` and `mode_reason` first: if the mode is not `ENGAGED`, the velocity is zero whatever the gesture. If it is `ENGAGED` but an axis stays zero, `limited` says why (`axis_not_owned:z` = the mask does not give us that stick). `intent` and `reason` are what this node accepted from `/gesture/intent` (§3.1).

Reading the velocity in code (as the controller will):

```python
from geometry_msgs.msg import TwistStamped


def on_target(msg: TwistStamped) -> None:
    vy = msg.twist.linear.y        # m/s, + = left
    vz = msg.twist.linear.z        # m/s, + = up
    yaw_rate = msg.twist.angular.z  # rad/s, + = left


node.create_subscription(TwistStamped, "/mission/velocity_target", on_target, 10)
```

`/mission/state` is read like `/gesture/intent`: `json.loads(msg.data)` ([[03 Intent Node]] §4). It is for people and logs; control code should only use `/mission/velocity_target`.

### 6.5 What happens downstream (not this node's job)

1. **Velocity controller** (week 5, tracks C + E, [[Study Plan v2]] §7) reads `/mission/velocity_target` and the estimator velocity (flow + IMU + baro), and outputs roll/pitch angles, yaw rate and throttle.
2. **`flight_interface`** packs those into `SETPOINT` (0x60): `mode 1` (attitude), `roll`, `pitch`, `yaw_rate` in −1000..1000, `throttle` 0..1000 ([[Protocol Spec v0]] §3). It sends at 50 Hz and its own watchdog sends `mode 0` (neutral) if the controller goes silent.
3. **ESP** checks, clamps and maps to `MSP_SET_RAW_RC` (1000–2000 µs, AETR, 8 channels).
4. **In simulation** the PX4 backend of `flight_interface` takes the velocity setpoint more directly ([[ADR-019 Sim Backends|ADR-019]]).

Until the controller exists, the node is tested on its own by watching its two topics (§10).

---

## 🎭 7. Pretend inputs

### 7.1 `fake_flight_state`

Publishes `/flight/state` at 10 Hz. It reads its parameters on every tick, so you can change the "drone" while everything runs:

| Parameter | Default | Try |
| :--- | :--- | :--- |
| `link_ok` | `true` | `false` → `IDLE` (`link_lost`) |
| `esp_state` | `STREAMING` | `FAILSAFE` → `IDLE` (`not_streaming`) |
| `override_active` | `true` | `false` → `IDLE` (`override_off`): the pilot takes over |
| `mask` | `11` | `8` (yaw only), `15` (all axes, climb works, mask-15 landing) |
| `airborne` | `true` | `false`: land requests are ignored |
| `publish` | `true` | `false`: sends nothing at all, like a dead `flight_interface` → `IDLE` (`flight_state_stale`) within 0.5 s. `true` again resumes |
| `raw` | `''` | any text: sent **as is** instead of the fields, to try bad messages. `''` goes back to the fields |
| `rate_hz`, `topic` | `10.0`, `/flight/state` | read at start-up only |

```bash
ros2 param set /fake_flight_state override_active false
ros2 param set /fake_flight_state mask 15
ros2 param set /fake_flight_state publish false                         # flight_interface died
ros2 param set /fake_flight_state raw 'not json'                        # ignored and counted -> stale -> IDLE
ros2 param set /fake_flight_state raw "'{\"link_ok\": \"true\"}'"      # a string, not a boolean -> IDLE
ros2 param set /fake_flight_state raw ''                                # back to the fields
```

JSON in `raw` needs the extra single quotes, or `ros2 param` reads it as YAML. At start-up the node warns `PRETEND flight state on /flight/state at 10 Hz. Not for use with a real drone.`, and it logs each new message text once. Each fault drops `mission_node` to `IDLE`, and after the fault a new deliberate `HOLD_POSITION` is needed (`test_every_pretend_fault_stops_and_needs_a_new_engage`).

Stopping `fake_flight_state` (Ctrl+C) is also a test: `mission_node` goes `IDLE` (`flight_state_stale`) within 0.5 s.

### 7.2 `fake_intent`

Publishes `/gesture/intent` at 20 Hz in `intent_node`'s exact format. **Never run it together with `intent_node`**: both would publish on the same topic.

| Parameter | Default | Meaning |
| :--- | :--- | :--- |
| `mode` | `demo` | `demo` plays the script below. `constant` publishes `intent` until stopped |
| `intent` | `NEUTRAL` | the intent in `constant` mode. Change it live with `ros2 param set /fake_intent intent MOVE_RIGHT` |
| `reason` | `ok` | the `reason` in `constant` mode, also live (for example `no_hand_long` with `HOVER_LAND_REQUEST`) |
| `loop` | `false` | `demo`: start again at the end instead of exiting |
| `rate_hz`, `topic` | `20.0`, `/gesture/intent` | |

The `gesture` field is filled with the gesture `intent_node` maps to that intent (`MOVE_RIGHT` → `INDEX_RIGHT`). A contract test checks that `fake_intent` uses `intent_node`'s exact format and gesture names (§10.2). At the end of the demo (without `loop`) it logs `Demo finished` and exits with code 0.

The **demo script** (29 s) and what `mission_node` should do with the default fake flight state (mask 11, airborne):

| Step | Time (s) | Fake intent | Expected mode | Expected `/mission/velocity_target` |
| :---: | :---: | :--- | :--- | :--- |
| 1 | 0–2 | `NEUTRAL` / `no_hand` | `READY` | zero |
| 2 | 2–4 | `HOLD_POSITION` | `ENGAGED` | zero |
| 3 | 4–7 | `MOVE_RIGHT` | `ENGAGED` | `linear.y` ramps 0 → −0.3 in 0.6 s, then holds |
| 4 | 7–9 | `YAW_LEFT` | `ENGAGED` | `linear.y` 0 at once, `angular.z` ramps to +0.349 |
| 5 | 9–11 | `CLIMB_SLOWLY` | `ENGAGED` | zero, `axis_not_owned:z` (with `mask:=15`: `linear.z` +0.2) |
| 6 | 11–12 | `BANANA` (unknown) | `ENGAGED` | zero, warning `unknown_intent` |
| 7 | 12–14 | `STOP_HOVER` | `READY` | zero |
| 8 | 14–16 | `MOVE_LEFT` | `READY` | **zero**: not engaged |
| 9 | 16–18 | `HOLD_POSITION` | `ENGAGED` | zero |
| 10 | 18–20 | `MOVE_LEFT` | `ENGAGED` | `linear.y` ramps to +0.3 |
| 11 | 20–21 | `NEUTRAL` / `no_hand` | `ENGAGED` | zero at once (dead-man) |
| 12 | 21–24 | `HOVER_LAND_REQUEST` / `no_hand_long` | `LAND_REQUEST` | zero (with `mask:=15`: `linear.z` −0.2 after 2 s) |
| 13 | 24–26 | `HOLD_POSITION` | `READY` (`operator_back`) | zero |
| 14 | 26–29 | nothing published | `READY`, then `LAND_REQUEST` (`intent_topic_lost`) | zero |

The unit test `test_demo_sequence` plays this same script through `mission_logic.py`, so the table is checked on every test run.

---

## 🔨 8. Build

**Without a local ROS 2:** nothing to build by hand. `make` (or `docker compose build mission`) builds the image `rosrangers/mission:jazzy` from `docker/mission/Dockerfile`, which runs `colcon build --packages-select mission_node` inside `ros:jazzy-ros-base` (§9.4).

**Natively**, with ROS 2 Jazzy installed: the workspace is `drone/ground/ros2_ws`, made by `make ws` (ignored by Git), the same as for the other nodes ([[01 ROS2 Nodes Installation|01]] §6). `make ws` links `src/mission_node` into `ros2_ws/src/` and builds it with the system Python:

```bash
source /opt/ros/jazzy/setup.zsh
cd ~/ROSRangers/drone/ground
make ws                                  # venv + gesture_node, intent_node, mission_node
source ros2_ws/install/setup.zsh
ros2 pkg executables mission_node
```

> [!NOTE] Using bash?
> Replace `setup.zsh` with `setup.bash` everywhere on this page ([[01 ROS2 Nodes Installation|01]] §5.4).

Expected:

```text
mission_node fake_flight_state
mission_node fake_intent
mission_node mission_node
```

- `~/ROSRangers` is wherever you cloned the repo.
- `make ws` only rebuilds what changed. It rebuilds `mission_node` after a change to its `setup.py` or `package.xml`. Add a launch file? Then `rm ros2_ws/.mission_node && make ws`: new launch files are only installed by a build. Edits to existing Python and launch files take effect without a rebuild (`--symlink-install`).
- By hand, from `ros2_ws/`: `colcon build --symlink-install --packages-select mission_node`, with the system Python (no venv). Only `gesture_node` is built from the MediaPipe venv ([[01 ROS2 Nodes Installation|01]] §6.4). Never a plain `colcon build` of everything.
- `mission_with_gestures.launch.py` also needs `intent_node` in the same workspace. `make ws` builds both.
- If you made the old symlink to `MISSION_NODE_PACKAGE/` by hand before 2026-10-23, `make ws` replaces it with the link to `src/mission_node`.

Every ROS terminal below starts with:

```bash
source /opt/ros/jazzy/setup.zsh
source ~/ROSRangers/drone/ground/ros2_ws/install/setup.zsh
```

---

## ▶️ 9. Run

### 9.1 On its own, with pretend values (no `intent_node`, no camera, no drone)

**The scripted demo**, one command:

```bash
ros2 launch mission_node mission_demo.launch.py
```

Watch it in a second terminal:

```bash
ros2 topic echo /mission/state --field data              # mode, intent, reason, limits
ros2 topic echo /mission/velocity_target --field twist   # the numbers
```

The launch log shows `fake_intent`'s `Step n/14` lines next to `mission_node`'s `Mode ->` and `Intent ->` lines. Compare them with the table in §7.2. Launch arguments:

```bash
ros2 launch mission_node mission_demo.launch.py mask:=15        # throttle is ours: climb + landing descent
ros2 launch mission_node mission_demo.launch.py airborne:=false # on the ground: land requests ignored
ros2 launch mission_node mission_demo.launch.py loop:=true      # repeat the demo forever
```

**By hand**, one intent at a time (four terminals):

| Terminal | Command |
| :---: | :--- |
| 1 | `ros2 run mission_node mission_node` |
| 2 | `ros2 run mission_node fake_flight_state` |
| 3 | `ros2 run mission_node fake_intent --ros-args -p mode:=constant` |
| 4 | `ros2 topic echo /mission/state --field data` |

Then, in a fifth terminal (or in terminal 4 after Ctrl+C):

```bash
ros2 param set /fake_intent intent HOLD_POSITION      # -> ENGAGED
ros2 param set /fake_intent intent MOVE_RIGHT         # linear.y ramps to -0.3
ros2 param set /fake_flight_state mask 8              # y is not ours any more: zero, axis_not_owned:y
ros2 param set /fake_flight_state override_active false   # -> IDLE
```

> [!NOTE] Why not `ros2 topic pub`?
> `ros2 topic pub -r 20 /gesture/intent std_msgs/msg/String "{data: '{\"intent\":\"MOVE_RIGHT\",\"reason\":\"ok\"}'}"` works too, but each new `ros2 topic pub` needs about a second to connect, and switching takes you longer than `intent_lost_sec` (2 s). So the node sees `intent_topic_lost` between two commands, and with `airborne: true` it goes to `LAND_REQUEST`. That is correct behaviour, but it gets in the way of a manual test. `fake_intent` in `constant` mode publishes without gaps.

### 9.2 With `intent_node` and the fake camera (fake `/gesture/raw`)

| Terminal | Command |
| :---: | :--- |
| 1 | `ros2 launch mission_node mission_with_gestures.launch.py` |
| 2 | `ros2 topic echo /mission/state --field data` |
| 3 | the scripted fake camera of [[01 ROS2 Nodes Installation\|01]] §9, Level 0 (publishes on `/gesture/raw`) |

The launch file starts `intent_node` (with `input_source: topic`, so it reads `/gesture/raw`), `fake_flight_state` and `mission_node`. It accepts the same `mask:=` and `airborne:=` arguments. Don't start `fake_intent` here: it would fight `intent_node` on `/gesture/intent` (`test_launch_files.py` checks the launch file doesn't start it).

`test_mission_launch.py` runs this launch file in CI with pretend `/gesture/raw` packets: open hand engages, index right moves, Ctrl+C stops every node (§10.2).

### 9.3 With `intent_node` and the webcam (`gesture_node`)

As 9.2, but terminal 3 is the real recogniser, built as in [[01 ROS2 Nodes Installation|01]] §6:

```bash
ros2 run gesture_node gesture_node    # on Wayland: QT_QPA_PLATFORM=xcb ros2 run gesture_node gesture_node
```

Try it: open hand up (engage) → index pointing right (moves right) → fist (stop, `READY`) → index right again (nothing: not engaged) → open hand (engage again) → hide your hand for 3 s (`LAND_REQUEST`) → open hand (`READY`, operator back).

### 9.4 In Docker (no local ROS 2)

From `drone/ground/`, `make` starts the whole chain: `gesture` (webcam), `intent`, `mission`, `flight` (= `fake_flight_state`) and `echo`. The terminal shows the `echo` live table: the gesture, the intent, the mission mode (`mode`, `why`, `mask`, `airborne`, `limited`) and the velocity target in words (`left/right  right 0.30 m/s`). Each node's own output goes to `logs/<service>/latest.log` ([[01 ROS2 Nodes Installation|01]] §4).

```bash
cd ~/ROSRangers/drone/ground
make                                     # xhost +local:, then docker compose up --build
tail -f logs/mission/latest.log          # second terminal: Mode -> / Intent -> lines
MASK=15 make                             # pretend flight state with mask 15 (throttle is ours)
AIRBORNE=false make                      # on the ground: land requests ignored
```

| Service | Image | Runs |
| :--- | :--- | :--- |
| `mission` | `rosrangers/mission:jazzy` | `mission_node` with the default parameters |
| `flight` | `rosrangers/mission:jazzy` (same image) | `fake_flight_state` with `mask:=${MASK:-11}` and `airborne:=${AIRBORNE:-true}`. **Pretend**, never with a real drone |

One per terminal: `docker compose up --build mission flight`, and the table alone with `docker compose run --rm echo`. Any `ros2` command of this page works through the echo image: `docker compose run --rm echo ros2 topic echo /mission/state std_msgs/msg/String --field data`, or `docker compose run --rm echo ros2 param set /fake_flight_state override_active false`.

The pretend demo of §9.1 (`fake_intent`) has no service of its own: it would fight `intent_node`. Run it in the image without the `intent` service:

```bash
docker compose up -d mission flight
docker compose run --rm -e LOG_NAME=fake_intent mission /ws/install/mission_node/lib/mission_node/fake_intent
docker compose down
```

`LOG_NAME=fake_intent` keeps its output in `logs/fake_intent/`, so `logs/mission/latest.log` stays the node's log. Compare `logs/mission/latest.log` with the table in §7.2.

`docker stop` (or Ctrl+C / `make down`) stops `mission_node` like Ctrl+C: one last zero target, exit code 0 (§5.6). The smoke test checks it (§10.2).

---

## 🧪 10. Test

Most of this section is automated (§10.1, §10.2) and runs in CI on every PR that touches `drone/ground/`. The manual levels below show the same behaviour with your own eyes, and Level 3 (real gestures) can only be done by hand. Work through the levels in order. Each one adds one link of the chain.

### 10.1 Automated tests

From `drone/ground/` (the same targets as for the other nodes, [[01 ROS2 Nodes Installation|01]] §10):

| Command | Needs | Runs for `mission_node` |
| :--- | :--- | :--- |
| `make unit` | Python 3 only | `test_mission_logic.py` and the contract tests in `drone/tests/ros/`. The tests that need `rclpy` are skipped |
| `make test` | ROS 2 Jazzy (`make ws` first) | `colcon test` of the package (all 135 tests), then `pytest drone/tests/ros` (contract, pipeline, launch) |
| `make smoke` | Docker only | the 135 tests **inside** `rosrangers/mission:jazzy` with none skipped, then gesture → intent → mission across containers and clean `docker stop`s |

One package natively (ROS sourced, from `drone/ground/src/mission_node/`): `python3 -m unittest discover -s test -v`, or one file: `python3 -m unittest test.test_mission_node_ros -v`.

In CI (`ci.yaml`): `ground-ros2` builds and tests `mission_node` with the other two packages, then runs pytest over the package tests and `drone/tests/ros` with the 90 % line-coverage gate. `ground-docker` runs `docker/smoke_test.sh`. On `dev` after #93 (2026-10-23): `239 tests, 0 errors, 0 failures, 0 skipped` (colcon, three packages), `286 passed` (pytest), coverage 99.21 %, smoke test `mission_node (in its image) PASS 135/135`, `steps PASS 6/6`.

### 10.2 What each test file checks

| File | Kind | Tests | Checks |
| :--- | :--- | :---: | :--- |
| `test/test_mission_logic.py` | unit, no ROS | 83 | every rule of §5 with a fake clock: modes and blockers, engage only on a new `HOLD_POSITION` (also after start-up, `IDLE` and delivery gaps), dead-man and `NEUTRAL` timers, zero within 0.25 s whatever the tick phase, land requests (ground, start-up, masks 11 and 15, touchdown, mask drop, `intent_node` dying), every mask × intent, strict `/flight/state` parsing, ramp and ceilings (also NaN, negative, infinite, stalled timer, clock going backwards), every `/mission/state` field, the demo script of §7.2, and random inputs that never break the safety rules |
| `test/test_mission_node_ros.py` | node, `rclpy` | 38 | the real `MissionNode` on real topics, next to a probe that plays `intent_node` and `flight_interface`: 20 Hz in every mode, ramp, mask 8 / 11, zero within 0.25 s of losing `/gesture/intent`, `IDLE` on lost or bad `/flight/state`, `/mission/state` on change and at 2 Hz, the health report and logs, topics and `frame_id` from parameters, a slower `publish_rate_hz`, clamped and wrong-type parameters; `fake_flight_state` and `fake_intent` on their own and driving the real node through every pretend fault |
| `test/test_mission_node_process.py` | process, `rclpy` | 10 | the three executables as programs: Ctrl+C, Ctrl+C twice and SIGTERM exit 0 without a traceback; the last `/mission/velocity_target` is zero even while `MOVE_RIGHT` is shown; a parameter that is not a number exits 1 with a `FATAL` line; `mission_demo.launch.py` reaches `ENGAGED` and a `MOVE_RIGHT` target, and Ctrl+C stops all of it |
| `test/test_launch_files.py` | static, ROS launch packages | 4 | both launch files start the right executables; `mission_with_gestures.launch.py` starts `intent_node` with `input_source: topic` and never `fake_intent` |
| `drone/tests/ros/test_mission_contract.py` | unit, no ROS | 12 | the real `intent_node` filter feeding the real `MissionLogic` on one fake clock: both sides know the same intents and reasons, `fake_intent` speaks `intent_node`'s format, every gesture moves only the owned axes. A rename on either side fails here instead of silently stopping (or not stopping) the drone |
| `drone/tests/ros/test_mission_pipeline.py` | pipeline, `rclpy` | 6 | landmarks → `gesture_node` → `/gesture/raw` → `intent_node` → `/gesture/intent` → `mission_node` (+ `fake_flight_state`) → `/mission/velocity_target`: no motion before the open hand, open hand engages and index moves, thumb up does not climb under mask 11, fist stops, hand lost and recogniser stopped request landing |
| `drone/tests/ros/test_mission_launch.py` | launch, `rclpy` | 1 | `ros2 launch mission_node mission_with_gestures.launch.py airborne:=false` with pretend `/gesture/raw` packets: open hand engages, index moves, Ctrl+C exits 0 and leaves no node running |
| `drone/tests/ros/test_monitor.py`, `test_entrypoint.py` | program | 13 + 10 | the `echo` live table (also the mission rows) and the Docker entrypoint that writes `logs/<service>/` ([[01 ROS2 Nodes Installation\|01]] §10.2) |

The smoke test (`make smoke`, [[01 ROS2 Nodes Installation|01]] §10.3) adds what only Docker can show: `INDEX_RIGHT` from the gesture container reaches the mission container as `MOVE_RIGHT` but **never engages** (nobody showed `HOLD_POSITION`); stopping the gesture container makes `intent_node` say `HOVER_LAND_REQUEST` (`no_packets`) and `mission_node`, airborne, go to `LAND_REQUEST`; `docker stop` ends `mission` and `flight` at once with exit code 0; `logs/smoke/mission/latest.log` holds `Mode -> LAND_REQUEST (no_packets)`.

The ROS tests are skipped without `rclpy`, unless `REQUIRE_ROS_TESTS=1` (CI and the smoke test set it). Each one uses its own `ROS_DOMAIN_ID` (81–85, 87–91; the smoke test 86), so they never mix with a running pipeline.

### Level 0: automated tests (no camera)

Run §10.1. **Pass:** every summary ends with `ALL PASSED`.

### Level 1: the node on its own, pretend values

Run §9.1 (the demo), or §9.4 in Docker. `test_demo_engages_moves_and_stops_cleanly` and the `FakeInputsDriveMissionTests` automate most of these checks. **Pass:**

| Check | How | Expected |
| :--- | :--- | :--- |
| Mode sequence | launch log | `IDLE → READY → ENGAGED → READY → ENGAGED → LAND_REQUEST → READY → LAND_REQUEST`, as in §7.2 |
| Rate | `ros2 topic hz /mission/velocity_target` | ~20 Hz in every mode |
| Ramp | `ros2 topic echo /mission/velocity_target --field twist.linear.y` during step 3 | steps of 0.025 per message up to −0.3 (0.5 m/s² at 20 Hz) |
| Masking | `/mission/state` during step 5 | `"limited":["axis_not_owned:z"]`, `linear.z` 0 |
| Pilot wins | by hand (§9.1): `ros2 param set /fake_flight_state override_active false` | `IDLE` (`override_off`) and zero on the next message |
| Intent watchdog | by hand: Ctrl+C on `fake_intent` while `MOVE_RIGHT` | zero within 0.25 s (`intent_stale`), `LAND_REQUEST` (`intent_topic_lost`) after 2 s |
| Flight watchdog | Ctrl+C on `fake_flight_state` | `IDLE` (`flight_state_stale`) within 0.5 s |

### Level 2: full chain, no camera

Run §9.2. The Level 0 fake-camera sequence ([[01 ROS2 Nodes Installation|01]] §9) should give the table below (checked by hand on 2026-10-19 with the old UDP sender; the topic version sends the same sequence). `test_mission_pipeline.py` and `test_mission_launch.py` check the same steps automatically on `/gesture/raw` (§10.2).

| Fake camera | `intent_node` intent | `mission_node` mode | Velocity |
| :--- | :--- | :--- | :--- |
| before it starts | `HOVER_LAND_REQUEST` / `no_packets_yet` | `READY` (seen as `NEUTRAL`) | zero |
| `OPEN_HAND_UP` 2 s | `NEUTRAL` / `unconfirmed`, then `HOLD_POSITION` | `ENGAGED` | zero |
| `INDEX_RIGHT` 3 s | `MOVE_RIGHT` | `ENGAGED` | `linear.y` ramps to −0.3 |
| no hand 0.6 s | `NEUTRAL` / `no_hand` | `ENGAGED` | zero at once |
| `CLOSED_HAND` 2 s | `STOP_HOVER` | `READY` | zero |
| no hand 3 s | `NEUTRAL`, then `HOVER_LAND_REQUEST` / `no_hand_long` | `LAND_REQUEST` | zero |
| `INDEX_LEFT` 2 s | `MOVE_LEFT` | `LAND_REQUEST` (needs an open hand first) | zero |
| sender ends | `HOVER_LAND_REQUEST` / `no_packets` | `LAND_REQUEST` | zero |

### Level 3: real gestures

Run §9.3 and hold each gesture of the vocabulary ([[00 Gesture Pipeline]] §3) for 1 s, plus the sequence at the end of §9.3. **Pass:** the mode and velocity follow §5 within about 0.5 s of each gesture. Keep evidence:

```bash
ros2 bag record -o mission_proof /gesture/intent /mission/state /mission/velocity_target /flight/state
```

Attach the result to the GitHub issue (Test session form).

### Level 4: simulation (later)

PX4 SITL behind `flight_interface` (week 6): gestures drive the sim drone: hover, move, land request ([[Study Plan v2]] §8). Needs the controller and `flight_interface` first.

Real-drone use is a **stretch** goal (week 7), only at low limits, with the pilot on the kill switch ([[Safety Rules]]). Never with `fake_flight_state`.

### Troubleshooting

| Symptom | Likely cause | Fix |
| :--- | :--- | :--- |
| `Package 'mission_node' not found` | workspace not loaded or not built | `make ws`, then `source ~/ROSRangers/drone/ground/ros2_ws/install/setup.zsh` |
| `file 'mission_demo.launch.py' was not found` | built before the launch files existed | `rm ros2_ws/.mission_node && make ws`: new launch files are only installed by a build |
| `Package 'intent_node' not found` from `mission_with_gestures.launch.py` | `intent_node` not built in the same workspace | `make ws` builds both |
| Mode stays `IDLE` / `no_flight_state` | `fake_flight_state` not running (natively), or the `flight` service not started (Docker) | start it, use a launch file, or `docker compose up mission flight` |
| `mission_node` exits at once with `FATAL ... must be a number` (exit code 1) | a number parameter got text | fix the `-p` value (§3.3) |
| `echo` table: `MISSION ... STALE` or `waiting` | the `mission` container stopped or never started | `docker compose ps`, then `logs/mission/latest.log` |
| Mode stays `READY` with an open hand | the hand was already open when the node became `READY` | close it or take it away, then show it again (§5.1) |
| `CLIMB_SLOWLY` does nothing | mask 11: the pilot owns throttle | expected. Try `mask:=15` |
| Goes `LAND_REQUEST` between manual commands | gaps over 2 s between `ros2 topic pub` runs | use `fake_intent` `constant` mode (§9.1) |
| Intents jump between two values | `fake_intent` and `intent_node` both running | run only one of them |
| `ros2 topic echo` says `Could not determine the type` | the echo started before the node | start it after, or give the type: `ros2 topic echo /mission/velocity_target geometry_msgs/msg/TwistStamped` |
| Echo shows nothing but the node runs | different `ROS_DOMAIN_ID` in the terminals (or containers) | export the same value everywhere, before `make` too ([[01 ROS2 Nodes Installation\|01]] §5.7) |

---

## 🧭 11. Decisions made while coding (for review)

These fill gaps in the original design, or change it. Each needs a team review before the numbers or rules are final. None changes an accepted ADR.

| # | Decision | Why |
| :---: | :--- | :--- |
| 1 | Only **speeding up** is ramped. Slowing down and stopping are immediate | The design said motion "starts and stops smoothly", but REQ-SAF (§12) needs zero within 0.25 s of losing input. A 0.5 m/s² stop from 0.3 m/s takes 0.6 s |
| 2 | New parameter `max_yaw_accel_dps2` (40 °/s²) | The design had no ramp for yaw |
| 3 | Engage = `HOLD_POSITION` **starting** while `READY`, from real messages only | Answers the open point "must `OPEN_HAND_UP` be re-shown after every `STOP_HOVER`?" with **yes**. The first real run had a 0.43 s delivery gap while DDS discovered new nodes. With a "level" rule that gap could have engaged the drone without a deliberate gesture |
| 4 | `intent_node`'s start-up `HOVER_LAND_REQUEST` / `no_packets_yet` → `NEUTRAL` | Required by [[03 Intent Node]] §5, also when airborne |
| 5 | No intent **ever** received → `NEUTRAL` / `no_intent_yet`, never `intent_topic_lost` | The watchdog is there to catch a `intent_node` that **died**. Without this, starting `mission_node` 2 s before `intent_node` gave a land request |
| 6 | Land request on the ground while `ENGAGED` → `READY` | Operator gone: require a new engage, but nothing to land |
| 7 | `LAND_REQUEST` → `READY` when `airborne` goes false (`landed`) | The design only had "until the estimator reports the ground" |
| 8 | `/flight/state` older than 0.5 s, or a mask other than 8/11/15 → `IDLE`. Booleans must be real JSON `true` | A dead `flight_interface` must not leave the last "override on" in place |
| 9 | Hard ceilings 0.5 m/s and 45 °/s are constants in `mission_logic.py`. Higher parameters are clamped with a warning, NaN or negative → 0 | The design asked for a ceiling that no parameter can pass |
| 10 | `/mission/state` adds `mode_reason`, `gesture`, `airborne`, and `ramping:<axis>` in `limited` | Makes the logs explain themselves |
| 11 | One last zero target is published on Ctrl+C **and SIGTERM** (`docker stop`), then exit code 0 (#93) | So a reader never keeps the last non-zero target. In Docker the stop signal is SIGTERM, so it must behave like Ctrl+C |
| 12 | Unknown intents count as `NEUTRAL` for the "`NEUTRAL` > 2 s → `READY`" rule | Unknown is never "keep moving" |
| 13 | An intent that would go stale before the next tick counts as stale now (#93) | Without it, the 20 Hz timer could send the zero up to one tick (50 ms) after the 0.25 s of REQ-SAF-017 |
| 14 | The ramp step uses at most 0.2 s of elapsed time; a clock going backwards gives no step (#93) | A stalled timer must not turn into one large jump in speed |
| 15 | `intent_lost_sec` is never shorter than `intent_timeout_sec` (#93) | "Topic lost" can never be decided before "stale" |
| 16 | A parameter that is not a number stops the node at start-up (`FATAL`, exit code 1); an integer is accepted (#93) | A typo in a safety setting must not start a node with a guessed value |
| 17 | In `LAND_REQUEST` only `HOLD_POSITION`, touchdown or a bad flight state change the mode; the mask-15 descent goes on whatever the intent (#93) | A land request ends only on a deliberate open hand or touchdown (`test_motion_intents_are_ignored_while_landing`, `test_mask_15_keeps_descending_whatever_the_intent`). A mask drop still stops the descent at once (§5.4) |
| 18 | `fake_flight_state` got `publish` and `raw` parameters (#93) | To test a dead `flight_interface` and bad messages without code changes (§7.1) |

---

## 🔖 12. Requirement IDs

Registered in [[Requirements]] (status `draft`). The tests that check each one are in brackets: unit tests in `test_mission_logic.py`, node tests in `test_mission_node_ros.py` (§10.2).

| ID | Requirement |
|---|---|
| REQ-SAF-017 | The mission node outputs zero velocity within 0.25 s of losing `/gesture/intent` (unit: `test_stale_intent_gives_zero_within_0_25_s`, `test_tick_period_cannot_make_the_watchdog_late`; node: `test_stale_intent_gives_zero_within_0_25_s`). |
| REQ-SAF-018 | The mission node never commands an axis outside the active override mask (unit: `test_mask_11_zeroes_z`, `test_mask_8_only_yaw`, `test_every_mask_and_intent`; node: `test_mask_11_never_commands_z`, `test_mask_8_only_yaw`). |
| not registered | Motion only starts after a deliberate `HOLD_POSITION` (unit: `test_engage_only_after_hold_position`, `test_delivery_gap_while_holding_does_not_engage`, `test_hold_shown_at_start_up_does_not_engage`; node: `test_stray_gesture_never_moves`). Proposed as a third REQ-SAF; it has no ID in [[Requirements]] yet (§13). |
| REQ-CTL-001 | Gesture speeds are limited to 0.3 m/s lateral, 0.2 m/s vertical and 20 °/s yaw (parameters), with a hard ceiling of 0.5 m/s and 45 °/s (unit: `test_intent_table`, `test_ceiling_holds_even_with_high_parameters`; node: `test_speeds_above_the_ceiling_are_clamped_with_a_warning`). |
| REQ-SW-001 | `/mission/velocity_target` is published at 20 Hz in every mode (node: `test_publishes_at_20_hz_in_every_mode`; by hand, Level 1: `ros2 topic hz`). |

---

## ❓ 13. Open points

- [ ] **Review §11** and agree the numbers: `max_vertical_mps`, `max_yaw_rate_dps`, `max_accel_mps2`, `max_yaw_accel_dps2`, `land_hover_sec`, the ceilings, `flight_state_timeout_sec`.
- [ ] **Register the engage rule** ("motion only starts after a deliberate `HOLD_POSITION`", §12) as a REQ-SAF in [[Requirements]].
- [ ] **First run with the webcam** (§10, Level 3), recorded in [[01 ROS2 Nodes Installation|01]] §13 with a `ros2 bag`.
- [x] **Move the package** from `MISSION_NODE_PACKAGE/` to `drone/ground/src/mission_node/` (#93, 2026-10-23).
- [ ] **Topic names and types.** `/mission/velocity_target` (TwistStamped), `/mission/state` and `/flight/state` (JSON strings). A typed message package (`drone_msgs`) would also fix `/gesture/intent` ([[03 Intent Node]] §9, [[05 Future Nodes]] §4).
- [ ] **Who publishes `/flight/state`** (planned: `flight_interface`), and how `airborne` is decided (baro height threshold?). Until then, `fake_flight_state`.
- [ ] **Left/right convention** in the cage: operator frame vs drone frame.
- [ ] **Forward/back.** Vocabulary v0 has no gesture for it. Add one, or keep `linear.x = 0` for Module 1.
- [ ] **Mission vs controller in one node?** [[System Overview]] draws "mission / controller" as one box. This page keeps them apart. Confirm in the architecture review.
- [ ] **Launch files and parameters** should move to a `gesture_bringup` package with one reviewed YAML ([[05 Future Nodes]] §11).
- [x] **Node wrapper tests.** The ROS wrapper has node, process and launch tests next to the logic's unit tests (#93, §10.2).
- [x] **CI and Docker.** `ground-ros2` builds and tests `mission_node` within the 90 % coverage gate, `ground-docker` runs its tests in its own image `rosrangers/mission:jazzy` (#84, #93, 2026-10-23).
- [x] **Maintainer fields** in `package.xml` and `setup.py` are filled in (#93).
- [x] **Launch file input.** `mission_with_gestures.launch.py` starts `intent_node` with `input_source: topic` (#93).

---

## 🔗 14. Related pages

[[00 Gesture Pipeline]] · [[01 ROS2 Nodes Installation]] · [[02 Gesture Node]] · [[03 Intent Node]] · [[05 Future Nodes]] · [[Requirements]] · [[Testing Strategy]] · [[System Overview]] · [[Protocol Spec v0]] · [[ADR-010 MSP Override|ADR-010]] · [[ADR-015 Gesture Input Source|ADR-015]] · [[ADR-019 Sim Backends|ADR-019]] · [[Study Plan v2]] · [[Safety Rules]]

---

## Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Body frame** | Axes fixed to the drone: x forward, y left, z up (FLU). |
| **Ceiling** | A hard-coded maximum that no parameter can exceed. |
| **colcon** | The build tool for ROS 2 workspaces. |
| **Contract test** | A test that runs two nodes' real logic together, so a renamed intent or reason on one side fails the build instead of the drone. |
| **Coverage** | The share of code lines the tests run; CI requires at least 90 % for the ROS 2 nodes. |
| **Dead-man** | A control that only works while the operator keeps doing it (here: the hand must stay visible). |
| **DDS** | The middleware under ROS 2 that finds nodes and carries messages. Discovery of new nodes can briefly delay delivery. |
| **Docker image / container** | A packaged Ubuntu 24.04 + ROS 2 Jazzy + one node (`rosrangers/mission:jazzy`); a container is one running copy of it. |
| **Engage** | Going from `READY` to `ENGAGED`: from then on gestures move the drone. |
| **Estimator** | Ground code that combines flow, IMU and baro height into a velocity estimate. |
| **Intent** | The action the operator asks for (for example `MOVE_LEFT`), published on `/gesture/intent`. |
| **Launch file** | A Python file that starts several nodes with their parameters (`ros2 launch`). |
| **Mask** | `msp_override_channels_mask`: which stick channels the ground controls (8, 11 or 15). |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **Pretend input** | A small node (`fake_flight_state`, `fake_intent`) that publishes made-up values so a node can be tested alone. |
| **Ramp** | Limiting how fast a target can change, so motion starts smoothly. |
| **REP-103** | The ROS convention for units (SI) and axis directions. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **SIGTERM** | The signal `docker stop` sends to ask a program to stop. `mission_node` treats it like Ctrl+C: last zero target, exit code 0. |
| **Smoke test** | A short end-to-end check that the built images start and work together (`make smoke`). |
| **TwistStamped** | Standard ROS 2 message holding a linear and angular velocity with a timestamp and frame. |
| **Watchdog** | A timer that triggers a safe action when expected messages stop arriving. |
