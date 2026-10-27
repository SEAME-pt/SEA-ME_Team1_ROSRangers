---
title: HIL Runner
type: ci-cd
status: draft
date: 2026-10-12
updated: 2026-10-26
project: SEA autonomous drone
tags:
  - ci
  - hil
  - safety
---

# HIL Runner: what it is and how to build ours

> [!danger] Hardware work follows [[Safety Rules]]: props off on the bench, the radio and the safety pilot win over code, no LiPo on the HIL bench.

> [!info] Decision: [[ADR-020 HIL Runner|ADR-020]]
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
5. Store the PSK as the repo secret `SD_PSK_HIL`. The current firmware (transparent bridge) does not use it: it builds with `SEAME_AP_SSID`, `SEAME_AP_PASS` and `SEAME_OTA_PASS` instead (§3.1).

### 3.1 As built (2026-10-22, GitHub #48)

| Item | Value |
|---|---|
| Runner machine | Raspberry Pi 4, Raspberry Pi OS 64-bit (desktop image), hostname `hil-drone`, user `runner` in `dialout` |
| Runner | `hil-drone-pi`, labels `self-hosted, linux, ARM64, hil-drone`, runner 2.337.0, installed as a systemd service |
| Repo setting | Fork PR workflows need approval for all external contributors (the repo is public). Set 2026-10-22. |
| Spare ESP | ESP8266EX, 26 MHz crystal, 4 MB flash (manufacturer `5e`, device `4016`), MAC `cc:50:e3:6d:23:e4` |
| USB-serial chip | FTDI FT232 (`0403:6001`), not CH340 |
| Device name | `/dev/hil-esp`, udev rule in `/etc/udev/rules.d/99-hil.rules`, matched on vendor, product and FTDI serial |
| Original firmware backup | `spare_esp_backup.bin`, 4 194 304 bytes, SHA-256 `b063fb4c08cee2f31d477310ab159551cc883cf616ddc385441d924795242f7e`. Kept off the repo. |
| Flashing | Over USB with `esptool`. Auto-reset via DTR/RTS works, no button needed. `hil.yaml` builds the `esp` env and uploads it to `/dev/hil-esp`. |
| SoftAP | Its own name and passwords, never the drone's: Secrets `SEAME_AP_SSID_HIL` (`seame-hil-23e4`), `SEAME_AP_PASS_HIL` and `SEAME_OTA_PASS_HIL` (8+ characters). `hil.yaml` stops if the SSID is empty. `SD_PSK_HIL` is kept for message signing (ADR-002) and is not used yet. |
| Network | The Pi has only its built-in Wi-Fi, used for the internet. It cannot join the spare ESP's SoftAP at the same time, so the first HIL test is USB only (`drone/tests/hil/test_smoke.py`: normal flash boot, then silence on UART0). Wi-Fi tests time-slice the radio: leave the lab Wi-Fi, join the ESP SoftAP, test, re-join, in windows under 2 minutes (decided 2026-10-26, [[ADR-020 HIL Runner|ADR-020]], #48). `sudo nmcli` is allowed without a password for `runner` (`/etc/sudoers.d/hil-nmcli`). The lab profile is `netplan-wlan0-SEAME`, autoconnect on. |

The spare ESP is **not wired to an FC**, so USB data is allowed for it ([[ADR-020 HIL Runner|ADR-020]] update 2026-10-22). §2's "no USB data" rule still applies to any ESP wired to an FC.

The udev rule (replace the serial with the one from `udevadm info -a -n /dev/ttyUSB0 | grep -m1 'ATTRS{serial}'`):

```text
SUBSYSTEM=="tty", ATTRS{idVendor}=="0403", ATTRS{idProduct}=="6001", ATTRS{serial}=="<serial>", SYMLINK+="hil-esp", GROUP="dialout", MODE="0660"
```

Gotcha: our ESP boards are ESP-12F dev boards (PlatformIO board `esp12e`) with an FTDI FT232R (`0403:6001`), not the CH340 (`1a86:7523`) that many ESP8266 boards use. Check `lsusb` before writing the rule.

Why USB and not `esp_ota`: OTA needs the Pi on the spare ESP's SoftAP (`192.168.4.1:8266`), and the Pi's only Wi-Fi is its internet link. USB flashing also recovers a build that broke OTA by itself, with no one at the bench.

## 4. First HIL tests (`drone/tests/hil/`)

1. **Smoke:** OTA flash → ESP boots → ground HELLO over Wi-Fi → STATE telemetry received. *Today (`test_smoke.py`): USB flash → `boot mode:(3,…)` in the ROM output, no crash markers → UART0 silent for 10 s at 115200.*
2. **Passthrough:** send a SETPOINT → read `MSP_RC` from the FC over USB → expected channel values (checks the AETR mapping end to end).
3. **Failsafe timing:** stop sending → measure time until telemetry reports `LINK_FAILSAFE` (expect 250 ms ± one loop).
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
| **SoftAP** | Software access point: the ESP creates its own Wi-Fi network that the ground PC joins (ADR-006). |
