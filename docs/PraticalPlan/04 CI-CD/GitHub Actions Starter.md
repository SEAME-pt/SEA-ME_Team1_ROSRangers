---
title: GitHub Actions Starter
type: ci-cd
status: draft
date: 2026-10-20
project: SEA autonomous drone
tags:
  - ci
  - github-actions
---

# GitHub Actions Starter

> [!warning] Status: STARTER. Adapt as the software repo grows.
> Both workflow files parse as valid YAML (checked 2026-10-12). The real files are `.github/workflows/ci.yaml` and `hil.yaml` and they have run on GitHub; they have drifted from this first draft (paths now start with `drone/`, and each job is gated by a component check). Treat the repo files as the source of truth.

---

## 1. `.github/workflows/ci.yaml`

Runs on PRs into `dev` and `main` and on pushes to both, as required by [[Dev_Strategy]] §11.

> [!note] The SITL job
> Betaflight's Makefile insists on an `arm-none-eabi-gcc` version even for SITL, which actually builds with the host gcc. The three-line shim fakes only that version probe. This built 2026.6.2 SITL in about 25 s (verified). 4.5.1 SITL fails to link out of the box, which is another reason for [[ADR-014 Betaflight Version|ADR-014]].

```yaml
name: ci
on:
  pull_request:
    branches: [dev, main]
  push:
    branches: [dev, main]

concurrency:
  group: ci-${{ github.ref }}
  cancel-in-progress: true

jobs:
  lint:
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pre-commit
      - run: pre-commit run --all-files

  protocol-cpp:
    needs: lint
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - run: sudo apt-get update && sudo apt-get install -y cmake ninja-build libgtest-dev gcovr clang-tidy cppcheck
      - run: cmake -S lib/protocol -B build -G Ninja -DCMAKE_BUILD_TYPE=Debug -DSD_COVERAGE=ON -DCMAKE_EXPORT_COMPILE_COMMANDS=ON
      - run: cmake --build build
      - run: ctest --test-dir build --output-on-failure
      - name: Coverage gate
        run: gcovr -r lib/protocol build --filter lib/protocol/src --fail-under-line 80 --xml-pretty -o coverage.xml
      - name: Static analysis
        run: |
          clang-tidy -p build $(git ls-files 'lib/protocol/src/*.cpp') --warnings-as-errors='*'
          cppcheck --enable=warning,style,performance --error-exitcode=1 --inline-suppr lib/protocol/src lib/protocol/include
      - uses: actions/upload-artifact@v4
        with:
          name: coverage-cpp
          path: coverage.xml

  ground-python:
    needs: lint
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pytest pytest-cov
      - run: pip install -e ground/src/sd_protocol_py
      - run: pytest ground/src/sd_protocol_py --cov=sd_protocol --cov-fail-under=80

  esp-firmware:
    needs: lint
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install platformio
      - run: pio run -d firmware/esp8266 -e d1_mini
        env:
          SD_PSK: ${{ secrets.SD_PSK_CI }}
      - uses: actions/upload-artifact@v4
        with:
          name: esp-firmware
          path: firmware/esp8266/.pio/build/d1_mini/firmware.bin

  betaflight-sitl:
    needs: [protocol-cpp, ground-python]
    runs-on: ubuntu-24.04
    steps:
      - uses: actions/checkout@v4
      - name: Build Betaflight SITL (same version as the drone)
        run: |
          git clone --depth 1 --branch 2026.6.2 https://github.com/betaflight/betaflight.git bf
          mkdir -p shim && printf '#!/bin/sh\n[ "$1" = "-dumpversion" ] && echo 13.3.1 && exit 0\nexit 1\n' > shim/arm-none-eabi-gcc
          chmod +x shim/arm-none-eabi-gcc
          PATH="$PWD/shim:$PATH" make -C bf TARGET=SITL -j"$(nproc)"
      - uses: actions/setup-python@v5
        with:
          python-version: "3.12"
      - run: pip install pytest && pip install -e ground/src/sd_protocol_py
      - name: MSP integration tests against SITL (TCP 5761)
        run: |
          (cd bf && ./obj/main/betaflight_SITL.elf > sitl.log 2>&1 &)
          sleep 3
          pytest tests/sitl -v
```

## 2. `.github/workflows/hil.yaml`

Runs on pushes to `dev`/`main` and manually. It **never** runs on PRs from forks: a self-hosted runner executes the workflow's code on our Pi ([[HIL Runner]]).

```yaml
name: hil
on:
  push:
    branches: [dev, main]
  workflow_dispatch:

concurrency:
  group: hil-bench
  cancel-in-progress: false

jobs:
  hil:
    runs-on: [self-hosted, linux, ARM64, hil-drone]
    timeout-minutes: 20
    steps:
      - uses: actions/checkout@v4
      - name: Build firmware on the runner
        run: |
          python3 -m venv .venv && . .venv/bin/activate
          pip install platformio pytest pyserial
          pio run -d firmware/esp8266 -e d1_mini
        env:
          SD_PSK: ${{ secrets.SD_PSK_HIL }}
      - name: Flash the bench D1 mini over the air (USB data never touches UART0)
        run: |
          . .venv/bin/activate
          pio run -d firmware/esp8266 -e d1_mini_ota -t upload
      - name: Bench tests (no battery, no props)
        run: |
          . .venv/bin/activate
          pytest tests/hil -v --junitxml=hil-report.xml
      - uses: actions/upload-artifact@v4
        if: always()
        with:
          name: hil-report
          path: hil-report.xml
```

## 3. `firmware/esp8266/platformio.ini`

```ini
[env:d1_mini]
platform = espressif8266
board = d1_mini
framework = arduino
monitor_speed = 115200
lib_deps =
    symlink://../../lib/protocol
    plerup/EspSoftwareSerial
build_flags =
    -DSD_PSK=\"${sysenv.SD_PSK}\"

; Over-the-air upload, used by the HIL job (USB data never touches UART0 there)
[env:d1_mini_ota]
extends = env:d1_mini
upload_protocol = espota
upload_port = 192.168.4.1
```

`d1_mini` uploads over USB (first flash or recovery, **FC wires unplugged**). `d1_mini_ota` uploads over Wi-Fi.

## 4. `.pre-commit-config.yaml` (start)

```yaml
repos:
  - repo: https://github.com/pre-commit/pre-commit-hooks
    rev: v5.0.0
    hooks:
      - id: end-of-file-fixer
      - id: trailing-whitespace
      - id: check-yaml
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.6.9
    hooks:
      - id: ruff
      - id: ruff-format
  - repo: https://github.com/pre-commit/mirrors-clang-format
    rev: v19.1.0
    hooks:
      - id: clang-format
        types_or: [c, c++]
```

Pin the hook versions to whatever `pre-commit autoupdate` gives you on day 1.

## 5. `.github/pull_request_template.md`

Based on [[Dev_Strategy]] §17, plus a safety line:

```markdown
## Description
What was changed?

## Related Issue
Closes #XX

## Requirements / ADRs
REQ-XXX-XXX · ADR-0XX

## Testing
- [ ] Build passes
- [ ] Unit tests pass
- [ ] Integration tests pass (vectors / SITL)
- [ ] HIL passed
- [ ] Simulation tested
- [ ] Hardware (bench, props off) tested
- [ ] Flight tested

## Safety
Could this change move a motor or affect failsafe? yes / no. If yes, explain.

## Notes
Anything reviewers should know?
```

## 6. Glossary

| Term | Meaning |
| --- | --- |
| **ADR** | Architecture Decision Record: a documented decision and its rationale. |
| **Branch** | An independent line of development in version control. |
| **CI** | Continuous Integration: automated validation of proposed changes. |
| **Commit** | A recorded snapshot of changes in a version-control repository. |
| **ESP8266** | A WiFi-enabled microcontroller used as the drone communication module. |
| **Failsafe** | An automatic safe response when control, communication, or a sensor fails. |
| **FC** | Flight Controller: the onboard controller responsible for stabilization and control. |
| **HIL** | Hardware-in-the-Loop: testing that connects real or emulated hardware to a simulator. |
| **MSP** | MultiWii Serial Protocol, used for communication with Betaflight flight controllers. |
| **TCP** | Transmission Control Protocol: reliable, ordered, connection-oriented transport. |
| **PR** | Pull Request: a proposed change submitted for review before merging. |
| **SITL** | Software In The Loop: the flight code runs on the PC against a simulator. |
