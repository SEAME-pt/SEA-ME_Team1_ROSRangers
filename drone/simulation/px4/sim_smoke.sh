#!/usr/bin/env bash
# PX4 SITL smoke test of the ground chain (#86). SIMULATION ONLY.
#
#   drone/simulation/px4/sim_smoke.sh up [mask] [gui]   start everything (mask 15 by default);
#                                                       gui also opens the Gazebo window
#   drone/simulation/px4/sim_smoke.sh gui     open the Gazebo window again (after closing it)
#   drone/simulation/px4/sim_smoke.sh check   is the chain alive? (topic rates, last /flight/state)
#   drone/simulation/px4/sim_smoke.sh shell   bash inside the container, ROS sourced
#   drone/simulation/px4/sim_smoke.sh logs    last lines of each log
#   drone/simulation/px4/sim_smoke.sh down    stop and remove the container
#
# Needs the image from drone/simulation/px4/Dockerfile (seame-px4-sim:jazzy-1.17).
# It runs in its own container (px4sim-smoke) with this repo mounted read-only.
# The ground packages are copied and built inside the container.
#
# Chain: /gesture/raw (scenario) -> intent_node -> mission_node ->
#        px4_offboard_bridge -> PX4 SITL (x500 in Gazebo).
# The bridge never arms: the scenario arms, takes off and switches to Offboard.
set -euo pipefail

IMAGE=${IMAGE:-seame-px4-sim:jazzy-1.17}
NAME=${NAME:-px4sim-smoke}
HERE=$(cd "$(dirname "$0")" && pwd)
REPO=$(git -C "$HERE" rev-parse --show-toplevel 2>/dev/null || (cd "$HERE/../../.." && pwd))
READY_TIMEOUT_SEC=${READY_TIMEOUT_SEC:-180}
ENV_SH='source /opt/ros/jazzy/setup.bash; source ~/ros2_px4_ws/install/setup.bash; [ -f /tmp/ws/install/setup.bash ] && source /tmp/ws/install/setup.bash'

in_box() { docker exec "$NAME" bash -lc "$ENV_SH; $1"; }
in_box_bg() { docker exec -d "$NAME" bash -lc "$ENV_SH; $1"; }
running() { [ "$(docker inspect -f '{{.State.Running}}' "$NAME" 2>/dev/null)" = true ]; }
usage() { sed -n '2,11p' "$0" | sed 's/^# \{0,1\}//'; }

gui() {
  running || { echo "$NAME is not running. Use: $0 up 15 gui"; exit 1; }
  in_box 'test -n "$DISPLAY"' || { echo "$NAME was started without gui. Use: $0 down, then $0 up 15 gui"; exit 1; }
  # The window is only a client: closing it leaves PX4 and Gazebo running.
  in_box_bg 'gz sim -g > /tmp/gz_gui.log 2>&1'
  echo "Gazebo window opening (software rendering: slow, low frame rate)"
}

up() {
  local mask=15 with_gui=""
  for arg in "$@"; do
    case "$arg" in
      8 | 11 | 15) mask=$arg ;;
      gui) with_gui=gui ;;
      *) echo "unknown argument '$arg': use a mask (8, 11 or 15) and/or gui" >&2; exit 2 ;;
    esac
  done
  if running; then
    echo "$NAME is already running. Use: $0 down"
    exit 1
  fi
  # Any failure from here leaves the container up for the logs: say how to clean up.
  trap 'echo "up failed. See: $0 logs, then: $0 down" >&2' ERR

  echo "1/5 Container $NAME ($IMAGE)"
  local display_args=()
  if [ "$with_gui" = gui ]; then
    [ -n "${DISPLAY:-}" ] || { echo "No DISPLAY: run it from the desktop session" >&2; exit 1; }
    # Only this user may draw on the screen (the container user has the same uid);
    # GNOME usually has this entry already.
    xhost +SI:localuser:"$(id -un)" >/dev/null
    display_args=(-e DISPLAY="$DISPLAY" -e LIBGL_ALWAYS_SOFTWARE=1 -v /tmp/.X11-unix:/tmp/.X11-unix)
    if [ -e /dev/dri ]; then display_args+=(--device /dev/dri); fi
  fi
  docker rm -f "$NAME" >/dev/null 2>&1 || true # a stopped leftover with the same name
  docker run -d --rm --name "$NAME" "${display_args[@]}" \
    -v "$REPO":/home/dev/repo:ro "$IMAGE" sleep infinity >/dev/null

  echo "2/5 Building intent_node, mission_node and px4_offboard_bridge"
  in_box 'mkdir -p /tmp/ws/src && cd /tmp/ws/src \
    && cp -r ~/repo/drone/ground/src/intent_node ~/repo/drone/ground/src/mission_node \
             ~/repo/drone/simulation/px4/ros/px4_offboard_bridge . \
    && cd /tmp/ws && { colcon build --packages-select intent_node mission_node px4_offboard_bridge \
       > /tmp/build.log 2>&1 || { tail -30 /tmp/build.log; exit 1; }; }'

  echo "3/5 Micro XRCE-DDS agent"
  in_box_bg 'MicroXRCEAgent udp4 -p 8888 > /tmp/agent.log 2>&1'

  echo "4/5 PX4 SITL + Gazebo server, headless (up to ${READY_TIMEOUT_SEC} s)"
  in_box_bg 'cd ~/PX4-Autopilot && tail -f /dev/null | HEADLESS=1 make px4_sitl gz_x500 > /tmp/px4.log 2>&1'
  # NAV_DLL_ACT 0: there is no ground station in the sim, and without it PX4 never
  # gets ready ("No connection to the GCS"). The PX4_PARAM_ start-up variable did not
  # apply on v1.17, and a set sent too early is lost while PX4 loads its parameters
  # (it still exits 0), so it is sent every second until PX4 is ready.
  # SITL only, never the real drone.
  local deadline=$((SECONDS + READY_TIMEOUT_SEC))
  until in_box 'grep -q "Ready for takeoff" /tmp/px4.log' 2>/dev/null; do
    if [ "$SECONDS" -ge "$deadline" ]; then
      echo "PX4 did not get ready in ${READY_TIMEOUT_SEC} s. Last lines of /tmp/px4.log:"
      in_box 'tail -20 /tmp/px4.log' || true
      false # runs the ERR trap
    fi
    in_box 'cd ~/PX4-Autopilot/build/px4_sitl_default && ./bin/px4-param set NAV_DLL_ACT 0' >/dev/null 2>&1 || true
    sleep 1
  done

  echo "5/5 Ground chain (mask $mask)"
  in_box_bg "ros2 launch px4_offboard_bridge sim_smoke.launch.py mask:=$mask > /tmp/ground.log 2>&1"
  sleep 5
  for node in intent_node mission_node px4_offboard_bridge; do
    if ! in_box "pgrep -f lib/$node/$node >/dev/null"; then
      echo "$node did not start. Last lines of /tmp/ground.log:"
      in_box 'tail -20 /tmp/ground.log' || true
      false # runs the ERR trap
    fi
  done
  trap - ERR
  if [ "$with_gui" = gui ]; then gui; fi
  echo "Ready. Next: $0 check"
}

check() {
  running || { echo "$NAME is not running. Use: $0 up"; exit 1; }
  for topic in /mission/velocity_target /flight/state /fmu/in/trajectory_setpoint /fmu/out/vehicle_status_v1; do
    printf '%-30s ' "$topic"
    in_box "timeout 6 ros2 topic hz $topic 2>/dev/null | grep -m1 'average rate'" || echo "no messages"
  done
  printf '%-30s ' "/flight/state (last)"
  in_box "timeout 10 ros2 topic echo --once --field data /flight/state 2>/dev/null | head -1" || echo "none"
}

logs() {
  running || { echo "$NAME is not running."; exit 1; }
  for log in build agent px4 ground gz_gui; do
    echo "== /tmp/$log.log"
    in_box "tail -15 /tmp/$log.log 2>/dev/null" || true
  done
}

down() {
  # The xhost entry is not removed: desktops such as Ubuntu's GNOME add the same
  # one by default, and removing it would stop the user's own windows opening.
  if docker rm -f "$NAME" >/dev/null 2>&1; then echo "Stopped $NAME"; else echo "$NAME was not running"; fi
}

case "${1:-}" in
  up) shift; up "$@" ;;
  gui) gui ;;
  check) check ;;
  shell) docker exec -it "$NAME" bash -c "$ENV_SH; exec bash" ;;
  logs) logs ;;
  down) down ;;
  help | -h | --help) usage ;;
  *) usage; exit 2 ;;
esac
