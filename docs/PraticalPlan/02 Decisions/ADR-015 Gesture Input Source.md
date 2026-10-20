---
title: ADR-015 — Gesture Input Source
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D15
  - D15 Gesture Input Source
tags:
  - adr
  - gestures
  - safety
---

# ADR-015 — Gesture Input Source

> [!success] Status: ACCEPTED (team, 2026-10-12)

## 1. Context

- The gesturing person is in the room where we code, inside the building, **not inside the cage**.
- [[01 Gesture Node|Gesture Recognition Program]] already works on a **laptop webcam** (MediaPipe + OpenCV).
- The drone's analog camera (Ratel 2 → VT5804 VTX → MS210x USB grabber) cannot see the operator.

## 2. Decision

Gestures come from a **webcam on the ground computer**. The drone's video is used for Module 2 perception (detector → target coordinates), not for gestures.

## 3. Consequences

> [!danger] Safety
> The gesture operator may not see the drone. The **safety pilot stands at the cage** with line of sight and the ELRS radio. Gestures can never arm, disarm the drone or override the pilot.

- The gesture loop latency excludes the analog video link.

## 4. Related pages

- [[00 Gesture Pipeline|Gesture Pipeline]] · [[01 Gesture Node|Gesture Recognition Program]] · [[Safety Rules]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **OpenCV** | A computer-vision library used here to read the webcam and draw the video window. |
