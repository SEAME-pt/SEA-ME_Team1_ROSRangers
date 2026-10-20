# Scripts: flight controller, ESP and transparent bridge

For the full execution-order walkthrough, see
[EXECUTION_GUIDE.md](./EXECUTION_GUIDE.md).
For the complete command catalog, see [COMMANDS.md](./COMMANDS.md).

## One command interface

The recommended way to navigate the scripts is the unified interface:

```bash
python3 scripts/seame.py
```

This opens an interactive menu. For copy/paste and automation, provide the
command directly:

```bash
python3 scripts/seame.py list
python3 scripts/seame.py network connect
python3 scripts/seame.py msp info
python3 scripts/seame.py test list
```

> [!warning] The ESP is soldered to the FC
> Never plug in the ESP's USB. The ESP stays in the production scenario
> (`seame-drone-0850`). `seame.py` refuses the commands that need the ESP on USB
> (`esp ...`, `network setup`, `msp cycle1a`, ...) unless `--esp-on-usb` comes
> first. See [COMMANDS.md](./COMMANDS.md).

The original specialist scripts remain available, so existing commands do not
need to change.

Two network scenarios, one script each. Both configure the ESP **and** this PC
automatically; `setup` needs the ESP on its own USB, so it was done before soldering.

| Scenario | Script | Network | Laptop internet | Use |
|---|---|---|---|---|
| **Test** | `setup_test.py` | ESP joins **SEAME** as a station (DHCP) | kept | bench work, props off, every day |
| **Production** | `setup_production.py` | ESP creates its own **WPA2** network `seame-drone-xxxx` | lost while connected | cage tests and flights |

| Script | What it does | Changes hardware? |
|---|---|---|
| `seame.py` | Interactive and direct command interface for all script groups | Depends on selected command |
| `setup_test.py` | `setup`, `status` | ESP settings |
| `setup_production.py` | `setup`, `connect`, `disconnect`, `status`, `reboot-test` | ESP settings, a NetworkManager profile |
| `msp_check.py` | `info`, `ping`, `attitude`, `rc`, `link-watch`, `status-watch`: to the active scenario, or `--usb`; `cycle1a`, `fake-fc`, `usb-relay` need the ESP on USB | Read-only MSP, except `rc-order --write` |
| `fc_cli.py` | Betaflight CLI: `dump`, `run`, `reboot`, `compare-diffs`, `enable-esp-uart`, `backup-firmware` | Only with `--write` (a `diff all` is saved first); leaving the CLI reboots the FC |
| `esp_at.py` | Manual AT work: `query`, `backup`, `exit-bridge`, `restore`, `raw` | `exit-bridge`, `restore`, `raw` |
| `msp.py`, `bridge_config.py` | Libraries used by the scripts | – |

Local state lives in `~/.config/seame-drone/` (outside Git): `bridge.json` (active scenario, addresses)
and `secrets.json` (Wi-Fi passwords, mode 600). `msp_check.py` reads the active scenario by itself.

## Setup (once per laptop)

```bash
cd SeameProject
python3 -m venv .venv && . .venv/bin/activate
pip install -r scripts/requirements.txt
sudo usermod -aG dialout $USER      # serial ports; log out and in again
sudo apt install dfu-util           # only for fc_cli.py backup-firmware
pytest tests/scripts                # unit tests, no hardware needed
```

## Safety rules

- **Props off. No battery** unless a step says so. Everything here runs on USB power.
- **Never connect the ESP USB and the FC to the ESP TX/RX at the same time.** The USB-serial chip shares those pins.
- **The bridge has no authentication.** In the test scenario anybody on SEAME can send packets to it:
  bench only, props off. Flights only in the production scenario, and the long-term answer is ADR-007 (HMAC, sessions).
- **Backups and secrets never go in Git.** They live in `~/Projects/SEAME/backups/` and `~/.config/seame-drone/`.

## Test cycles

```text
Test:        PC (SEAME, DHCP) ──UDP 5760── ESP (SEAME, DHCP)  ──UART 115200── FC UART4 (MSP)
Production:  PC 192.168.4.2   ──UDP 5760── ESP AP 192.168.4.1 ──UART 115200── FC UART4 (MSP)
```

### Cycle 0 · Baseline over USB (no ESP)

```bash
python3 scripts/fc_cli.py dump
python3 scripts/msp_check.py --usb info
python3 scripts/msp_check.py --usb ping -n 500 --csv baseline_usb.csv
```

### Cycle 1a · Bridge without the drone

ESP on its USB, FC **not** wired to it. The PC plays the FC on the ESP USB port.

```bash
python3 scripts/setup_test.py                     # asks the SEAME Wi-Fi password once
python3 scripts/msp_check.py cycle1a --csv cycle1a_test.csv
```

Pass: `Cycle 1a PASS` (≤ 1 % loss at 50 Hz, no bad checksums). Optional: repeat with `setup_production.py`,
`setup_production.py connect`, `msp_check.py cycle1a`, then `setup_production.py disconnect`.

### Cycle 1b · Bridge with the flight controller (props off, USB power)

Before: instructor OK for soldering on the lab drone, `ESP_to_FC` §10 checks done (T4/R4 continuity, no shorts, 5 V rail).

1. ESP on USB: `python3 scripts/setup_test.py` (refreshes the PC address).
2. `python3 scripts/fc_cli.py --write enable-esp-uart` (UART4 = MSP 115200).
3. Unplug the ESP USB. Wire FC **T4 → RX**, **R4 → TX**, **5V → 5V**, **GND → G**.
4. Power the FC by USB (no battery). The ESP boots straight into the bridge.
5. `python3 scripts/msp_check.py info` → API 1.46, BTFL 4.5.1.
6. `python3 scripts/msp_check.py ping -n 1000 --csv cycle1b_test.csv`
7. `python3 scripts/msp_check.py attitude` and tilt the drone by hand.

### When something does not answer

| Symptom | Check |
|---|---|
| `setup_test.py` cannot ping the ESP | SEAME may isolate clients: use the production scenario |
| `msp_check.py` gets no answers in the test scenario | `setup_test.py status`: if this PC has a new IP, run `setup_test.py` again (ESP on USB) |
| `esp_at.py query` gets no `OK` | The ESP is in transparent mode: `esp_at.py exit-bridge` |
| Production: no network `seame-drone-xxxx` | The ESP needs power; wait 5 s after boot; `setup_production.py connect` again |

### Undo

```bash
python3 scripts/setup_production.py disconnect   # back to SEAME
python3 scripts/esp_at.py exit-bridge            # ESP back to AT mode (ESP on USB, FC wires off)
python3 scripts/esp_at.py restore                # factory AT settings
```
