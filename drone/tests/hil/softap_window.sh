#!/usr/bin/env bash
# Run a command while the Pi is on the spare ESP's SoftAP, then re-join the
# lab Wi-Fi. The Pi has one Wi-Fi radio, so the runner is offline for the
# whole window: keep it under 2 min (ADR-020 update 2026-10-26, #106).
#
#   softap_window.sh SECONDS -- COMMAND [ARGS...]
#
# Needs SEAME_AP_SSID and SEAME_AP_PASS (the spare ESP's SoftAP) and
# passwordless `sudo nmcli` (/etc/sudoers.d/hil-nmcli on the Pi).
#
# If the job dies inside the window, a watchdog started outside the job
# re-joins the lab Wi-Fi after SECONDS + 180 s (both joins + 120 s grace).
set -euo pipefail

LAB_CON=${HIL_LAB_CON:-netplan-wlan0-SEAME}
IFACE=${HIL_WIFI_IFACE:-wlan0}
JOIN_WAIT_S=30
MAX_TEST_S=60 # join 30 + test 60 + re-join 30 = 120 s offline at most
WATCHDOG_GRACE_S=120

usage() {
  echo "usage: $0 SECONDS -- COMMAND [ARGS...]  (SECONDS 1..$MAX_TEST_S)" >&2
  exit 2
}

[[ $# -ge 3 && $2 == "--" && $1 =~ ^[0-9]+$ ]] || usage
test_s=$1
shift 2
((test_s >= 1 && test_s <= MAX_TEST_S)) || usage
: "${SEAME_AP_SSID:?SoftAP SSID not set}"
: "${SEAME_AP_PASS:?SoftAP password not set}"

# One profile per run, so a late watchdog never touches another run's window
con="hil-softap-${GITHUB_RUN_ID:-local}-$$"

# Outside the job: setsid leaves the job's process group, and an empty
# RUNNER_TRACKING_ID stops the runner from killing it when the job ends.
# It only acts if the lab Wi-Fi is down and this run's profile still exists.
RUNNER_TRACKING_ID="" setsid nohup bash -c '
  sleep "$1"
  nmcli -t -f NAME connection show --active | grep -qxF "$2" && exit 0
  nmcli -t -f NAME connection show | grep -qxF "$3" || exit 0
  sudo nmcli connection delete "$3"
  sudo nmcli connection up "$2"
' watchdog "$((JOIN_WAIT_S + test_s + JOIN_WAIT_S + WATCHDOG_GRACE_S))" \
  "$LAB_CON" "$con" >/dev/null 2>&1 &

psk_file=$(mktemp)
restore() {
  local rc=$?
  rm -f "$psk_file"
  sudo nmcli connection delete "$con" >/dev/null 2>&1 || true
  if ! sudo nmcli --wait "$JOIN_WAIT_S" connection up "$LAB_CON"; then
    echo "::error title=HIL Wi-Fi::could not re-join $LAB_CON, the watchdog retries in ${WATCHDOG_GRACE_S} s"
    rc=1
  fi
  exit "$rc"
}
trap restore EXIT

# autoconnect off: after a reboot the Pi must come back on the lab Wi-Fi.
# The password goes through a file, not the command line (ps shows arguments).
chmod 600 "$psk_file"
printf '802-11-wireless-security.psk:%s\n' "$SEAME_AP_PASS" >"$psk_file"
sudo nmcli connection add type wifi ifname "$IFACE" con-name "$con" \
  ssid "$SEAME_AP_SSID" autoconnect no wifi-sec.key-mgmt wpa-psk >/dev/null
sudo nmcli --wait "$JOIN_WAIT_S" connection up "$con" passwd-file "$psk_file"

echo "On $SEAME_AP_SSID, running for at most ${test_s} s: $*"
timeout "$test_s" "$@"
