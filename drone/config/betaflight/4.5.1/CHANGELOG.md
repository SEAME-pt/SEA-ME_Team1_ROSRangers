# Betaflight 4.5.1 configuration changes

Each change to the flight controller gets a new `diff all` file and an entry here.
To restore a version, paste its `diff all` file in the Betaflight CLI (it ends with `save`).

## 2026-10-13 · `diff_all_2026-10-13.txt` (baseline)

Configuration as found, read over USB only (no battery). Read-only CLI output in `cli_read_2026-10-13.txt`.

## 2026-10-13 · `diff_all_2026-10-13_v2.txt`

| Change | Before | After | Why |
|---|---|---|---|
| `feature 3D` | ON | **OFF** | With 3D on, stick low = motors in reverse and the middle = stopped. The failsafe, MSP Override and motor tests all assume normal throttle. |
| `vtx_power` | 5 (1.3 W) | **1 (25 mW)** | 25 mW is the EU licence-free level for 5.8 GHz, and the VTX overheats on the bench at 1.3 W. |

> [!warning] To check at the first battery test (props off)
> It is not known whether the **ESC** is in 3D (bidirectional) mode. In the Motors tab, with props off, raise each motor slowly from the minimum: it must start from stopped and spin in the expected direction. If it spins at low slider values or the wrong way, stop and check the ESC settings.

## 2026-10-13 · Firmware backup (not in Git)

Full 512 KB internal flash read over DFU (`bl`, then `dfu-util -a 0 -s 0x08000000:524288 -U`), read twice, both reads identical.
Kept outside the repository: `~/Projects/SEAME/backups/betaflight/betaflight_4.5.1_ODDITYRCF7AIO_flash_2026-10-13.bin`.

| Item | Value |
|---|---|
| SHA-256 | `37ec792dbfebb47322af94c45e928389139c7e947ede7e6f5c16438c07614f12` |
| Contains | Betaflight 4.5.1 for `ODDITYRCF7AIO` plus the configuration at that moment (v2) |
| Restore | `dfu-util -a 0 -s 0x08000000:leave -D <file>` (FC in DFU mode) |

## 2026-10-13 · `diff_all_2026-10-13_v3.txt`

| Change | Before | After | Why |
|---|---|---|---|
| `serial 3` (UART4) | unused (`serial 3 0 …`) | **MSP at 115200** (`serial 3 1 115200 57600 0 115200`) | Link to the ESP (ADR-011). Made with `fc_cli.py --write enable-esp-uart`; the `diff all` taken just before was identical to v2. |

Nothing is wired to T4/R4 yet: soldering waits for instructor approval and the `ESP_to_FC` §10 checks.

## 2026-10-16 · Check, and a firmware backup with v3 (not in Git)

`diff all` read again on 16 Oct, before and after the ESP bench tests: identical to `diff_all_2026-10-13_v3.txt` (the only difference is a trailing space after `signature`). No new `diff all` file: the configuration did not change.

The 13 Oct firmware backup holds v2. This one holds v3 and is the current restore point.
Kept outside the repository: `~/Projects/SEAME/backups/betaflight/betaflight_4.5.1_flash_2026-10-16.bin`.

| Item | Value |
|---|---|
| SHA-256 | `9c10f274a9a62ce23d3d5c099551a4441571308ad96e5eb1da40aeb01463a7c8` |
| Size | 524 288 bytes (full internal flash) |
| Contains | Betaflight 4.5.1 for `ODDITYRCF7AIO` plus configuration v3 (UART4 = MSP 115200) |
| Made with | `fc_cli.py backup-firmware` (DFU, read twice and compared) |
| Restore | `dfu-util -a 0 -s 0x08000000:leave -D <file>` (FC in DFU mode) |
