---
title: Weekly Rituals
type: process
status: active
date: 2026-10-12
project: SEA autonomous drone
tags:
  - plan
  - process
---

# Weekly Rituals

---

## 1. Cadence

| When | What | Output |
|---|---|---|
| Daily 15 min | Stand-up: yesterday, today, blockers | GitHub Issues board updated |
| Daily last hour | Documentation | Notes in this vault, logs, photos, commit |
| Wed 30 min | Decision review | New or updated ADRs in the [[Decision Register]] |
| Fri (every 2nd) | Sprint demo + retrospective | Demo video, retro notes |
| Every flight day | Pre-flight checklist ([[Safety Rules]]) | Flight-test report (Obsidian, per [[Dev_Strategy]] §3) |
| Daily, at stand-up | **Drone schedule.** There is one drone for A (flights), B (link tests) and D (HIL). Book slots, e.g. A in the morning and B/E in the afternoon. After a B or D session, restore the config from `drone/config/betaflight` and check it with `compare-diffs` before anyone flies. | Slots in the stand-up notes |

## 2. Weekly checklist

- [ ] Week goal met (or reason written)
- [ ] CI green on `dev`
- [ ] Coverage trend not decreasing
- [ ] Incidents and crashes logged as GitHub Issues (program rule)
- [ ] Measurements saved (latency, jitter, flow error)
- [ ] Safety reviewed
- [ ] Vault updated, board refreshed (Status, Sprint, Phase fields)

## 3. Glossary

| Term | Meaning |
| --- | --- |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
