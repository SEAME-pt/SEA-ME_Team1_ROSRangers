---
title: ADR-015 — Gesture Input Source
type: adr
status: accepted
date: 2026-10-12
updated: 2026-10-27
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
- [[02 Gesture Node|Gesture Recognition Program]] already works on a **laptop webcam** (MediaPipe + OpenCV).
- The drone's analog camera (Ratel 2 → VT5804 VTX → MS210x USB grabber) cannot see the operator.

## 2. Decision

Gestures come from a **webcam on the ground computer**. The drone's video is used for Module 2 perception (detector → target coordinates), not for gestures.

## 3. Consequences

> [!danger] Safety
> The gesture operator may not see the drone. The **safety pilot stands at the cage** with line of sight and the ELRS radio. Gestures can never arm, disarm the drone or override the pilot.

- The gesture loop latency excludes the analog video link.

### Update 2026-10-27: two hands, MediaPipe Holistic and the elbow rule

The decision does not change: gestures still come from the ground webcam. How they are read changed with #81 (PR #120):

- `gesture_node` uses **MediaPipe Holistic** instead of MediaPipe Hands. It finds the body pose and both hands, each labelled as the operator's left or right ([[02 Gesture Node]] §3.1).
- **Elbow rule:** a hand counts only when its wrist is above its elbow. A lowered arm is ignored, and both arms down means "no hand", so the drone hovers ([[02 Gesture Node]] §3.2).
- The vocabulary is two-handed: both open hands engage, both fists held 1 s lock and unlock, one raised hand gives the motion commands ([[00 Gesture Pipeline]] §3).
- The camera must see the operator's shoulders and elbows, so the operator stands further from the webcam. Holistic is slower: 14.5 FPS measured on 2026-10-27.
- The safety consequence above still holds: no gesture arms, disarms or overrides the pilot. Since the same change no gesture or timeout asks for a landing either; the operator gone means hover ([[04 Mission Node]] §10, waiting for a team review).

## 4. Related pages

- [[00 Gesture Pipeline|Gesture Pipeline]] · [[02 Gesture Node|Gesture Recognition Program]] · [[Safety Rules]]

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Holistic** | MediaPipe Holistic: one model for the body pose and both hands. Used by `gesture_node` since 2026-10-27. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **Elbow rule** | A hand counts only when its wrist is above its elbow in the image. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **OpenCV** | A computer-vision library used here to read the webcam and draw the video window. |
