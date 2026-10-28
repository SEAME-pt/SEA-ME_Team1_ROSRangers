---
title: HIL Overview
type: ci-cd
status: draft
date: 2026-10-28
project: SEA autonomous drone
owner: Track D
tags:
  - ci
  - hil
  - safety
---

# HIL Overview: what the hardware-in-the-loop job does and why

> [!danger] No LiPo on the HIL bench, ever ([[Safety Rules]] #10).
> The bench may run unattended only with no FC, no LiPo and no motors ([[Safety Rules]] #17).

> [!info] Start here. The build and setup details are in [[HIL Runner]], the decision in [[ADR-020 HIL Runner|ADR-020]].

---

## 1. What this is

Most of our CI runs on GitHub's cloud machines. They can build the firmware and run unit tests, but they have no hardware attached. The HIL job runs on a machine we own: a Raspberry Pi 4 with a real ESP8266 plugged into it. On every merge to `dev` it **builds our ESP firmware, flashes it onto that real board and runs tests against it**.

The tests are in `drone/tests/hil/`. The workflow is `.github/workflows/hil.yaml`.

## 2. Why we need it

1. **The program requires it.** SEA:ME Module 1 lists as a deliverable a "green CI/CD pipeline … hardware-in-the-loop job on a self-hosted runner", described as "self-hosted hardware-in-the-loop runners that flash and test the target" (`SEAME-pt/seame-2.0`, M1 README). It is part of the Module 1 delivery (`v0.1.0`, 11 Dec 2026, [[Roadmap to April 2027]]).
2. **Some bugs only show up on the real chip.** The ESP is the bridge between the ground computer (Wi-Fi) and the FC (UART, MSP). A build that compiles and passes unit tests can still crash at boot, hit the watchdog or reboot in a loop on the real ESP8266. HIL catches that before the firmware goes near the drone.
3. **We have to build it ourselves.** The program lists lab runners as shared equipment, but the instructors confirmed on 2026-10-14 that none exist ([[ADR-020 HIL Runner|ADR-020]]).

## 3. Where it sits in our test levels

| Level | What | Where |
|---|---|---|
| 1 Unit | C++ / Python logic | GitHub-hosted (GoogleTest, pytest) |
| 2 Integration | golden vectors, MSP against Betaflight SITL | GitHub-hosted |
| 3 Simulation | PX4 SITL + Gazebo | team laptops |
| **4 HIL** | **real ESP, flashed by CI** | **Pi 4 runner `hil-drone`** |
| 4 Bench (manual) | flight unit, props off | lab, booked slot |
| 5 Flight | cage, safety pilot | lab |

Full table: [[Testing Strategy]] §1.

## 4. Scope (decided 2026-10-26, GitHub #61)

The team has one spare ESP and **no spare FC**, so:

- **The HIL target is the spare ESP only.** It is the same board as the flight ESP.
- **The FC side is tested in simulation:** Betaflight 2026.6.2 SITL for MSP behaviour and PX4 SITL + Gazebo for flight behaviour ([[ADR-019 Sim Backends|ADR-019]]).
- **Tested by hand, not by HIL** (props off, booked slot): the wired connections, the kill switch, the radio failsafe, how the real FC reacts to MSP Override (GitHub #104) and the real-time profile.
- The flight unit is **never** connected to the HIL bench.

## 5. The bench (as built, GitHub #48)

```text
GitHub Actions ──(lab Wi-Fi)──► Raspberry Pi 4 "hil-drone"
                                    │ USB (FTDI FT232R, /dev/hil-esp)
                                    ▼
                                spare ESP8266 (ESP-12F)
                                not wired to any FC, no battery, no motors
```

| Item | Value |
|---|---|
| Runner | `hil-drone-pi`, labels `self-hosted, linux, ARM64, hil-drone`, systemd service |
| Board | ESP8266EX, 4 MB flash, PlatformIO board `esp12e`, FTDI `0403:6001` (not CH340) |
| Device | `/dev/hil-esp` via a udev rule matched on the FTDI serial |
| Flashing | Over USB with auto-reset (DTR/RTS). No button press is needed. |
| SoftAP | Its own SSID and passwords (`SEAME_*_HIL` secrets), never the drone's |

USB flashing is allowed **only because this ESP is not wired to an FC**. Any ESP wired to an FC is flashed over the air only ([[ADR-012 ESP UART Pins|ADR-012]]: USB and the FC share UART0).

The original firmware backup (SHA-256 `b063fb4c…242f7e`) is kept off the repo because it may contain Wi-Fi credentials. See `drone/firmware/esp8266/hil_esp/README.md`.

## 6. What the job does

Trigger: push to `dev` or `main`, or manual (`workflow_dispatch`). **Never on pull requests.**

1. `check-hil` (GitHub-hosted) skips the job unless `drone/firmware/esp8266/platformio.ini` and a test in `drone/tests/hil/` exist.
2. `hil` (on the Pi, one job at a time, `concurrency: hil-bench`):
   - checks that `/dev/hil-esp` exists and that the SoftAP SSID secret is set
   - builds the `esp` firmware environment
   - flashes it to `/dev/hil-esp` over USB
   - runs `pytest drone/tests/hil` with `HIL_REQUIRED=1`, so a missing board is a failure, not a skip
   - uploads `hil-report.xml` (JUnit) as an artifact

> [!note] First real run
> Until #116 (2026-10-28) added `platformio.ini`, every `hil` run was skipped by `check-hil`. Those green runs did not touch hardware. The first run with the real job is the one triggered by #116.

## 7. Tests

### 7.1 Today: `test_smoke.py`

After the flash, the ESP is reset over RTS. The test then checks:

- the ROM banner (74880 baud) shows `boot mode:(3,…)`, a normal boot from flash
- the banner has no crash markers (exception, soft WDT, `rst cause:4`)
- UART0 stays **silent for 10 s** at 115200. Our firmware never prints on UART0, so any bytes there mean a crash dump or a reboot.

Off the bench (no board), the hardware test is skipped and only the parser tests run.

### 7.2 Planned (GitHub #106, Module 1)

| Test | Checks | Depends on |
|---|---|---|
| Passthrough | allowed MSP messages go through, all others are dropped | gateway firmware #101 |
| Failsafe timing | link loss detected at 250 ms ± tolerance ([[ADR-004 ESP Failsafe\|ADR-004]]) | state machine #102 |

Listed in [[HIL Runner]] §4 but not in an issue yet: security (bad tag or stale sequence increases the counters) and latency (RTT histogram as an artifact).

Wi-Fi tests will **time-slice** the Pi's only radio: leave the lab Wi-Fi, join the spare ESP's SoftAP, test, re-join. Each window stays under 2 minutes. A watchdog outside the job re-joins the lab Wi-Fi if the job dies ([[ADR-020 HIL Runner|ADR-020]], 2026-10-26).

## 8. Role in the pipeline

HIL runs after each merge to `dev`. It must be green before `dev` → `main` ([[ADR-017 Branching Model|ADR-017]], [[ADR-020 HIL Runner|ADR-020]]). **This gate is not enforced yet:** no required checks exist ([[ADR-022 CI Gates and Lint Rules|ADR-022]]).

## 9. Security

A self-hosted runner executes whatever code the workflow gives it, and the repo is public. So:

- the HIL job never runs on `pull_request`
- only collaborators can open PRs (interaction limit until 2027-04-26, **renew it**)
- workflows from fork PRs need approval for all external contributors

## 10. Maintenance

- Before touching the board by hand, stop the runner (`sudo ~/actions-runner/svc.sh stop`) so a CI job cannot grab the port. Start it again afterwards.
- If the Pi is unpowered or offline, `hil` jobs wait in the queue until it comes back.
- GitHub deletes a runner that has been offline for more than 14 days.
- Backup and restore commands: `drone/firmware/esp8266/hil_esp/README.md`.

## 11. Related pages

- [[HIL Runner]] · [[ADR-020 HIL Runner|ADR-020]] · [[CI-CD Overview]] · [[Testing Strategy]] · [[Safety Rules]]

## 12. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **OTA** | Over-the-air update: flashing firmware through Wi-Fi instead of USB. |
| **Self-hosted runner** | A machine of ours (the Pi 4 `hil-drone`) that runs GitHub Actions jobs, used for the HIL job. |
| **SITL** | Software-in-the-loop: the real flight firmware running as a program on a PC. |
| **SoftAP** | Software access point: the ESP creates its own Wi-Fi network. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **udev** | The Linux device manager. A udev rule gives a USB device a fixed name such as `/dev/hil-esp`. |
