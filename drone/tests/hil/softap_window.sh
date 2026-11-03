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
# re-joins the lab Wi-Fi after SECONDS + 185 s (both joins, kill grace and
# 120 s), also when the re-join below failed.
set -euo pipefail

LAB_CON=${HIL_LAB_CON:-netplan-wlan0-SEAME}
IFACE=${HIL_WIFI_IFACE:-wlan0}
JOIN_WAIT_S=30
KILL_GRACE_S=5
MAX_TEST_S=50 # join 30 + test 50 + kill 5 + re-join 30 = 115 s offline at most
WATCHDOG_GRACE_S=120

usage() {
  echo "usage: $0 SECONDS -- COMMAND [ARGS...]  (SECONDS 1..$MAX_TEST_S)" >&2
  exit 2
}

[[ $# -ge 3 && $2 == "--" && $1 =~ ^[1-9][0-9]*$ ]] || usage # no leading 0: bash reads 010 as octal
test_s=$1
shift 2
((test_s <= MAX_TEST_S)) || usage
: "${SEAME_AP_SSID:?SoftAP SSID not set}"
: "${SEAME_AP_PASS:?SoftAP password not set}"

# One window at a time, including its re-join: a late watchdog of an earlier
# run waits for this lock, so it never races this run's re-join
lock=${HIL_WIFI_LOCK:-/tmp/hil-softap.lock}
exec 9>"$lock"
if ! flock -w 60 9; then
  echo "::error title=HIL Wi-Fi::another SoftAP window still holds $lock"
  exit 1
fi

# One profile per run, so a late watchdog never touches another run's profile
con="hil-softap-${GITHUB_RUN_ID:-local}-$$"

# Outside the job: setsid -f forks into a new session at once, so no kill of
# the step's process tree reaches it, and an empty RUNNER_TRACKING_ID stops
# the runner from killing it when the job ends. 9>&-: it must not hold this
# run's lock. Under the lock it removes this run's profile, switches the lab
# autoconnect back on and re-joins the lab if it is not up.
RUNNER_TRACKING_ID="" setsid -f nohup bash -c '
  sleep "$1"
  exec 9>"$4"
  flock -w 120 9 || exit 1 # a later window is open: its own restore re-joins
  sudo nmcli connection delete "$3" >/dev/null 2>&1
  sudo nmcli connection modify --temporary "$2" connection.autoconnect yes
  nmcli -t -f NAME connection show --active | grep -qxF "$2" && exit 0
  sudo nmcli connection up "$2"
' watchdog "$((JOIN_WAIT_S + test_s + KILL_GRACE_S + JOIN_WAIT_S + WATCHDOG_GRACE_S))" \
  "$LAB_CON" "$con" "$lock" >/dev/null 2>&1 9>&- &

# RUNNER_TEMP is wiped after each job, so a SIGKILL does not leave the
# password behind. mktemp creates it 0600.
psk_file=$(mktemp -p "${RUNNER_TEMP:-/tmp}")
cmd_pid=
restore() {
  local rc=$?
  trap - EXIT INT TERM
  # timeout passes the TERM on to the test and kills it KILL_GRACE_S later,
  # so it lets go of /dev/hil-esp
  [[ -z $cmd_pid ]] || kill -TERM "$cmd_pid" 2>/dev/null || true
  rm -f "$psk_file"
  sudo nmcli connection delete "$con" >/dev/null 2>&1 || true
  sudo nmcli connection modify --temporary "$LAB_CON" connection.autoconnect yes || true
  if ! sudo nmcli --wait "$JOIN_WAIT_S" connection up "$LAB_CON"; then
    echo "::error title=HIL Wi-Fi::could not re-join $LAB_CON, the watchdog retries about ${WATCHDOG_GRACE_S} s after the window"
    rc=1
  fi
  exit "$rc"
}
trap restore EXIT
# A cancel or step timeout sends INT/TERM: trapped, they interrupt the `wait`
# below at once, so restore runs before the runner's SIGKILL
trap 'exit 130' INT
trap 'exit 143' TERM

# While the window is open the lab must not autoconnect: if the SoftAP drops
# for a moment (ESP reset), NetworkManager would fall back to the lab and the
# tests would time out. Both changes are in memory only (--temporary, save
# no), so a reboot always brings the Pi back on the lab Wi-Fi.
# The password goes through a file, not the command line (ps shows arguments).
printf '802-11-wireless-security.psk:%s\n' "$SEAME_AP_PASS" >"$psk_file"
sudo nmcli connection modify --temporary "$LAB_CON" connection.autoconnect no
sudo nmcli connection add save no type wifi ifname "$IFACE" con-name "$con" \
  ssid "$SEAME_AP_SSID" autoconnect yes wifi-sec.key-mgmt wpa-psk >/dev/null
sudo nmcli --wait "$JOIN_WAIT_S" connection up "$con" passwd-file "$psk_file"

echo "On $SEAME_AP_SSID, running for at most ${test_s} s: $*"
timeout -k "$KILL_GRACE_S" "$test_s" "$@" 9>&- &
cmd_pid=$!
wait "$cmd_pid"
