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
