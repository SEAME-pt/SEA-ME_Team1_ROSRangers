# px4_offboard_bridge (SIMULATION ONLY, #86)

Stand-in for the PX4 backend of `flight_interface` (ADR-019), so the ground chain can be tried against PX4 SITL + Gazebo. **Never use it with the real drone.** Week 5 replaces it with the controller behind `flight_interface`.

```
/gesture/raw -> intent_node -> mission_node -> /mission/velocity_target (TwistStamped, body FLU)
                                                    |
                                        px4_offboard_bridge
                                                    |
        /fmu/in/offboard_control_mode + /fmu/in/trajectory_setpoint (NED, velocity)  -> PX4
        /fmu/out/vehicle_status_v1, vehicle_local_position_v1, vehicle_land_detected -> /flight/state
```

## What it does

- Streams `OffboardControlMode` (velocity) and `TrajectorySetpoint` at 20 Hz all the time: PX4 needs them before and during Offboard.
- Sends **zero** (hover) when the target would be older than `target_timeout_sec` by the next tick, so the zero is out within 0.25 s whatever the tick phase (REQ-SAF-017, the same rule as `mission_node`). The timeout can be shorter but never longer than 0.25 s.
- Also sends zero when the PX4 heading is missing, not `heading_good_for_control`, or older than 0.5 s: a frozen heading would send the drone the wrong way.
- Zeroes the axes outside `mask` (8, 11 or 15, as in `mission_node`, REQ-SAF-018) and clamps to the same ceiling values (0.5 m/s horizontal speed as a whole, so a diagonal is not faster; 0.5 m/s vertical; 45 °/s). The axes outside the mask are zeroed first, so they never slow down an owned axis.
- Publishes `/flight/state` for `mission_node`: `link_ok` = `vehicle_status` within 1.5 s (PX4 publishes it at 2 Hz) and the local position within 0.5 s; `override_active` = armed **and** in Offboard (the sim's "takeover switch on"); `airborne` = PX4 says not landed (false until it says so, and false again if `vehicle_land_detected` is older than 3 s; PX4 sends it at least once a second); `mask` = the parameter. `esp_state` is synthetic (there is no ESP): it only mirrors `link_ok`.
- Freshness uses the monotonic clock, so a wall-clock step cannot make an old target look fresh.
- On Ctrl+C or `docker stop` it sends one last zero setpoint, so PX4 does not keep the last one until its offboard-loss timeout. It then logs how many ticks sent zero because the target was stale or the heading was not usable.
- **Never arms, disarms or changes mode.** That is the scenario's job (the safety pilot's, on the real drone).

Limits of this stand-in:

- With mask 11 the bridge sends `v_down = 0`, so PX4 **holds** the altitude: in the sim nobody else flies z. The real `flight_interface` backend must leave the pilot's axes to the pilot (Betaflight MSP override mask), not command them to zero.
- Freshness is measured on the wall (monotonic) clock while PX4 SITL runs on Gazebo time. If Gazebo runs much slower than real time (real-time factor below about 0.33), `link_ok` and `airborne` can flicker to false.
- `airborne` reads false when the land detector is silent: fine here, where it is display only. The real backend must treat "unknown" as airborne wherever it drives a landing or a disarm.
- `mission_node` always publishes `/mission/velocity_target`. A scenario must drive the chain through `/gesture/raw` only; publishing `/mission/velocity_target` directly while `mission_node` runs gives two interleaved publishers.

## Frames: body FLU to local NED

`mission_node` speaks REP-103 body FLU (x forward, y **left**, z **up**, yaw + = counter-clockwise). PX4 offboard velocity is local NED (x north, y east, z **down**, yawspeed + = clockwise). With `yaw` = PX4 `heading` (from north, clockwise):

```
v_north  =  vx·cos(yaw) + vy·sin(yaw)
v_east   =  vx·sin(yaw) − vy·cos(yaw)
v_down   = −vz
yawspeed = −wz
```

Body forward is (cos yaw, sin yaw) in (north, east), body right is (−sin yaw, cos yaw), and left = −right. `test/test_bridge_logic.py` checks all four headings.

Checked in SITL on 2026-11-05 (x500 facing east, 95°): forward 0.3 m/s × 4 s moved 1.07 m east, left 0.3 m/s × 4 s moved 0.98 m north, up 0.2 m/s × 4 s climbed 0.80 m, yaw left 0.3 rad/s × 3 s turned 94° → 49°, and with no target for 3 s it drifted at most 7 cm.

## Parameters

| Name | Default | What |
|---|---|---|
| `velocity_topic` | `/mission/velocity_target` | Input |
| `flight_state_topic` | `/flight/state` | Output for `mission_node` |
| `mask` | 15 | Axes the ground owns: 8 yaw, 11 x y yaw, 15 all. Any other value: FATAL at start-up |
| `rate_hz` | 20 | Setpoint rate, kept within 10–50 (an integer is accepted) |
| `target_timeout_sec` | 0.25 | Older target → zero. Never above 0.25 |

## Run

With the rest of the ground nodes, from `drone/ground/`: `make sim` (camera gestures), `make sim-smoke` (scripted test, see below), `make sim-down`.

By hand, from the repo root, in its own container (`px4sim-smoke`):

```sh
drone/simulation/px4/sim_smoke.sh up            # headless, mask 15; or: up 11
drone/simulation/px4/sim_smoke.sh up 15 gui     # also opens the Gazebo window
drone/simulation/px4/sim_smoke.sh check         # topic rates and the last /flight/state
drone/simulation/px4/sim_smoke.sh shell         # ROS sourced, for the scenario
drone/simulation/px4/sim_smoke.sh down
```

`up` stops with an error (and says how to see the logs) if the build fails, PX4 is not ready within 180 s, or a node does not start.

## Tests

The logic tests need no ROS and no `px4_msgs` (`bridge_logic.py` has no ROS imports). They use pytest, and CI runs them in the `sim-bridge-python` job:

```sh
cd drone/simulation/px4/ros/px4_offboard_bridge && python3 -m pytest test
```

The node itself (`bridge_node.py`) needs `px4_msgs`, so it is checked only in SITL (`sim_smoke.sh`, `make sim-smoke`), not in CI.

`make sim-smoke` runs `sim_smoke_scenario` (`smoke_scenario.py`, needs ROS and `px4_msgs`). It starts its own `intent_node`, sends gestures on `/gesture/raw` (no hands → ENGAGE → MOVE_RIGHT 4 s → CLIMB_UP 3 s → MOVE_RIGHT, then `intent_node` killed with SIGKILL), records a rosbag and judges the run with `smoke_checks.py` (no ROS, unit-tested in CI):

| Check | Pass when |
|---|---|
| Direction | the commanded body axis moved ≥ 0.3 m the right way, the others ≤ 0.25 m; an axis outside the mask does not move |
| REQ-CTL-001 | every target ≤ 0.3 m/s lateral, 0.2 m/s vertical, 20 °/s yaw; measured speed ≤ 0.5 m/s |
| REQ-SAF-017 | zero target from `mission_node` ≤ 0.25 s after the last `/gesture/intent` (timed with the publish stamps); zero setpoint at PX4 ≤ 0.30 s (one more bridge tick); 2.5 s after the kill, measured horizontal and vertical speed ≤ 0.1 m/s |
| REQ-SAF-018 | no target or PX4 setpoint on an axis outside the mask |
| REQ-SW-001 | `/mission/velocity_target` at 20 Hz ± 10 %, no gap above 150 ms |

Masks 11 and 15 only (`make sim-smoke` refuses 8): the scenario flies right and up, which mask 8 does not own.

## Glossary

| Term | Meaning |
|---|---|
| **FLU** | Body frame: x forward, y left, z up (ROS REP-103). |
| **NED** | Local frame used by PX4: x north, y east, z down. |
| **Offboard** | PX4 mode that follows setpoints from a companion computer; it needs a setpoint stream at ≥ 2 Hz. |
| **SITL** | Software In The Loop: PX4 running as a program on the PC with Gazebo as the world. |
| **Micro XRCE-DDS agent** | The bridge between PX4's uORB topics and ROS 2 (`/fmu/in`, `/fmu/out`). |
