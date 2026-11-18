#!/usr/bin/env bash
# The px4 service of make sim / make sim-smoke (#86). SIMULATION ONLY.
#
# 1. Micro XRCE-DDS agent and PX4 SITL + Gazebo (x500, headless)
# 2. waits for "Ready for takeoff" (NAV_DLL_ACT 0: there is no ground station)
# 3. px4_offboard_bridge (mask $MASK), which streams setpoints from now on
# 4. arms, takes off, then switches to Offboard: the drone hovers at zero
#    velocity until mission_node is ENGAGED
# 5. touches /tmp/sim_ready (the compose healthcheck), then runs until stopped
#
# Env: MASK 8|11|15 (default 15; the Makefile allows only 11|15 for sim-smoke), GUI 1 opens the Gazebo window (needs DISPLAY),
# READY_TIMEOUT_SEC (default 300). Output: this script and the bridge in
# $LOG_DIR/px4/ (entrypoint.sh), PX4, the agent and the Gazebo window in
# $LOG_DIR/px4-sitl/. On docker stop the bridge stops first and sends one last
# zero setpoint. Arming and Offboard happen here only because this is the sim;
# on the real drone the safety pilot does them, never code.
set -euo pipefail

MASK=${MASK:-15}
GUI=${GUI:-0}
READY_TIMEOUT_SEC=${READY_TIMEOUT_SEC:-300}
PX4_DIR=$HOME/PX4-Autopilot
BIN=$PX4_DIR/build/px4_sitl_default/bin
OUT=${LOG_DIR:-/tmp}/px4-sitl
READY=/tmp/sim_ready

say() { echo "[sim] $*"; }
fail() {
    say "FAILED: $*. See $OUT/px4.log"
    exit 1
}

case "$MASK" in
    8 | 11 | 15) ;;
    *)
        say "MASK must be 8, 11 or 15, got '$MASK'"
        exit 2
        ;;
esac

rm -f "$READY"
mkdir -p "$OUT"
pids=()
bridge=""
stop() {
    local code=${1:-0}
    trap - TERM INT
    rm -f "$READY"
    if [ -n "$bridge" ]; then
        kill -TERM "$bridge" 2>/dev/null || true
        wait "$bridge" 2>/dev/null || true
    fi
    kill -TERM "${pids[@]}" 2>/dev/null || true
    exit "$code"
}
trap 'stop 0' TERM INT

# Value of one field of a PX4 uORB topic, from the PX4 shell (px4-listener).
px4_field() {
    "$BIN/px4-listener" "$1" 2>/dev/null | awk -F': ' -v f="$2" '$1 ~ "^ *" f "$" {print $2; exit}'
}

# Waits until the command succeeds, at most $1 s, once per second.
wait_until() {
    local timeout=$1
    shift
    local deadline=$((SECONDS + timeout))
    until "$@"; do
        [ "$SECONDS" -lt "$deadline" ] || return 1
        sleep 1
    done
}

say "1/5 Micro XRCE-DDS agent, PX4 SITL + Gazebo (x500, headless)"
MicroXRCEAgent udp4 -p 8888 >"$OUT/agent.log" 2>&1 &
pids+=($!)
# The PX4 shell exits on end of input: tail keeps its stdin open.
(cd "$PX4_DIR" && tail -f /dev/null | HEADLESS=1 make px4_sitl gz_x500) >"$OUT/px4.log" 2>&1 &
pids+=($!)

say "2/5 Waiting for PX4 (up to $READY_TIMEOUT_SEC s)"
# A param set sent while PX4 still loads its parameters is lost (it exits 0
# anyway), so it is sent every second until PX4 is ready. Sim only.
px4_ready() {
    "$BIN/px4-param" set NAV_DLL_ACT 0 >/dev/null 2>&1 || true
    grep -q "Ready for takeoff" "$OUT/px4.log"
}
wait_until "$READY_TIMEOUT_SEC" px4_ready || fail "PX4 not ready in $READY_TIMEOUT_SEC s"

if [ "$GUI" = 1 ]; then
    say "Gazebo window. Closing it leaves the sim running"
    gz sim -g >"$OUT/gz_gui.log" 2>&1 &
fi

say "3/5 px4_offboard_bridge (mask $MASK)"
/ws/install/px4_offboard_bridge/lib/px4_offboard_bridge/px4_offboard_bridge \
    --ros-args -p mask:="$MASK" &
bridge=$!
# PX4 only accepts Offboard once setpoints have been streaming for a while.
sleep 3
kill -0 "$bridge" 2>/dev/null || fail "px4_offboard_bridge exited"

say "4/5 Arm, take off, Offboard"
# nav_state: 4 AUTO_LOITER (take-off done), 14 OFFBOARD, 17 AUTO_TAKEOFF
took_off() {
    [ "$(px4_field vehicle_status nav_state)" = 17 ] && return 0
    # Arming can be refused for a few seconds after "Ready" (estimator settling)
    "$BIN/px4-commander" arm >/dev/null 2>&1 || true
    "$BIN/px4-commander" takeoff >/dev/null 2>&1 || true
    [ "$(px4_field vehicle_status nav_state)" = 17 ]
}
wait_until 30 took_off || fail "no take-off"
loitering() { [ "$(px4_field vehicle_status nav_state)" = 4 ]; }
wait_until 60 loitering || fail "take-off did not finish"
offboard() {
    "$BIN/px4-commander" mode offboard >/dev/null 2>&1 || true
    [ "$(px4_field vehicle_status nav_state)" = 14 ]
}
wait_until 20 offboard || fail "Offboard refused"

touch "$READY"
say "5/5 Ready: armed, hovering in Offboard. The ground chain drives it now"

# Any of them dying ends the container (and make sim-smoke).
set +e
wait -n "$bridge" "${pids[@]}"
say "a process exited: stopping"
stop 1
