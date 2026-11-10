---
title: HIL Runner
type: ci-cd
status: draft
date: 2026-10-12
updated: 2026-11-10
project: SEA autonomous drone
tags:
  - ci
  - hil
  - safety
---

# HIL Runner: what it is and how to build ours

> [!danger] Hardware work follows [[Safety Rules]]: props off on the bench, the radio and the safety pilot win over code, no LiPo on the HIL bench.

> [!info] Decision: [[ADR-020 HIL Runner|ADR-020]] · What it does and why: [[HIL Overview]]
> Instructors confirmed (2026-10-14) there are no lab self-hosted runners. We set up and own the Pi runner ourselves.

> [!warning] One spare ESP, no spare FC (2026-10-22)
> The team has one spare ESP and no spare FC ([[ADR-020 HIL Runner|ADR-020]] update 2026-10-22). The bench today is the Pi plus the spare ESP on USB, with no FC (§3.1). How the FC side is tested (Betaflight SITL, or the flight unit on the bench) is the spike in GitHub #61. Until #61 closes, treat the FC parts of §2 and §4 as the target, not the plan.

---

## 1. What a HIL runner is

A normal CI job runs on GitHub's cloud machines, which have no drone attached. A **self-hosted runner** is a machine **we** own (a Raspberry Pi) that registers with our GitHub repo and picks up jobs labelled for it. Because real hardware is plugged into it, the job can **flash the real ESP and test it**. That is "hardware-in-the-loop" (level 4 in [[Dev_Strategy]] §12). Module 1 requires one job like this.

## 2. Bench setup (safe by construction)

This is the target setup. What is built today (one spare ESP, no FC, USB flashing) is in §3.1.

```text
        5V + GND only            Wi-Fi: OTA flash + tests (joins ESP SoftAP)
   Pi ─────────────► ESP ◄───────────────────────────── Pi wlan0
     │  (power, no USB data) │ UART4 (MSP)
     │ USB              ▼
     └──────────────► FC (USB power only: NO battery → ESCs unpowered, motors cannot spin)
   Pi eth0 ─► lab network / internet (GitHub)
```

- **No battery connected, no props.** Motors are powered from the battery. USB power alone cannot spin them. Label the bench "HIL – no LiPo".
- The VTX is also battery-powered, so it stays off. That respects the "antenna before battery" rule in [[2.5Drone]].
- Use a **dedicated** Pi, not the Raspberry Pi 5 meant as the ground computer.
- Stable device names with udev rules (`/dev/hil-esp`, `/dev/hil-fc`).
- **USB data is never connected to the ESP in the loop** ([[ADR-012 ESP UART Pins|ADR-012]]): the FC drives UART0 RX, so USB flashing would collide with it. The Pi powers the ESP through its **5V pin**, and CI flashes it **over the air** (`upload_protocol = espota`). The very first flash, or a recovery, is done by hand over USB with the FC wires unplugged.

## 3. Setup steps

1. Raspberry Pi OS 64-bit, user `runner`, member of the `dialout` group.
2. GitHub → the repo → Settings → Actions → Runners → **New self-hosted runner** → Linux ARM64. Follow the commands. Add the label `hil-drone`. Install it as a service.
3. udev rules for the two USB-serial devices.
4. Connect `wlan0` to the ESP SoftAP (NetworkManager profile, static IP). Internet goes through Ethernet. The firmware must include ArduinoOTA, protected by the same PSK. As built, the Pi has no Ethernet, so it time-slices its Wi-Fi instead (§3.1).
5. Store the PSK as the repo secret `SD_PSK_HIL`: 64 hex characters (32 bytes). `hil.yaml` passes it as `SEAME_PSK`, which the gateway build and the session tests read; the bridge ignores it (§3.1).

### 3.1 As built (2026-10-22, GitHub #48)

| Item | Value |
|---|---|
| Runner machine | Raspberry Pi 4, Raspberry Pi OS 64-bit (desktop image), hostname `hil-drone`, user `runner` in `dialout` |
| Runner | `hil-drone-pi`, labels `self-hosted, linux, ARM64, hil-drone`, runner 2.337.0, installed as a systemd service |
| Users | `runner` runs the jobs: not in `sudo`, its only sudo rule is `nmcli` (`/etc/sudoers.d/hil-nmcli`). `hiladmin` (in `sudo`) is for admin work. Checked 2026-11-02. |
| Repo setting | Fork PR workflows need approval for all external contributors (the repo is public). Set 2026-10-22. |
| Spare ESP | ESP8266EX, 26 MHz crystal, 4 MB flash (manufacturer `5e`, device `4016`), MAC `cc:50:e3:6d:23:e4` |
| USB-serial chip | FTDI FT232 (`0403:6001`), not CH340. Latency timer 16 ms by default; the tests switch it to 1 ms with pyserial's low-latency mode, which `runner` is allowed to do (checked 2026-11-09, no warning in run 42613321029). |
| Device name | `/dev/hil-esp`, udev rule in `/etc/udev/rules.d/99-hil.rules`, matched on vendor, product and FTDI serial |
| Original firmware backup | `spare_esp_backup.bin`, 4 194 304 bytes, SHA-256 `b063fb4c08cee2f31d477310ab159551cc883cf616ddc385441d924795242f7e`. Kept off the repo. |
| Flashing | Over USB with `esptool`. Auto-reset via DTR/RTS works, no button needed. `hil.yaml` builds the `esp` env (bridge) and uploads it to `/dev/hil-esp`, runs the bridge tests, then does the same with `esp_gateway_usb` (the gateway, #101, uploaded over USB) and runs the gateway tests (§4). The job has a 30 min timeout. **After every run the spare ESP is left on the gateway**: flash `esp` by hand if you need the bridge. |
| SoftAP | Its own name and passwords, never the drone's: Secrets `SEAME_AP_SSID_HIL` (`seame-hil-23e4`), `SEAME_AP_PASS_HIL` and `SEAME_OTA_PASS_HIL` (8+ characters, different from each other: `secrets.py` stops the build otherwise, ADR-024 D3). `hil.yaml` stops if the SSID is empty. `SD_PSK_HIL` is the protocol PSK (ADR-002, 64 hex characters), passed as `SEAME_PSK` to the gateway build and the session tests; it was reset to a new random key on 2026-11-10, when the gateway (#174) started needing it. |
| Network | The Pi has only its built-in Wi-Fi, used for the internet, so it time-slices it: Wi-Fi tests run in windows of under 2 minutes (decided 2026-10-26, [[ADR-020 HIL Runner|ADR-020]], #48). The lab profile is `netplan-wlan0-SEAME`, autoconnect on. `runner` may run `sudo nmcli` without a password (`/etc/sudoers.d/hil-nmcli`). The window is `drone/tests/hil/softap_window.sh` (§3.2). |

### 3.2 Wi-Fi window (`softap_window.sh`, #106)

`drone/tests/hil/softap_window.sh SECONDS -- COMMAND`, called by `hil.yaml` twice per run, 50 s each: `pytest drone/tests/hil/wifi` on the bridge, then `pytest drone/tests/hil/gateway/wifi` on the gateway. Each window is offline for under 2 min on its own:

- **Join:** an in-memory SoftAP profile (`save no`) and the lab profile's autoconnect switched off in memory only (`modify --temporary`). The Pi cannot fall back to the lab mid-test, and a reboot always brings it back to the lab.
- **Limits:** the command runs for at most 50 s. A window is 105 s offline at most (join 25 + test 50 + kill 5 + re-join 25).
- **Restore:** the lab is re-joined after the command, on failure, and at once on a step timeout (INT/TERM). One window at a time: a lock in `/tmp/hil-softap.lock`.
- **Watchdog:** started outside the job (`setsid -f`, empty `RUNNER_TRACKING_ID`). If the job dies inside the window or its re-join fails, it re-joins the lab after SECONDS + 65 s (115 s for a 50 s window). It never acts while the run is still alive.
- **Tests:** `test_softap_window.py` checks the script against a fake `nmcli` in PR CI (`hil-unit` job), not on the Pi. Wi-Fi tests live in `drone/tests/hil/wifi/` (bridge) and `drone/tests/hil/gateway/wifi/` (gateway) and run only inside a window.

Bench checks (PR #140):

- **2026-11-02:** SIGKILL inside the window, lab back after 235 s through the watchdog. That was the first version, with a 120 s grace. The grace is now 10 s: the watchdog takes the lock before acting, so it cannot race the restore.
- **2026-11-03:** a step-timeout signal sent during the join. The restore started 0.1 s later, and the lab was back 5.3 s after the signal.
- A cancel from GitHub cannot reach the runner while it is offline, so it lands after the window.
- **2026-11-09 (PR #173):** two windows in one run (bridge, then gateway). Runs 42613321029 and 42614646745 re-joined the lab both times.

The spare ESP is **not wired to an FC**, so USB data is allowed for it ([[ADR-020 HIL Runner|ADR-020]] update 2026-10-22). §2's "no USB data" rule still applies to any ESP wired to an FC.

The udev rule (replace the serial with the one from `udevadm info -a -n /dev/ttyUSB0 | grep -m1 'ATTRS{serial}'`):

```text
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", ATTRS{serial}=="<serial>", SYMLINK+="hil-esp", GROUP="dialout", MODE="0660"
```

Gotcha: our ESP boards are ESP-12F dev boards (PlatformIO board `esp12e`) with an FTDI FT232R (`0403:6001`), not the CH340 (`1a86:7523`) that many ESP8266 boards use. Check `lsusb` before writing the rule.

Why USB and not `esp_ota`: OTA needs the Pi on the spare ESP's SoftAP (`192.168.4.1:8266`), and the Pi's only Wi-Fi is its internet link. USB flashing also recovers a build that broke OTA by itself, with no one at the bench.

### 3.3 Runner gotchas (found 2026-10-26, GitHub #48)

| Symptom | Cause / fix |
|---|---|
| Jobs stay queued, the runner looks online in GitHub | The service had exited on 2026-10-22 ("runner registration has been deleted") while GitHub still listed it. Check `systemctl status` on the Pi. The service is now **enabled** (`systemctl enable --now`), so it starts on boot. |
| Runner refused after an unclean shutdown ("A session for this runner already exists") | GitHub holds the old session for about 2.5 min, then accepts the runner. Wait, don't re-register. |
| A job stays queued for minutes with the runner online and idle | `sudo systemctl restart` the runner service. Seen on 2026-10-26 (20 min); two `dev` runs on 2026-10-28 waited 21 h and 24 h before being cancelled. |
| A job loses the runner | GitHub fails a job about 11 min after the runner disappears. Off-network windows of 3 and 5 min survived, so keep Wi-Fi test windows **under 2 min** (§3.1 Network). |
| Runner logs gone after a reboot | Raspberry Pi OS sets `Storage=volatile` in `/usr/lib/systemd/journald.conf.d/40-rpi-volatile-storage.conf`, so creating `/var/log/journal` is not enough. Override it with `/etc/systemd/journald.conf.d/99-hil-persistent.conf` (`[Journal]` / `Storage=persistent`), then `sudo systemctl restart systemd-journald`. Check with `systemd-analyze cat-config systemd/journald.conf \| grep -i '^Storage'` and `journalctl --list-boots`. |

## 4. First HIL tests (`drone/tests/hil/`)

The bench has no FC, so for the gateway the Pi plays the FC on `/dev/hil-esp`: `FakeFc` in `bench.py` answers `MSP_STATUS`, `MSP_ATTITUDE`, `MSP_ANALOG` and `MSP_API_VERSION` with fixed values, acknowledges `MSP_SET_RAW_RC`, and records every byte the ESP sends. Over Wi-Fi, the session tests play the ground with the code of `scripts/ground_link.py` and the HIL PSK. `bench.py` also holds the serial and boot helpers shared by all the tests.

1. **Smoke:** USB flash (§3.1, not OTA) → ESP boots → ground HELLO over Wi-Fi → STATE telemetry received. *Today (`test_smoke.py`): USB flash → `boot mode:(3,…)` in the ROM output, no crash markers → UART0 silent for 10 s at 115200. Boot output read at 115200 from the reset edge holds no MSP header or frame, over 3 resets (ESP-S1-03). Gateway BOOT / NO_SESSION (`gateway/test_uart.py`, USB): after a reset with the fake FC silent, no RC at all, only polls (BOOT, REQ-SAF-016); once it answers `MSP_API_VERSION`, neutral `MSP_SET_RAW_RC` at 50 Hz plus the polls at 25 / 5 / 5 / 1 Hz ([[ADR-009 ESP to FC Link|ADR-009]]); when the FC goes silent again, RC does not starve the polls. Every byte on the UART is part of an MSP frame, and every RC is neutral. Gateway session (`gateway/wifi/test_session.py`, inside the window): HELLO / HELLO_ACK with the HIL PSK, STATE at 25 Hz carries the fake FC's values, setpoints are accepted in SESSION_IDLE but never reach the FC, garbage raises `rx_rejected`, and a HELLO signed with another PSK gets no ACK while the session keeps running. The gateway becomes env `esp` in place of the bridge in a later step of #106.*
2. **Passthrough (ESP only, GitHub #106):** allowed MSP messages go through the gateway firmware (#101), all others are dropped. The 2026-10-26 scope decision ([[ADR-020 HIL Runner|ADR-020]]) takes the FC off the bench, so the AETR mapping on the real FC is checked by hand (GitHub #104) and in Betaflight SITL, not here. *Today (`wifi/test_bridge.py`, inside the window): on the transparent bridge, UDP 5760 ↔ UART0 in both directions.*
3. **Failsafe timing:** stop sending → measure time until telemetry reports `LINK_FAILSAFE` (expect 250 ms ± one loop). *Written as skipped in `gateway/wifi/test_session.py`, with the passthrough and resume tests: they need STREAMING, so an automated `ARM_CONTROL_REQUEST`, and REQ-SAF-023 makes ARM an operator action. Waiting for the safety pilot's decision for the HIL bench (#106).*
4. **Security:** bad tag / stale seq → counters increase, FC values unchanged.
5. **Latency:** RTT histogram saved as an artifact.

> [!warning] Self-hosted runner security
> It executes whatever code the workflow runs. The repo is public, so fork PR workflows need approval (set 2026-10-22, §3.1). Never run the HIL job on `pull_request` from forks.

## 5. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **VTX** | Video transmitter: hardware that sends the aircraft video feed. |
| **ESC** | Electronic Speed Controller: hardware that drives a motor. |
| **OTA** | Over-the-air update: flashing firmware through Wi-Fi instead of USB. |
| **LiPo** | Lithium-polymer battery: the 4S flight pack. Handle it by the rules in Safety Rules. |
| **RTT** | Round-trip time: how long a packet takes to go out and come back. |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
| **udev** | The Linux device manager. A udev rule gives a USB device a fixed name such as `/dev/hil-esp`. |
| **FTDI** | A maker of USB-to-serial chips (the FT232 on our spare ESP board). |
| **DTR / RTS** | Serial control lines. `esptool` uses them to reset the ESP into flash mode without a button press. |
| **SHA-256** | A hash used to check that a file (here the firmware backup) has not changed. |
| **Self-hosted runner** | A machine of ours (the Pi 4 `hil-drone`) that runs GitHub Actions jobs, used for the HIL job. |
| **Fake FC** | The Pi playing the flight controller on the spare ESP's serial port (`FakeFc` in `drone/tests/hil/bench.py`), since the HIL bench has no FC. |
| **SoftAP** | Software access point: the ESP creates its own Wi-Fi network that the ground PC joins (ADR-006). |
