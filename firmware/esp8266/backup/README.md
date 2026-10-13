# ESP8266 factory firmware backup (AT 1.7.4)

The flash images are **not** in Git. The first one contains Wi-Fi credentials saved by `AT+CWJAP` / `AT+CWSAP`.
They are kept outside the repositories, on the machine that made them.

Board: **ESP-12F module** (`ESP8266MOD`, Ai-Thinker style, PCB antenna) on a dev board with an FTDI FT232R and micro-USB. **Not a Wemos D1 mini.**

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

Both were checked with `esptool verify-flash 0 <image>` (digest matched).

## State after `AT+RESTORE` (2026-10-13)

| Query | Answer |
|---|---|
| `AT+CWMODE_DEF?` | 2 (SoftAP) |
| `AT+CWJAP_DEF?` | No AP |
| `AT+CWSAP_DEF?` | `"ESP-1D0850","",1,0,4,0` → **open SoftAP, no password**, channel 1 |
| `AT+CWAUTOCONN?` | 1 |
| `AT+CIPMODE?` | 0 |

## How it was made

```bash
esptool --port /dev/ttyUSB0 flash-id
esptool --port /dev/ttyUSB0 --baud 460800 read-flash 0 ALL <image>
esptool --port /dev/ttyUSB0 verify-flash 0 <image>
```

## Restore

```bash
esptool --port /dev/ttyUSB0 --baud 460800 write-flash 0 <image>
```
