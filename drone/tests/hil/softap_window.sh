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
# re-joins the lab Wi-Fi after SECONDS + 65 s (both joins, kill grace and
# 10 s; 115 s for a 50 s test), also when the re-join below failed.
set -euo pipefail

LAB_CON=${HIL_LAB_CON:-netplan-wlan0-SEAME}
IFACE=${HIL_WIFI_IFACE:-wlan0}
JOIN_WAIT_S=25 # a join takes about 6 s on the bench
KILL_GRACE_S=5
MAX_TEST_S=50 # join 25 + test 50 + kill 5 + re-join 25 = 105 s offline at most
# The watchdog waits for the lock, so it can never race this run's restore:
# a short grace is enough, and even a killed run is back within 2 min
WATCHDOG_GRACE_S=10

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
# run's lock. It fires WATCHDOG_GRACE_S after this window can have ended,
# and waits for a later window for as long as a whole window can last: both
# follow from the limits above, so changing those keeps it in step.
window_max_s=$((JOIN_WAIT_S + MAX_TEST_S + KILL_GRACE_S + JOIN_WAIT_S))
RUNNER_TRACKING_ID="" setsid -f bash -c '
  sleep "$1" || exit 1 # killed while waiting: someone stopped the watchdog
  # Never act while this run is still alive: nothing bounds how long a
  # stalled nmcli takes, and its own restore re-joins. A run stuck for good
  # is killed by the step timeout (hil.yaml), and then this goes on
  while kill -0 "$7" 2>/dev/null; do sleep 5 || exit 1; done
  # Its own profile first, lock or not: the name is unique to this run, and
  # a leftover autoconnect profile could pull the Pi onto the SoftAP later
  sudo nmcli connection delete "$3" >/dev/null 2>&1
  exec 9>"$4"
  flock -w "$5" 9 || exit 1 # a later window is open: its own restore re-joins
  sudo nmcli connection modify --temporary "$2" connection.autoconnect yes
  nmcli -t -f NAME connection show --active | grep -qxF "$2" && exit 0
  sudo nmcli --wait "$6" connection up "$2"
' watchdog "$((JOIN_WAIT_S + test_s + KILL_GRACE_S + JOIN_WAIT_S + WATCHDOG_GRACE_S))" \
  "$LAB_CON" "$con" "$lock" "$window_max_s" "$JOIN_WAIT_S" "$$" >/dev/null 2>&1 9>&-

# RUNNER_TEMP is wiped after each job, so a SIGKILL does not leave the
# password behind. mktemp creates it 0600.
psk_file=$(mktemp -p "${RUNNER_TEMP:-/tmp}")

# A cancel or step timeout sends INT/TERM (hil.yaml execs this script, so the
# signal reaches it). Bash defers a trap while a foreground command runs, so
# every command that talks to NetworkManager or runs the test goes through
# step(): in the background, then `wait`, which a trapped signal interrupts
# at once. 9>&-: a step still running after the script exits must not hold
# the lock.
bg_pid=
step() {
  "$@" 9>&- &
  bg_pid=$!
  local rc=0
  wait "$bg_pid" || rc=$?
  bg_pid= # finished: restore must not signal a PID that may be reused
  return "$rc"
}

restore() {
  local rc=$?
  # Ignore, not reset: the runner sends SIGTERM 7.5 s after SIGINT
  trap - EXIT
  trap '' INT TERM
  rm -f "$psk_file"
  # Network first: a SIGKILL can follow a cancel within about 10 s. Once
  # nmcli has asked NetworkManager to activate the lab, killing nmcli no
  # longer stops the activation. A join or test still running gets a TERM
  # (timeout passes it on, and kills the test KILL_GRACE_S later). Its child
  # gets one too: sudo may not pass on a signal sent from its own process
  # group, so the TERM also goes to nmcli directly
  if [[ -n $bg_pid ]]; then
    pkill -TERM -P "$bg_pid" 2>/dev/null || true
    kill -TERM "$bg_pid" 2>/dev/null || true
  fi
  sudo nmcli connection delete "$con" >/dev/null 2>&1 || true
  sudo nmcli connection modify --temporary "$LAB_CON" connection.autoconnect yes || true
  if ! sudo nmcli --wait "$JOIN_WAIT_S" connection up "$LAB_CON"; then
    echo "::error title=HIL Wi-Fi::could not re-join $LAB_CON, the watchdog retries about ${WATCHDOG_GRACE_S} s after the window"
    rc=1
  fi
  # Then wait for the test, so pytest has let go of /dev/hil-esp before the
  # next step
  [[ -z $bg_pid ]] || wait "$bg_pid" 2>/dev/null || true
  exit "$rc"
}
trap restore EXIT
trap 'exit 130' INT
trap 'exit 143' TERM

# While the window is open the lab must not autoconnect: if the SoftAP drops
# for a moment (ESP reset), NetworkManager would fall back to the lab and the
# tests would time out. Both changes are in memory only (--temporary, save
# no), so a reboot always brings the Pi back on the lab Wi-Fi.
# The password goes through a file, not the command line (ps shows arguments).
printf '802-11-wireless-security.psk:%s\n' "$SEAME_AP_PASS" >"$psk_file"
step sudo nmcli connection modify --temporary "$LAB_CON" connection.autoconnect no
step sudo nmcli connection add save no type wifi ifname "$IFACE" con-name "$con" \
  ssid "$SEAME_AP_SSID" autoconnect yes wifi-sec.key-mgmt wpa-psk >/dev/null
step sudo nmcli --wait "$JOIN_WAIT_S" connection up "$con" passwd-file "$psk_file"

echo "On $SEAME_AP_SSID, running for at most ${test_s} s: $*"
step timeout -k "$KILL_GRACE_S" "$test_s" "$@"
