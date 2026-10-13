# Scripts: flight controller, ESP and transparent bridge

| Script | What it does | Changes hardware? |
|---|---|---|
| `fc_cli.py` | Betaflight CLI: dump config, run commands, enable the ESP UART, back up the firmware | Only with `--write` (a `diff all` is saved first) |
| `esp_at.py` | ESP8266 AT firmware: query, back up, set up / undo the transparent bridge, restore | `setup-bridge`, `exit-bridge`, `restore`, `raw` |
| `bridge_test.py` | MSP tests (info, round-trip time, loss, attitude) over USB or through the bridge; fake FC | Never: read-only MSP only |
| `msp.py` | Small MSP v1 library used by the scripts | – |

## Setup

```bash
python3 -m venv .venv && . .venv/bin/activate
pip install -r scripts/requirements.txt
sudo usermod -aG dialout $USER     # serial ports; log out and in again
sudo apt install dfu-util          # only for fc_cli.py backup-firmware
pytest tests/scripts               # unit tests, no hardware needed
```

## Safety rules for every script

- **Props off. No battery** unless a test step says so. Everything here works on USB power.
- **Never connect the ESP USB and the FC to the ESP TX/RX at the same time.** The USB-serial chip shares those pins.
- **Backups never go in Git.** They are written to `~/Projects/SEAME/backups/` and can contain Wi-Fi passwords.
- **The bridge is unauthenticated.** Use it only on the ESP's own WPA2 SoftAP, props off. It is a test spike, not flight software (see ADR-007).

## Transparent bridge: test cycles

```text
 PC (192.168.4.2:5760) ──UDP── ESP SoftAP (192.168.4.1:5760) ──UART 115200── FC UART4 (MSP)
```

### Cycle 0 · Baseline over USB (no ESP)

```bash
python3 scripts/fc_cli.py dump
python3 scripts/bridge_test.py --serial /dev/ttyACM0 info
python3 scripts/bridge_test.py --serial /dev/ttyACM0 ping -n 500 --rate 50 --csv baseline_usb.csv
```

### Cycle 1a · Bridge without the drone (ESP on USB, the PC plays the FC)

1. `python3 scripts/esp_at.py backup` (once).
2. `python3 scripts/esp_at.py setup-bridge --ssid drone-<team> --channel 6` (asks for the WPA2 password).
3. Join `drone-<team>` and give this PC the static IP `192.168.4.2/24`.
4. Terminal 1: `python3 scripts/bridge_test.py fake-fc --serial /dev/ttyUSB0`
5. Terminal 2: `python3 scripts/bridge_test.py --udp 192.168.4.1:5760 ping -n 500 --rate 50 --csv bridge_fake.csv`

Pass: no loss at 50 Hz, round-trip times recorded.

### Cycle 1b · Bridge with the flight controller (props off, USB power)

Before: instructor OK for soldering on the lab drone, `ESP_to_FC` §10 checks done (T4/R4 continuity, no shorts, 5 V rail).

1. `python3 scripts/fc_cli.py --write enable-esp-uart` (UART4 = MSP 115200).
2. Unplug the ESP USB. Wire FC **T4 → D1 mini RX**, **R4 → TX**, **5V → 5V**, **GND → G**.
3. Power the FC by USB (no battery). The ESP boots straight into the bridge.
4. `python3 scripts/bridge_test.py --udp 192.168.4.1:5760 info` → must show API 1.46, BTFL 4.5.1.
5. `python3 scripts/bridge_test.py --udp 192.168.4.1:5760 ping -n 1000 --rate 50 --csv bridge_fc.csv`
6. `python3 scripts/bridge_test.py --udp 192.168.4.1:5760 attitude` and tilt the drone by hand.

### Undo

```bash
python3 scripts/esp_at.py exit-bridge     # ESP back to AT mode (ESP on USB, FC wires off)
python3 scripts/esp_at.py restore         # factory AT settings
```
