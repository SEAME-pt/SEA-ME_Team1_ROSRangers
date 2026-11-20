#!/usr/bin/env bash
# real_check.sh PSK_FILE ESP_HOST: before make real starts the real mode.
#
#   * PSK_FILE (the file the flight_interface container mounts, psk.json by
#     default, ADR-025 E3.2) exists, has mode 600 and holds a PSK of 64 hex
#     characters. The key is never printed.
#   * ESP_HOST (esp_host in ground_bringup's config/real.yaml) answers ping.
#     ping only: a TCP connection to the gateway's port could start a session.
# Every problem on stderr; exit 1 if there is one. make real-dry skips this
# (loopback backend: no PSK, no drone).
set -euo pipefail

if (($# != 2)); then
    echo "usage: $0 PSK_FILE ESP_HOST" >&2
    exit 2
fi
psk=$1
host=$2

errors=0
problem() {
    echo "make real: $*" >&2
    errors=1
}

if [[ ! -f $psk ]]; then
    problem "$psk is missing: make psk-file (ADR-025 E3.2)"
else
    mode=$(stat -c %a "$psk")
    [[ $mode == 600 ]] || problem "$psk has mode $mode, must be 600: chmod 600 $psk"
    grep -qE '"psk"[[:space:]]*:[[:space:]]*"[0-9a-fA-F]{64}"' "$psk" ||
        problem "$psk holds no valid PSK (64 hex characters): make psk-file"
fi

if [[ -z $host ]]; then
    problem "no esp_host in ground_bringup's config/real.yaml"
elif ! command -v ping >/dev/null; then
    problem "ping not found: cannot check that the ESP ($host) answers"
elif ! ping -c 1 -W 2 "$host" >/dev/null 2>&1; then
    problem "the ESP ($host) does not answer: join the drone's Wi-Fi first" \
        "(python3 drone/tools/bench/seame.py network connect)"
fi

((errors)) && exit 1
echo "make real: $psk and the ESP ($host) OK"
