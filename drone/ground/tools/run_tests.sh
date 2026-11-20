#!/usr/bin/env bash
# run_tests.sh GOAL: the test suites behind make test, unit, pkg-test, e2e-test
# and test-sitl (#186). Run from drone/ground/ (the Makefile does).
#
#   test       every suite: with ROS 2 Jazzy, colcon (one row per package) and
#              the coverage run (package tests + drone/tests/ros, 90 %, as CI's
#              ground-ros2); without it, the package tests that need no ROS
#              and a SKIP row for the ROS suites. Then sd_protocol_py, the C++
#              protocol library, px4_offboard_bridge, drone/tests/tools, the HIL
#              host part and pre-commit
#   unit       the suites that need no ROS (no pre-commit)
#   pkg        colcon test only            e2e   drone/tests/ros only
#   sitl       Betaflight SITL (drone/tests/sitl), make test-sitl
#
# Every suite runs even if one fails, each under SUITE_TIMEOUT. One summary
# row per suite (per package for colcon): PASS, FAIL, SKIP (a tool is
# missing; does not fail the run) or TIMEOUT. Full output in
# $TESTLOG/<suite>.log. Exit 1 if any row is FAIL or TIMEOUT.
#
# Settings (the Makefile passes them): ROS, WS, VENV, PKGS, TESTLOG, BUILD_DIR,
# BF_DIR, BF_VERSION, SUITE_TIMEOUT.
# Not -u: ROS 2's setup.bash reads unset variables
set -o pipefail

ROS=${ROS:-/opt/ros/jazzy/setup.bash}
WS=${WS:-ros2_ws}
VENV=${VENV:-gesture-venv}
PKGS=${PKGS:-gesture_node intent_node mission_node flight_interface ground_bringup}
TESTLOG=${TESTLOG:-logs/test}
BUILD_DIR=${BUILD_DIR:-build}
BF_DIR=${BF_DIR:-$BUILD_DIR/betaflight}
BF_VERSION=${BF_VERSION:-2026.6.2}
SUITE_TIMEOUT=${SUITE_TIMEOUT:-20m}

GROUND=$(pwd)
REPO=$(cd ../.. && pwd)
E2E=$REPO/drone/tests/ros
PY=$GROUND/$VENV/bin/python
# Each pytest-cov suite keeps its data file here, not in the repo
export COVERAGE_FILE=$GROUND/$TESTLOG/.coverage
# No .pytest_cache folders scattered in the source tree
PYTEST=(-m pytest -p no:cacheprovider)

# ---------- rows ----------

# row NAME STATUS PASSED TOTAL [NOTE]: one summary line, appended to $ROWS
row() {
    printf '  %-28s %-7s %5s/%-5s %s\n' "$1" "$2" "$3" "$4" "${5:-}" >>"$ROWS"
}
skip_row() { row "$1" SKIP - - "$2"; }

# "3 failed" -> 3 (0 if absent)
n_before() {
    local n
    n=$(grep -oE "[0-9]+ $1" <<<"$2" | grep -oE '^[0-9]+' | head -n 1)
    echo "${n:-0}"
}
# notes N LABEL ...: "3 skipped, 1 failed" (non-zero counts only)
notes() {
    local out=()
    while (($# >= 2)); do
        (($1 > 0)) && out+=("$1 $2")
        shift 2
    done
    local IFS=,
    sed 's/,/, /g' <<<"${out[*]}"
}

# pytest_row NAME CODE LOG [NOTE]: a row from pytest's last summary line
pytest_row() {
    local last p f e k status=PASS
    last=$(grep -E '[0-9]+ (passed|failed|errors?|skipped)' "$3" | tail -n 1)
    p=$(n_before passed "$last")
    f=$(n_before failed "$last")
    e=$(n_before 'errors?' "$last")
    k=$(n_before skipped "$last")
    (($2)) && status=FAIL
    local n
    n=$(notes "$k" skipped "$f" failed "$e" errors)
    [[ -n ${4:-} ]] && n=${n:+$n, }$4
    row "$1" "$status" "$p" $((p + f + e + k)) "$n"
}

# The coverage total pytest-cov printed ("TOTAL ... 93%"), or ?
cov_total() {
    local pct
    pct=$(grep -E '^TOTAL ' "$1" | tail -n 1 | grep -oE '[0-9.]+%$')
    echo "${pct:-?}"
}

# run LOG CMD...: CMD's output to the terminal and LOG; returns CMD's code
run() {
    local log=$1
    shift
    "$@" 2>&1 | tee -a "$log"
    return "${PIPESTATUS[0]}"
}

have_ros() { [[ -f $ROS ]]; }
need_venv() {
    [[ -x $PY ]] || {
        echo "run_tests.sh: $PY not found (make test-deps makes it)" >&2
        return 1
    }
}

# ---------- suites ----------

# colcon test in the workspace, one row per package (as CI's first step)
suite_colcon() {
    local log=$LOG code line t e f k p status failed=0
    (
        # shellcheck disable=SC1090
        source "$ROS" && source "$WS/install/setup.bash" && source "$VENV/bin/activate" &&
            cd "$WS" && { colcon test-result --delete-yes >/dev/null || true; } &&
            REQUIRE_ROS_TESTS=1 python3 -m colcon test --packages-select $PKGS \
                --event-handlers console_direct+ &&
            colcon test-result --verbose
    ) 2>&1 | tee -a "$log"
    code=${PIPESTATUS[0]}
    for p in $PKGS; do
        # shellcheck disable=SC1090
        line=$(cd "$WS" && source "$ROS" && colcon test-result --all 2>/dev/null | grep "^build/$p/")
        if [[ -z $line ]]; then
            row "$p" FAIL 0 '?' 'no results (crash or not built)'
            continue
        fi
        t=$(n_before tests "$line")
        e=$(n_before errors "$line")
        f=$(n_before failures "$line")
        k=$(n_before skipped "$line")
        status=PASS
        ((e + f)) && status=FAIL && failed=1
        row "$p" "$status" $((t - e - f - k)) "$t" "$(notes "$k" skipped "$f" failed "$e" errors)"
    done
    # colcon failed but no package shows it (a build or setup error)
    ((code)) && ! ((failed)) && row colcon FAIL - - "exit $code, see $log"
}

# CI's coverage gate: every package's test/ plus drone/tests/ros in one pytest
# run against the installed packages, 90 % combined. Rows: drone/tests/ros
# (from the JUnit report) and the gate itself.
suite_coverage() {
    local log=$LOG xml=$TESTLOG/coverage.xml code dirs dir pkg
    local paths=() cov=()
    dirs=$(tools/pkg_dirs.sh src $PKGS) || {
        row coverage FAIL 0 '?' 'package folders not found (tools/pkg_dirs.sh)'
        return
    }
    for dir in $dirs; do
        paths+=("$GROUND/$dir/test")
        pkg=${dir##*/}
        [[ -d $dir/$pkg ]] && cov+=("--cov=$pkg")
    done
    (
        # shellcheck disable=SC1090
        source "$ROS" && source "$WS/install/setup.bash" && source "$VENV/bin/activate" &&
            cd "$REPO" && REQUIRE_ROS_TESTS=1 python3 "${PYTEST[@]}" "${paths[@]}" "$E2E" \
            "${cov[@]}" --cov-report=term-missing --cov-fail-under=90 \
            --junitxml="$GROUND/$xml"
    ) 2>&1 | tee -a "$log"
    code=${PIPESTATUS[0]}
    e2e_row_from_junit "$xml"
    local status=PASS note
    note="total $(cov_total "$log")"
    if ((code)); then
        status=FAIL
        grep -q 'Required test coverage of 90% reached' "$log" || note+=', below 90%'
        grep -qE '[0-9]+ (failed|errors?)' <(grep -E '[0-9]+ passed' "$log" | tail -n 1) &&
            note+=', tests failed'
    fi
    row "coverage (>=90%)" "$status" - - "$note"
}

# The drone/tests/ros row from a JUnit report: its test cases only
e2e_row_from_junit() {
    local counts
    counts=$("$PY" - "$1" <<'PY'
import sys
import xml.etree.ElementTree as ET
try:
    root = ET.parse(sys.argv[1]).getroot()
except (OSError, ET.ParseError):
    print("0 0 0 0")
    sys.exit()
p = f = k = 0
for case in root.iter("testcase"):
    # classname: the module path from pytest's rootdir, e.g. tests.ros.test_x
    if "tests/ros/" not in (case.get("classname") or "").replace(".", "/") + "/":
        continue
    if case.find("failure") is not None or case.find("error") is not None:
        f += 1
    elif case.find("skipped") is not None:
        k += 1
    else:
        p += 1
print(p, f, k, p + f + k)
PY
)
    local p f k t status=PASS
    read -r p f k t <<<"$counts"
    ((t == 0)) && { row drone/tests/ros FAIL 0 0 'no results'; return; }
    ((f)) && status=FAIL
    row drone/tests/ros "$status" "$p" "$t" "$(notes "$k" skipped "$f" 'failed or errors')"
}

# drone/tests/ros alone (make e2e-test)
suite_e2e() {
    local log=$LOG code
    (
        # shellcheck disable=SC1090
        source "$ROS" && source "$WS/install/setup.bash" && source "$VENV/bin/activate" &&
            cd "$REPO" && REQUIRE_ROS_TESTS=1 python3 "${PYTEST[@]}" "$E2E" -v
    ) 2>&1 | tee -a "$log"
    code=${PIPESTATUS[0]}
    pytest_row drone/tests/ros "$code" "$log"
}

# Without ROS: each package's tests with pytest (the ROS ones skip), and
# drone/tests/ros. One row each. pytest, not unittest: most of
# flight_interface's tests are plain pytest functions, which unittest
# discover never finds. Run from the package folder (python -m puts it on
# sys.path), so the package imports from the source tree.
suite_nonros_pkgs() {
    local log=$LOG part=$TESTLOG/unit.part dirs dir c name
    dirs=$(tools/pkg_dirs.sh src $PKGS) || {
        row packages FAIL 0 '?' 'package folders not found (tools/pkg_dirs.sh)'
        return
    }
    for dir in $dirs "$E2E"; do
        if [[ $dir == "$E2E" ]]; then
            "$PY" "${PYTEST[@]}" "$dir" 2>&1 | tee "$part"
        else
            (cd "$dir" && "$PY" "${PYTEST[@]}" test) 2>&1 | tee "$part"
        fi
        c=${PIPESTATUS[0]}
        cat "$part" >>"$log"
        name=${dir##*/}
        [[ $dir == "$E2E" ]] && name=drone/tests/ros
        pytest_row "$name" "$c" "$part"
    done
    rm -f "$part"
}

suite_sd_protocol() {
    local log=$LOG code
    run "$log" "$PY" "${PYTEST[@]}" src/sd_protocol_py --cov=sd_protocol \
        --cov-report=term --cov-fail-under=80
    code=$?
    pytest_row sd_protocol_py "$code" "$log" "coverage $(cov_total "$log") (>=80%)"
}

# CI's protocol-cpp without the gcovr gate (CI keeps it). nlohmann-json is
# downloaded on the first configure when the system has none (CMakeLists.txt).
suite_cpp() {
    local log=$LOG code b=$BUILD_DIR/protocol passed failed
    command -v cmake >/dev/null || { skip_row protocol-cpp 'cmake not found'; return; }
    run "$log" cmake -S "$REPO/drone/lib/protocol" -B "$b" -DCMAKE_BUILD_TYPE=Debug &&
        run "$log" cmake --build "$b" -j "$(nproc)" &&
        run "$log" ctest --test-dir "$b" -V
    code=$?
    passed=$(grep -oE '\[  PASSED  \] [0-9]+' "$log" | tail -n 1 | grep -oE '[0-9]+$')
    failed=$(grep -oE '\[  FAILED  \] [0-9]+ tests?,' "$log" | tail -n 1 | grep -oE '[0-9]+')
    passed=${passed:-0}
    failed=${failed:-0}
    local status=PASS note
    ((code)) && status=FAIL
    note=$(notes "$failed" failed)
    ((code)) && ((passed + failed == 0)) && note='build failed'
    row protocol-cpp "$status" "$passed" $((passed + failed)) "${note:+$note, }no coverage gate (CI: 80%)"
}

suite_bridge() {
    local log=$LOG code
    (cd src/simulation/px4_offboard_bridge && "$PY" "${PYTEST[@]}" test \
        --cov=px4_offboard_bridge.bridge_logic --cov=px4_offboard_bridge.smoke_checks \
        --cov-report=term --cov-fail-under=90) 2>&1 | tee -a "$log"
    code=${PIPESTATUS[0]}
    pytest_row px4_offboard_bridge "$code" "$log" "coverage $(cov_total "$log") (>=90%)"
}

suite_scripts() {
    local log=$LOG code
    run "$log" "$PY" "${PYTEST[@]}" "$REPO/drone/tests/tools"
    code=$?
    pytest_row drone/tests/tools "$code" "$log"
}

# drone/tests/hil without the bench (as CI's hil-unit)
suite_hil() {
    local log=$LOG code
    # -p no:launch_testing: with ROS sourced, that plugin's collect hook
    # overrides --ignore and collects gateway/wifi; these aren't launch tests
    run "$log" "$PY" "${PYTEST[@]}" -p no:launch_testing "$REPO/drone/tests/hil" \
        --ignore="$REPO/drone/tests/hil/gateway/wifi"
    code=$?
    pytest_row 'hil (host part)' "$code" "$log"
}

# Fixes files in place (end-of-file, whitespace): run it again after a FAIL
suite_precommit() {
    local log=$LOG code p f k note=
    command -v pre-commit >/dev/null || { skip_row pre-commit 'pre-commit not found'; return; }
    (cd "$REPO" && pre-commit run --all-files --color never) 2>&1 | tee -a "$log"
    code=${PIPESTATUS[0]}
    p=$(grep -cE '\.Passed$' "$log")
    f=$(grep -cE '\.Failed$' "$log")
    k=$(grep -cE '\)Skipped$|\.Skipped$' "$log")
    grep -q 'files were modified by this hook' "$log" && note='files modified, run again'
    local status=PASS
    ((code)) && status=FAIL
    note=$(notes "$f" failed "$k" skipped)${note:+, $note}
    row pre-commit "$status" "$p" $((p + f + k)) "${note#, }"
}

# Betaflight SITL + drone/tests/sitl (as CI's betaflight-sitl). The first run
# clones $BF_VERSION into $BF_DIR and builds the SITL target; later runs reuse it.
suite_sitl() {
    local log=$LOG sitllog=$GROUND/$TESTLOG/betaflight-sitl.log elf code boot pid
    elf=$BF_DIR/obj/main/betaflight_SITL.elf
    for t in git make gcc; do
        command -v "$t" >/dev/null || { skip_row betaflight-sitl "$t not found"; return; }
    done
    # Something already on 5760/5761 (another SITL?) would answer the tests
    if "$PY" -c 'import socket, sys
for port in (5760, 5761):
    s = socket.socket()
    s.settimeout(0.5)
    if s.connect_ex(("127.0.0.1", port)) == 0:
        sys.exit(f"TCP {port} is in use")
sys.exit(0)' 2>>"$log"; then
        :
    else
        row betaflight-sitl FAIL 0 '?' 'TCP 5760/5761 in use (another SITL?)'
        return
    fi
    if [[ ! -x $elf ]]; then
        if [[ ! -d $BF_DIR/.git ]]; then
            run "$log" git clone --depth 1 --branch "$BF_VERSION" \
                https://github.com/betaflight/betaflight.git "$BF_DIR" || {
                row betaflight-sitl FAIL 0 '?' "clone of $BF_VERSION failed"
                return
            }
        fi
        # Submodules first: make -j fetches them in parallel and the jobs race
        # for .git/config (as in CI)
        run "$log" git -C "$BF_DIR" submodule update --init --recursive --depth 1 &&
            mkdir -p "$BUILD_DIR/bf-shim" &&
            printf '#!/bin/sh\n[ "$1" = "-dumpversion" ] && echo 13.3.1 && exit 0\nexit 1\n' \
                >"$BUILD_DIR/bf-shim/arm-none-eabi-gcc" &&
            chmod +x "$BUILD_DIR/bf-shim/arm-none-eabi-gcc" &&
            PATH="$GROUND/$BUILD_DIR/bf-shim:$PATH" run "$log" make -C "$BF_DIR" TARGET=SITL -j"$(nproc)" || {
            row betaflight-sitl FAIL 0 '?' 'SITL build failed'
            return
        }
    fi
    local version
    version=$(git -C "$BF_DIR" describe --tags 2>/dev/null || echo '?')
    [[ $version == "$BF_VERSION" ]] || echo "run_tests.sh: $BF_DIR is $version, CI uses $BF_VERSION" | tee -a "$log"
    # Known SITL race (betaflight#15840, #181): boot again when it does not
    # answer MSP within 15 s, at most 3 boots (as in CI)
    pid=
    for boot in 1 2 3; do
        (cd "$BF_DIR" && exec stdbuf -oL -eL ./obj/main/betaflight_SITL.elf) >"$sitllog" 2>&1 &
        pid=$!
        if (cd "$REPO" && "$PY" - <<'PY') 2>&1 | tee -a "$log"; then
import sys
from collections import Counter
sys.path.insert(0, "drone/tests/sitl")
from conftest import connect_when_ready
tries = Counter()
link = connect_when_ready(tries, timeout_s=15)
if link is None:
    print(f"no MSP answer within 15 s: {dict(tries)}")
    sys.exit(1)
link.sock.close()
PY
            break
        fi
        echo "run_tests.sh: SITL boot $boot never answered MSP on 5761 (known race, #181)" | tee -a "$log"
        stop_sitl "$pid"
        pid=
    done
    if [[ -z $pid ]]; then
        row betaflight-sitl FAIL 0 '?' "SITL never answered after 3 boots, see $sitllog"
        return
    fi
    (cd "$REPO" && SITL_REQUIRED=1 "$PY" "${PYTEST[@]}" drone/tests/sitl -v) 2>&1 | tee -a "$log"
    code=${PIPESTATUS[0]}
    stop_sitl "$pid"
    pytest_row betaflight-sitl "$code" "$log" "Betaflight $version"
}

stop_sitl() {
    kill "$1" 2>/dev/null
    for _ in $(seq 20); do kill -0 "$1" 2>/dev/null || return 0; sleep 0.25; done
    kill -9 "$1" 2>/dev/null
    return 0
}

# ---------- driver ----------

# kill_tree PID: PID and every process below it
kill_tree() {
    local c
    for c in $(pgrep -P "$1"); do kill_tree "$c"; done
    kill -TERM "$1" 2>/dev/null
}

# One suite in a child of this script, so `timeout` can stop it. The suite
# runs in the background and is waited for: bash runs the TERM trap only
# between commands, and a background job's whole tree can then be stopped.
if [[ ${1:-} == --suite ]]; then
    ROWS=$3
    LOG=$TESTLOG/$4.log
    : >"$LOG"
    "suite_$2" &
    child=$!
    trap 'kill_tree "$child"; exit 124' TERM INT
    wait "$child"
    exit 0
fi

goal=${1:-test}
case $goal in
    test)
        if have_ros; then suites=(colcon coverage); else suites=(nonros_pkgs); fi
        suites+=(sd_protocol cpp bridge scripts hil precommit)
        ;;
    unit) suites=(nonros_pkgs sd_protocol cpp bridge scripts hil) ;;
    pkg) suites=(colcon) ;;
    e2e) suites=(e2e) ;;
    sitl) suites=(sitl) ;;
    *)
        echo "usage: $0 test|unit|pkg|e2e|sitl" >&2
        exit 2
        ;;
esac

mkdir -p "$TESTLOG"
need_venv || exit 1
ROWS=$TESTLOG/$goal.rows
: >"$ROWS"
# Log name of each suite: $TESTLOG/<name>.log
declare -A LOGNAME=([colcon]=colcon [coverage]=coverage [e2e]=e2e [nonros_pkgs]=unit
    [sd_protocol]=sd_protocol [cpp]=protocol-cpp [bridge]=px4_offboard_bridge
    [scripts]=scripts [hil]=hil-host [precommit]=pre-commit [sitl]=sitl)

if [[ $goal == test ]] && ! have_ros; then
    skip_row 'colcon + coverage' "ROS 2 Jazzy not found ($ROS)"
fi

for s in "${suites[@]}"; do
    echo
    echo "=================== suite: ${LOGNAME[$s]} ==================="
    timeout --foreground -k 30s "$SUITE_TIMEOUT" "$0" --suite "$s" "$ROWS" "${LOGNAME[$s]}"
    c=$?
    ((c == 124 || c == 137)) && row "${LOGNAME[$s]}" TIMEOUT - - "stopped after $SUITE_TIMEOUT, see $TESTLOG/${LOGNAME[$s]}.log"
done

echo
declare -A TARGET=([test]=test [unit]=unit [pkg]=pkg-test [e2e]=e2e-test [sitl]=test-sitl)
echo "=================== summary: make ${TARGET[$goal]} ==================="
cat "$ROWS"
echo "  logs: $TESTLOG/"
if grep -qE ' (FAIL|TIMEOUT) ' "$ROWS"; then
    echo FAILED
    exit 1
fi
echo "ALL PASSED"
