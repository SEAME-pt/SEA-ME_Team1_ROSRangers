#!/usr/bin/env bash
# The PX4 readers of sim_start.sh (SIMULATION ONLY), in their own file so
# sim_smoke/test/test_sim_lib.py can run them against a fake px4-listener.
# Needs BIN (the folder with px4-listener). Sourced, never run.

# Value of one field of a PX4 uORB topic, from the PX4 shell (px4-listener).
# Example: "    nav_state: 14" -> px4_field vehicle_status nav_state -> 14
px4_field() {
    "$BIN/px4-listener" "$1" 2>/dev/null | awk -F': ' -v f="$2" '$1 ~ "^ *" f "$" {print $2; exit}'
}

# PX4 refuses Offboard unless offboard_control_mode keeps arriving: two
# different timestamps one call apart. The listener prints
# "timestamp: 123456789 (0.012 seconds ago)": only the number counts (the
# "seconds ago" part changes even when nothing arrives).
stream_last=""
streaming() {
    local now
    now=$(px4_field offboard_control_mode timestamp | awk '{print $1}')
    local previous=$stream_last
    stream_last=$now
    if [ -n "$now" ] && [ -n "$previous" ] && [ "$now" != "$previous" ]; then
        return 0
    fi
    return 1
}

# vehicle_status: arming_state 2 ARMED; nav_state 4 AUTO_LOITER (take-off
# done), 14 OFFBOARD, 17 AUTO_TAKEOFF.
armed() { [ "$(px4_field vehicle_status arming_state)" = 2 ]; }
nav_state() { px4_field vehicle_status nav_state; }

# Above MIN_ALT m: PX4 calls its heading good for control only above 1.5 m
# (F23); local z is NED (down +), so 1.6 m up is z -1.6.
MIN_ALT=1.6
up_high() {
    local z
    z=$(px4_field vehicle_local_position z | awk '{print $1}')
    [ -n "$z" ] && awk -v z="$z" -v min="$MIN_ALT" 'BEGIN { exit !(-z >= min) }'
}

# PX4's estimated height (m, up), for the log
height() { px4_field vehicle_local_position z | awk '{printf "%.2f", -$1}'; }

# Without it flight_interface publishes no /estimate/velocity and the
# controller stays inactive (F23).
heading_good() { [ "$(px4_field vehicle_local_position heading_good_for_control)" = True ]; }
