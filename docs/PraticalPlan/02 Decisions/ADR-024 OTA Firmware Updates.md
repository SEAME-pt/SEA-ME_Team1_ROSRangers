---
title: ADR-024 — OTA Firmware Updates on the Flight ESP
type: adr
status: proposed
date: 2026-11-04
project: SEA autonomous drone
tags:
  - adr
  - esp8266
  - safety
  - security
---

# ADR-024 — OTA Firmware Updates on the Flight ESP

> [!warning] Status: PROPOSED (2026-11-04, GitHub #62)
> D1 to D3 were decided by the team on 2026-11-04, and the upload lock in D2 on 2026-11-05. D4 and D5 are proposals. The approving review of the PR that adds this ADR accepts them: its last commit before the merge sets this ADR, the Decision Register and Home to Accepted.
> Resolves OD-23 in [[PC_to_ESP]] in part. Signed images are still planned for Module 3.

## 1. Context

- The ESP is wired to FC UART4, and its USB shares UART0 with that link. USB is never connected while the ESP is wired to the FC ([[ADR-012 ESP UART Pins|ADR-012]]). Without OTA, every update means taking the ESP off the FC. Until the [[ADR-021 Removable ESP Connector|ADR-021]] connector is fitted, that means removing soldered wires.
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
- the link is not STREAMING (no ground control session, [[ESP Link State Machine]]);
- the FC is **disarmed** according to its latest `MSP_STATUS`;
- that `MSP_STATUS` is recent (proposed: under 500 ms old).

If the arm state is unknown (no FC reply, UART fault), OTA is refused.

How the gate works (ArduinoOTA, ESP8266 core 3.1.2): `ArduinoOTA.begin()` answers the upload request and checks the password on its own, from a UDP callback; only `ArduinoOTA.handle()` runs the transfer. The firmware calls `handle()` only while the conditions hold. So with the gate closed:
- a wrong password is still refused;
- with the right password, `espota` passes the password check and then times out, because the transfer never starts;
- that request stays pending. When the gate opens again, the ESP tries to connect back, fails (`espota` has given up) and drops it. No image is written.

Once an upload starts (ArduinoOTA's start callback), the ESP locks the drone on the ground until the update ends (team, 2026-11-05):
1. It asks the FC for a **fresh** `MSP_STATUS` and aborts the upload if the FC is armed or does not answer.
2. Only then it sends `MSP_SET_ARMING_DISABLED` (MSP 99, value 1), so the FC **cannot be armed** during the upload. **Never send it without step 1:** Betaflight disarms at once if it gets this command while armed (`msp.c`, 4.5.1 and 2026.6.2).
3. It refuses any `HELLO` until the upload ends, so no ground session can open.

The lock is released with `MSP_SET_ARMING_DISABLED` value 0: by the error callback if the upload fails, and by the new firmware at boot after a successful update. Until then the FC refuses to arm, which is the safe side; an FC power cycle also clears it. The D5 procedure stays as a second layer.

**D3. Credentials are required** (team, 2026-11-04). Two separate secrets protect a flash:
- the SoftAP Wi-Fi password (WPA2, one client at a time, [[ADR-006 Network|ADR-006]]);
- the **OTA password**, `ArduinoOTA.setPassword(OTA_PASS)`.

Both come from `secrets.py` (`~/.config/seame-drone/` or the `SEAME_*` environment variables). Both must be at least 8 characters and different from each other; `secrets.py` stops the build otherwise. They are never committed. A build without an OTA password is not allowed.

**D4. Flight builds keep OTA** (proposal). `WITH_OTA` stays `1` in every env. The D2 gate already covers the flight case, and compiling OTA out would bring back the USB-on-FC problem from ADR-012. A build that turns it off must say so in its env comment.

**D5. Written procedure** (proposal). This is the procedure in `drone/firmware/esp8266/README.md` ("Flash"):
- **Every update:** OTA, [[Safety Rules]] apply.
  - Props off, battery out, powered from the FC USB, arm switch off.
  - No ground session during the upload: the ground node is stopped (the ESP refuses `HELLO` anyway, D2).
  - Note the image SHA-256 when you flash.
- **First flash or recovery:** USB, only with the ESP taken off the FC wires (ADR-012).
  - Back up the flash first.
  - If an image breaks OTA, recovery is over USB the same way.

## 4. Consequences

- **#102 (link state machine)** implements the D2 gate and the upload lock.
  - The ESP polls `MSP_STATUS` itself at 5 Hz ([[ADR-009 ESP to FC Link|ADR-009]]), also with no session open, so 500 ms tolerates two missed replies. These replies are used by the ESP and **not forwarded** to the ground PC.
  - **Bench test** (props off, on the real FC: Betaflight SITL ignores `MSP_SET_ARMING_DISABLED`):
    - an upload is refused while armed, while STREAMING, and with no recent `MSP_STATUS`; accepted when disarmed with no session;
    - a wrong password is refused while armed;
    - a request left pending while the gate was closed does not flash when the gate reopens;
    - during an upload the FC refuses to arm (`MSP_STATUS` shows `ARMING_DISABLED_MSP`) and the ESP refuses `HELLO`;
    - arming works again after a successful update and after a failed one.
- **ArduinoOTA's password is an MD5 challenge-response.** The password itself is not sent, but the image is neither encrypted nor signed.
  - Anyone who has both passwords and is on the network can flash any image.
  - Signed OTA (RSA-2048 + SHA-256, supported by the ESP8266 core) is planned for Module 3 ([[Roadmap to April 2027]], OD-23).
- An OTA flash ends any session: the ESP reboots and the ground must send a new HELLO.

## 5. Related pages

- [[ADR-007 Portable Protocol Library and Custom ESP Firmware|ADR-007]] · [[ADR-012 ESP UART Pins|ADR-012]] · [[ADR-020 HIL Runner|ADR-020]] · [[ADR-021 Removable ESP Connector|ADR-021]] · [[Safety Rules]] · [[Decision Register]]

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
| **OTA** | Over-the-air: flashing the ESP firmware over Wi-Fi instead of USB (env `esp_ota`). |
| **Signed OTA** | The device accepts only images signed with the team's private key. |
| **SoftAP** | Software access point: the ESP creates its own Wi-Fi network that the ground PC joins (ADR-006). |
| **STREAMING** | Link state in which the ground is sending setpoints in an open session. |
