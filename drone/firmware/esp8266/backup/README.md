# ESP8266 factory firmware backup (AT 1.7.4)

The flash images are **not** in Git. The first one contains Wi-Fi credentials saved by `AT+CWJAP` / `AT+CWSAP`.
They are kept outside the repositories, on the machine that made them.

Board: **ESP-12F module** (`ESP8266MOD`, Ai-Thinker style, PCB antenna) on a dev board with an FTDI FT232R and micro-USB.

| Item | Value |
|---|---|
| Date | 2026-10-13 |
| Chip | ESP8266EX, 26 MHz crystal, 160 MHz |
| Station MAC | `80:7d:3a:1d:08:50` (SoftAP MAC `82:7d:3a:1d:08:50`) |
| Flash | 4 MB (manufacturer `0x5e`, device `0x4016`), map 512 KB + 512 KB, SPI DOUT 40 MHz |
| USB-serial | FTDI FT232R (`0403:6001`), on the board's micro-USB |
| Firmware | AT 1.7.4.0 (11 May 2020), SDK 3.0.5 (52383f9), Bin 1.7.4 |

## Images

| Image (4 194 304 bytes each) | SHA-256 | Notes |
|---|---|---|
| `esp8266_AT1.7.4_807d3a1d0850_2026-10-13.bin` | `ecbc494c933f7e47cb2a65ae316a3b96ae71815a2a94fb8664f8d8de1656523b` | Before `AT+RESTORE`. **Contains Wi-Fi credentials.** Never publish. |
| `esp8266_AT1.7.4_807d3a1d0850_2026-10-13_factory-reset.bin` | `26436ff4af3828561c30fa37a1a316be85f98ad237717b5ef326278d36823d5c` | After `AT+RESTORE`. No credentials found. Differs from the first only in sectors `0x3FD000`–`0x3FFFFF` (system parameters). |
| `esp8266_flash_2026-10-16_164249.bin` | `c6a2dfc5a50b1a2d113b1d959937d8af7f106e3a0cba40a479bd7e2b6d3c5506` | Bench settings of 16 Oct: drone SoftAP and the transparent UDP bridge at boot. **Contains Wi-Fi credentials.** Never publish. Current restore point. |

All were checked with `esptool verify_flash 0 <image>` (digest matched).

The 16 Oct image passed ESP-S1-01 (size = the 4 MB reported by `flash_id`) and ESP-S1-02: blink was flashed, then this image was written back and verified, and the ESP booted into the AT firmware again (`ready`, same boot output as before).

> [!note] Boot warning, present in every image
> The ESP prints `mismatch map 2,spi_size_map 4` and `system_partition_table_regist fail` at boot: the flash header says 512 KB + 512 KB, but AT 1.7.4 / SDK 3.0.5 expects 1024 KB + 1024 KB on a 4 MB chip. The AT firmware still starts and the bridge works. Leave it as is, so the backups stay the known-good state. Set the right map when the custom firmware is flashed (ADR-007).

## State after `AT+RESTORE` (2026-10-13)

| Query | Answer |
|---|---|
| `AT+CWMODE_DEF?` | 2 (SoftAP) |
| `AT+CWJAP_DEF?` | No AP |
| `AT+CWSAP_DEF?` | `"ESP-1D0850","",1,0,4,0` → **open SoftAP, no password**, channel 1 |
| `AT+CWAUTOCONN?` | 1 |
| `AT+CIPMODE?` | 0 |

## How it was made

Command names use underscores: esptool 4.x accepts only those, esptool 5 accepts both.
`scripts/esp_at.py backup` runs the same three commands and checks the size.

```bash
esptool --port /dev/ttyUSB0 flash_id
esptool --port /dev/ttyUSB0 --baud 460800 read_flash 0 ALL <image>
esptool --port /dev/ttyUSB0 verify_flash 0 <image>
```

## Restore

```bash
esptool --port /dev/ttyUSB0 --baud 460800 write_flash 0 <image>
esptool --port /dev/ttyUSB0 verify_flash 0 <image>
```

Or `scripts/esp_at.py write-flash [<image>]` (default: the newest backup); it asks for confirmation and verifies.
