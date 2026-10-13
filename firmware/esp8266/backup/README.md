# ESP8266 factory firmware backup (AT 1.7.4)

The full flash image is **not** in Git: it contains Wi-Fi credentials saved by `AT+CWJAP` / `AT+CWSAP`.
It is kept outside the repositories, on the machine that made it.

| Item | Value |
|---|---|
| Date | 2026-10-13 |
| Chip | ESP8266EX, 26 MHz crystal, 160 MHz |
| Station MAC | `80:7d:3a:1d:08:50` (SoftAP MAC `82:7d:3a:1d:08:50`) |
| Flash | 4 MB (manufacturer `0x5e`, device `0x4016`), map 512 KB + 512 KB, SPI DOUT 40 MHz |
| USB-serial | FTDI FT232R (`0403:6001`), on the board's micro-USB |
| Firmware | AT 1.7.4.0 (11 May 2020), SDK 3.0.5 (52383f9), Bin 1.7.4 |
| Image | `esp8266_AT1.7.4_807d3a1d0850_2026-10-13.bin`, 4 194 304 bytes |
| SHA-256 | `ecbc494c933f7e47cb2a65ae316a3b96ae71815a2a94fb8664f8d8de1656523b` |
| Check | `esptool verify-flash 0 <image>` → digest matched |

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
