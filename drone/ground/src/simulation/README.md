# PX4 SITL + Gazebo + ROS 2 (issue #29)

Ubuntu 24.04 + ROS 2 Jazzy + Gazebo Harmonic + PX4 v1.17.0. Setup and test steps: vault page "PX4 SITL Setup".

```sh
docker build --target px4 -t seame-px4-sim:jazzy-1.17 -f drone/ground/src/simulation/Dockerfile drone/ground
```

Run the sim with the Gazebo GUI (software rendering) and the ROS 2 offboard example: see the vault page. Start `offboard_control` only after PX4 prints `Ready for takeoff!`.

## Ground chain with make (#86, #199)

From `drone/ground/` (the Makefile builds this image the first time, then reuses it):

```sh
make sim          # PX4 + Gazebo window, flight_interface (px4 backend), controller, camera gestures, intent, mission, echo
make sim GUI=0    # same, Gazebo headless
make sim-smoke    # headless test: scripted gestures + the checks, rosbag and summary.md
make sim-hold     # SITL hold test (REQ-NAV-001) + neutral phase, 3 runs; RUNS=1 for one
make sim-down
```

The ground flies PX4 the way it flies the drone: `mission_node` → `controller_node` (velocity target → attitude setpoint) → `flight_interface` with the `px4` backend (ADR-025 §2.4). That `flight_interface` runs in its own image, the `sim` stage of `src/flight_interface/Dockerfile` (it adds `px4_msgs`), as the compose service `flight_interface_sim`. The mask (15) and the hover throttle (0.73, measured) are in `ground_bringup/config/sim.yaml`.

The `px4` service (`Dockerfile`, final stage, started by `sim_start.sh`) does what the operator and the safety pilot do on the real drone, in the sim only: it sets `NAV_DLL_ACT 0` (no ground station, PX4 never gets ready without it) and `MIS_TAKEOFF_ALT 2.0` (PX4 calls the heading good for control only after climbing above 1.5 m, ADR-025 F23), configures and activates `flight_interface`, waits for its Offboard stream, arms, takes off to 2.0 m and switches to Offboard. The controller then holds zero velocity until a gesture moves it.

`make sim-smoke` runs `sim_smoke_scenario` (package `sim_smoke/`): no hands → ENGAGE → MOVE_RIGHT 4 s → CLIMB_UP 3 s → MOVE_RIGHT with `intent_node` killed. It checks direction, REQ-CTL-001, REQ-SAF-017 (`mission_node`'s zero within 0.25 s, then the drone hovers), REQ-SAF-018 (velocity targets, attitude setpoints and the measured drift) and REQ-SW-001, and writes the bag and `summary.md` to `drone/ground/logs/sim-smoke/<start time>/`. The mask must be 11 or 15: the scenario flies right and up only.

`make sim-hold` is the SITL hold test (REQ-NAV-001, ADR-025 F26): it descends to 1.0 m by gesture, holds 60 s and judges the run on Gazebo's ground truth, 3 runs in a row (`RUNS=1` for one). It also checks that the controller, not PX4, held the drone, and then pauses the controller for 20 s (its `hover_throttle` set to 0.0 live, F28 note) to check PX4's neutral position hold (F19). Results in `drone/ground/logs/sim-hold/<start time>/`.

`sim_start.sh` reads PX4's state with the helpers in `sim_lib.sh` (tested against a fake `px4-listener` in `sim_smoke/test/test_sim_lib.py`).
