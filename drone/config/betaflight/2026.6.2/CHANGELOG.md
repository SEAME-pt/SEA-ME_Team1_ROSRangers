# Betaflight 2026.6.2 configuration changes

Each change to the flight controller gets an entry here, and a new `diff all` file when it is a checkpoint.
To restore a version, flash the firmware below and paste its `diff all` file in the Betaflight CLI (it ends with `save`).
A 4.5.1 `diff all` cannot be pasted into 2026.6.2 (ADR-014).

## 2026-10-28 · Firmware flash

| Item | Value |
|---|---|
| Firmware | Betaflight 2026.6.2 (`e0b7bb01b`), MSP API 1.48, target `ODDITYRCF7AIO` (STM32F722) |
| Build | Cloud build from app.betaflight.com, 2026-10-28 19:14. Options: CRSF, OSD (analog), DSHOT, GPS, Optical Flow, Pin IO, Range Finder, VTX, **Altitude Hold**, **Position Hold** |
| Size | 408 935 bytes, so ALTHOLD fits the 512 KB F722 |
| Hex | `betaflight_2026.6.2_STM32F722_ODDITYRCF7AIO_d5751476.hex`, SHA-256 `0ed4c9789e24d73d603e13c85327f412911cdd150b604ab193fab49bf46fe6ea`. Not in Git: attached as `betaflight_2026.6.2_ODDITYRCF7AIO_hex.zip` to PR #133 |
| Flash | Full chip erase. On Ubuntu, Chrome needs a udev rule for the DFU device (`0483:df11`) |

## 2026-10-28 · `diff_all_2026-10-28.txt` (configuration re-entered by hand)

Re-entered from `../4.5.1/diff_all_2026-10-28_before_upgrade.txt`. Differences from 4.5.1:

| Setting | 4.5.1 | 2026.6.2 | Why |
|---|---|---|---|
| `align_board_roll` | (default) | **180** | After the flash the Setup tab showed roll 179° with the board flat. 2026.6.2 handles this target's gyro orientation differently. Checked: nose down, roll right and yaw right all match. `align_board_yaw = -45` unchanged. |
| `d_min_roll/pitch` → `d_roll/pitch` | 44 / 49 | 44 / 49 | Renamed in 2026.6. The old `d_roll/pitch` (57 / 49) is now `d_max_roll/pitch`. Same tune. |
| `dshot_idle_value` → `motor_idle` | default | default (550) | Renamed. |
| `acc_calibration` | 8,0,-45 | **21,-11,-52** | Re-calibrated after the flash (`MSP_ACC_CALIBRATION`; the 2026.6.2 app has no button for it). |
| `aux` | ARM AUX1, ANGLE AUX2 high, HORIZON AUX2 middle, BEEPER, USER1 | **ARM AUX1** 1700–2100 (SA), **ANGLE AUX2** 1300–2100 (SB up or middle), **ALTHOLD AUX3** 1700–2100 (SC forward) | ALTHOLD on its own switch. HORIZON removed: SB up and middle are both ANGLE, SB down is acro (never used). BEEPER and USER1 not re-entered. |
| `angle_limit` | 60 (default) | **20** | Small flight area. |
| `beeper`, `osd_*_pos`, `osd_canvas_*`, `blackbox_disable_debug/rpm/gps` | set | default | Not re-entered. |

Left out on purpose: the beepers, the OSD element positions, `blackbox_disable_debug/rpm/gps` and the BEEPER and USER1 modes were not needed for the flight tests. Re-enter them from the 4.5.1 diff if they are needed again.

Radio (RadioMaster Pocket, model MODEL06): CH5 = SA, CH6 = SB (its source was `MAX`, so SB did nothing and ANGLE was always on; fixed), CH7 = SC (new).

Bench check on 2026-10-28, LiPo connected, **props off**: SA arms (idle about 1060, no runaway), SC reaches AUX3, switching the radio off stops the motors in about 1 s (failsafe DROP), and the drone does not re-arm until SA is reset. ALTHOLD does not show on the bench: 2026.6.2 only engages it once the throttle has been raised.

## 2026-10-28 to 2026-10-29 · Throttle and ALTHOLD tuning (no `diff all` per step)

| Date | Change | Why |
|---|---|---|
| 2026-10-28 | `throttle_limit_type = SCALE`, `throttle_limit_percent = 75`, `thr_mid = 50`, `thr_hover = 45`, `thr_expo = 50` | Soften the throttle. In 2026.6.2 `thr_mid` is the stick position of the hover point and `thr_hover` the output there. |
| 2026-10-29 | `throttle_limit_percent` 75 → **60**, `thr_expo` 50 → **80**, `angle_limit` 20 → **15**, `alt_hold_climb_rate` 50 → **30** | In the first flights a small stick move climbed far too fast for a small indoor area. |
| 2026-10-29 | `altitude_lpf` 300 → **100** | Blackbox: the baro reading jumps by metres within half a second when the props blow on it. |
| 2026-10-29 | `thr_hover` 45 → **30**, `ap_hover_throttle` 1275 → **1300** | Blackbox: the drone hovers at throttle command ~1300 (motors ~21 %). Stick centre is now hover, which is also ALTHOLD's hold band. |

### Blackbox findings, 2026-10-29

Logs not in Git: `BTFL_BLACKBOX_LOG_PILOTIX-BROOK_20261029_122340` and `_144308`, attached as `blackbox_2026-10-29.zip` to GitHub #134.

- **Hover:** throttle command ~1300, motors ~21 %, attitude within ±1° with the sticks centred.
- **ALTHOLD worked once:** engaged after ~15 s of steady hover, it held for ~45 s with steady motors (log 15).
- **ALTHOLD failed twice:** engaged during the climb, right after the baro reading jumped from about −5 m to +5.5 m, the drone kept climbing and hit something (logs 16 and 17). The DPS310 sits uncovered under the props. The usual fix is open-cell, non-conductive foam over it; not done.
- **Drift:** level on the bench (pitch 0.1°, roll −0.1°) and level in flight, yet it drifts slowly. Without a position sensor that is expected.
- **#131:** stable hover with no stick input until a physical contact drove one motor to 0 % and the others to ~91 %.

## 2026-10-30 · `diff_all_2026-10-30.txt` (current)

Configuration after the changes above. Same as `diff_all_2026-10-28.txt` plus `alt_hold_climb_rate`, `altitude_lpf`, `ap_hover_throttle`, `angle_limit = 15` and the rateprofile 0 throttle settings.
