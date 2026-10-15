---
title: ADR-017 — Branching and Merge Model
type: adr
status: accepted
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - D17
  - D17 Branching Model
tags:
  - adr
  - git
  - ci
---

# ADR-017 — Branching and Merge Model

> [!success] Status: ACCEPTED: the team's model in [[Dev_Strategy]] is authoritative
> This replaces the earlier GitHub Flow suggestion. Instructors confirmed that "Git flow" in the program README is not a mandate.

## 1. Decision

Follow [[Dev_Strategy]] §4–§9 exactly:

- Permanent branches **`main`** (stable) and **`dev`** (integration). No direct pushes to either.
- Work happens on `feature/<issue>-<topic>` and `bugfix/<issue>-<topic>` branches ([[Feature_and_Bugfix_Branches]]).
- Feature → `dev`: **squash merge**. `dev` → `main`: **merge commit** ([[Merge_Strategy]]).
- Rebase only your own branches ([[Rebase_Policy]]).
- Protection: `dev` needs CI and a reviewer. `main` needs CI and **two approvals** ([[Branch_Protection]]).
- Stable versions are tagged `vX.Y.Z` on `main` ([[Dev_Strategy]] §20).

## 2. Consequences

- CI runs on PRs to `dev` and `main` and on pushes to both ([[GitHub Actions Starter]]).
- The HIL job runs on pushes to `dev` and `main`, never on PRs from forks ([[HIL Runner]]).
- Track rule in [[Team Tracks]]: reviewers come from outside the author's track.

## 3. Related pages

- [[Dev_Strategy]] · [[Pull_Request_Workflow]] · [[Decision Register]]

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Branch** | An independent line of development in version control. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **PR** | Pull Request: a proposed change submitted for review before merging. |
