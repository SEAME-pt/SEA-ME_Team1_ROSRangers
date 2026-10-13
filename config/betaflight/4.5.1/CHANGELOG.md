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
