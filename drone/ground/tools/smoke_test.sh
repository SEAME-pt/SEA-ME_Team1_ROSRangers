#!/bin/bash
# Docker smoke test for the ground images (CI job ground-docker, `make smoke`).
#
#   1. Build gesture, intent, mission, echo and flight_interface.
#   2. Run each package's tests inside its own image, none skipped: the
#      image's own OpenCV, MediaPipe, sd_protocol and ROS must work, not only
#      CI's install.
#   3. Across containers: gesture_node's real main() with a fake camera
#      (src/gesture/gesture_node/test/fake_camera.py) in the gesture image publishes
#      L:INDEX_RIGHT+R:NONE -> intent container -> MOVE_RIGHT on
#      /gesture/intent ->
#      mission container (pretend /flight/state from the flight container)
#      sees MOVE_RIGHT on /mission/state but never engages: nobody showed
#      ENGAGE. The echo image's own command (src/echo/monitor.py,
#      the table of `docker compose up`, here one line per change) shows it.
#   4. docker stop the gesture container: it exits 0 at once, intent falls
#      back to NEUTRAL (reason no_packets) and mission, airborne, stays
#      WAITING: no land request.
#   5. docker stop the intent, mission, flight and monitor containers: exit 0
#      at once. The two `ros2 topic echo` probes are test instruments, not our
#      nodes: they are killed without a check (the ROS 2 CLI sometimes hangs
#      on SIGTERM, #181), and kept until cleanup() so their logs are saved.
#   6. Node logs (docker/entrypoint.sh): intent, mission and flight, started
#      by compose, wrote logs/smoke/<service>/latest.log, every line
#      timestamped. A container stopped with docker stop keeps every line its
#      command prints while stopping (the entrypoint is PID 1 there).
#   7. flight_interface, from its compose service (loopback backend, a dummy
#      secrets file mounted, no PSK): configure + activate, its /flight/state
#      reaches mission, which stays IDLE (not_streaming) for 5 s: the
#      loopback drone has no override, so nothing can engage. /flight/state
#      has exactly one publisher (ros2 topic info). Never
#      backend:=esp, never ARM. docker stop: it deactivates (last zero
#      setpoint, "inactive: no more setpoints" in its log) and exits 0 at once.
#
# Own compose project, ROS domain and log folder (logs/smoke), so a running
# `make up` stack (or teammates on the network) is neither stopped nor mixed
# in, and its logs/<service>/latest.log stay as they were.
# Run from anywhere:  drone/ground/tools/smoke_test.sh
# SMOKE_LOG_DIR=dir (CI sets it): save each container's full log there as
# <container>.log, pass or fail. Unset (make smoke): only logs/smoke is written.
set -euo pipefail

SELF=$(realpath "$0")
# Absolute before the cd below, so a relative SMOKE_LOG_DIR still works
LOG_DIR=${SMOKE_LOG_DIR:+$(realpath -m "$SMOKE_LOG_DIR")}
cd "$(dirname "$0")/.."
export ROS_DOMAIN_ID=86 # tests use 81-85 and 87-91: `grep -r TEST_DOMAIN_ID src ../tests`
# The compose services' log folder (docker-compose.yml). Made here, not by
# Docker, so it belongs to the user and the next run can empty it.
export GROUND_LOG_DIR=./logs/smoke
# The images' user `ros` gets this user's uid and gid (docker/base/), so it
# can write to the log folder made below (CI's runner is not uid 1000). The
# Makefile sets them too; root: the image's default, 1000.
uid=$(id -u) gid=$(id -g)
export GROUND_UID=${GROUND_UID:-$( ((uid)) && echo "$uid" || echo 1000)}
export GROUND_GID=${GROUND_GID:-$( ((gid)) && echo "$gid" || echo 1000)}
rm -rf "$GROUND_LOG_DIR"
mkdir -p "$GROUND_LOG_DIR"
PROJECT=ground-smoke
# --profile flight_interface: build (step 1) and start (step 7) it too
COMPOSE="docker compose -p $PROJECT -f docker-compose.yml --profile flight_interface"
INTENT=$PROJECT-intent-1
GESTURE=$PROJECT-gesture
MISSION=$PROJECT-mission-1
FLIGHT=$PROJECT-flight-1
FLIGHT_INTERFACE=$PROJECT-flight_interface-1
ECHO=$PROJECT-echo
MISSION_ECHO=$PROJECT-mission-echo
FI_MISSION_ECHO=$PROJECT-fi-mission-echo
LIFECYCLE=$PROJECT-lifecycle
MONITOR=$PROJECT-monitor
STOPPER=$PROJECT-stopper
CONTAINERS=("$INTENT" "$MISSION" "$FLIGHT" "$GESTURE" "$ECHO" "$MISSION_ECHO"
    "$MONITOR" "$STOPPER" "$FLIGHT_INTERFACE" "$FI_MISSION_ECHO" "$LIFECYCLE")
# flight_interface's service mounts the PSK file (psk.json by default)
# read-only and refuses to start without one. The smoke test only runs the loopback backend, which
# never reads it: an empty dummy, never a real key.
SEAME_SECRETS_FILE=$(mktemp)
export SEAME_SECRETS_FILE
echo '{}' >"$SEAME_SECRETS_FILE"
chmod 600 "$SEAME_SECRETS_FILE"
# A line as entrypoint.sh writes it to a log file: wall-clock time first
STAMP='^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}:[0-9]{2}\.[0-9]{6} '
# Talk ROS with the intent service: host network and IPC, same domain
ROS=(--network host --ipc host -e ROS_DOMAIN_ID -e PYTHONUNBUFFERED=1)

# The summary printed at the end, pass or fail: one row per in-image test
# run, then the steps reached. STEP is the step running now.
ROWS=()
STEP=0
STEPS=$(grep -cE '^step [0-9]+ ' "$SELF")

# step N TITLE: start step N
step() {
    STEP=$1
    echo "--- $1. $2"
}

# row NAME CODE PASSED TOTAL [NOTE]: one summary line (as in the Makefile)
row() {
    local status=PASS
    (($2)) && status=FAIL
    printf '  %-34s %s %5s/%-5s %s\n' "$1" "$status" "$3" "$4" "${5:-}"
}

# n_after KEY TEXT: "failures=2" -> 2 (0 if absent)
n_after() {
    local n
    n=$(grep -oE "$1=[0-9]+" <<<"$2" | grep -oE '[0-9]+$' | head -n 1 || true)
    echo "${n:-0}"
}

# notes N LABEL ...: "3 skipped, 1 failed" for the non-zero counts
notes() {
    local out=()
    while (($# >= 2)); do
        (($1 > 0)) && out+=("$1 $2")
        shift 2
    done
    local IFS=,
    sed 's/,/, /g' <<<"${out[*]}"
}

summary() {
    local code=$1 reached=$STEP
    ((code)) && reached=$((STEP - 1))
    echo
    echo "=================== summary: make smoke (Docker) ==================="
    ((${#ROWS[@]})) && printf '%s\n' "${ROWS[@]}"
    row "steps" "$code" "$reached" "$STEPS" "$( ((code)) && echo "failed in step $STEP")"
    ((code)) && echo "FAILED" || echo "ALL PASSED"
}

cleanup() {
    local code=$?
    # Save the logs first: removing the containers deletes them
    if [[ -n $LOG_DIR ]]; then
        mkdir -p "$LOG_DIR"
        for container in "${CONTAINERS[@]}"; do
            docker logs "$container" >"$LOG_DIR/$container.log" 2>&1 ||
                rm -f "$LOG_DIR/$container.log" # never started
        done
    fi
    docker rm -f "$GESTURE" "$ECHO" "$MISSION_ECHO" "$MONITOR" "$STOPPER" \
        "$FI_MISSION_ECHO" "$LIFECYCLE" >/dev/null 2>&1 || true
    $COMPOSE down >/dev/null 2>&1 || true
    rm -f "$SEAME_SECRETS_FILE"
    summary "$code"
}
trap cleanup EXIT

fail() {
    echo "FAIL: $*"
    for container in "${CONTAINERS[@]}"; do
        echo "--- last log lines of $container"
        docker logs "$container" 2>&1 | tail -n 15 || true
    done
    exit 1
}

# run_tests IMAGE PACKAGE: the package's tests inside its image, none skipped
run_tests() {
    local out code=0 ran end f e k
    out=$(docker run --rm -e REQUIRE_ROS_TESTS=1 -w "/ws/src/$2" "$1" \
        python3 -m unittest discover -s test 2>&1) || code=$?
    ran=$(grep -oE '^Ran [0-9]+' <<<"$out" | grep -oE '[0-9]+' || true)
    end=$(grep -E '^(OK|FAILED)' <<<"$out" | tail -n 1 || true)
    f=$(n_after failures "$end")
    e=$(n_after errors "$end")
    k=$(n_after skipped "$end")
    ROWS+=("$(row "$2 (in its image)" $((code || k)) $((${ran:-0} - f - e - k)) "${ran:-0}" \
        "$(notes "$k" "skipped (not allowed here)" "$f" failed "$e" errors)")")
    if ((code)); then
        echo "$out"
        fail "$2 tests failed inside $1"
    fi
    if grep -q "skipped" <<<"$out"; then
        echo "$out"
        fail "$2 tests were skipped inside $1"
    fi
    echo "    $2: $(grep -E '^Ran ' <<<"$out")"
}

# run_pytest IMAGE PACKAGE [PYTEST ARGS]: as run_tests, for a package whose
# tests are pytest functions (unittest discover would not see them)
run_pytest() {
    local image=$1 pkg=$2 out code=0 last p f e k
    shift 2
    out=$(docker run --rm -e REQUIRE_ROS_TESTS=1 -w "/ws/src/$pkg" "$image" \
        python3 -m pytest test -q -p no:cacheprovider "$@" 2>&1) || code=$?
    last=$(grep -E '^=*.*[0-9]+ (passed|failed|error)' <<<"$out" | tail -n 1 || true)
    p=$(grep -oE '[0-9]+ passed' <<<"$last" | grep -oE '[0-9]+' || true)
    f=$(grep -oE '[0-9]+ failed' <<<"$last" | grep -oE '[0-9]+' || true)
    e=$(grep -oE '[0-9]+ errors?' <<<"$last" | grep -oE '[0-9]+' || true)
    k=$(grep -oE '[0-9]+ skipped' <<<"$last" | grep -oE '[0-9]+' || true)
    p=${p:-0} f=${f:-0} e=${e:-0} k=${k:-0}
    ROWS+=("$(row "$pkg (in its image)" $((code || k)) "$p" $((p + f + e + k)) \
        "$(notes "$k" "skipped (not allowed here)" "$f" failed "$e" errors)")")
    if ((code)); then
        echo "$out"
        fail "$pkg tests failed inside $image"
    fi
    if ((k)); then
        echo "$out"
        fail "$pkg tests were skipped inside $image"
    fi
    echo "    $pkg: $last"
}

# wait_for CONTAINER TEXT SECONDS [SKIP_LINES]: TEXT appears in its log.
# Not grep -q: it exits at the first match, tail then dies of SIGPIPE, and
# pipefail turns the match into a failure.
wait_for() {
    local end=$((SECONDS + $3))
    while ((SECONDS < end)); do
        if docker logs "$1" 2>&1 | tail -n "+$((${4:-0} + 1))" | grep -F -- "$2" >/dev/null; then
            return 0
        fi
        sleep 0.5
    done
    return 1
}

# stop_and_check CONTAINER [CODES]: docker stop ends it quickly with one of
# CODES (default 0); 137 means it ignored SIGTERM and was killed after 10 s
stop_and_check() {
    local start=$SECONDS code
    docker stop "$1" >/dev/null
    code=$(docker inspect -f '{{.State.ExitCode}}' "$1")
    [[ " ${2:-0} " == *" $code "* ]] || fail "$1 exited with $code after docker stop"
    ((SECONDS - start < 8)) || fail "$1 took $((SECONDS - start)) s to stop"
    echo "    $1 stopped cleanly (exit code $code)"
}

step 1 "build gesture, intent, mission, echo, flight_interface"
$COMPOSE build

step 2 "package tests inside their images"
run_tests rosrangers/gesture:jazzy gesture_node
run_tests rosrangers/intent:jazzy intent_node
run_tests rosrangers/mission:jazzy mission_node
# Deselected: the cross-package check needs mission_node, which this image
# does not have. CI's ground-ros2 runs it with both packages installed.
run_pytest rosrangers/flight_interface:jazzy flight_interface \
    --deselect test/test_guard.py::test_masks_and_ceilings_match_mission_node

step 3 "gesture container -> intent container -> mission container"
$COMPOSE up -d intent mission flight
docker run -d --name "$ECHO" "${ROS[@]}" rosrangers/echo:jazzy \
    ros2 topic echo --field data /gesture/intent std_msgs/msg/String >/dev/null
docker run -d --name "$MISSION_ECHO" "${ROS[@]}" rosrangers/echo:jazzy \
    ros2 topic echo --field data /mission/state std_msgs/msg/String >/dev/null
docker run -d --name "$MONITOR" "${ROS[@]}" rosrangers/echo:jazzy >/dev/null
docker run -d --name "$GESTURE" "${ROS[@]}" rosrangers/gesture:jazzy \
    python3 /ws/src/gesture_node/test/fake_camera.py run >/dev/null
wait_for "$ECHO" '"intent":"MOVE_RIGHT"' 60 ||
    fail "no MOVE_RIGHT on /gesture/intent within 60 s"
echo "    L:INDEX_RIGHT+R:NONE from the gesture container became MOVE_RIGHT"
wait_for "$MISSION_ECHO" '"intent":"MOVE_RIGHT","reason":"ok"' 15 ||
    fail "mission did not see MOVE_RIGHT on /mission/state within 15 s"
! docker logs "$MISSION_ECHO" 2>&1 | grep -F '"mode":"ENGAGED"' >/dev/null ||
    fail "mission engaged without ENGAGE"
echo "    mission saw MOVE_RIGHT and stayed WAITING (no ENGAGE, no motion)"
wait_for "$MONITOR" "intent: MOVE_RIGHT ok L:INDEX_RIGHT+R:NONE" 15 ||
    fail "the echo image's monitor did not show MOVE_RIGHT within 15 s"
wait_for "$MONITOR" "mission: WAITING" 15 ||
    fail "the echo image's monitor did not show mission WAITING within 15 s"
echo "    the echo image's monitor shows intent MOVE_RIGHT and mission WAITING"

step 4 "stop the gesture container: NEUTRAL, mission stays WAITING"
seen=$(docker logs "$ECHO" 2>&1 | wc -l)
mission_seen=$(docker logs "$MISSION_ECHO" 2>&1 | wc -l)
stop_and_check "$GESTURE"
wait_for "$ECHO" '"intent":"NEUTRAL","gesture":null,"reason":"no_packets"' 15 "$seen" ||
    fail "no NEUTRAL (no_packets) within 15 s of stopping the gesture container"
echo "    intent fell back to NEUTRAL (no_packets)"
wait_for "$MISSION_ECHO" '"intent":"NEUTRAL","reason":"no_packets"' 15 "$mission_seen" ||
    fail "mission did not see NEUTRAL (no_packets) within 15 s of stopping the gesture container"
docker logs "$MISSION_ECHO" 2>&1 | grep -F '"reason":"no_packets"' | tail -n 1 |
    grep -F '"mode":"WAITING"' >/dev/null ||
    fail "mission left WAITING after the gesture container stopped"
! docker logs "$MISSION_ECHO" 2>&1 | grep -F '"land_request":true' >/dev/null ||
    fail "mission asked for a landing"
echo "    mission stayed WAITING, no land request (airborne, pretend flight state)"
wait_for "$MONITOR" "intent: NEUTRAL no_packets" 15 ||
    fail "the echo image's monitor did not show NEUTRAL no_packets within 15 s"
echo "    the monitor shows it too"

step 5 "stop the intent, mission, flight and monitor containers"
stop_and_check "$INTENT"
stop_and_check "$MISSION"
stop_and_check "$FLIGHT"
stop_and_check "$MONITOR" # monitor.py is ours, even though it runs in the echo image
# The ros2 topic echo probes: kill, not rm, so cleanup() still saves their logs
docker kill "$ECHO" "$MISSION_ECHO" >/dev/null 2>&1 || true # not checked: instruments

step 6 "node logs in $GROUND_LOG_DIR"
# check_log SERVICE TEXT: latest.log exists, every line is timestamped, TEXT is in it
check_log() {
    local log=$GROUND_LOG_DIR/$1/latest.log
    [[ -s $log ]] || fail "no $log"
    ! grep -vE "$STAMP" "$log" >/dev/null ||
        fail "lines without a timestamp in $log: $(grep -vE "$STAMP" "$log" | head -n 3)"
    grep -F -- "$2" "$log" >/dev/null || fail "\"$2\" not in $log"
    echo "    $log: $(wc -l <"$log") timestamped lines, \"$2\" among them"
}
check_log intent "Intent -> MOVE_RIGHT (reason: ok"
check_log mission "Intent -> NEUTRAL (reason: no_packets)"
check_log flight "PRETEND flight state"
# A pretend node that prints 5000 lines and "stopping" on SIGTERM. The
# entrypoint is PID 1: when PID 1 exits the kernel kills the logger, so it
# must wait for the logger. A plain `exec "$@"` keeps only the first few of
# these lines (checked on 2026-10-23: 11 of 5002).
docker run -d --name "$STOPPER" -e LOG_DIR=/logs -e LOG_NAME=stopper \
    -v "$PWD/$GROUND_LOG_DIR:/logs" rosrangers/mission:jazzy bash -c \
    'trap "seq 1 5000; echo stopping; exit 0" TERM; echo ready; while :; do sleep 0.05; done' \
    >/dev/null
wait_for "$STOPPER" ready 15 || fail "the stopper container never started"
stop_and_check "$STOPPER"
log=$GROUND_LOG_DIR/stopper/latest.log
[[ $(wc -l <"$log") -eq 5002 && $(tail -n 1 "$log") =~ $STAMP"stopping"$ ]] ||
    fail "$log lost lines printed during docker stop ($(wc -l <"$log") of 5002)"
echo "    all 5000 lines printed during docker stop, and the last one, are in the log file"

step 7 "flight_interface (loopback) -> /flight/state -> mission stays IDLE"
# lifecycle_cli SECONDS ARGS: ros2 lifecycle ARGS from a throwaway container,
# cut after SECONDS; its output, never a failure
lifecycle_cli() {
    local t=$1
    shift
    docker run --rm --name "$LIFECYCLE" "${ROS[@]}" rosrangers/flight_interface:jazzy \
        timeout "$t" ros2 lifecycle "$@" 2>&1 || true
}
# lifecycle TRANSITION STATE: ros2 lifecycle set until the node is in STATE
# (at most about 30 s). The state is read before each try, so a transition
# that worked but whose reply was lost (or cut by timeout) is not retried
# from the new state and reported as failed. Configure and activate only:
# never ARM.
lifecycle() {
    local end=$((SECONDS + 30)) left
    while ((left = end - SECONDS, left > 0)); do
        if lifecycle_cli $((left < 10 ? left : 10)) get /flight_interface |
            grep -E "^$2 \[[0-9]+\]" >/dev/null; then
            echo "    /flight_interface is $2 (ros2 lifecycle set $1)"
            return 0
        fi
        ((left = end - SECONDS, left > 0)) || break
        lifecycle_cli $((left < 20 ? left : 20)) set /flight_interface "$1" >/dev/null
        sleep 1
    done
    fail "/flight_interface did not reach $2 (ros2 lifecycle set $1) within 30 s"
}
# mission alone: flight (the pretend /flight/state) stopped in step 5, so
# flight_interface is the only /flight/state publisher here
$COMPOSE up -d mission flight_interface
docker run -d --name "$FI_MISSION_ECHO" "${ROS[@]}" rosrangers/echo:jazzy \
    ros2 topic echo --field data /mission/state std_msgs/msg/String >/dev/null
wait_for "$FLIGHT_INTERFACE" "unconfigured: use ros2 lifecycle set" 30 ||
    fail "flight_interface did not start within 30 s"
lifecycle configure inactive
lifecycle activate active
# not_streaming, not no_flight_state: mission got flight_interface's state
# (link_ok while active), and the loopback drone is not STREAMING
IDLE='"mode":"IDLE","mode_reason":"not_streaming"'
wait_for "$FI_MISSION_ECHO" "$IDLE" 15 ||
    fail "mission did not report IDLE (not_streaming) from flight_interface's /flight/state within 15 s"
# Held, not only reached: mission repeats its state at 2 Hz, so over HOLD
# seconds new IDLE lines keep coming, and no other mode ever shows
HOLD=5
seen=$(docker logs "$FI_MISSION_ECHO" 2>&1 | wc -l)
sleep "$HOLD"
wait_for "$FI_MISSION_ECHO" "$IDLE" 2 "$seen" ||
    fail "mission stopped reporting IDLE (not_streaming) during the ${HOLD} s hold"
! docker logs "$FI_MISSION_ECHO" 2>&1 | grep -E '"mode":"(WAITING|ENGAGED|LOCKED)"' >/dev/null ||
    fail "mission left IDLE with the loopback drone"
echo "    mission reads flight_interface's /flight/state and stays IDLE (not_streaming) for ${HOLD} s"
# Exactly one /flight/state publisher: flight_interface (flight stopped in
# step 5). Retried for discovery, which can show 0 at first; 2 or more fails.
pubs=
end=$((SECONDS + 20))
while ((SECONDS < end)); do
    pubs=$(docker run --rm --name "$LIFECYCLE" "${ROS[@]}" rosrangers/flight_interface:jazzy \
        timeout 10 ros2 topic info /flight/state 2>&1 |
        grep -oE '^Publisher count: [0-9]+' | grep -oE '[0-9]+$' || true)
    [[ $pubs == 1 ]] && break
    sleep 1
done
[[ $pubs == 1 ]] ||
    fail "/flight/state has ${pubs:-no} publisher(s), expected exactly 1 (flight_interface)"
echo "    /flight/state has exactly one publisher (ros2 topic info)"
stop_and_check "$FLIGHT_INTERFACE"
stop_and_check "$MISSION"
docker kill "$FI_MISSION_ECHO" >/dev/null 2>&1 || true # not checked: instrument
check_log flight_interface "active: setpoints go out from the next fresh target"
# docker stop deactivated it first: one last zero setpoint, then this line
check_log flight_interface "inactive: no more setpoints"

echo "PASS: images, in-image tests, gesture -> intent -> mission across containers," \
    "monitor, clean stops, node logs, flight_interface -> mission"
