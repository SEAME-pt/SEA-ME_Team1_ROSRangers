# SEA:ME script commands

Use [`seame.py`](./seame.py) as the single entry point. It can be used as an
interactive menu:

```bash
python3 scripts/seame.py
```

The interactive menu is numeric. Choose a group first, then its scenario or
action. For example:

```text
Choose a command group:
  1) network
  2) fc
  3) msp
  4) esp
Select an option: 1

Choose a network scenario:
  1) Test network (SEAME Wi-Fi)
  2) Production network (drone access point)
Select an option: 2
```

The screen is cleared whenever a new menu opens, so only the current options
remain visible. Type `b` to go back one menu or `q` to exit immediately from
any menu. Commands needing extra options ask for them after the numbered
selection. Direct command-line usage remains available for automation and
advanced options. After a command finishes, the interactive interface stays
open and clears the screen before showing the next menu. Choose `q` when you
are finished. The command output remains visible until you press Enter at the
`Press Enter to return to the menu...` prompt.

It also accepts commands directly, which is useful for the execution guide,
automation, and copy/paste:

```bash
python3 scripts/seame.py <command> [options]
```

The interface dispatches to the existing specialist scripts. Their complete
low-level options remain available with `--help`.

## MSP latency and loss options

The most useful bridge test is:

```bash
python3 scripts/seame.py msp ping -n 30000 --rate 50 --timeout 0.5 \
  --csv bridge_10min.csv
```

This sends 30,000 read-only `MSP_API_VERSION` requests at 50 requests per
second, which is approximately 10 minutes. Each request waits up to 0.5
seconds for a matching response.

### Options available for `msp ping`

| Option | Meaning | Default |
|---|---|---|
| `-n N` | Number of requests to send. | `100` |
| `--rate HZ` | Request rate in requests per second. Use `0` for maximum speed. | `50` |
| `--timeout SECONDS` | Maximum time to wait for each response. | `0.5` seconds |
| `--csv FILE` | Save detailed request data and an analytics CSV. | Not saved |
| `--quiet` | Hide periodic progress messages. | Off |
| `--trace` | Print raw MSP frames sent and received. | Off |

The shared MSP options are:

| Option | Meaning | Default |
|---|---|---|
| `--usb` | Use the FC directly over USB instead of the ESP bridge. | Off |
| `--fc-port PORT` | Manually select the FC USB serial port. | Auto-detect |
| `--esp IP` | Override the ESP IP from the saved scenario. | Saved profile |
| `--esp-port-usb PORT` | Select the ESP USB port for `cycle1a` or `fake-fc`. | Auto-detect |

The options can be placed before or after the MSP subcommand when using
`seame.py`:

```bash
# Shared option before the subcommand
python3 scripts/seame.py msp --timeout 1 ping -n 30000 --rate 50

# Equivalent form, easier to read
python3 scripts/seame.py msp ping -n 30000 --rate 50 --timeout 1
```

### Examples

```bash
# Quick test: 100 requests at 50 Hz (about 2 seconds)
python3 scripts/seame.py msp ping

# One-minute test: 3,000 requests at 50 Hz
python3 scripts/seame.py msp ping -n 3000 --rate 50 --csv bridge_1min.csv

# Ten-minute test: 30,000 requests at 50 Hz
python3 scripts/seame.py msp ping -n 30000 --rate 50 --csv bridge_10min.csv

# Ten-minute test with a 1-second response timeout
python3 scripts/seame.py msp ping -n 30000 --rate 50 --timeout 1 \
  --csv bridge_10min_timeout_1s.csv

# Five low-rate requests with raw frame output
python3 scripts/seame.py msp ping -n 5 --rate 1 --trace
```

The printed `lost` value means requests without a matching response before
the timeout. When `--csv FILE` is used, two files are created:

- `FILE`: raw request data with `request`, `status`, and `rtt_ms` columns.
  Every attempted request appears, including `status=timeout` rows.
- `FILE` with `_analytics` before the extension: summary metrics matching the
  terminal output, including request count, answers, loss percentage, bad
  checksums, minimum, mean, median, p95, p99, maximum, and standard deviation.

For example:

```text
results/bridge_10min.csv
results/bridge_10min_analytics.csv
```

The raw file is useful for finding when timeouts occurred. The analytics file
is the one to use for a report or comparison between tests.

When using `seame.py`, CSV output paths are normalized as follows:

- A filename such as `--csv bridge_10min.csv` is saved under `results/`.
- A path containing a folder, such as `--csv logs/bridge.csv`, is preserved.
- Missing folders are created automatically.

## Other command options

The interactive menu uses the defaults for network setup, FC diagnostics, and
ESP inspection. Use the direct command form when you need to change one of
these values. The exact options are listed below.

### Network: test scenario

```bash
python3 scripts/seame.py network test setup [--port PORT] \
  [--ssid SSID] [--ask-password]
```

| Option | Meaning | Default |
|---|---|---|
| `--port PORT` | ESP USB serial port. | Auto-detect |
| `--ssid SSID` | Wi-Fi network the ESP should join. | `SEAME` |
| `--ask-password` | Ask for the Wi-Fi password again instead of using the stored one. | Off |

Examples:

```bash
# Use the default SEAME network and stored password
python3 scripts/seame.py network test setup

# Join another Wi-Fi network
python3 scripts/seame.py network test setup --ssid MyNetwork

# Ask for the password again
python3 scripts/seame.py network test setup --ask-password
```

`network test status` has no additional options. It reads the saved profile
from `~/.config/seame-drone/bridge.json` and checks reachability.

### Network: production scenario

```bash
python3 scripts/seame.py network production setup [--port PORT] \
  [--ssid SSID] [--channel 1-13] [--max-clients 1-4] [--ask-password]
```

| Option | Meaning | Default |
|---|---|---|
| `--port PORT` | ESP USB serial port. | Auto-detect |
| `--ssid SSID` | Name of the ESP access point. | `seame-drone-<last 4 MAC digits>` |
| `--channel 1-13` | Wi-Fi channel used by the access point. | `6` |
| `--max-clients 1-4` | Maximum number of connected clients. | `1` |
| `--ask-password` | Choose a new access-point password. | Off |

Examples:

```bash
# Default production access point
python3 scripts/seame.py network production setup

# Fixed name and channel
python3 scripts/seame.py network production setup \
  --ssid seame-drone-lab --channel 11
```

`connect`, `disconnect`, and `status` have no additional options. The
production `connect` command retrieves the saved password and joins the saved
NetworkManager profile.

### Flight controller (FC)

Global FC options:

| Option | Meaning | Default |
|---|---|---|
| `--port PORT` | FC USB serial port. | Auto-detect STM32 `0483:5740` |
| `--write` | Allow commands that change FC configuration. | Off |

Commands:

```bash
# Read-only diagnostics and diff backup
python3 scripts/seame.py fc dump

# Configure UART4 for MSP and save the FC configuration
python3 scripts/seame.py fc --write enable-esp-uart

# Run one or more Betaflight CLI commands
python3 scripts/seame.py fc run "get failsafe" "serial"

# Run write commands and save the result
python3 scripts/seame.py fc --write run \
  "set vtx_power = 1" --save
```

`fc run` is read-only unless `--write` is supplied. `--save` requires
`--write`. Before write operations, the script saves a `diff all` backup.
`fc dump` and `fc backup-firmware` do not change FC settings.

### MSP information and attitude

```bash
python3 scripts/seame.py msp [shared-options] info
python3 scripts/seame.py msp [shared-options] core
python3 scripts/seame.py msp [shared-options] attitude
```

| Command | Extra option | Default |
|---|---|---|
| `info` | `--trace` | Off |
| `core` | `--trace` | Off; requires `--usb` |
| `attitude` | `--rate HZ` | `10` Hz |
| `info`, `core`, `attitude` | `--trace` | Off |

Examples:

```bash
python3 scripts/seame.py msp info
python3 scripts/seame.py msp --usb core --trace
python3 scripts/seame.py msp attitude --rate 5
```

The interactive menu provides two attitude options:

- `attitude`: read continuously through the active ESP bridge.
- `--usb attitude`: read continuously from the FC USB serial connection,
  without using the ESP.

Direct commands:

```bash
# Through the ESP bridge
python3 scripts/seame.py msp attitude --rate 10

# Directly from the FC over USB
python3 scripts/seame.py msp --usb attitude --rate 10

# Select a specific FC USB port
python3 scripts/seame.py msp --usb --fc-port /dev/ttyACM0 attitude
```

Both modes use `--rate 10` updates per second by default and accept
`--trace`. The USB mode requires the FC to be connected directly by USB; it
does not require the ESP to be connected or configured. Stop `attitude` with
`Ctrl+C`.

### MSP Cycle 1a

```bash
python3 scripts/seame.py msp cycle1a [options]
```

Cycle 1a uses a fake FC on the ESP USB port and tests the bridge without the
real FC.

| Option | Meaning | Default |
|---|---|---:|
| `-n N` | Number of MSP requests. | `500` |
| `--rate HZ` | Requests per second; `0` means maximum speed. | `50` |
| `--timeout SECONDS` | Wait time for each response. | `0.5` seconds |
| `--max-loss PERCENT` | Maximum estimated loss required for PASS. | `1.0%` |
| `--wait SECONDS` | Maximum time to wait for the bridge after ESP USB opens. | `20` seconds |
| `--csv FILE` | Save detailed request data and an analytics CSV. | Not saved |
| `--quiet` | Hide progress messages. | Off |
| `--trace` | Print raw MSP frames. | Off |
| `--esp-port-usb PORT` | ESP USB serial port. | Auto-detect |

Example:

```bash
python3 scripts/seame.py msp cycle1a \
  -n 500 --rate 50 --max-loss 1 --wait 20 --csv cycle1a.csv
```

### ESP AT commands

Global ESP option:

| Option | Meaning | Default |
|---|---|---|
| `--port PORT` | ESP USB serial port. | Auto-detect |

The `raw` command has one additional option:

| Option | Meaning | Default |
|---|---|---|
| `--wait SECONDS` | Maximum time to wait for each AT response. | `3` seconds |

Examples:

```bash
python3 scripts/seame.py esp query
python3 scripts/seame.py esp raw --wait 5 "AT+CWLAP"
python3 scripts/seame.py esp raw "AT+GMR" "AT+CIFSR"
```

`restore` asks for confirmation by requiring the user to type `yes`.

### Fake FC responder

The interactive menu exposes `msp → fake-fc`. It starts a fake flight
controller that listens on the ESP USB serial port and responds to supported
read-only MSP requests with predefined mock data:

| MSP response | Mock value |
|---|---|
| API version | `1.46` |
| FC variant | `BTFL` |
| FC version | `4.5.1` |
| Board | `FAKE` |
| Status | Cycle time `125 µs`, sensors `0x23`, disarmed |
| Attitude | Roll `0°`, pitch `0°`, yaw `0°` |
| Analog | Battery value `0 V` |

Select it interactively:

```text
1) info
2) ping
3) cycle1a
4) fake-fc
...
```

Or run it directly:

```bash
python3 scripts/seame.py msp fake-fc
```

Optional arguments:

| Option | Meaning | Default |
|---|---|---|
| `--esp-port-usb PORT` | ESP USB serial port where the fake FC listens. | Auto-detect |
| `--trace` | Print received requests and generated mock responses. | Off |

Example:

```bash
python3 scripts/seame.py msp fake-fc --trace
```

This command keeps running until `Ctrl+C`. It only responds to the predefined
mock values above; it does not currently provide an interactive prompt for
changing attitude, battery, or status values. For an automated bridge test
that starts the fake FC and sends requests automatically, use `msp cycle1a`.

### Wi-Fi ESP to USB FC relay

If the ESP and FC are both connected to the same PC by USB, but are not
physically wired together, use the USB relay:

```text
PC ──Wi-Fi── ESP
             │ USB
             ▼
        PC USB relay
             ▲
             │ USB
             FC
```

The relay copies bytes in both directions:

```text
ESP USB serial → FC USB serial
FC USB serial  → ESP USB serial
```

This lets the ESP remain connected to the PC over Wi-Fi while the FC remains
connected directly to the PC by USB. The PC software acts as the missing
serial wire. It does not connect the ESP USB pins physically to the FC.

Select `msp → USB relay` in the interactive menu, or run:

```bash
python3 scripts/seame.py msp usb-relay
```

The defaults auto-detect both ports. To select them explicitly:

```bash
python3 scripts/seame.py msp usb-relay \
  --esp-port-usb /dev/ttyUSB0 \
  --fc-port /dev/ttyACM0
```

Before starting:

1. Configure the ESP bridge and connect the PC to its Wi-Fi scenario.
2. Connect the ESP USB port to the PC.
3. Connect the FC USB port to the PC.
4. Do not run `fake-fc`, `cycle1a`, or another serial monitor at the same time.
5. Start the relay.
6. In another terminal, run a client command through the ESP bridge:

```bash
python3 scripts/seame.py msp info
python3 scripts/seame.py msp ping -n 1000 --rate 50
python3 scripts/seame.py msp attitude
```

The relay is a byte forwarder, so it does not decode or modify MSP frames.
Stop it with `Ctrl+C`. This tests the Wi-Fi/ESP path plus the FC USB path, but
it also measures the extra PC serial relay rather than a direct physical
ESP-to-FC UART connection.

## Command groups

| Command | Purpose | Hardware effect |
|---|---|---|
| `network test setup` | Configure the ESP as a station on the SEAME Wi-Fi and save the active bridge profile. | Changes ESP settings |
| `network test status` | Check the saved test profile and ESP reachability. | Read-only |
| `network production setup` | Configure the ESP access point and the PC NetworkManager profile. | Changes ESP and PC network settings |
| `network production connect` | Join the saved drone access point. | Changes PC network connection |
| `network production disconnect` | Leave the drone access point. | Changes PC network connection |
| `network production status` | Show the saved production profile and connection state. | Read-only |
| `fc dump` | Save Betaflight read-only diagnostics and `diff all`. | Read-only |
| `fc --write enable-esp-uart` | Configure FC UART4 for MSP at 115200 and save it. | Writes FC configuration |
| `fc run <command>...` | Run Betaflight CLI commands. | Read-only unless `--write` is used |
| `fc backup-firmware` | Read and verify the FC flash through DFU. | Read-only, requires `dfu-util` |
| `msp --usb info` | Read FC information directly over USB. | Read-only |
| `msp --usb ping -n 500` | Measure direct USB MSP latency and loss. | Read-only |
| `msp cycle1a --csv result.csv` | Test the ESP bridge using a fake FC. | Read-only |
| `msp fake-fc` | Run only the predefined fake FC responder. | Read-only |
| `msp usb-relay` | Relay bytes between ESP USB and FC USB through the PC. | Read-only |
| `msp info` | Read FC information through the active ESP bridge. | Read-only |
| `msp ping -n 1000 --rate 50` | Measure bridge latency and loss. | Read-only |
| `msp attitude` | Continuously display roll, pitch, and yaw. | Read-only |
| `esp query` | Display ESP AT firmware and network settings. | Read-only |
| `esp backup` | Back up and verify the ESP flash. | Read-only |
| `esp exit-bridge` | Leave transparent mode and disable it at boot. | Changes ESP settings |
| `esp raw "AT+CWLAP"` | Send one or more manual AT commands. | Depends on command |
| `esp restore` | Factory-reset ESP AT settings after confirmation. | Erases ESP settings |

## Direct specialist scripts

The specialist scripts are intentionally still runnable directly for
troubleshooting and backwards compatibility:

- [`setup_test.py`](./setup_test.py) and [`setup_production.py`](./setup_production.py):
  network scenarios.
- [`fc_cli.py`](./fc_cli.py): Betaflight CLI and firmware backup.
- [`msp_check.py`](./msp_check.py): MSP checks and bridge cycles.
- [`esp_at.py`](./esp_at.py): low-level ESP AT operations.
- [`bridge_config.py`](./bridge_config.py): local profile and secret storage.
- [`msp.py`](./msp.py): shared MSP protocol and transport library.

Generated measurements belong outside source directories. Hardware backups and
Wi-Fi secrets must never be committed.
