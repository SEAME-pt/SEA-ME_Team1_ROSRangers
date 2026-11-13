# PX4 SITL + Gazebo + ROS 2 (issue #29)

Ubuntu 24.04 + ROS 2 Jazzy + Gazebo Harmonic + PX4 v1.17.0. Setup and test steps: vault page "PX4 SITL Setup".

```sh
docker build -t seame-px4-sim:jazzy-1.17 drone/simulation/px4
```

Run the sim with the Gazebo GUI (software rendering) and the ROS 2 offboard example: see the vault page. Start `offboard_control` only after PX4 prints `Ready for takeoff!`.

## Ground chain with make (#86)

From `drone/ground/` (the Makefile builds this image the first time, then reuses it):

```sh
make sim          # PX4 + Gazebo window, bridge, camera gestures, intent, mission, echo
make sim GUI=0    # same, Gazebo headless; MASK=11 leaves z to PX4 (default 15)
make sim-smoke    # headless test: scripted gestures + the #86 checks, rosbag and summary.md
make sim-down
```

The `px4` service (`drone/ground/docker/px4/`) arms, takes off and switches to Offboard by itself, in the sim only. `make sim-smoke` runs `sim_smoke_scenario` (in `ros/px4_offboard_bridge`): no hands → ENGAGE → MOVE_RIGHT 4 s → CLIMB_UP 3 s → MOVE_RIGHT with `intent_node` killed. It checks direction, REQ-CTL-001, REQ-SAF-017, REQ-SAF-018 and REQ-SW-001 and writes the bag and `summary.md` to `drone/ground/logs/sim-smoke/<start time>/`.

## Ground chain smoke test by hand (`sim_smoke.sh`)

`sim_smoke.sh` starts PX4 SITL headless in its own container (`px4sim-smoke`), builds `intent_node`, `mission_node` and the sim-only `px4_offboard_bridge` inside it, and launches the chain `/gesture/raw → intent_node → mission_node → px4_offboard_bridge → PX4`:

```sh
drone/simulation/px4/sim_smoke.sh up       # mask 15 (or: up 11)
drone/simulation/px4/sim_smoke.sh check
drone/simulation/px4/sim_smoke.sh down
```

It sets `NAV_DLL_ACT 0` in the sim only (there is no ground station, and PX4 never gets ready without it). The bridge never arms: the scenario arms, takes off and switches to Offboard. Details and the FLU → NED conversion: `ros/px4_offboard_bridge/README.md`.
