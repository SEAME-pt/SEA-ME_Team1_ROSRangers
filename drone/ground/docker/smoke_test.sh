#!/bin/bash
# Docker smoke test for the ground images (CI job ground-docker, `make smoke`).
#
#   1. Build gesture, intent and echo.
#   2. Run each package's tests inside its own image, none skipped: the
#      image's own OpenCV, MediaPipe and ROS must work, not only CI's install.
#   3. Across containers: gesture_node's real main() with a fake camera
#      (src/gesture_node/test/fake_camera.py) in the gesture image publishes
#      INDEX_RIGHT -> intent container -> MOVE_RIGHT on /gesture/intent.
#   4. docker stop the gesture container: it exits 0 at once, and intent falls
#      back to HOVER_LAND_REQUEST (reason no_packets).
#   5. docker stop the intent and echo containers: exit 0 at once.
#
# Own compose project and ROS domain, so a running `make up` stack (or
# teammates on the network) is neither stopped nor mixed in.
# Run from anywhere:  drone/ground/docker/smoke_test.sh
set -euo pipefail

cd "$(dirname "$0")/.."
export ROS_DOMAIN_ID=86 # tests use 81-85: `grep -r TEST_DOMAIN_ID src ../tests`
PROJECT=ground-smoke
COMPOSE="docker compose -p $PROJECT -f docker-compose.yml"
INTENT=$PROJECT-intent-1
GESTURE=$PROJECT-gesture
ECHO=$PROJECT-echo
# Talk ROS with the intent service: host network and IPC, same domain
ROS=(--network host --ipc host -e ROS_DOMAIN_ID -e PYTHONUNBUFFERED=1)

cleanup() {
    docker rm -f "$GESTURE" "$ECHO" >/dev/null 2>&1 || true
    $COMPOSE down >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
    echo "FAIL: $*"
    for container in "$INTENT" "$GESTURE" "$ECHO"; do
        echo "--- last log lines of $container"
        docker logs "$container" 2>&1 | tail -n 15 || true
    done
    exit 1
}

# run_tests IMAGE PACKAGE: the package's tests inside its image, none skipped
run_tests() {
    local out
    if ! out=$(docker run --rm -e REQUIRE_ROS_TESTS=1 -w "/ws/src/$2" "$1" \
        python3 -m unittest discover -s test 2>&1); then
        echo "$out"
        fail "$2 tests failed inside $1"
    fi
    if grep -q "skipped" <<<"$out"; then
        echo "$out"
        fail "$2 tests were skipped inside $1"
    fi
    echo "    $2: $(grep -E '^Ran ' <<<"$out")"
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

echo "--- 1. build gesture, intent, echo"
$COMPOSE build

echo "--- 2. package tests inside their images"
run_tests rosrangers/gesture:jazzy gesture_node
run_tests rosrangers/intent:jazzy intent_node

echo "--- 3. gesture container -> intent container -> /gesture/intent"
$COMPOSE up -d intent
docker run -d --name "$ECHO" "${ROS[@]}" rosrangers/echo:jazzy \
    ros2 topic echo --field data /gesture/intent std_msgs/msg/String >/dev/null
docker run -d --name "$GESTURE" "${ROS[@]}" rosrangers/gesture:jazzy \
    python3 /ws/src/gesture_node/test/fake_camera.py run >/dev/null
wait_for "$ECHO" '"intent":"MOVE_RIGHT"' 60 ||
    fail "no MOVE_RIGHT on /gesture/intent within 60 s"
echo "    INDEX_RIGHT from the gesture container became MOVE_RIGHT"

echo "--- 4. stop the gesture container: intent falls back to HOVER_LAND_REQUEST"
seen=$(docker logs "$ECHO" 2>&1 | wc -l)
stop_and_check "$GESTURE"
wait_for "$ECHO" '"reason":"no_packets"' 15 "$seen" ||
    fail "no HOVER_LAND_REQUEST (no_packets) within 15 s of stopping the gesture container"
echo "    intent fell back to HOVER_LAND_REQUEST (no_packets)"

echo "--- 5. stop the intent and echo containers"
stop_and_check "$INTENT"
stop_and_check "$ECHO" "0 15" # the ros2 CLI exits with the signal number

echo "PASS: images, in-image tests, gesture -> intent across containers, clean stops"
