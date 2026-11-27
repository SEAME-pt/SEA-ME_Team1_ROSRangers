---
title: Final Connections and Missing Parts
type: architecture
status: draft
date: 2026-10-16
updated: 2026-11-27
project: SEA autonomous drone
tags:
  - gestures
  - ros2
  - roadmap
aliases:
  - Future Nodes
---

# 🔭 Final connections and missing parts

What the gesture chain still needs before a gesture moves the real drone (§2), and nodes that would make it better later (§3). Turn an item into work with the Spike or Task issue form.

## 🗺️ 1. Overview

| § | Part | Why | Status |
| :---: | :--- | :--- | :---: |
| 2.1 | controller | velocity target → attitude setpoints | ==🟢built, simulation only== (#199); on the drone Betaflight holds position |
| 2.2 | PX4 backend for `flight_interface` | the simulator through the real node | ==🟢built== (#199) |
| 2.3 | gestures driving `flight_interface` in the simulator | the whole chain end to end | ==🟡runs== (`make sim`); ==🟠webcam tests== (#202) |
| 2.4 | `override_active` from the ESP | the gate in `mission_node` and `flight_interface` | ==🟢built== (#176) |
| 2.5 | `gesture_node` leftovers | ADR and the MediaPipe Tasks API | ==🟠to do== |
| 3.1 | `camera_node` | camera frames as a ROS topic | ==🟣idea, optional== |
| 3.2 | `drone_msgs` | typed messages instead of JSON strings | ==🟠proposed== |
| 3.3 | motion tracker | wave / swipe gestures | ==⚪not planned== |
| 3.4 | trained classifier | replace the rule-based `recognize()` | ==🟠Module 2== |
| 3.5 | car / arm adapter | portability proof | ==🟠Module 2== |
| 3.6 | operator display | show mode and reason to the operator | ==🟣idea== |

---

## 🔗 2. Missing connections

```mermaid
flowchart LR
  M[mission_node] -->|"/mission/velocity_target"| C["controller_node<br/>(2.1)"]
  C -->|"/control/attitude_setpoint"| FI[flight_interface]
  FI -->|esp| ESP([ESP gateway])
  FI -->|"px4 (2.2)"| PX4([PX4 SITL])
  FI -->|"/estimate/velocity<br/>(sim)"| C
```

### 2.1 Controller

Turns `/mission/velocity_target` and the measured velocity into attitude and throttle on `/control/attitude_setpoint`, which `flight_interface` sends. Its contract: [[ADR-025 flight_interface Ground Node|ADR-025]] C1 and §2.4.

Controller built (#199): `controller_node`, [[07 Controller Node]]. It flies the simulation, where the `px4` backend gives the measured velocity; `make sim-hold` holds 60 s within REQ-NAV-001. It is simulation-only: on the real drone nothing publishes `/estimate/velocity`, so it stays inactive. There, Betaflight's Position Hold holds position with the flow sensor ([[ADR-016 Flow Sensor Path|ADR-016]]), and gestures will move the drone by nudging it: a roll or pitch deflection beyond Betaflight's `pos_hold_deadband` becomes a target velocity, and on release the drone brakes and holds the new point. That needs a roll / pitch mask and the FC's override mask set on the drone, and is flown only after the cage ([[ADR-025 flight_interface Ground Node|ADR-025]] F29).

### 2.2 PX4 backend for `flight_interface`

Built (#199). The `px4` backend sends the attitude setpoints to PX4 SITL (or PX4's own position hold while nothing commands), publishes `/flight/state` from PX4 and the measured velocity on `/estimate/velocity`. It runs only in the simulation image of `flight_interface`, as the service `flight_interface_sim` of `make sim` ([[PX4 SITL Setup]] §7, [[05 Flight Interface Node|05]], [[ADR-019 Sim Backends|ADR-019]]).

### 2.3 Gestures driving `flight_interface` in the simulator

The whole chain, webcam → gesture → intent → mission → controller → `flight_interface` → PX4, with the gesture speed limits, hand loss → hover, and no gesture ever arming, each checked by a test. The chain runs (`make sim`, #199), and `make sim-smoke` checks it with scripted gestures; the webcam tests are #202.

### 2.4 `override_active` from the ESP

Built (#176, PR #198). The ESP reports whether the pilot has handed control to the ground (from `MSP_BOXIDS` and `MSP_STATUS`), and the ground's `override_active` is true only when the ESP reports it on, never when it is unknown. While it is false, `mission_node` stays `IDLE` and `flight_interface` sends only neutral ([[05 Flight Interface Node|05]] §5).

### 2.5 `gesture_node` leftovers

`gesture_node` is built ([[02 Gesture Node]]). Still to do: the ADR that amends [[ADR-018 Ground OS and ROS 2|ADR-018]] (MediaPipe inside a ROS 2 node), and a port of Holistic to MediaPipe's Tasks API before moving past `mediapipe==0.10.21`.

---

## 🧩 3. Nodes to add later

### 3.1 `camera_node`

Publishes the webcam as `/image_raw`, only if we need to see or record the picture. It would replace the capture inside `gesture_node` (one webcam, one reader) and cost a frame copy between processes. An existing package (`v4l2_camera` or `usb_cam`) in its own container.

### 3.2 `drone_msgs`

A message package so `/gesture/intent`, `/mission/state` and `/flight/state` are typed instead of JSON strings. Must be an `ament_cmake` package; a message change is a contract change between tracks.

### 3.3 Motion tracker

Not planned: the vocabulary uses static poses only ([[00 Gesture Pipeline]] §3).

### 3.4 Trained classifier (Module 2)

A classifier on the hand landmarks instead of `recognize()`, with the same output names so `intent_node` doesn't change ([[Roadmap to April 2027]] §4).

### 3.5 Car / arm adapter (Module 2)

The same `/gesture/intent` driving a car (`cmd_vel`) or an arm, with its own limits ([[Roadmap to April 2027]] §4).

### 3.6 Operator display

Shows the mode, intent and reason from `/mission/state` to the operator. Display only, never sends commands.

---

## Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Backend** | The part of `flight_interface` that talks to one kind of drone: `loopback`, `esp`, `px4` (simulation only). |
| **Bringup package** | A ROS 2 package that holds only launch and parameter files to start a whole system. |
| **Compose profile** | A named group of services in `docker-compose.yml`, started together with `--profile`. |
| **JSON** | A text format for structured data, used inside the gesture topics. |
| **Launch file** | A Python file that starts several nodes with their parameters (`ros2 launch`). |
| **MediaPipe** | A Google framework used here to detect the body pose and hand landmarks (Holistic). |
| **Override** | MSP override: the pilot's switch that hands chosen stick channels to the ground. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **Spike** | A short, time-boxed investigation to answer a question before building. |
| **Tasks API** | MediaPipe's newer API that replaces the removed `mp.solutions`. |
| **Attitude setpoint** | Roll, pitch, yaw rate and throttle: what the controller asks the drone to hold. |
| **Controller** | `controller_node`: turns the velocity target into an attitude setpoint ([[07 Controller Node]]). |
| **Measured velocity** | The velocity the drone really has, on `/estimate/velocity`: from PX4, in the simulation only. On the real drone nothing publishes it: Betaflight's Position Hold holds position itself. |
| **PX4** | An open-source flight-control stack, used here only in simulation (PX4 SITL + Gazebo). |
| **REQ ID** | A requirement identifier, `REQ-<area>-NNN`, registered in [[Requirements]]. |
