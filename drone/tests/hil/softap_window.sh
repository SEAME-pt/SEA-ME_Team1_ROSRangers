#!/usr/bin/env bash
# Run a command while the Pi is on the spare ESP's SoftAP, then re-join the
# lab Wi-Fi. The Pi has one Wi-Fi radio, so the runner is offline for the
# whole window: keep it under 2 min (ADR-020 update 2026-10-26, #106).
#
#   softap_window.sh SECONDS -- COMMAND [ARGS...]
#
# Needs SEAME_AP_SSID and SEAME_AP_PASS (the spare ESP's SoftAP; its channel
# from SEAME_AP_CHANNEL, default 6) and
# passwordless `sudo nmcli` (/etc/sudoers.d/hil-nmcli on the Pi).
#
# If the job dies inside the window, a watchdog started outside the job
# re-joins the lab Wi-Fi once the longest window with this SECONDS has
# passed, plus WATCHDOG_GRACE_S, also when the re-join below failed.
set -euo pipefail

LAB_CON=${HIL_LAB_CON:-netplan-wlan0-SEAME}
IFACE=${HIL_WIFI_IFACE:-wlan0}
JOIN_WAIT_S=25 # a join takes about 6 s on the bench
# The lab's 5 GHz AP sometimes ignores the Pi's logins after the window, and
# NetworkManager keeps retrying: back after 25.6 s, 25.6 s and 49 s (#193,
# 2026-11-17 to 2026-11-19). So the re-join waits for what is left of this
# run's window (window_s below), REJOIN_WAIT_S at least; a run whose test
# ends early gets more.
REJOIN_WAIT_S=35
KILL_GRACE_S=5
# A window is JOIN_WAIT_S + MAX_TEST_S + KILL_GRACE_S + REJOIN_WAIT_S offline
# at most: 115 s, under ADR-020's 2 min. Green runs of the gateway's Wi-Fi
# tests took 27-39 s (2026-11-13 to 2026-11-17).
MAX_TEST_S=50
# The watchdog waits for the lock, so it can never race this run's restore:
# a short grace is enough. It only acts for a job that is already dead
# (killed), so the 2 min a live job needs (ADR-020) does not bind it.
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
window_max_s=$((JOIN_WAIT_S + MAX_TEST_S + KILL_GRACE_S + REJOIN_WAIT_S))
window_s=$((JOIN_WAIT_S + test_s + KILL_GRACE_S + REJOIN_WAIT_S)) # this run's
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
' watchdog "$((window_s + WATCHDOG_GRACE_S))" \
  "$LAB_CON" "$con" "$lock" "$window_max_s" "$REJOIN_WAIT_S" "$$" >/dev/null 2>&1 9>&-

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
left_lab=
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
  # What is left of the window since the lab was left; REJOIN_WAIT_S if it
  # never was (a failed add) or at least
  local wait_s=$REJOIN_WAIT_S t0
  if [[ -n $left_lab ]]; then
    wait_s=$((window_s - (SECONDS - left_lab)))
    ((wait_s >= REJOIN_WAIT_S)) || wait_s=$REJOIN_WAIT_S
  fi
  t0=$(date +%s.%N)
  if sudo nmcli --wait "$wait_s" connection up "$LAB_CON"; then
    # Over JOIN_WAIT_S: the AP ignored the first login and the longer wait
    # saved the run (#193)
    awk -v t0="$t0" -v t1="$(date +%s.%N)" 'BEGIN { printf "Lab re-join took %.1f s\n", t1 - t0 }'
  else
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
# Power save off (2 = disable): a dozing Pi makes the ESP's SoftAP hold its
# packets, which stalled TCP connects and EVENTs (#106, 2026-11-13). The Pi's
# NetworkManager config sets it too; this keeps it off without that file.
# Static address, as the ground PC has (scripts/setup_production.py): the
# ESP's DHCP took 16 s of the 25 s join on 2026-11-16 (#207). No gateway, so
# the SoftAP never becomes the Pi's default route.
# Band and channel pinned: the Pi skips the 5 GHz scan and tunes straight to
# the SoftAP, coming from the lab's 5 GHz network (#207). SEAME_AP_CHANNEL
# is the one the firmware was built with (hil.yaml, secrets.py; default 6)
step sudo nmcli connection add save no type wifi ifname "$IFACE" con-name "$con" \
  ssid "$SEAME_AP_SSID" autoconnect yes wifi-sec.key-mgmt wpa-psk \
  802-11-wireless.powersave 2 \
  802-11-wireless.band bg 802-11-wireless.channel "${SEAME_AP_CHANNEL:-6}" \
  ipv4.method manual ipv4.addresses 192.168.4.2/24 ipv6.method disabled >/dev/null
left_lab=$SECONDS # the window starts: the lab goes down with this join
step sudo nmcli --wait "$JOIN_WAIT_S" connection up "$con" passwd-file "$psk_file"
# What the driver really does, in the log of every run
iw dev "$IFACE" get power_save 2>/dev/null || echo "Power save: unknown (no iw)"

echo "On $SEAME_AP_SSID, running for at most ${test_s} s: $*"
step timeout -k "$KILL_GRACE_S" "$test_s" "$@"
