---
title: ADR-024 — OTA Firmware Updates on the Flight ESP
type: adr
status: accepted
date: 2026-11-04
updated: 2026-11-06
project: SEA autonomous drone
tags:
  - adr
  - esp8266
  - safety
  - security
---

# ADR-024 — OTA Firmware Updates on the Flight ESP

> [!success] Status: ACCEPTED (2026-11-05, GitHub #62)
> D1 to D3 were decided by the team on 2026-11-04, and the upload lock in D2 on 2026-11-05. D4 and D5 were accepted by BrunoBMFT's approving review of PR #145 (2026-11-05). The status change and the follow-ups from that review (release at every boot, the socket check, the bench cases) were meant to be the last commit of #145; that PR merged first, so they are applied here (PR #153).
> Resolves OD-23 in [[PC_to_ESP]] in part. Signed images are still planned for Module 3.

## 1. Context

- The ESP is wired to FC UART4, and its USB shares UART0 with that link. USB is never connected while the ESP is wired to the FC ([[ADR-012 ESP UART Pins|ADR-012]]). The ESP is soldered directly to the FC ([[ADR-021 Removable ESP Connector|ADR-021]], amended 2026-10-26), so without OTA every update means desoldering it.
- The firmware already has an `esp_ota` env with ArduinoOTA and a password (`drone/firmware/esp8266/`). This ADR is about the **flight ESP** only. The HIL runner tests a separate spare ESP that is not wired to any FC, and flashes it over USB ([[ADR-020 HIL Runner|ADR-020]], update 2026-10-22).
- Risks found in the #53 review (follow-up 12):
  - **Mid-flight flash:** while the ESP flashes and reboots, it sends no MSP for 5–20 s.
  - **Unauthorised flash:** anyone on the drone network could replace the firmware.

## 2. Options considered

| Option | Pros | Cons |
|---|---|---|
| No OTA; USB only | Nothing can flash over Wi-Fi | ESP off the FC for every update; repeated soldering on the FC |
| OTA with no limits | Simplest | Anyone on the network can flash; a flash could start in flight |
| **OTA with credentials, only on the ground** | Fast updates without touching the wiring, both risks covered | Firmware must know the arm and link state; MD5-based password only (see §4) |

## 3. Decision

**D1. Keep OTA** on the flight ESP (team, 2026-11-04). It is the normal way to update the firmware.

**D2. Only when the drone is not flying** (team, 2026-11-04). The drone may be powered on, but it must not be flying. The ESP accepts an OTA update only if **all** of these are true:
- the link state is NO_SESSION: no ground session is open (not SESSION_IDLE, STREAMING or LINK_FAILSAFE, [[ESP Link State Machine]]);
- the FC is **disarmed** according to its latest `MSP_STATUS`;
- that `MSP_STATUS` is recent (under 500 ms old).

If the arm state is unknown (no FC reply, UART fault), OTA is refused.

How the gate works (ArduinoOTA, ESP8266 core 3.1.2): `ArduinoOTA.begin()` answers the upload request and checks the password on its own, from a UDP callback; only `ArduinoOTA.handle()` runs the transfer. The firmware calls `handle()` only while the conditions hold. So with the gate closed:
- a wrong password is still refused (no lock is taken, see below);
- with the right password, `espota` passes the password check and then times out, because the transfer never starts;
- that request stays pending. When the gate opens again, ArduinoOTA runs it: the start callback runs (so the lock below engages), the ESP fails to connect back (`espota` has given up), and the lock is released. No image is written.

**Upload lock** (team, 2026-11-05). In ArduinoOTA's start callback, before any byte is written:
1. The ESP asks the FC for a **fresh** `MSP_STATUS`: it discards buffered replies and uses only the reply to this request. If the FC is armed or does not answer, it aborts with `ESP.restart()`.
2. Only then it sends `MSP_SET_ARMING_DISABLED` (MSP 99, value 1), reads `MSP_STATUS` back, and aborts the same way unless `ARMING_DISABLED_MSP` is set. **Never send value 1 without step 1:** Betaflight disarms at once if it gets it while armed (`msp.c`, 4.5.1 and 2026.6.2).
3. It sets a `locked` flag. While the image is transferred, ArduinoOTA blocks the main loop, so a `HELLO` is not processed: it gets no reply and the ground times out. No session can open, and the wire format does not change.

**Release:** `MSP_SET_ARMING_DISABLED` value 0, in two places:
- **At every boot**, once, whatever the reason for the boot: after a successful update, after an abort in step 1 or 2 (`ESP.restart()`), and after a watchdog reset or power glitch during the transfer (the old image boots and does not know a lock was taken). This is harmless at any time: value 0 never arms or disarms.
- **In the running firmware** after a failed upload: sent once and only if `locked` is set, by the main loop. Never from ArduinoOTA's callbacks: the error callback also runs for a wrong password (from the UDP callback, before any lock) and can run twice for one failed upload; it only marks the release as due.

 Value 0 only clears the lock set from the ESP's own UART: Betaflight keeps a lock set from another port (for example the Configurator on USB). Until the release, the FC refuses to arm, which is the safe side; an FC power cycle also clears it.

**Today:** the firmware is still the transparent bridge. It calls `handle()` without any gate and has neither the lock nor sessions. #102 implements D2; until then the D5 procedure is the only protection.

**D3. Credentials are required** (team, 2026-11-04). Two separate secrets protect a flash:
- the SoftAP Wi-Fi password (WPA2, one client at a time, [[ADR-006 Network|ADR-006]]);
- the **OTA password**, `ArduinoOTA.setPassword(OTA_PASS)`.

Both come from `secrets.py` (`~/.config/seame-drone/` or the `SEAME_*` environment variables). Both must be at least 8 characters and different from each other; `secrets.py` stops the build otherwise. They are never committed. A build without an OTA password is not allowed.

**D4. Flight builds keep OTA** (accepted 2026-11-05, #145). `WITH_OTA` stays `1` in every env. The D2 gate already covers the flight case, and compiling OTA out would bring back the USB-on-FC problem from ADR-012. A build that turns it off must say so in its env comment.

**D5. Written procedure** (accepted 2026-11-05, #145). This is the procedure in `drone/firmware/esp8266/README.md` ("Flash"):
- **Every update:** OTA, [[Safety Rules]] apply.
  - Props off, battery out, powered from the FC USB, arm switch and takeover (MSP Override) switch off.
  - 2026-11-06, #164 (see the dated entry in §5): **the battery must be connected**, because the FC USB cannot power the ESP; "battery out" above no longer applies.
  - No ground session during the upload: the ground node is stopped (after #102 the ESP also blocks it, D2).
  - Note the image SHA-256 when you flash.
- **First flash or recovery:** USB, only with the ESP taken off the FC wires (ADR-012).
  - Back up the flash first.
  - If an image breaks OTA, recovery is over USB the same way.

## 4. Consequences

- **#102 (link state machine)** implements the D2 gate and the upload lock, and adds them to [[ESP Link State Machine]] (an UPDATING state: the loop blocks and no neutral frames are sent while the image is transferred; value 0 sent at every boot).
  - The MSP allow-list ([[MSP Cheat Sheet]]) gets `MSP_SET_ARMING_DISABLED` (99) as **ESP-originated only**: never forwarded from the network.
  - The ESP polls `MSP_STATUS` itself at 5 Hz ([[ADR-009 ESP to FC Link|ADR-009]]), also with no session open, so 500 ms tolerates two missed replies. These replies are used by the ESP and **not forwarded** to the ground PC.
  - ArduinoOTA in core 3.1.2 does not close other sockets: `_runUpdate()` never calls `WiFiUDP::stopAll()` or `WiFiClient::stopAll()` (older 2.x cores did). So the bridge's UDP socket stays open after a failed or stale upload. If the core is updated, check this again; if it does close them, #102 reopens the sockets or restarts after any OTA error.
  - **Bench test** (props off, on the real FC: Betaflight SITL ignores `MSP_SET_ARMING_DISABLED`):
    - an upload is refused while armed, with a session open (SESSION_IDLE, STREAMING, LINK_FAILSAFE) and with no recent `MSP_STATUS`; accepted when disarmed with no session;
    - the upload is aborted if the fresh `MSP_STATUS` in the start callback says armed or does not come;
    - a wrong password is refused while armed, no lock is taken and arming is unaffected;
    - a request left pending while the gate was closed does not flash when the gate reopens, arming works afterwards, and the ground can open a session afterwards;
    - during an upload the FC refuses to arm (`MSP_STATUS` shows `ARMING_DISABLED_MSP`) and the ESP refuses `HELLO`;
    - arming works again after a successful update, after a failed one, after an abort in step 2 (readback fails, `ESP.restart()`), and after a reset during the transfer (value 0 at every boot).
- **ArduinoOTA's password is an MD5 challenge-response.** The password itself is not sent, but the image is neither encrypted nor signed.
  - Anyone who has both passwords and is on the network can flash any image.
  - Signed OTA (RSA-2048 + SHA-256, supported by the ESP8266 core) is planned for Module 3 ([[Roadmap to April 2027]], OD-23).
- An OTA flash ends any session: the ESP reboots and the ground must send a new HELLO.

## 5. Related pages

- [[ADR-007 Portable Protocol Library and Custom ESP Firmware|ADR-007]] · [[ADR-012 ESP UART Pins|ADR-012]] · [[ADR-020 HIL Runner|ADR-020]] · [[ADR-021 Removable ESP Connector|ADR-021]] · [[Safety Rules]] · [[Decision Register]]

> 2026-11-06 (incident GitHub #164): **D5 corrected: an OTA update needs the battery connected.** The ESP is powered from the FC's 5 V pad ([[ADR-013 Wiring and Power|ADR-013]]), and the FC USB does not give enough power to run it, so "battery out, powered from the FC USB" in D5 could not work. Every update is now: **battery connected, props off, FC disarmed, arm and takeover switches off**, [[Safety Rules]] apply. The D2 gate (#102) does **not** refuse an update because a battery is connected; it refuses only when the FC is armed or its state is unknown or stale, or a ground session is open. USB on the ESP itself stays for the first flash and recovery only, with the ESP off the FC (ADR-012). The rest of D5 is unchanged.

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **ArduinoOTA** | The ESP8266 Arduino library that receives a firmware image over Wi-Fi (port 8266). |
| **espota** | The PC tool PlatformIO uses to send an image to ArduinoOTA (`upload_protocol = espota`). |
| **ESP** | The ESP-12F module (ESP8266) on the drone: the Wi-Fi bridge between the ground PC and the FC, wired to FC UART4. |
| **FC** | Flight Controller: the OddityRC F7 AIO running Betaflight. |
| **MD5 challenge-response** | The device sends a random value; the PC answers with a hash of it and the password. This proves the PC knows the password without sending it. |
| **MSP_STATUS** | MSP message from the FC that includes the arming flags. |
| **MSP_SET_ARMING_DISABLED** | MSP 99. Value 1 stops the FC from arming (and disarms it if armed); value 0 allows arming again. |
| **ARMING_DISABLED_MSP** | The Betaflight arming-disable flag set by MSP 99 (bit 16 of the flags in `MSP_STATUS`). |
| **HELLO** | The first message of a ground session ([[Protocol Spec v0]] §3). |
| **MSP** | MultiWii Serial Protocol: the FC's command protocol, spoken by the ESP on UART4. |
| **NO_SESSION** | Link state with no ground session open ([[ESP Link State Machine]]). |
| **SHA-256** | A hash of the image file, noted at every flash so the team knows which image is on the ESP. |
| **SITL** | Software In The Loop: Betaflight running as a program on a PC. |
| **WITH_OTA** | Build flag in `platformio.ini`: 1 compiles ArduinoOTA in. |
| **OTA** | Over-the-air: flashing the ESP firmware over Wi-Fi instead of USB (env `esp_ota`). |
| **Signed OTA** | The device accepts only images signed with the team's private key. |
| **SoftAP** | Software access point: the ESP creates its own Wi-Fi network that the ground PC joins (ADR-006). |
| **STREAMING** | Link state in which the ground is sending setpoints in an open session. |
