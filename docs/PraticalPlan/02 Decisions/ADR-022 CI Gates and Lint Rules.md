---
title: ADR-022 — CI Gates and Lint Rules
type: adr
status: proposed
date: 2026-10-21
project: SEA autonomous drone
aliases:
  - D22
  - D22 CI Gates and Lint Rules
tags:
  - adr
  - ci
  - ros2
---

# ADR-022 — CI Gates and Lint Rules

> [!info] Status: PROPOSED (2026-10-21, GitHub #73, PR #74). Accepted when PR #74 is approved and merged into `dev`
> Taken while preparing `ci.yaml` for the ROS 2 ground nodes (`gesture_node`, `intent_node`), from the review of `drone/ground` on 2026-10-21. Amends the CI consequence of [[ADR-017 Branching Model|ADR-017]] (see its update of 2026-10-21).

## 1. Context

- The ground nodes come with 108 tests at 98 % line coverage (measured on 2026-10-21 in the `ros:jazzy-ros-base` container on `feature/ros2nodes`). CI had no coverage gate, no time limit and no test report for them.
- `ground-ros2` and `ground-docker` are the slowest jobs. `ground-docker` builds the images with MediaPipe from scratch on every run, so docs-only PRs paid for it too.
- GitHub ends a hung job only after **6 h**.
- ruff ran with its default rules only (`E4`, `E7`, `E9`, `F`).

## 2. Options considered

| Topic | Options | Chosen |
|---|---|---|
| Skipping the slow jobs | `on: paths` filter on the workflow · filter inside `check-components` (`if:` on the job) | **Inside `check-components`.** A job skipped through `if:` counts as passed for a required check. A workflow that never starts leaves the check pending, and that blocks the merge. |
| How to run the ROS 2 tests | Only `colcon test` · only pytest with coverage · both | **Both.** `colcon test` runs the tests the way ROS does. A pytest step adds the gate and the pipeline tests (about 40 s more). |
| ruff rules | Defaults · `E, W, F, B, I` · `E, W, F, B, I` without `E501` | **`E, W, F, B, I` without `E501`.** `ruff-format` already wraps code at 88 columns. `E501` would only flag long comments and strings (74 lines on 2026-10-21, 73 of them in `scripts/`). |

## 3. Decision

1. **Time limits.** Every job in `ci.yaml` has `timeout-minutes`, from 5 to 30 min. New jobs get one too.
2. **Coverage gates.** Line coverage of at least **90 %** for `gesture_node` + `intent_node`, measured over the package and pipeline tests (`drone/tests/ros`). The 80 % gates for the C++ library and `sd_protocol_py` stay as they are. `ground-ros2` uploads its JUnit report (artifact `ground-ros2-report`), even when a test fails.
3. **Path filters live inside the jobs.** On a PR, `ground-ros2` and `ground-docker` run only when it changes `drone/ground/`, `drone/tests/ros/` or `.github/workflows/ci.yaml`. On pushes to `dev` and `main` the path filter never skips a job; a job is skipped only when its component is missing. Never use `on: paths`.
4. **Lint.**
   - Ruff rules are set repo-wide in `ruff.toml`: `select = ["E", "W", "F", "B", "I"]`, with `E501` ignored.
   - `gesture_node`, `intent_node` and `sd_protocol` sort as first-party imports.
   - Shell scripts are checked by `shellcheck` (`--severity=warning`).
   - Both run in pre-commit, so in the `lint` job.

## 4. Consequences

- A docs-only PR no longer builds the Docker images. After the merge, the push to `dev` runs every job whose component exists.
- A branch that adds Python code must pass the stricter ruff. `ruff check --fix` sorts imports. `feature/ros2nodes` needs it once, for one import-order finding.
- The 90 % gate applies once the ground nodes are on `dev`. Until then, `check-components` skips both jobs with a `::notice`.
- **Deferred** (see [[CI-CD Overview]] §4):
  - Run `ground-ros2` inside the gesture image.
  - Pin `ros:jazzy-ros-base` by digest, with Dependabot.
  - Docker layer cache.
  - hadolint.
  - Required checks on `dev`.

  Each needs its own entry here when it is decided.

## 5. Related pages

- [[CI-CD Overview]] · [[GitHub Actions Starter]] · [[Testing Strategy]] · [[ADR-017 Branching Model|ADR-017]] · [[ADR-018 Ground OS and ROS 2|ADR-018]] · [[Decision Register]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **colcon** | The build tool for ROS 2 workspaces; `colcon test` runs each package's tests. |
| **Dependabot** | GitHub bot that opens PRs to update pinned versions (actions, images, packages). |
| **Digest** | A content hash (`sha256:…`) that pins one exact Docker image, unlike a tag such as `jazzy-ros-base`. |
| **Docker** | Tool that runs software in containers: isolated environments built from an image (Dockerfile). |
| **hadolint** | Linter for Dockerfiles. |
| **MediaPipe** | A framework used here for detecting and interpreting hand landmarks and gestures. |
| **pre-commit** | Tool that runs lint and format hooks on the repo; the same checks run in CI as `lint`. |
| **pytest** | Python test runner; with `pytest-cov` it measures coverage and enforces the gate. |
| **Coverage gate** | A CI step that fails when tests run less than a set share of the code (80 % C++/Python, 90 % ROS 2 nodes). |
| **JUnit report** | An XML file listing each test and its result, which CI tools can display. |
| **Path filter** | A rule that runs a CI job only when a PR changes certain files. |
| **PR** | Pull Request: a proposed change submitted for review before merging. |
| **Required check** | A CI job that must pass before a PR can be merged (set in the repo's branch protection). |
| **ROS 2** | The second-generation Robot Operating System middleware used for robotics integration. |
| **ruff** | Python linter and formatter (`ruff` and `ruff-format` hooks). |
| **shellcheck** | Linter for shell scripts (`.sh`), run as a pre-commit hook. |
