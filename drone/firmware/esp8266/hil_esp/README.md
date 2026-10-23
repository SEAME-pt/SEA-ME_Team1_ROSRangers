# HIL ESP: spare ESP8266 on the HIL Pi

This folder is for the **spare ESP** that sits on the HIL runner (Raspberry Pi 4, `hil-drone`), not the ESP on the drone. The drone ESP's backups are in `../backup/`.

The flash images are **not** in Git (`.gitignore` excludes `*.bin`): they may contain saved Wi-Fi credentials. Keep them on the Pi (`/home/runner/`) and on the machine that copied them.

Setup and wiring: `docs/PraticalPlan/04 CI-CD/HIL Runner.md` §3.1, ADR-020 update 2026-10-22, GitHub #48.

## Board

| Item | Value |
|---|---|
| Board | Same as the drone ESP: ESP-12F dev board with an FTDI FT232R (`0403:6001`) on micro-USB |
| Chip | ESP8266EX, 26 MHz crystal, 160 MHz |
| Station MAC | `cc:50:e3:6d:23:e4` |
| Flash | 4 MB (manufacturer `0x5e`, device `0x4016`) |
| Device on the Pi | `/dev/hil-esp` (udev rule matched on the FTDI serial) |
| Wired to an FC | No. USB flashing is allowed (ADR-020 update 2026-10-22). |
| Firmware at backup time | Not identified yet (probably the stock AT firmware). Check the boot output before the first CI flash. |

## Images

| Image (4 194 304 bytes each) | SHA-256 | Notes |
|---|---|---|
| `spare_esp_backup.bin`, made 2026-10-22 | `b063fb4c08cee2f31d477310ab159551cc883cf616ddc385441d924795242f7e` | Original firmware, before any CI flash. esptool 5.6.0 `read-flash 0 0x400000` on the Pi. `verify-flash` digest matched (2026-10-22). May contain Wi-Fi credentials: never publish. |

## How to make or restore a backup (on the Pi)

```bash
esptool --port /dev/hil-esp flash-id
esptool --port /dev/hil-esp read-flash 0 0x400000 <image>
esptool --port /dev/hil-esp verify-flash 0 <image>

# restore
esptool --port /dev/hil-esp write-flash 0 <image>
esptool --port /dev/hil-esp verify-flash 0 <image>
```

Stop the runner first (`sudo ~/actions-runner/svc.sh stop`) so a CI job can't use the port at the same time, and start it again afterwards.
