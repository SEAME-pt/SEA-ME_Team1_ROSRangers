# ESP8266 firmware: transparent UART↔UDP bridge and gateway

Custom Arduino firmware for the drone's ESP-12F (ADR-007). It replaces the Espressif AT 1.7.4 firmware since 2026-10-21. The default build (`esp`, `esp_ota`) is still a **transparent** bridge: bytes pass through unchanged, nothing is parsed, there is no allow-list (Study Plan §5). The **gateway** (`esp_gateway`, #101) is the allow-listed replacement; see "Gateway" below.

```text
ground PC 192.168.4.2 ──UDP 5760── ESP SoftAP 192.168.4.1 ──UART0 115200── FC UART4 (MSP)
```

Spike: #30 (ESP Bench Test Plan Stage 2). Results and open questions are in that issue and in `docs/PraticalPlan/05 Hardware/ESP Bench Test Plan.md` §4.

## What changed from the AT firmware

| | AT 1.7.4 (until 2026-10-21) | Custom bridge (now) |
|---|---|---|
| Firmware | Espressif AT, set up with `AT+CWSAP` / `AT+CIPSTART` | `src/main.cpp`, Arduino core 3.1.2 (`espressif8266@4.2.1`) |
| UART → UDP packets | One packet every 20 ms (or 2048 bytes) | One packet as soon as the UART has been quiet for 1 ms: an MSP reply leaves as one packet |
| RTT, PC next to the drone | Median ~50 ms, p99 148–186 ms | Median ~12 ms, p99 54 ms (`s1-05_new_fw_fix_10_min`, 2026-10-21) |
| Wi-Fi settings | Stored in the ESP flash by AT commands | Compiled in at build time from `~/.config/seame-drone/` (`secrets.py`) |
| Channel change | `network setup --channel`, ESP on USB | Edit `bridge.json`, rebuild, OTA upload |
| Updates | USB only | OTA over Wi-Fi (port 8266, password), USB for the first flash and recovery |
| Flash map | 512 KB + 512 KB header, boot warning `mismatch map` | 4 MB (DOUT), `eagle.flash.4m1m.ld`: ~1 MB for the app (334 KB used), no warning |
| Sleep | Set by hand (`AT+SLEEP`) | `WIFI_NONE_SLEEP` always |
| AT commands | Yes | No: `esp_at.py` checks that send `AT` fail |

Same as before: SoftAP `seame-drone-0850`, 192.168.4.1, at most 1 client, UDP 5760 both ways, replies go to whoever sent the last packet. Nothing is printed on UART0 except the boot ROM (~731 bytes at 74880 baud at power-up, no MSP headers, ESP-S1-03) and crash dumps.

Not there yet in the bridge: no TCP session (ADR-005), no version query, no counters or status packet, no reboot command. The normal build has no status port; the timestamps on UDP 5770 exist only in the diagnostic env `esp_diag_timing` (see below).

**Env rename (2026-10-22).** The envs were called `d1_mini*`, but the board is an ESP-12F module, not a D1 mini. They are now `esp*` with `board = esp12e`. The binary is the same code: compared with the 2026-10-21 image (`firmware.bin` SHA-256 `836717987db3…`), only 12 bytes differ (the board name string `d1_mini` → `esp12e`, a build stamp and the image checksum).

## Envs

| Env | Upload | Use |
|---|---|---|
| `esp` (default) | USB, 460800 baud | First flash and recovery. CI builds this one |
| `esp_ota` | Wi-Fi, `espota` to 192.168.4.1 | Every later update of the flight ESP (ADR-024). The HIL runner flashes its spare ESP over USB with `esp` (`hil.yaml`) |
| `esp_diag_udp` | USB | Bench diagnosis: UDP → UART only (`WITH_UART=0`) |
| `esp_diag_uart` | USB | Bench diagnosis: UART → UDP only (`WITH_UDP=0`) |
| `esp_diag_timing` | Wi-Fi (OTA) | Bench diagnosis: four timestamps per request on UDP 5770 (`WITH_TIMING=1`), see below |
| `esp_diag_tcp` | Wi-Fi (OTA) | S1-07: TCP echo server on TCP 5759 next to the bridge, plus the timestamps (`WITH_TCP_ECHO=1 WITH_TIMING=1`) |
| `esp_diag_wdt` | Wi-Fi (OTA) | S1-10: hangs on command (UDP 5771) so the watchdog resets the ESP (`WITH_WDT_TEST=1`) |
| `esp_gateway` | Wi-Fi (OTA) | **Gateway firmware (#101)**, not the default yet: allow-listed FC link, no bridge. Bench view on UDP 5770, see "Gateway" below |

## Build

1. **PlatformIO 6.x** from pip. The system `pio` 4.3.4 is too old.
   ```bash
   pip install -U platformio
   pio --version
   ```
2. **Secrets.** `secrets.py` runs before every build and compiles in `AP_SSID`, `AP_PASS`, `OTA_PASS` and `AP_CHANNEL`. Nothing secret is in the repo. It reads, in order:
   - environment variables `SEAME_AP_SSID`, `SEAME_AP_PASS`, `SEAME_OTA_PASS`;
   - otherwise `~/.config/seame-drone/bridge.json` (SSID, channel, default 6) and `secrets.json` (`wifi:<ssid>`, written by `scripts/setup_production.py`, and `ota`, which you add by hand: no script writes it yet).

   Both passwords must be at least 8 characters and different from each other, or the build stops (ADR-024 D3). The build also stops if `bridge.json` is the TEST scenario: run `setup_production.py` first. The current AP password is still the tutorial example and must be changed.
3. **Build:**
   ```bash
   pio run -d drone/firmware/esp8266 -e esp
   ```
   Build-only check without the config folder (what CI does):
   ```bash
   SEAME_AP_SSID=ci-dummy-ssid SEAME_AP_PASS=ci-dummy-ap-pass SEAME_OTA_PASS=ci-dummy-ota-pass pio run -d drone/firmware/esp8266 -e esp
   ```
   The image is `.pio/build/<env>/firmware.bin`.

## Flash

This is the procedure of ADR-024 D5. Follow `docs/PraticalPlan/05 Hardware/Safety Rules.md`: props off. An OTA update needs the battery connected: the ESP is powered from the FC's 5 V pad, and the FC USB does not give it enough power (ADR-024 entry of 2026-11-06, #164). After every flash, note the image SHA-256 (`sha256sum .pio/build/<env>/firmware.bin`) in #30 or in `backup/README.md`: the firmware cannot report its version.

### First flash or recovery (USB)

> [!warning] Never plug in the ESP USB while the ESP is wired to the FC
> Its USB chip and the FC T4 pin would both drive the ESP RX line (ADR-012). Take the ESP off the FC first. There is no exception (ADR-024 D5).

```bash
pio run -d drone/firmware/esp8266 -e esp -t upload
```

Back up the flash before the first custom flash (`scripts/esp_at.py backup`, see `backup/README.md`). That file also lists the known-good images and how to restore one.

### Every later update (OTA)

1. **Battery connected** (the ESP needs it), props off, FC disarmed, **arm switch and takeover (MSP Override) switch off**: the ESP sends no neutral frames while it updates and reboots.
2. No ground session: stop the ground node (`flight_interface`).
3. Join the drone network: `python3 scripts/seame.py network connect`.
4. Upload (the OTA password comes from `secrets.py`). The current firmware does **not** check the arm state or the session yet: steps 1 and 2 are the only protection until #102 adds the ADR-024 gate and lock.
   ```bash
   pio run -d drone/firmware/esp8266 -e esp_ota -t upload
   ```
5. The ESP reboots and the PC loses the network for 5–20 s. Wait until it has rejoined before any test.
6. Check the link: `python3 scripts/seame.py msp info` (expect API 1.46), and note the SHA-256. After #102: if the FC refuses to arm (`ARMING_DISABLED_MSP` in `MSP_STATUS`), the image did not release the ADR-024 lock; power-cycle the FC (unplug its USB).

If a build breaks OTA, the only way back is a USB recovery (above).

## Gateway (#101, env `esp_gateway`)

The gateway replaces the transparent bridge step by step. Until it has passed its bench tests, `esp` stays the bridge and the gateway is its own env.

What it does now (ADR-009):
- **One scheduler owns the FC UART** (`sd::FcLink` in `drone/lib/protocol`). It polls `MSP_ATTITUDE` at 25 Hz and `MSP_STATUS` / `MSP_ANALOG` at 5 Hz, one request at a time (50 ms reply timeout), and keeps the latest values with their age.
- **Allow-list:** every MSP frame it sends is built by `sd::msp::encode_request`, which refuses any command not in the MSP Cheat Sheet list (1, 101, 105, 108, 110, 200). Nothing from the network is ever written to the UART. There is no UDP 5760 bridge in this build, so `seame.py msp` over Wi-Fi does not work with it: flash the bridge back for those tools.
- **Never sends RC.** Sessions, `SETPOINT` → `MSP_SET_RAW_RC`, the failsafe and `STATE` are #102. OTA is still ungated here, as in the bridge (the ADR-024 gate is #102).
- **Still missing for #102:** an `MSP_API_VERSION` check at boot (the state machine's BOOT → NO_SESSION), reading `override_active` from `MSP_STATUS`, and a separate internal encoder for `MSP_SET_ARMING_DISABLED` (99, ADR-024 lock): `encode_request` refuses it on purpose.

Flash it like any OTA update (battery connected, props off, FC disarmed, arm and takeover switches off, see "Flash"), with `-e esp_gateway`. Step 6 of the OTA steps (`seame.py msp info`) does not work on the gateway (no 5760 bridge): check with the bench view below instead. To go back to the bridge, flash `-e esp_ota`.

**Bench view (read-only, diagnostic):** any datagram to UDP 5770 gets one text line back:

```bash
echo | nc -u -w1 192.168.4.1 5770
# armed=0 arming_flags=0x00000000 status_age_ms=87 roll=-12 pitch=4 heading=271 vbat_cV=1110 att_age_ms=12 req=1520 rep=1520 timeout=0 bad_cksum=0 too_long=0 err=0 unexpected=0
```

The bench view is for this pre-release env only: it is compiled in with `WITH_STATUS_VIEW=1` (set in `esp_gateway`) and is dropped when #102 sends `STATE`.

A value that has not arrived yet (or is older than 10 s) prints as `-1`: `armed`, `arming_flags` and `status_age_ms` for `MSP_STATUS`, `roll` / `pitch` / `heading` / `att_age_ms` for `MSP_ATTITUDE`, `vbat_cV` for `MSP_ANALOG`. `req` and `rep` should grow together (about 35 per second); `timeout` growing means the FC does not answer (wiring, baud, MSP not enabled on UART4).

## Diagnosis test: link dead periods (2026-10-22)

**Symptom.** Two 10-min `test latency` runs at close range on 2026-10-22 (`s1-05_custom_close_run2`, `run3`) had periods of **100 % loss** lasting 5–188 s, at the start and also mid-run. Between them the link was better than ever: p99 53 / 80 ms, only 4 / 3 gaps over 250 ms.

| Run | Dead periods | Total loss |
|---|---|---|
| run2 (13:01) | 0–188 s, then 48 s, 16 s, 5 s | 43 % |
| run3 (13:16) | 0–21 s, then 12 s | 5.6 % |

**Already ruled out**

- *The PC rejoining after OTA.* The PC had been on `seame-drone-0850` for ~30 s before each run and stayed connected (system log: no disconnect, no beacon loss until after each run).
- *Weak radio.* Radio loss is scattered; here it is all or nothing.
- *An ESP reboot.* The PC would have been disconnected.
- *The env rename.* Same code as the 2026-10-21 image (see above).

**Suspects**

1. The soldered UART4 joints (intermittent contact). This fits the all-or-nothing pattern and the few bad checksums (4 and 2). The ESP_to_FC §10 continuity evidence is still missing (#69).
2. The FC stops answering on UART4 (reboot, something holding its USB CLI).

**The test.** Three terminals at the same time, props off, no battery, FC on USB. Write down the start time.

```bash
# 1. the bridge, 10 min at 50 Hz (CSV: s1-05_diag_run4.csv). Request i is sent at start + i × 20 ms
date +%T; python3 scripts/seame.py test latency

# 2. ICMP to the ESP: answered by the ESP itself, the FC is not involved (-D = timestamps)
ping -D -i 0.2 192.168.4.1 | tee results/icmp_run4.txt

# 3. the FC over its own USB, 5 Hz for 10 min
python3 scripts/seame.py msp --usb ping -n 3000 --rate 5 --timeout 0.15 --csv usb_run4.csv
```

**Reading it** (during a dead period in terminal 1):

| ICMP (2) | FC USB (3) | Meaning | Next step |
|---|---|---|---|
| OK | OK | The break is between the ESP and the FC (UART4) | Joint check below |
| Dead | any | The ESP or the Wi-Fi after all | Rerun with `esp_diag_udp` / `esp_diag_uart` to split the directions |
| OK | Dead | The FC is not answering | Check the FC (uptime, reboot, CLI open) |

**Joint check.** With `python3 scripts/seame.py msp attitude` running, press each UART4 joint (T4, R4, GND) gently with an insulated tool. If the readout freezes when you touch one, that joint is the cause. Post the result on #30 and #69.

Post run2, run3 and this run on #30 as they are, failures included.

## Diagnosis: timestamps per request (`esp_diag_timing`)

For each request the ESP records four `micros()` stamps and sends them every 100 ms to the PC on UDP 5770. The bridge on 5760 is unchanged, and the normal `esp` / `esp_ota` build is byte for byte the same (`WITH_TIMING` defaults to 0).

| Stamp | When | |
|---|---|---|
| `esp_in` | The UDP request is read | Gap between two `esp_in` = silence on the Wi-Fi PC → ESP |
| `uart_out` | The request is written to the FC UART | `uart_out − esp_in` = the ESP before the FC |
| `uart_in` | The first reply byte arrives from the FC | `uart_in − uart_out` = the FC + the UART (0 = no FC reply in 500 ms) |
| `esp_out` | The reply is handed to the Wi-Fi stack | `esp_out − uart_in` = the ESP after the FC (includes the 1 ms idle wait) |

Each report also has the ESP's longest loop time in those 100 ms (an ESP stall shows up there). The ESP can't see time on air: a slow PC RTT with small ESP stages means the time was spent on Wi-Fi.

```bash
pio run -d drone/firmware/esp8266 -e esp_diag_timing -t upload           # OTA
python3 scripts/esp_timing.py listen --csv s1-09_run1_esp.csv            # terminal 1, start first
python3 scripts/msp_check.py ...                                         # terminal 2, the test as usual
# Ctrl+C in terminal 1 after the test; split the loss with the PC's numbers:
python3 scripts/esp_timing.py summary s1-09_run1_esp.csv --sent 30000 --pc-lost 12
pio run -d drone/firmware/esp8266 -e esp_ota -t upload                   # back to the normal firmware
```

The reports add about 3 kB/s of ESP → PC traffic. Record which runs used this build.

## S1-07: TCP and UDP at the same time (`esp_diag_tcp`)

The ESP accepts one TCP client on 5759 (a second one is refused, as in ADR-005) and echoes what it receives. It only moves what the TCP send buffer can take, so a TCP stall can't block the loop. The UDP bridge and the timestamps on 5770 run as usual. Setup as for S1-05: PC next to the drone, props off, PC Wi-Fi power save off.

```bash
pio run -d drone/firmware/esp8266 -e esp_diag_tcp -t upload            # OTA, then wait for the network
python3 scripts/esp_timing.py listen --csv s1-07_run1_esp.csv          # terminal 1
python3 scripts/tcp_load.py --duration 600 --csv s1-07_run1_tcp.csv    # terminal 2: 20 kbit/s, new connection every 30 s
python3 scripts/seame.py msp ping --stream --duration 600 --csv s1-07_run1.csv   # terminal 3
```

Pass: the longest silence of the `msp ping` run is at most **203 ms**, the S1-05 maximum. `tcp_load.py` also reports the TCP echo times; slow TCP with fine UDP is allowed, as ADR-005 says UDP decides liveness.

## S1-10: watchdog recovery (`esp_diag_wdt`)

| Command on UDP 5771 | ESP |
|---|---|
| `REASON?` | `REASON,<reset reason>,<uptime ms>` |
| `HANG` | Answers `HANGING`, then loops without yielding: the software watchdog resets it after about 3 s |
| `HANG_HW` | Same with the software watchdog off: the hardware watchdog resets it (about 8 s) |

Props off, no battery. FC on USB, so the FC can be watched while the ESP reboots. **Stop every MSP stream first**: the bridge forwards whatever the PC sends, so a running stream would look like control output.

```bash
pio run -d drone/firmware/esp8266 -e esp_diag_wdt -t upload            # OTA, then wait for the network
python3 scripts/seame.py msp --usb status-watch --csv s1-10_fc.csv     # terminal 1, FC over USB
python3 scripts/esp_wdt.py cycle -n 5 --csv s1-10_sw.csv               # terminal 2, software watchdog
python3 scripts/esp_wdt.py cycle -n 2 --hw --csv s1-10_hw.csv          # hardware watchdog
pio run -d drone/firmware/esp8266 -e esp_ota -t upload                 # back to the normal firmware
```

Pass: every cycle comes back by itself with the expected reason (`Software Watchdog` / `Hardware Watchdog`) and a small uptime; `status-watch` shows the FC never armed, no change in the arming flags, and no FC reboot. This is the bridge part of REQ-SAF-016. "Forwarding only inside a session" waits for the gateway firmware.

## Files

| File | What |
|---|---|
| `platformio.ini` | Envs and platform version |
| `secrets.py` | Pre-build script: Wi-Fi and OTA secrets, channel |
| `src/main.cpp` | The bridge (and the `WITH_*` diagnosis switches) |
| `../../../scripts/esp_timing.py` | Reads the `esp_diag_timing` reports |
| `../../../scripts/tcp_load.py` | S1-07 TCP load against `esp_diag_tcp` |
| `../../../scripts/esp_wdt.py` | S1-10 hang / reason cycles against `esp_diag_wdt` |
| `backup/README.md` | Flash backups (AT and custom), SHA-256, restore |
