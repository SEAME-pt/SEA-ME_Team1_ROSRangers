---
title: GitHub Actions Starter
type: ci-cd
status: draft
date: 2026-10-20
project: SEA autonomous drone
tags:
  - ci
  - github-actions
---

# GitHub Actions Starter

> [!info] The workflow files are the source of truth
> The first draft of the YAML that used to be copied here drifted from the repo (paths now start with `drone/`, every job is gated by a component check). It was removed on 2026-10-20 so there is one copy only. Read the real files; this page summarises them.

---

## 1. `.github/workflows/ci.yaml`

File: `.github/workflows/ci.yaml`. Runs on PRs into `dev` and `main` and on pushes to both, as required by [[Dev_Strategy]] §11. A new push to the same ref cancels the running one.

| Job | What it does | Runs when |
| --- | --- | --- |
| `lint` | `pre-commit run --all-files` | always |
| `check-components` | Sets outputs `cpp`, `python`, `esp`, `sitl` from which files exist and prints a `::notice` for each skip | always |
| `protocol-cpp` | CMake + Ninja build, GoogleTest, gcovr coverage gate, clang-tidy, cppcheck | `drone/lib/protocol/CMakeLists.txt` exists |
| `ground-python` | pytest with a coverage gate on `sd_protocol_py` | `drone/ground/src/sd_protocol_py/pyproject.toml` exists |
| `esp-firmware` | PlatformIO build of env `d1_mini`, uploads `firmware.bin` | `drone/firmware/esp8266/platformio.ini` exists |
| `betaflight-sitl` | Builds Betaflight SITL, runs `pytest drone/tests/sitl` against it | both components above, and at least one `*.py` in `drone/tests/sitl` |

Adding a component turns its job on immediately, so ship at least one real test with it: an empty pytest or coverage gate fails.

> [!note] The SITL build
> Betaflight's Makefile insists on an `arm-none-eabi-gcc` version even for SITL, which actually builds with the host gcc. A three-line shim fakes only that version probe. This built 2026.6.2 SITL in about 25 s (verified). 4.5.1 SITL fails to link out of the box, which is another reason for [[ADR-014 Betaflight Version|ADR-014]].

## 2. `.github/workflows/hil.yaml`

File: `.github/workflows/hil.yaml`. Runs on pushes to `dev`/`main` and manually. It **never** runs on PRs from forks: a self-hosted runner executes the workflow's code on our Pi ([[HIL Runner]]).

- `check-hil` (GitHub-hosted) enables the job only when `drone/firmware/esp8266/platformio.ini` and a `*.py` test in `drone/tests/hil` exist.
- `hil` (runner labels `self-hosted, linux, ARM64, hil-drone`, 20 min timeout) builds env `d1_mini_ota`, flashes the bench D1 mini over the air (USB data never touches UART0), runs `pytest drone/tests/hil` and uploads `hil-report.xml`. No battery, no props.

## 3. `.github/workflows/close-linked-issues.yaml`

File: `.github/workflows/close-linked-issues.yaml`. GitHub closes linked issues by itself only when a PR merges into the default branch (`main`). Our PRs target `dev`, so this workflow closes the issues named with `Closes #N` when a PR into `dev` merges.

## 4. Other files

- `drone/firmware/esp8266/platformio.ini`: envs `d1_mini` (USB, first flash or recovery, **FC wires unplugged**) and `d1_mini_ota` (Wi-Fi).
- `.pre-commit-config.yaml`: hooks listed in [[CI-CD Overview]] §3. Pin versions with `pre-commit autoupdate`.
- `.github/pull_request_template.md`: the PR sections from [[Dev_Strategy]] §17 plus a safety line.

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Branch** | An independent line of development in version control. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **PR** | Pull Request: a proposed change submitted for review before merging. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
