---
title: Future nodes to add
type: architecture
status: draft
date: 2026-10-16
updated: 2026-10-27
project: SEA autonomous drone
tags:
  - gestures
  - ros2
  - roadmap
---

# 🔭 Future nodes to add

Nodes the gesture chain will need later, or that would make it better. Nothing here is decided except §2. Turn an item into work with the Spike or Task issue form.

## 🗺️ 1. Overview

| § | Node or package | Why | Status |
| :---: | :--- | :--- | :---: |
| 2 | `gesture_node` | recogniser inside ROS 2 | ==🟢built, ADR still needed== |
| 3 | `camera_node` | camera frames as a ROS topic | ==🟣idea, optional== |
| 4 | `drone_msgs` | typed messages instead of JSON strings | ==🟠proposed== |
| 5 | controller + estimator | velocity target → attitude setpoints | ==🟠planned, Module 1== |
| 6 | `flight_interface` | ground ↔ drone link | ==🟠planned, Module 1== |
| 7 | motion tracker | wave / swipe gestures | ==⚪not planned== |
| 8 | trained classifier | replace the rule-based `recognize()` | ==🟠Module 2== |
| 9 | car / arm adapter | portability proof | ==🟠Module 2== |
| 10 | operator display | show mode and reason to the operator | ==🟣idea== |
| 11 | `gesture_bringup` | one launch file and one parameter file | ==🟠proposed== |

---

## 🖐️ 2. `gesture_node` (built)

See [[02 Gesture Node]]. Still to do: the ADR that amends [[ADR-018 Ground OS and ROS 2|ADR-018]] (MediaPipe inside a ROS 2 node), and a port of Holistic to MediaPipe's Tasks API before moving past `mediapipe==0.10.21`.

## 📷 3. `camera_node`

Publishes the webcam as `/image_raw`, only if we need to see or record the picture. It would replace the capture inside `gesture_node` (one webcam, one reader) and cost a frame copy between processes. An existing package (`v4l2_camera` or `usb_cam`) in its own container.

## 📨 4. `drone_msgs`

A message package so `/gesture/intent`, `/mission/state` and `/flight/state` are typed instead of JSON strings. Must be an `ament_cmake` package; a message change is a contract change between tracks.

## 🎛️ 5. Controller + estimator

Turns `/mission/velocity_target` and the estimated velocity (flow, IMU, baro) into attitude and throttle ([[04 Mission Node]] §6.5).

## 📡 6. `flight_interface`

A lifecycle node that sends setpoints to the ESP8266 ([[Protocol Spec v0]]) or to PX4 SITL ([[ADR-019 Sim Backends|ADR-019]]), and publishes the real `/flight/state` that `fake_flight_state` stands in for today.

## 👋 7. Motion tracker

Not planned: the vocabulary uses static poses only ([[00 Gesture Pipeline]] §3).

## 🧠 8. Trained classifier (Module 2)

A classifier on the hand landmarks instead of `recognize()`, with the same output names so `intent_node` doesn't change ([[Roadmap to April 2027]] §4).

## 🚗 9. Car / arm adapter (Module 2)

The same `/gesture/intent` driving a car (`cmd_vel`) or an arm, with its own limits ([[Roadmap to April 2027]] §4).

## 🖥️ 10. Operator display

Shows the mode, intent and reason from `/mission/state` to the operator. Display only, never sends commands.

## 🚀 11. `gesture_bringup`

One launch file and one reviewed parameter file for the whole chain, so the safety parameters ([[03 Intent Node]] §6, [[04 Mission Node]] §3.3) live in one place. Today `docker-compose.yml` starts the chain ([[01 ROS2 Nodes Installation|01]] §4).

---

## Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **JSON** | A text format for structured data, used inside the gesture topics. |
| **Launch file** | A Python file that starts several nodes with their parameters (`ros2 launch`). |
| **Lifecycle node** | A ROS 2 node with managed states (configure, activate, deactivate). |
| **MediaPipe** | A Google framework used here to detect the body pose and hand landmarks (Holistic). |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
| **Spike** | A short, time-boxed investigation to answer a question before building. |
| **Tasks API** | MediaPipe's newer API that replaces the removed `mp.solutions`. |
