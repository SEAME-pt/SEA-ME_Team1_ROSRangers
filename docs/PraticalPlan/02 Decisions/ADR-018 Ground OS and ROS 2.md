---
title: ADR-018 — Ground Computer OS and ROS 2
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D18
  - D18 Ground OS and ROS 2
tags:
  - adr
  - ros2
  - ground
---

# ADR-018 — Ground Computer OS and ROS 2

> [!success] Status: ACCEPTED (team, 2026-10-12). Instructors confirmed no distro is mandated (2026-10-14)

## 1. Decision

- **Ubuntu 24.04 LTS + ROS 2 Jazzy (LTS)**, the same image on every laptop and in CI (`ros:jazzy` container).
- Ubuntu 24.04 ships Python **3.12**, which fits the **3.10–3.12** requirement of [[01 Gesture Recognition with OpenCV and MediaPipe|Gesture Recognition Program]] (`mediapipe==0.10.21`).

## 2. Consequences

- MediaPipe pulls `numpy` and OpenCV with pip. These can clash with the apt-installed ROS packages (`cv_bridge`). Keep the gesture recogniser in **its own venv/process** and talk to ROS 2 through a thin node ([[00 Gesture Pipeline|Gesture Pipeline]]).
- PX4 SITL + Gazebo support for this exact combination must be checked on the official pages before installing (not verified here).

## 3. Related pages

- [[00 Gesture Pipeline|Gesture Pipeline]] · [[Decision Register]]

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
