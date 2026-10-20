---
title: ESP Bench Test Plan
type: test-plan
status: draft
date: 2026-10-12
project: SEA autonomous drone
aliases:
  - ESP8266 Bench Tests
  - D1 mini tests
tags:
  - testing
  - esp8266
  - bench
  - safety
---

# ESP Bench Test Plan

> [!warning] Status: PLAN, with results from 2026-10-14 to 2026-10-19 in §4.
> The original rule was: nothing is soldered to the flight controller until Stage 1 and Stage 2 have passed and the [[ESP_to_FC]] §10 checklist is complete. **On 2026-10-19 the ESP was soldered to FC UART4 (wires that are easy to remove) before that was complete**: S2-02 and S2-06 had only been rehearsed through a PC USB relay, and the §10 evidence on the soldered joints is still to be posted (GitHub #30). Fill in the result tables as you go and commit them.

> [!info] Why test before soldering
> Desoldering a D1 mini from a 2.5" AIO risks lifting the FC pads. Testing first also separates hardware problems from software problems.

---

## 1. Stage 1 — D1 mini alone (USB, no drone)

**Setup:** D1 mini on the laptop's USB. No FC connected. Tools: `esptool.py`, PlatformIO, a USB power meter.

| ID | Test | What it proves | Pass criterion | Links |
|---|---|---|---|---|
| ESP-S1-01 | Back up the AT firmware: `esptool.py flash_id`, then `read_flash 0 ALL esp8266_AT1.7.4_<station-MAC>_<date>.bin` (backup names and checksums: `drone/firmware/esp8266/backup/README.md`) | We can always go back to the original firmware | File is exactly the flash size reported by `flash_id` | [[ADR-007 Portable Protocol Library and Custom ESP Firmware\|ADR-007]] |
| ESP-S1-02 | Flash a blink sketch, then restore the backup once, then flash blink again | Board, USB chip and flash are OK; restore works | Blink runs; AT firmware answers `AT` after the restore | ADR-007 |
| ESP-S1-03 | Capture boot output at **74880 baud** | What the FC will see on its RX at ESP power-up | Bytes recorded here | [[ADR-012 ESP UART Pins\|ADR-012]], F5 in [[ESP_to_FC]] |
| ESP-S1-04 | SoftAP up with WPA2-PSK; laptop connects, 20 reboots | The network design works on this board | 20/20 connects; time-to-connect recorded | [[ADR-006 Network\|ADR-006]] |
| ESP-S1-05 | UDP echo at 50 Hz for 10 min, `WIFI_NONE_SLEEP` | Real latency and loss | Median, p99, max RTT and loss % recorded; threshold agreed beforehand | OD-05/06/07 in [[PC_to_ESP]] |
| ESP-S1-06 | Same as S1-05 with sleep enabled | Why ADR-006 disables sleep | Difference recorded | ADR-006 |
| ESP-S1-07 | TCP session open + UDP stream at the same time | TCP does not stall control | No UDP gap above the S1-05 max | [[ADR-005 TCP Session\|ADR-005]] |
| ESP-S1-08 | Current draw with a USB power meter during Wi-Fi transmit | Whether the FC 5V rail can feed it | Peak and average mA recorded | FC-05, [[ADR-013 Wiring and Power\|ADR-013]] |
| ESP-S1-09 | Range: laptop in the coding room, D1 mini in the cage | The link reaches through the building | S1-05 repeated at the real distance; loss and RTT recorded | ADR-006 |
| ESP-S1-10 | Watchdog: hang the loop on purpose | The ESP recovers on its own, in a safe state | Reboot + reset reason logged; no control output after reboot | REQ-FS-04 in [[PC_to_ESP]] |

> [!tip] Reuse
> S1-05 to S1-07 are the first version of the protocol code. Write them against the portable library so they become unit/HIL tests later ([[Testing Strategy]]).

## 2. Stage 2 — D1 mini + FC on the bench, temporary wiring

**Setup**

- FC powered **by USB only**. **No battery, no props**: the motors cannot spin.
- D1 mini connected to the FC with **temporary** wires (pogo pins, or wires held by tape and a clip). UART4 (T4/R4), or UART1 (T1/R1) if easier to reach ([[ADR-011 FC UART Allocation|ADR-011]]).
- **The D1 mini's USB is never connected while the FC wires are attached** ([[ADR-012 ESP UART Pins|ADR-012]]). Power it from a USB power bank or the 5V pin. Logs go over Wi-Fi.
- MSP enabled on the chosen UART at 115200, with the pre-change `diff all` saved ([[Betaflight Setup]]).

| ID | Test | What it proves | Pass criterion | Links |
|---|---|---|---|---|
| ESP-S2-01 | `MSP_API_VERSION` round trip | Wiring, crossover and baud are right | Reply 1.46 (4.5.1), 100/100 requests | [[ADR-009 ESP to FC Link\|ADR-009]] |
| ESP-S2-02 | Power-cycle the ESP 20× while the FC runs | The FC ignores the boot bytes | No FC errors, no change in Betaflight | F5, F7 in [[ESP_to_FC]] |
| ESP-S2-03 | `MSP_ATTITUDE` while tilting the FC by hand | Telemetry path FC → ESP → laptop | Angles follow the tilt | TEL-03 |
| ESP-S2-04 | `MSP_SET_RAW_RC` with distinct values → `MSP_RC` readback | Channel order (AETR) | Each value lands on the expected channel | Phase 3 of [[ESP_to_FC]] §11 |
| ESP-S2-05 | ELRS receiver bound, Wi-Fi streaming next to it | Wi-Fi ↔ ELRS coexistence | ELRS link quality recorded on channels 1, 6, 11 | [[Study Plan v2]] week 2 |
| ESP-S2-06 | Unplug a jumper wire mid-stream | The ESP detects the lost FC | Error reported to the ground within 500 ms | F2 in [[ESP_to_FC]] |

MSP Override tests (Phases 4–5 of [[ESP_to_FC]] §11) need ELRS bound and the mode configured. Run them in this same setup before soldering.

## 3. Stage 3 — mounting and soldering

Only after Stages 1 and 2 pass:

1. Run the [[ESP_to_FC]] §10 checklist: T4/R4 continuity to the MCU side, no shorts to neighbouring pads, T4 idle level measured.
2. Check the FC 5V rail can supply the **peak current from ESP-S1-08**.
3. Mount per [[ADR-021 Removable ESP Connector|ADR-021]] (accepted 2026-10-20: a 4-pin connector, so the ESP can be unplugged). Not fitted yet: on 2026-10-19 the ESP was soldered with wires that are easy to remove.
4. Repeat ESP-S2-01 and ESP-S2-03 on the installed hardware.

## 4. Results log

Recorded from GitHub #30 (comments of 2026-10-16 and 2026-10-19) and #25 (session 2026-10-14). The "Who" column is the GitHub author of the record, not necessarily who ran the test. Raw files are in `results/` and `config/betaflight/4.5.1/` of the tooling branch (PR #11), not in this repo yet.

**Setups:**

- **2026-10-14:** the ESP alone on USB, no FC (#25).
- **2026-10-16:** before the temporary ESP↔FC wires. Props off, no battery, FC on USB only. Latency runs are 10 min at a true 50 Hz (`--stream`, 30,000 requests).
- **2026-10-19:** the ESP soldered to UART4 and the real FC, production AP (`seame-drone-0850`, channel 6). Props off. FC **not** on USB (powered on its own; what powered it is an open question). ESP USB unplugged. `--stream` runs, 10 min, 30,000 requests.

| Test ID | Date | Who | Result (pass/fail + numbers) | Notes / issue # |
|---|---|---|---|---|
| ESP-S1-01 | 2026-10-16 | ptorrao-git (comment on #30) | PASS. 4,194,304-byte image = flash size, verified. FC firmware also backed up over DFU (524,288 bytes, two reads identical). | #30. Image: `esp8266_flash_2026-10-16_164249.bin` (current restore point, contains Wi-Fi credentials, kept outside Git). Earlier images of 2026-10-13: `esp8266_AT1.7.4_807d3a1d0850_2026-10-13.bin` and `..._factory-reset.bin`. See `drone/firmware/esp8266/backup/README.md` |
| ESP-S1-02 | 2026-10-14 | Dot99 (body of #25) | Not done. The LED blinked in use, but that is serial activity; the blink → restore → blink sequence was not run. | #25. Superseded by the 2026-10-16 row |
| ESP-S1-02 | 2026-10-16 | ptorrao-git (comment on #30) | PASS. Blink flashed and ran; backup written back and verified; boot output identical before and after (AT `ready`). | #30 |
| ESP-S1-03 | 2026-10-16 | ptorrao-git (comment on #30) | PASS. 731 bytes at 115200 for each ESP power-up: 0 `$M` headers, 0 MSP frames, so Betaflight's MSP parser never sees a frame start. | #30. The test plan says 74880 baud; the record states 115200 |
| ESP-S1-04 | 2026-10-16 | ptorrao-git (comment on #30) | PASS. 20/20 reconnects. First 4.4 s (initial scan), then 0.28–0.58 s from reset to ping. | #30 |
| ESP-S1-05 | 2026-10-14 | Dot99 (body of #25) | Not a valid result. Test scenario (ESP on SEAME), sleep mode not set. 500 requests: 0 % loss, median 32.7 ms, p99 80.4 ms, max 127.5 ms, 0 bad checksums. 10-min run (30,000 requests planned): 8,079 answered, 12.6 % loss, median 33.2 ms, p99 87.9 ms, max 495 ms. **Cut off:** the ESP USB was unplugged at about 2.5 min, so the losses after that are not link losses. | #25. To redo with the board left plugged in, sleep off, threshold agreed beforehand |
| ESP-S1-05 | 2026-10-16 | ptorrao-git (comment on #30) | PASS (SEAME network, sleep off, fake FC). 0 lost / 30,000. RTT median 35.8 ms, p99 106 ms, max 238 ms. Longest silence 219 ms, 0 over 250 ms. | #30. The pass threshold was still a proposal (see below) |
| ESP-S1-05 | 2026-10-19 | ptorrao-git (comment on #30) | **FAIL against the proposed threshold** (loss ≤ 0.1 %, p99 ≤ 150 ms). Soldered link, production AP, PC next to the drone (`s1-05_soldered.csv`): loss **0.69 %** (206), RTT median 55 ms, p99 328 ms, max 499 ms, longest silence **830 ms**, 57 silences over 250 ms. Loss per minute 0.1–2.3 %, no trend, longest burst 26. | #30, open investigation. 5–10× worse than the same AP before soldering. Cause not found |
| ESP-S1-06 | 2026-10-16 | ptorrao-git (comment on #30) | Worse, as expected (modem sleep). 164 lost (0.55 %), p99 200 ms, max 488 ms, longest silence 949 ms, 21 silences over 250 ms. The first 5 min matched S1-05, so SEAME traffic probably added to it. | #30. Confirms [[ADR-006 Network\|ADR-006]] (sleep off) |
| ESP-S1-07 | 2026-10-16 | ptorrao-git (comment on #30) | Not run (n/a). AT transparent mode has one link; needs the custom firmware. | #30, [[ADR-007 Portable Protocol Library and Custom ESP Firmware\|ADR-007]] |
| ESP-S1-08 | 2026-10-19 | ptorrao-git (comment on #30) | No number recorded. 2026-10-16: "not recorded here yet". 2026-10-19: reported as done by the team, numbers still to be posted. It is also the first candidate cause listed for the 2026-10-19 loss (a 5V rail that cannot cover the ESP's TX peaks). | #30. Still open: peak and average mA (decides power bank vs FC 5V pad, FC-05) |
| ESP-S1-09 | 2026-10-14 | Dot99 (body of #25) | ~2 % loss (490 answers of 500 requests assumed), ESP on its own WPA2 SoftAP `seame-drone-0850` channel 6, about 10 m, `msp_check.py ping` at 50 Hz. RTT median 26.6 ms, mean 32.8 ms, p95 58.6 ms, p99 95.5 ms, max 150.7 ms, 4 over 100 ms. Signal reported good. RSSI not logged. | #25. Loss above the 1 % script default; the box stays unticked until a threshold is agreed |
| ESP-S1-09 | 2026-10-16 | ptorrao-git (comment on #30) | WARNING. Range 12 m, production AP, cage, loopback: 15 lost (0.05 %), longest burst 4. RTT median 32.0 ms, p99 134 ms, max 307 ms. Longest silence **340 ms**, 8 silences over 250 ms. | #30. With `T_loss` = 250 ms the ESP failsafe would have fired 8 times in 10 min |
| ESP-S1-09 | 2026-10-19 | ptorrao-git (comment on #30) | **FAIL against the proposed threshold.** Soldered link, cage, PC in the coding room (`s2_latency_cage.csv`): loss **3.71 %** (1,114), RTT median 72 ms, p99 429 ms, max 500 ms, longest silence **1,149 ms**, 202 silences over 250 ms. Loss per minute: 0.8, 0.8, 1.3, 1.9, 3.4, 4.2, 4.1, 4.3, 7.8, 8.6 %; median RTT rises from 65 to 86 ms. Longest burst 37 requests (0.74 s); 35 bursts of 10 or more. | #30, open investigation. The steady decline suggests something drifting, not only distance. A short-range S1-09 baseline was reported done, but its numbers are not posted |
| ESP-S1-10 |  |  | Not run. | Needs the custom firmware |
| ESP-S2-01 | 2026-10-19 | ptorrao-git (comment on #30) | PASS. `test s2-01` on the soldered link: 0 / 100 lost, RTT median 33 ms, p99 127 ms, max 130 ms, longest silence 130 ms, 0 over 250 ms. The FC answers MSP through the soldered UART4: wiring and crossover are right. | #30. The 2026-10-16 relay run is listed as reported done, with no saved files |
| ESP-S2-02 | 2026-10-16 | ptorrao-git (comment on #30) | PASS (rehearsal through a PC USB relay, no wires). `status-watch` 234 polls / 26 s while the ESP was reset: 0 timeouts, I2C errors 0, never armed, `reboot_required` false. Arming-disable flags constant at 0x100004 (RXLOSS + DSHOT_TELEM; radio off, no ESC power), same as the CLI. FC uptime 2,894 s at the end: no FC reboot. `compare-diffs`: no configuration change (16:38 vs 17:27). | #30. Not yet run on the soldered link (needs the FC on USB, ESP reset button ×20) |
| ESP-S2-03 | 2026-10-19 | ptorrao-git (comment on #30) | Run and reported done (`msp attitude` while tilting). No numbers or saved files. The 2026-10-16 relay run is also reported done, terminal output only. | #30 |
| ESP-S2-04 | 2026-10-16 | ptorrao-git (comment on #30) | Read part reported done (relay run), terminal output only, no saved files. | #30. The write part waits for MSP Override to be configured and approved |
| ESP-S2-05 |  |  | Not run. | Only the current AP channel (6) is possible with the ESP USB unplugged |
| ESP-S2-06 | 2026-10-16 | ptorrao-git (comment on #30) | PASS (rehearsal through a PC USB relay). Relay stopped twice: loss detected after 302 ms both times (limit 500 ms). Outages 8.2 s and 4.7 s. | #30. Not run on the soldered link: a soldered wire cannot be unplugged, so the FC would be rebooted over USB, which is closer to F6 than F2 |

### Decision threshold (proposal, not agreed)

From #30, 2026-10-16. With the initial `T_loss` = 250 ms ([[ADR-004 ESP Failsafe|ADR-004]], OD-07), the ESP failsafe would have fired 8 times in 10 min at 12 m on the production AP and 0 times on SEAME at short range. Proposal:

- Loss ≤ 0.1 % over 10 min at 50 Hz, on the production AP at the operating distance.
- `T_loss` = 400 ms, just above the worst silence measured on 2026-10-16 (340 ms), per OD-07. Revisit after a channel survey (`nmcli dev wifi list`, try channels 1 and 11) and the custom firmware.
- p99 RTT ≤ 150 ms.

**The soldered link of 2026-10-19 fails this proposal**, even next to the PC (loss 0.69 % and 3.71 %, p99 328 and 429 ms, longest silences 830 and 1,149 ms, all above 400 ms). The 2026-10-16 numbers (before soldering) met the proposal at 12 m. This is an open investigation in #30, which stays open until the cause is found.

### Candidate causes and next runs (from #30, 2026-10-19)

1. **Power:** what fed the FC and ESP (battery, FC 5V pad). A sagging battery or a 5V rail that cannot cover the ESP's TX peaks fits the drift. S1-08 is unmeasured.
2. **ELRS 2.4 GHz** next to the ESP, if the radio was on (S2-05).
3. **Antenna placement:** the ESP-12F PCB antenna is now against the FC and frame.
4. **Heat** inside the frame (ESP and VTX).

Next runs (near, 10 min each): FC on the laptop USB with no battery and the radio off (if this matches 2026-10-16, power was the cause); the same with the radio on and bound (ELRS coexistence); if still bad, change the ESP antenna position and check for heat.

### Still open

- S1-08 numbers; S1-05 on the production AP at short range and the short-range S1-09 baseline (reported done, numbers not posted).
- [[ESP_to_FC]] §10.1–§10.3 evidence on the soldered joints (continuity, no shorts, crossover, T4 idle about 3.3 V, photos).
- S2-02, S2-06 and S2-05 on the soldered link.
- Agree the threshold.

## 5. Related pages

- [[ESP_to_FC]] §10–§11 · [[PC_to_ESP]] §9.2 · [[Testing Strategy]] · [[Wiring and Pinout]] · [[Safety Rules]]

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **ESP8266** | The Wi-Fi microcontroller of the drone's ESP module (an ESP-12F, Wemos D1 mini class). It bridges the ground PC to the FC over UART4. |
| **FC** | Flight Controller: on this project the OddityRC F7 AIO (STM32F722, Betaflight target `ODDITYRCF7AIO`) of the 2.5" drone. It stabilises the aircraft and takes MSP from the ESP on UART4. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **Telemetry** | Periodic data sent from a system to report state, measurements, or health. |
| **UDP** | User Datagram Protocol: connectionless transport suited to refreshed, time-sensitive data. |
| **UART** | Universal Asynchronous Receiver/Transmitter: an asynchronous serial interface. |
| **API** | Application Programming Interface: a defined way for software components to communicate. |
| **ELRS** | ExpressLRS: the open-source radio-control link between the RadioMaster Pocket and the BETAFPV Nano receiver on the drone. |
| **REQ** | Requirement ID of the form `REQ-<SYS, NAV, SEN, COM, CTL, SAF, PWR, SW or HW>-NNN`, used for traceability. |
| **RTT** | Round-trip time: how long a packet takes to go out and come back. |
| **PlatformIO** | The build system used for the ESP8266 firmware (envs `d1_mini`, `d1_mini_ota`). |
| **USB** | Universal Serial Bus: used to flash the ESP and to power or configure the FC from a PC. |
| **AT** | The Hayes-style AT command set of the stock ESP8266 firmware (1.7.4.0), replaced by custom firmware (ADR-007). |
| **OD-xx / FC-xx / TF-xx** | Open-decision IDs in PC_to_ESP, ESP_to_FC and ThoneFlow. The Decision Register shows which ADR resolves each one. |
