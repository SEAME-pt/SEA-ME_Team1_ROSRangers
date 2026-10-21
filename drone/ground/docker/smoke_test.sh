#!/bin/bash
# Docker smoke test: builds all images, runs the intent container, feeds it
# INDEX_RIGHT on /gesture/raw and waits for MOVE_RIGHT on /gesture/intent.
# The gesture image is built but not run (no webcam in CI).
#
# Run from anywhere:  drone/ground/docker/smoke_test.sh
set -euo pipefail

cd "$(dirname "$0")/.."
COMPOSE="docker compose -f docker-compose.yml"
PUB=gesture-smoke-pub
TIMEOUT_SEC=60

cleanup() {
    docker rm -f "$PUB" >/dev/null 2>&1 || true
    $COMPOSE down >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "--- build gesture, intent, echo"
$COMPOSE build

echo "--- start intent"
$COMPOSE up -d intent

echo "--- publish INDEX_RIGHT on /gesture/raw at 10 Hz"
$COMPOSE run -d --rm --name "$PUB" echo \
    ros2 topic pub -r 10 /gesture/raw std_msgs/msg/String \
    '{data: "{\"gesture\": \"INDEX_RIGHT\", \"t\": 1.0, \"xy\": null}"}'

echo "--- wait for MOVE_RIGHT on /gesture/intent (up to ${TIMEOUT_SEC} s)"
end=$((SECONDS + TIMEOUT_SEC))
while ((SECONDS < end)); do
    line=$($COMPOSE run --rm echo \
        timeout 10 ros2 topic echo --once --field data /gesture/intent std_msgs/msg/String \
        2>/dev/null || true)
    echo "    ${line:-<nothing yet>}"
    if [[ "$line" == *MOVE_RIGHT* ]]; then  # however echo quotes the JSON string
        echo "PASS: intent container turned INDEX_RIGHT into MOVE_RIGHT"
        exit 0
    fi
done

echo "FAIL: no MOVE_RIGHT within ${TIMEOUT_SEC} s"
$COMPOSE logs intent
exit 1
