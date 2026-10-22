# ESP8266 firmware: transparent UART↔UDP bridge

Custom Arduino firmware for the drone's ESP-12F (ADR-007). It replaces the Espressif AT 1.7.4 firmware since 2026-10-21. It is still a **transparent** bridge: bytes pass through unchanged, nothing is parsed, there is no allow-list yet (Study Plan §5).

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

Not there yet: no TCP session (ADR-005), no version query, no counters or status packet, no reboot command. A status port on UDP 5761 was tried on 2026-10-22 and removed again.

**Env rename (2026-10-22).** The envs were called `d1_mini*`, but the board is an ESP-12F module, not a D1 mini. They are now `esp*` with `board = esp12e`. The binary is the same code: compared with the 2026-10-21 image (`firmware.bin` SHA-256 `836717987db3…`), only 12 bytes differ (the board name string `d1_mini` → `esp12e`, a build stamp and the image checksum).

## Envs

| Env | Upload | Use |
|---|---|---|
| `esp` (default) | USB, 460800 baud | First flash and recovery. CI builds this one |
| `esp_ota` | Wi-Fi, `espota` to 192.168.4.1 | Every later update. `hil.yaml` uses it |
| `esp_diag_udp` | USB | Bench diagnosis: UDP → UART only (`WITH_UART=0`) |
| `esp_diag_uart` | USB | Bench diagnosis: UART → UDP only (`WITH_UDP=0`) |

## Build

1. **PlatformIO 6.x** from pip. The system `pio` 4.3.4 is too old.
   ```bash
   pip install -U platformio
   pio --version
   ```
2. **Secrets.** `secrets.py` runs before every build and compiles in `AP_SSID`, `AP_PASS`, `OTA_PASS` and `AP_CHANNEL`. Nothing secret is in the repo. It reads, in order:
   - environment variables `SEAME_AP_SSID`, `SEAME_AP_PASS`, `SEAME_OTA_PASS`;
   - otherwise `~/.config/seame-drone/bridge.json` (SSID, channel, default 6) and `secrets.json` (`wifi:<ssid>` and `ota`), as written by `scripts/setup_production.py`.

   Both passwords must be at least 8 characters, or the build stops. The build also stops if `bridge.json` is the TEST scenario: run `setup_production.py` first. The current AP password is still the tutorial example and must be changed.
3. **Build:**
   ```bash
   pio run -d drone/firmware/esp8266 -e esp
   ```
   Build-only check without the config folder (what CI does):
   ```bash
   SEAME_AP_PASS=ci-dummy-pass SEAME_OTA_PASS=ci-dummy-pass pio run -d drone/firmware/esp8266 -e esp
   ```
   The image is `.pio/build/<env>/firmware.bin`. Note its SHA-256 (`sha256sum`) in #30 or in `backup/README.md` when you flash it: the firmware cannot report its version.

## Flash

Follow `docs/PraticalPlan/05 Hardware/Safety Rules.md`: props off, no battery.

### First flash or recovery (USB)

> [!warning] Never plug in the ESP USB while the ESP is wired to the FC
> Its USB chip and the FC T4 pin would both drive the ESP RX line (ADR-012). Take the ESP off the FC first, or follow a written, reviewed procedure.

```bash
pio run -d drone/firmware/esp8266 -e esp -t upload
```

Back up the flash before the first custom flash (`scripts/esp_at.py backup`, see `backup/README.md`). That file also lists the known-good images and how to restore one.

### Every later update (OTA)

1. Drone powered from the FC USB, no battery, props off.
2. Join the drone network: `python3 scripts/seame.py network connect`.
3. Upload (the OTA password comes from `secrets.py`):
   ```bash
   pio run -d drone/firmware/esp8266 -e esp_ota -t upload
   ```
4. The ESP reboots and the PC loses the network for 5–20 s. Wait until it has rejoined before any test.
5. Check the link: `python3 scripts/seame.py msp info` (expect API 1.46).

If a build breaks OTA, the only way back is a USB recovery (above).

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

## Files

| File | What |
|---|---|
| `platformio.ini` | Envs and platform version |
| `secrets.py` | Pre-build script: Wi-Fi and OTA secrets, channel |
| `src/main.cpp` | The bridge |
| `backup/README.md` | Flash backups (AT and custom), SHA-256, restore |
