#!/usr/bin/env bash
# The px4 service of make sim / make sim-smoke (#86, #199). SIMULATION ONLY.
#
# 1. Micro XRCE-DDS agent and PX4 SITL + Gazebo (x500, headless)
# 2. waits for "Ready for takeoff" (NAV_DLL_ACT 0: there is no ground station;
#    MIS_TAKEOFF_ALT 2.0: above the 1.5 m PX4's EKF2 needs before it calls
#    the heading good for control, ADR-025 F23)
# 3. configures and activates flight_interface (flight_interface_sim, backend
#    px4) with ros2 lifecycle (F22): the operator's steps in real mode. From
#    then on it streams setpoints: PX4's position hold while it does not
#    command (F19)
# 4. waits until PX4 receives that stream (PX4 refuses Offboard without it)
# 5. arms, takes off to 2.0 m, waits until PX4 calls its heading good for
#    control, then switches to Offboard: the controller flies from then on,
#    holding zero velocity until mission_node is ENGAGED (F10)
# 6. touches /tmp/sim_ready (the compose healthcheck), then runs until stopped
#
# Env: GUI 1 opens the Gazebo window (needs DISPLAY), READY_TIMEOUT_SEC
# (default 300). The mask is in config/sim.yaml (F24). Output: this script in
# $LOG_DIR/px4/ (entrypoint.sh), PX4, the agent and the Gazebo window in
# $LOG_DIR/px4-sitl/, flight_interface in $LOG_DIR/flight_interface_sim/.
# Configure, activate, arming and Offboard happen here only because this is
# the sim; on the real drone the operator and the safety pilot do them, never
# code.
set -euo pipefail

GUI=${GUI:-0}
READY_TIMEOUT_SEC=${READY_TIMEOUT_SEC:-300}
PX4_DIR=$HOME/PX4-Autopilot
BIN=$PX4_DIR/build/px4_sitl_default/bin
OUT=${LOG_DIR:-/tmp}/px4-sitl
READY=/tmp/sim_ready
# m (F23, changed 2026-11-24). PX4's EKF2 finishes its in-flight yaw
# alignment only above 1.5 m (mag_control.cpp, mag_anomalies_max_hagl);
# until then heading_good_for_control is false and flight_interface's px4
# backend publishes no /estimate/velocity, so the controller never flies.
# The hold test descends to 1.0 m through the controller afterwards.
TAKEOFF_ALT=2.0
FI=/flight_interface

say() { echo "[sim] $*"; }
fail() {
    say "FAILED: $*. See $OUT/px4.log"
    exit 1
}

rm -f "$READY"
mkdir -p "$OUT"
pids=()
stop() {
    local code=${1:-0}
    trap - TERM INT
    rm -f "$READY"
    kill -TERM "${pids[@]}" 2>/dev/null || true
    exit "$code"
}
trap 'stop 0' TERM INT

# px4_field, streaming, armed, nav_state, up_high, height, heading_good
# shellcheck source=sim_lib.sh
source "$(dirname "$0")/sim_lib.sh"

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

say "1/6 Micro XRCE-DDS agent, PX4 SITL + Gazebo (x500, headless)"
MicroXRCEAgent udp4 -p 8888 >"$OUT/agent.log" 2>&1 &
pids+=($!)
# The PX4 shell exits on end of input: tail keeps its stdin open.
(cd "$PX4_DIR" && tail -f /dev/null | HEADLESS=1 make px4_sitl gz_x500) >"$OUT/px4.log" 2>&1 &
pids+=($!)

say "2/6 Waiting for PX4 (up to $READY_TIMEOUT_SEC s)"
# A param set sent while PX4 still loads its parameters is lost (it exits 0
# anyway), so they are sent every second until PX4 is ready. Sim only.
px4_ready() {
    "$BIN/px4-param" set NAV_DLL_ACT 0 >/dev/null 2>&1 || true
    "$BIN/px4-param" set MIS_TAKEOFF_ALT "$TAKEOFF_ALT" >/dev/null 2>&1 || true
    grep -q "Ready for takeoff" "$OUT/px4.log"
}
wait_until "$READY_TIMEOUT_SEC" px4_ready || fail "PX4 not ready in $READY_TIMEOUT_SEC s"
# param compare exits 0 only when the value is the one given
takeoff_alt_set() {
    "$BIN/px4-param" set MIS_TAKEOFF_ALT "$TAKEOFF_ALT" >/dev/null 2>&1 || true
    "$BIN/px4-param" compare MIS_TAKEOFF_ALT "$TAKEOFF_ALT" >/dev/null 2>&1
}
wait_until 10 takeoff_alt_set || fail "MIS_TAKEOFF_ALT not set to $TAKEOFF_ALT"

if [ "$GUI" = 1 ]; then
    say "Gazebo window. Closing it leaves the sim running"
    gz sim -g >"$OUT/gz_gui.log" 2>&1 &
fi

say "3/6 Configure and activate flight_interface (flight_interface_sim)"
# One step per call: configure while unconfigured, activate while inactive.
# A configure that fails (e.g. no px4_msgs) leaves it unconfigured: tried
# again until the timeout, then its log says why.
# ros2 lifecycle without the daemon, waiting up to 3 s for discovery (a
# fresh container has none). get prints e.g. "active [3]".
lifecycle() {
    local verb=$1
    shift
    ros2 lifecycle "$verb" --no-daemon --spin-time 3 "$FI" "$@" 2>/dev/null
}
fi_active() {
    local state
    state=$(lifecycle get | awk '{print $1}')
    case "$state" in
        active) return 0 ;;
        unconfigured) lifecycle set configure >/dev/null || true ;;
        inactive) lifecycle set activate >/dev/null || true ;;
    esac
    return 1
}
wait_until 120 fi_active ||
    fail "flight_interface not active in 120 s (its log: logs/flight_interface_sim/latest.log)"

say "4/6 Waiting for the Offboard stream from flight_interface"
# PX4 refuses Offboard unless offboard_control_mode keeps arriving
# (streaming, sim_lib.sh), checked once per second.
wait_until 30 streaming || fail "no offboard_control_mode from flight_interface"

say "5/6 Arm, take off to $TAKEOFF_ALT m, Offboard"
# Every step checks that PX4 is ARMED (armed, sim_lib.sh): a mode switch
# (takeoff, loiter, Offboard) is accepted while disarmed too, so the mode
# alone does not say the drone flies.
took_off() {
    if ! armed; then
        # Arming can be refused for a few seconds after "Ready" (estimator settling)
        "$BIN/px4-commander" arm >/dev/null 2>&1 || true
        return 1
    fi
    [ "$(nav_state)" = 17 ] && return 0
    "$BIN/px4-commander" takeoff >/dev/null 2>&1 || true
    armed && [ "$(nav_state)" = 17 ]
}
wait_until 30 took_off || fail "no take-off (not armed, or no AUTO_TAKEOFF)"
# Take-off done: loitering, still armed, and above MIN_ALT (up_high).
loitering() { armed && [ "$(nav_state)" = 4 ] && up_high; }
wait_until 60 loitering || fail "take-off did not reach $MIN_ALT m"
# height (sim_lib.sh), for the log: the 0.25-0.4 m lost between take-off
# and the scenarios' start (#199 Phase 8)
say "take-off done: height $(height) m (PX4)"
# heading_good (sim_lib.sh): without it the controller stays inactive (F23)
wait_until 30 heading_good || fail "PX4's heading not good for control at $MIN_ALT m"
offboard() {
    "$BIN/px4-commander" mode offboard >/dev/null 2>&1 || true
    armed && [ "$(nav_state)" = 14 ]
}
wait_until 20 offboard || fail "Offboard refused"
say "in Offboard: height $(height) m (PX4)"

touch "$READY"
say "6/6 Ready: armed, in Offboard. The controller holds zero velocity; gestures drive it"
# The first seconds after the switch to the controller, for the log only
(
    previous=0
    for t in 1 2 3 5 8 12; do
        sleep $((t - previous))
        previous=$t
        say "Offboard + ${t} s: height $(height) m (PX4)"
    done
) &

# Either of them dying ends the container (and make sim-smoke).
set +e
wait -n "${pids[@]}"
say "a process exited: stopping"
stop 1
