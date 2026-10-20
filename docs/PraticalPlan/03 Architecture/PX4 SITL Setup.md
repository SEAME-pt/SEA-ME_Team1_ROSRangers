---
title: PX4 SITL Setup
type: how-to
status: verified
date: 2026-10-15
project: SEA autonomous drone
tags:
  - simulation
  - px4
  - gazebo
  - ros2
  - track-e
---

# PX4 SITL Setup

> [!success] Verified 2026-10-15 (issue #29, Track E)
> Default quad (`gz_x500`) arms, takes off to 5 m and holds, flown from the ROS 2 `offboard_control` example. Run in a container, headless and with the Gazebo GUI window (§6).

## 1. What this gives us

PX4 SITL + Gazebo is the flight-behaviour backend of [[ADR-019 Sim Backends|ADR-019]]. The platform follows [[ADR-018 Ground OS and ROS 2|ADR-018]]. The PX4 docs list **Ubuntu 24.04 + ROS 2 Jazzy + Gazebo Harmonic** as the primary combination, which closes the open check in ADR-018. Ubuntu 22.04 also works, but with ROS 2 Humble.

| Part | Version |
|---|---|
| Base | `ros:jazzy` (Ubuntu 24.04) |
| PX4 | `v1.17.0` (latest stable on 2026-10-15) |
| `px4_msgs` | branch `release/1.17`, **must match the PX4 version** |
| Micro XRCE-DDS Agent | `v2.4.3` (Jazzy) |

## 2. Build the image

The `Dockerfile` is in the code repo under `simulation/px4/` (branch `feature/29-px4-sitl-gazebo`). It runs PX4's `ubuntu.sh --no-nuttx`, builds the ROS 2 workspace and pre-builds `px4_sitl_default`.

```sh
docker build -t seame-px4-sim:jazzy-1.17 simulation/px4
```

Your user must be in the `docker` group (`sudo usermod -aG docker $USER`, then log in again).

## 3. Fly the default quad

```sh
docker run -d --name px4sim --network host -e HEADLESS=1 seame-px4-sim:jazzy-1.17 sleep infinity

# 1) DDS agent
docker exec -d px4sim bash -lc 'source ~/ros2_px4_ws/install/setup.bash; MicroXRCEAgent udp4 -p 8888'
# 2) PX4 + Gazebo x500 (stdin kept open, or the PX4 shell spams the log)
docker exec -d px4sim bash -lc 'cd ~/PX4-Autopilot && tail -f /dev/null | HEADLESS=1 make px4_sitl gz_x500 > /tmp/px4.log 2>&1'
```

Ready when the PX4 log shows `Startup script returned successfully` and `uxrce_dds_client ... successfully created rt/fmu/out/...`.

## 4. Offboard from the ROS 2 example

Arming needs a GCS. Without one, PX4 reports `Preflight Fail: No connection to the GCS`. Either connect QGroundControl (UDP 14550), or in a headless run disable the failsafe:

```sh
docker exec px4sim bash -lc 'cd ~/PX4-Autopilot/build/px4_sitl_default && ./bin/px4-param set NAV_DLL_ACT 0'
```

> [!warning] Sim only
> `NAV_DLL_ACT 0` turns off the data-link-loss failsafe. Never use it for the real drone ([[Safety Rules]]). The parameter resets when the sim state is wiped.

```sh
docker exec -it px4sim bash -lc 'source ~/ros2_px4_ws/install/setup.bash; ros2 run px4_ros_com offboard_control'
```

> [!tip] Start order
> The example sends **one** arm command about a second after it starts. Start it only after PX4 prints `Ready for takeoff!`. If it was too early, the quad stays on the ground: stop the node and start it again.

Expected: `Arm command send`, then `Armed by external command` and `Takeoff detected` in the PX4 log. `ros2 topic echo --once /fmu/out/vehicle_local_position_v1 --field z` gives about `-5.0` (NED: negative is up, so 5 m).

## 5. Result (2026-10-15)

| Acceptance criterion (#29) | State |
|---|---|
| PX4 SITL + Gazebo on Ubuntu 24.04 | Done (24.04 container) |
| Default quad flies in sim | Done, headless and with GUI |
| Offboard from the ROS 2 example | Done, z = −5.003 m |
| Setup steps in the vault | This page |

## 6. Gazebo GUI

Works on this host (X11, `DISPLAY=:0`) with **software rendering**, because `nvidia-smi` reports a driver/library mismatch on the laptop:

```sh
xhost +local:docker
docker run -d --name px4sim --network host -e DISPLAY=$DISPLAY -e LIBGL_ALWAYS_SOFTWARE=1 \
  -v /tmp/.X11-unix:/tmp/.X11-unix --device /dev/dri seame-px4-sim:jazzy-1.17 sleep infinity
```

Then start the agent and `make px4_sitl gz_x500` as in §3, **without** `HEADLESS=1`. The "Gazebo Sim" window opens and the quad flew to 5 m while it ran. Software rendering is slow, so expect a low frame rate. Visually confirmed on 2026-10-15: the quad climbs in the Gazebo window.

## 6.1 Known gaps

- The `px4io/px4-sitl` image on the PX4 quickstart page is headless without Gazebo or ROS 2. It is a smoke test only.
- Clean up with `docker rm -f px4sim`.

## 7. Related pages

- [[ADR-019 Sim Backends|ADR-019]] · [[ADR-018 Ground OS and ROS 2|ADR-018]] · [[Study Plan v2]] · [[Testing Strategy]]

## 8. Glossary

| Term | Meaning |
| --- | --- |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **Gazebo** | The 3D physics simulator used with PX4 (Harmonic release). |
| **x500** | PX4's default simulated quadcopter. |
| **XRCE-DDS** | Protocol and agent that bridge PX4 uORB topics to ROS 2. |
| **Offboard** | PX4 mode where an external computer streams setpoints. |
| **NED** | North-East-Down frame: negative z is up. |
| **GCS** | Ground Control Station, e.g. QGroundControl. |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
