# Script Execution Guide

This guide describes the scripts in the order they are normally used, from
initial setup through testing the flight controller over the ESP8266 bridge.

The scripts support this data path:

```text
Ground PC ── UDP/MSP ── ESP8266 ── UART/MSP at 115200 ── Betaflight FC
```

There are two network scenarios:

```text
Test:       PC on SEAME ── UDP 5760 ── ESP on SEAME
Production: PC 192.168.4.2 ── UDP 5760 ── ESP AP 192.168.4.1
```

## Safety requirements

- Keep the propellers off for every test.
- Do not connect the ESP USB cable while the ESP TX/RX pins are wired to the
  flight controller. The USB-serial chip and the FC would share those lines.
- Configure the ESP over USB first, then unplug it before connecting it to the
  FC.
- The test bridge has no authentication. Use it only for bench testing.
- Do not commit Wi-Fi passwords or flash backups.

## 1. Install dependencies

From the repository root:

```bash
python3 -m venv .venv
. .venv/bin/activate
pip install -r scripts/requirements.txt
sudo usermod -aG dialout "$USER"
```

Log out and back in after changing the `dialout` group. The optional FC
firmware-backup command also requires:

```bash
sudo apt install dfu-util
```

Run the hardware-independent tests:

```bash
pytest tests/scripts
```

## 2. Inspect and configure the flight controller

The flight controller is connected directly by USB for this stage.

### 2.1 Capture the current configuration

```bash
python3 scripts/fc_cli.py dump
```

[`fc_cli.py`](./fc_cli.py) performs these actions:

1. Detects the Betaflight USB serial port.
2. Opens the Betaflight CLI at 115200 baud.
3. Reads the firmware version and diagnostic settings.
4. Saves read-only CLI output and `diff all`.
5. Exits the CLI without saving changes.

Captures are saved under:

```text
config/betaflight/<firmware-version>/
```

### 2.2 Configure UART4 for MSP

```bash
python3 scripts/fc_cli.py --write enable-esp-uart
```

This command:

1. Reads the current serial configuration.
2. Saves a `diff all` backup before changing anything.
3. Configures UART4 for MSP at 115200 baud.
4. Saves the Betaflight configuration.
5. Reboots the FC.

The `--write` flag is required for this operation. Without it, the script
does not change the FC.

## 3. Configure the ESP bridge

Choose one of the following scenarios.

### 3.1 Test scenario: use the SEAME Wi-Fi

```bash
python3 scripts/setup_test.py
```

[`setup_test.py`](./setup_test.py) performs the following sequence:

1. Checks which Wi-Fi network the PC is using.
2. Reads or requests the SEAME password.
3. Opens the ESP USB serial port.
4. Leaves transparent mode if necessary.
5. Configures the ESP as a Wi-Fi station using DHCP.
6. Joins the SEAME network.
7. Reads the ESP IP address.
8. Finds the PC IP address toward the ESP.
9. Pings the ESP.
10. Configures the persistent UDP transparent bridge.
11. Saves the active scenario.
12. Reboots the ESP into bridge mode.

The default SSID is `SEAME`. To specify another SSID or request the password
again:

```bash
python3 scripts/setup_test.py setup --ssid SEAME --ask-password
```

Check the saved scenario and reachability with:

```bash
python3 scripts/setup_test.py status
```

### 3.2 Production scenario: use the ESP access point

Configure the ESP:

```bash
python3 scripts/setup_production.py
```

[`setup_production.py`](./setup_production.py) performs the following sequence:

1. Opens the ESP USB serial port.
2. Ensures that the ESP is in AT mode.
3. Reads the ESP MAC address.
4. Chooses an SSID such as `seame-drone-xxxx`.
5. Reads or requests a WPA2 password.
6. Configures the ESP as a SoftAP.
7. Assigns the ESP address `192.168.4.1`.
8. Enables DHCP on the ESP.
9. Configures the persistent UDP transparent bridge.
10. Reboots the ESP.
11. Creates or updates the PC NetworkManager profile.
12. Saves the active scenario.

Connect the PC to the new network:

```bash
python3 scripts/setup_production.py connect
```

The PC uses static address `192.168.4.2/24`. Internet access is normally
unavailable while this connection is active.

Check the scenario:

```bash
python3 scripts/setup_production.py status
```

Leave the network when finished:

```bash
python3 scripts/setup_production.py disconnect
```

## 4. Understand the saved bridge state

Both setup scripts use [`bridge_config.py`](./bridge_config.py) to store local
state outside the repository:

```text
~/.config/seame-drone/bridge.json
~/.config/seame-drone/secrets.json
```

`bridge.json` contains the active scenario, ESP and PC addresses, and UDP
ports. `secrets.json` contains Wi-Fi passwords. The configuration directory is
created with mode `0700`, and the secrets file with mode `0600`.

[`msp_check.py`](./msp_check.py) reads `bridge.json` automatically when it is
run without `--usb`.

## 5. Test the bridge without the real FC

For Cycle 1a, keep the ESP on USB and do not connect it to the FC:

```bash
python3 scripts/msp_check.py cycle1a --csv cycle1a_test.csv
```

The execution sequence is:

1. Read the active scenario from `bridge.json`.
2. Open the ESP USB serial port.
3. Start a fake FC in a background thread.
4. Send MSP requests through UDP to the ESP.
5. Let the ESP forward those requests to the fake FC over UART.
6. Return fake MSP responses through the ESP and UDP.
7. Measure packet loss, latency, and checksum errors.
8. Print `Cycle 1a PASS` or `Cycle 1a FAIL`.

The default pass threshold is at most 1% loss at 50 requests per second and
zero bad checksums.

To see the traffic in both directions, add `--trace`:

```bash
python3 scripts/msp_check.py cycle1a --trace -n 5 --rate 1
```

The output shows:

```text
PC -> ESP/FC:      MSP request bytes
ESP/FC received:   request received by the fake FC
ESP/FC sent:       response generated by the fake FC
PC <- ESP/FC:      response received back by the PC
```

The hexadecimal bytes include the MSP header, payload length, command, payload,
and checksum. A short run such as five requests at one request per second is
recommended while inspecting the traffic.

## 6. Establish a direct USB MSP baseline

Before testing the bridge with the real FC, test the FC directly:

```bash
python3 scripts/msp_check.py --usb info
python3 scripts/msp_check.py --usb ping -n 500 --csv baseline_usb.csv
```

With `--usb`, [`msp_check.py`](./msp_check.py) uses the FC USB serial port
instead of `bridge.json`. It sends read-only MSP requests and reports decoded
FC data, latency, packet loss, and checksum errors.

To read only the three core FC values:

```bash
python3 scripts/msp_check.py --usb core
```

This sends exactly these requests, in order:

```text
MSP_API_VERSION
MSP_STATUS
MSP_ATTITUDE
```

To display the raw MSP frames as well as the decoded values:

```bash
python3 scripts/msp_check.py --usb core --trace
```

The `core` command requires `--usb`; it does not use the ESP bridge or the
saved `bridge.json` profile.

## 7. Test the real FC through the ESP

After Cycle 1a passes:

1. Stop using the ESP USB connection.
2. Wire FC `T4` to ESP `RX`.
3. Wire FC `R4` to ESP `TX`.
4. Wire `5V` to `5V` and `GND` to `G`.
5. Power the FC by USB only.

Then query the FC through the bridge:

```bash
python3 scripts/msp_check.py info
```

For a single query with raw traffic visible:

```bash
python3 scripts/msp_check.py info --trace
```

The request path is:

```text
msp_check.py
  → msp.py UdpTransport
  → UDP port 5760
  → ESP transparent bridge
  → FC UART4
  → ESP
  → UDP port 5760
  → msp.py Parser and decoder
```

Useful checks:

```bash
python3 scripts/msp_check.py ping -n 1000 --rate 50 --csv cycle1b_test.csv
python3 scripts/msp_check.py attitude
```

`attitude` continuously displays roll, pitch, and yaw. Stop it with
`Ctrl+C`.

## 8. Recover or inspect the ESP manually

[`esp_at.py`](./esp_at.py) provides manual AT-command operations. The ESP must
be connected to USB and not wired to the FC.

Read the current state:

```bash
python3 scripts/esp_at.py query
```

If the ESP is in transparent mode, leave it:

```bash
python3 scripts/esp_at.py exit-bridge
```

Send an AT command manually:

```bash
python3 scripts/esp_at.py raw "AT+CWLAP"
```

Back up the complete ESP flash:

```bash
python3 scripts/esp_at.py backup
```

Backups are saved under:

```text
~/Projects/SEAME/backups/esp8266/
```

The backup can contain Wi-Fi passwords.

Factory-reset the ESP:

```bash
python3 scripts/esp_at.py restore
```

This requires typing `yes` and erases saved AT settings and Wi-Fi
credentials.

## 9. Finish and return to a normal state

For production mode:

```bash
python3 scripts/setup_production.py disconnect
```

To leave the ESP in AT mode, with the ESP USB connected and FC wiring
disconnected:

```bash
python3 scripts/esp_at.py exit-bridge
```

To factory-reset the ESP:

```bash
python3 scripts/esp_at.py restore
```

## Script dependency order

The runtime relationships are:

```text
setup_test.py
  ├─ bridge_config.py
  └─ esp_at.py

setup_production.py
  ├─ bridge_config.py
  └─ esp_at.py

msp_check.py
  ├─ bridge_config.py
  ├─ esp_at.py
  ├─ msp.py
  └─ fc_cli.py  (only for FC USB-port detection)

msp.py
  └─ pyserial

fc_cli.py
  └─ pyserial

esp_at.py
  ├─ pyserial
  └─ esptool  (backup command only)
```

The complete recommended order is therefore:

```text
1. Install dependencies
2. fc_cli.py dump
3. fc_cli.py --write enable-esp-uart
4. setup_test.py OR setup_production.py
5. setup_production.py connect (production only)
6. msp_check.py cycle1a
7. Wire the ESP to the real FC
8. msp_check.py --usb info (optional baseline)
9. msp_check.py info
10. msp_check.py ping
11. msp_check.py attitude
12. setup_production.py disconnect (production only)
```
