MSP integration tests against Betaflight SITL (ADR-019, Testing Strategy level 2)

Files:
- `msp_frames.py`: minimal MSP v1/v2 encoder and decoder (moves into sd_protocol later)
- `test_msp_frames.py`: unit tests for it, no SITL needed
- `test_msp_handshake.py`: MSP_API_VERSION over v1/v2, bad CRC/checksum and line noise ignored
- `conftest.py`: waits for TCP 5761 (UART1) and an MSP answer, up to `SITL_TIMEOUT_S` (60 s with `SITL_REQUIRED=1`, else 5 s)

Run locally (same steps as the `betaflight-sitl` job in `ci.yaml`):

```sh
git clone --depth 1 --branch 2026.6.2 https://github.com/betaflight/betaflight.git bf
git -C bf submodule update --init --recursive --depth 1   # avoids a make -j race
mkdir -p shim
printf '#!/bin/sh\n[ "$1" = "-dumpversion" ] && echo 13.3.1 && exit 0\nexit 1\n' > shim/arm-none-eabi-gcc
chmod +x shim/arm-none-eabi-gcc
PATH="$PWD/shim:$PATH" make -C bf TARGET=SITL -j"$(nproc)"
(cd bf && ./obj/main/betaflight_SITL.elf > ../sitl.log 2>&1 &)
SITL_REQUIRED=1 pytest drone/tests/sitl -v
pkill -f betaflight_SITL.elf
```

Without SITL the integration tests are skipped and only the unit tests run.
`SITL_REQUIRED=1` turns that skip into a failure (CI sets it).
Other variables: `SITL_HOST` (127.0.0.1), `SITL_MSP_PORT` (5761).
