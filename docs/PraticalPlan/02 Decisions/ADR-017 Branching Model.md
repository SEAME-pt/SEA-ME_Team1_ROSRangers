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

### Update 2026-10-20: review counts

Team decision: **1 approving review** on PRs into `dev`, **2 approving reviews** on PRs from `dev` into `main`. Reviewers still come from outside the author's track ([[Team Tracks]] §2). This replaces the earlier wording "two reviewers on every PR" in [[Team Tracks]] and "a reviewer where practical" in [[Dev_Strategy]] §9. Aligned in [[Branch_Protection]] and [[Pull_Request_Workflow]]. The GitHub branch-protection settings must match (`dev`: 1 required approval, `main`: 2).

### Update 2026-10-21: path filter for the slow ground jobs

§2 says CI runs on PRs and on pushes. That is still true for the workflow. Inside it, on a PR, `ground-ros2` and `ground-docker` now run only when the PR changes `drone/ground/`, `drone/tests/ros/` or `.github/workflows/ci.yaml`. On pushes to `dev` and `main` the path filter never skips a job; a job is skipped only when its component is missing. The filter is an `if:` on the job, not `on: paths`, so a skipped job still counts as passed for a required check. Decided in [[ADR-022 CI Gates and Lint Rules|ADR-022]] (#73).

## 3. Related pages

- [[Dev_Strategy]] · [[Pull_Request_Workflow]] · [[ADR-022 CI Gates and Lint Rules|ADR-022]] · [[Decision Register]]

## 4. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Branch** | An independent line of development in version control. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **PR** | Pull Request: a proposed change submitted for review before merging. |
