#!/bin/bash
# Source ROS 2 and the workspace (if this image built one), then run the command.
#
# LOG_DIR set (docker-compose.yml sets it): the command's stdout and stderr also
# go, each line prefixed with the wall-clock time, to
# $LOG_DIR/$LOG_NAME/<start time>.log, with latest.log pointing at it. One file
# per run, so two runs (before and after a code change) can be diffed.
set -e
source /opt/ros/jazzy/setup.bash
if [ -f /ws/install/setup.bash ]; then
    source /ws/install/setup.bash
fi
if [ -z "${LOG_DIR:-}" ]; then
    exec "$@"
fi

# Files the host user can delete (the container runs as root). LOG_DIR too:
# when the host folder is missing, Docker creates it as root with mode 755,
# and nothing in it could then be removed without sudo.
umask 000
mkdir -p "$LOG_DIR"
chmod 777 "$LOG_DIR" 2>/dev/null || true
dir=$LOG_DIR/${LOG_NAME:-$(hostname)}
name=$(date +%F_%H-%M-%S).log
mkdir -p "$dir"
ln -sfn "$name" "$dir/latest.log"

stamp() {
    local line t
    while IFS= read -r line || [ -n "$line" ]; do
        t=$EPOCHREALTIME
        printf '%(%F %T)T.%s %s\n' "${t%.*}" "${t#*.}" "$line"
    done
}

# Not `exec`: when PID 1 exits the kernel kills everything else in the
# container, and the logger loses what it has not written yet (with `exec`,
# 4991 of 5002 lines printed during docker stop were lost). So bash stays
# PID 1, forwards SIGTERM / SIGINT (docker stop, Ctrl+C) to the node, and
# exits with the node's code once the logger has written everything.
exec 3> >(stamp | tee -a "$dir/$name")
logger=$!
"$@" >&3 2>&3 &
node=$!
exec 3>&-
trap 'kill -TERM "$node" 2>/dev/null || true' TERM INT
set +e
wait "$node"
code=$?
while kill -0 "$node" 2>/dev/null; do # wait was cut short by the trap
    wait "$node"
    code=$?
done
wait "$logger"
exit "$code"
