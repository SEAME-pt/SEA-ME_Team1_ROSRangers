# PX4 SITL + Gazebo + ROS 2 (issue #29)

Ubuntu 24.04 + ROS 2 Jazzy + Gazebo Harmonic + PX4 v1.17.0. Setup and test steps: vault page "PX4 SITL Setup".

```sh
docker build -t seame-px4-sim:jazzy-1.17 drone/simulation/px4
```

Run the sim with the Gazebo GUI (software rendering) and the ROS 2 offboard example: see the vault page. Start `offboard_control` only after PX4 prints `Ready for takeoff!`.

## Ground chain smoke test (#86)

`sim_smoke.sh` starts PX4 SITL headless in its own container (`px4sim-smoke`), builds `intent_node`, `mission_node` and the sim-only `px4_offboard_bridge` inside it, and launches the chain `/gesture/raw → intent_node → mission_node → px4_offboard_bridge → PX4`:

```sh
drone/simulation/px4/sim_smoke.sh up       # mask 15 (or: up 11)
drone/simulation/px4/sim_smoke.sh check
drone/simulation/px4/sim_smoke.sh down
```

It sets `NAV_DLL_ACT 0` in the sim only (there is no ground station, and PX4 never gets ready without it). The bridge never arms: the scenario arms, takes off and switches to Offboard. Details and the FLU → NED conversion: `ros/px4_offboard_bridge/README.md`.
