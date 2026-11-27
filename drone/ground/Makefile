# Ground nodes: native venv + colcon workspace, and the Docker containers.
#
#   make          venv + ros2_ws (only if ROS 2 Jazzy is installed), then the
#                 demo mode (env/demo.env): gesture, intent, echo, mission,
#                 fake_flight_state (pretend /flight/state). echo's live table in
#                 this terminal, each node's output in logs/<service>/latest.log
#   make down     stop the demo and the simulation (never flight_interface)
#   make clean    down + remove this project's Docker images
#   make fclean   clean + remove the venv, the workspace, build/ (C++ library,
#                 Betaflight SITL), logs/, Python caches and the rest of this
#                 project's Docker objects (make smoke's too), leaving
#                 drone/ground/ as it was cloned. The rest of Docker on this PC
#                 is not touched
#   make re       fclean, then all
#   make test     every suite, each runs even if one fails (tools/run_tests.sh):
#                 colcon test of every ground package, the coverage gate (package
#                 tests + drone/tests/ros, 90 %), sd_protocol_py, the C++ protocol
#                 library (ctest, no coverage gate), sim_smoke's checks,
#                 drone/tests/tools, the HIL host part, pre-commit --all-files (fixes
#                 files in place). Without Jazzy the ROS suites show SKIP and the
#                 package tests run without ROS. A missing tool shows SKIP
#   make unit     the suites that need no ROS (no pre-commit)
#   make pkg-test / e2e-test   colcon test only / drone/tests/ros only
#   make test-sitl Betaflight SITL + drone/tests/sitl. The first run clones
#                 Betaflight (BF_VERSION, as CI) into build/betaflight, builds it
#                 (network, a few minutes); BF_DIR=<checkout> uses another one
#   make smoke    Docker smoke test: builds the images, runs the package tests
#                 inside them, gesture -> intent -> mission across containers,
#                 the echo monitor, clean stops, node logs (in logs/smoke)
#   make psk-file copy the PSK from ~/.config/seame-drone/secrets.json into
#                 psk.json next to it (mode 600), the only secret the
#                 flight_interface container mounts (ADR-025 E3.2)
#
# REAL MODE: the drone (ADR-025). Safety Rules first: props off on the bench,
# the safety pilot holds the radio.
#   make real       checks (env/real.env, the PSK file, the ESP answers ping),
#                   then gesture, intent, mission and flight_interface (backend
#                   esp, UNCONFIGURED), prints the operator steps, shows the live
#                   table. It never configures, activates or ARMs (REQ-SAF-023)
#   make real-dry   the same with flight_interface on the loopback backend: no
#                   drone, no PSK. To rehearse the operator steps at a desk
#   make real-steps print the operator steps again
#   make real-down  asks first (it cuts the drone's link: the ESP's failsafe
#                   takes over), then stops flight_interface, the chain and the
#                   live table
#
# Modes: one compose profile and env file each (env/<mode>.env: profile,
# parameter file, ROS domain, ADR-025 E1.3). Git ignores env/: copy each
# template from env.example/ and fill in the values the safety pilot gives you
# (tools/env_check.sh checks them before a mode starts):
#   mkdir -p env && cp env.example/demo.env.example env/demo.env
#
# make, make up, make sim and make sim-smoke refuse to start while a
# flight_interface container is running: each starts its own /flight/state
# publisher (fake_flight_state or flight_interface_sim), and /flight/state
# must have one. make down, sim-down and clean refuse too: they would stop the
# chain (gesture, intent, mission) that flight_interface flies with.
#
# SIMULATION ONLY (#86, #199): PX4 SITL + Gazebo instead of the pretend
# /flight/state. The first run builds the PX4 image (long, several GB) and
# the sim stage of flight_interface's image (px4_msgs, a few minutes); later
# runs reuse them.
#   make sim        PX4 SITL + Gazebo (headless) + XRCE agent, flight_interface
#                   (backend px4), the controller, gesture (camera), intent,
#                   mission, echo. The px4 container configures and activates
#                   flight_interface, arms PX4, takes off to 2.0 m and switches
#                   to Offboard by itself; the controller then holds zero
#                   velocity. ENGAGE (both open hands) and fly. Never
#                   fake_flight_state (flight_interface publishes /flight/state)
#   make sim-smoke  headless test, no camera: scripted gestures (ENGAGE, right,
#                   climb, intent_node killed) and the checks of #86 / #199,
#                   then stops. Rosbag + summary.md in logs/sim-smoke/<start time>/
#   make sim-hold   the SITL hold test (REQ-NAV-001, ADR-025 F26): headless,
#                   descend to 1.0 m by gesture, hold 60 s, judged on Gazebo's
#                   ground truth. RUNS=3 (default) runs it 3 times and passes
#                   only if every run passes; RUNS=1 for one. Rosbag,
#                   truth.csv and summary.md in logs/sim-hold/<start time>/
#   make sim-down   stop the simulation
#   make px4-image  rebuild the PX4 image (after a change to its Dockerfile)
#   The mask (axes the ground owns) is config/sim.yaml's (15; ADR-025 F24):
#   edit it there. sim-smoke flies right and up only, so it needs 11 or 15.
#   Options: GUI=0 (no Gazebo window, make sim only; sim-smoke is always
#   headless). Logs: logs/px4/ (sim start-up), logs/px4-sitl/ (PX4, agent),
#   logs/flight_interface_sim/, logs/<node>/ as above.
#
# Every test target ends with a summary (one row per suite, per package for
# colcon: PASS, FAIL, SKIP or TIMEOUT) and keeps each suite's full output in
# logs/test/<suite>.log. Each suite is stopped after SUITE_TIMEOUT (20m).
#
# The native workspace follows docs/PraticalPlan/06 Gestures/00 Gesture Pipeline.md
# (Step 0), but lives here instead of ~/ros2_ws and ~/gesture-venv:
#   gesture_node is built with the venv's Python (MediaPipe, OpenCV),
#   intent_node and mission_node with the system Python.

SHELL   := /bin/bash
COMPOSE := docker compose -f docker-compose.yml
ROS     := /opt/ros/jazzy/setup.bash

VENV    := gesture-venv
WS      := ros2_ws
PKGS    := gesture_node intent_node mission_node controller_node flight_interface ground_bringup
# Each package's folder (src/<component>/<package>), found by tools/pkg_dirs.sh: one
# folder per package or it fails (#186). Looked up in the rules that use it, so
# a missing package never breaks make down or make clean.
FIND_PKG_DIRS := tools/pkg_dirs.sh src $(PKGS)
E2E     := ../tests/ros
TESTLOG := logs/test
# Build output outside colcon: the C++ protocol library, Betaflight SITL.
# Git ignores it (build*/); make fclean removes it.
BUILD   := build
BF_VERSION := 2026.6.2
BF_DIR  ?= $(BUILD)/betaflight
# Each test suite is stopped after this (CI's ground-ros2 limit)
SUITE_TIMEOUT ?= 20m
RUN_TESTS = ROS=$(ROS) WS=$(WS) VENV=$(VENV) PKGS="$(PKGS)" TESTLOG=$(TESTLOG) \
	BUILD_DIR=$(BUILD) BF_DIR=$(BF_DIR) BF_VERSION=$(BF_VERSION) \
	SUITE_TIMEOUT=$(SUITE_TIMEOUT) tools/run_tests.sh

# Simulation (#86). The PX4 image (stage px4 of src/simulation/Dockerfile) is built
# with plain docker build, not by compose, so make clean and fclean keep it:
# rebuilding it takes a long time. Remove it by hand: docker rmi $(PX4_IMAGE)
PX4_IMAGE    := seame-px4-sim:jazzy-1.17
# Every profile but real: the rules that stop containers. Never real, so they
# never touch flight_interface (no-flight-interface also refuses while it runs).
# Stopping needs no env file: stand-in values, which only satisfy compose's
# check (a stop starts nothing, so no domain is used).
STOP_PROFILES := --profile demo --profile sim --profile sim-smoke --profile sim-hold
STOP_COMPOSE  := GROUND_MODE=demo GROUND_DOMAIN_ID=0 GROUND_DISCOVERY_RANGE=LOCALHOST \
	$(COMPOSE) $(STOP_PROFILES)
DEMO_COMPOSE  := $(COMPOSE) --env-file env/demo.env
SIM_COMPOSE   := $(COMPOSE) --env-file env/sim.env
SIM          := px4 flight_interface_sim controller gesture intent mission echo
SIM_SMOKE    := px4 flight_interface_sim controller mission sim_smoke
SIM_HOLD     := px4 flight_interface_sim controller mission sim_hold
RUNS         ?= 3
GUI          ?= 1
# Host group of the GPU render node, so the px4 and gesture containers (not
# root) can use it. No render node: video, a harmless group both images have.
RENDER_GID   := $(shell stat -c %g /dev/dri/renderD128 2>/dev/null || echo video)

# The ground images' user `ros` gets the host user's uid and gid (docker/base/),
# so the files it writes to logs/ are the host user's and it can read
# psk.json (mode 600). Root on the host: 1000, the image's default.
GROUND_UID := $(shell u=$$(id -u); [ "$$u" = 0 ] && echo 1000 || echo "$$u")
GROUND_GID := $(shell g=$$(id -g); [ "$$g" = 0 ] && echo 1000 || echo "$$g")
# compose reads them from the environment of every rule (gesture: RENDER_GID too)
export GROUND_UID GROUND_GID RENDER_GID
# The sim's domain and subnet discovery (ADR-025 E1.2) come from env/sim.env
SIM_ENV      := GUI=$(GUI) RENDER_GID=$(RENDER_GID) PX4_IMAGE=$(PX4_IMAGE)
SMOKE_ENV    := GUI=0 RENDER_GID=$(RENDER_GID) PX4_IMAGE=$(PX4_IMAGE)

# Compose projects: ground (make, make up: named after this folder) and
# ground-smoke (make smoke). Compose labels their containers, images and
# networks with the name, so fclean removes only those.
DOCKER_PROJECTS := ground ground-smoke

# The PSK (ADR-002). SECRETS holds it with the Wi-Fi and OTA passwords;
# PSK_FILE holds only the PSK and is what the flight_interface service mounts
# (docker-compose.yml, ADR-025 E3.2). make psk-file writes PSK_FILE.
SECRETS  ?= $(HOME)/.config/seame-drone/secrets.json
PSK_FILE ?= $(HOME)/.config/seame-drone/psk.json

# Teammates without Jazzy only get the containers and the unit tests
ifneq ($(wildcard $(ROS)),)
NATIVE  := ws
else
NATIVE  := no-ros
endif

all: no-flight-interface env-demo $(NATIVE) up

# ---------- native venv + workspace ----------

venv: $(VENV)/.installed

$(VENV)/.installed: src/gesture/gesture_node/requirements.txt
	python3.12 -m venv --system-site-packages $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r $<
	touch $@

# --symlink-install links the installed files to the sources. A source file
# removed or renamed (a launch file, say) leaves a dangling link in build/ or
# install/, and colcon then fails on it: remove those before each build.
# Only links whose target is gone, only under the workspace's build/ and install/.
PRUNE_LINKS = find $(WS)/build $(WS)/install -xtype l -delete 2>/dev/null || true

ws: $(WS)/.gesture_node $(WS)/.intent_node $(WS)/.mission_node $(WS)/.controller_node \
	$(WS)/.flight_interface $(WS)/.ground_bringup

# Symlinks, not copies, so --symlink-install picks up .py edits without a rebuild.
# Redone when PKGS changes, so an existing workspace gets new packages too.
$(WS)/.src: Makefile tools/pkg_dirs.sh
	dirs=$$($(FIND_PKG_DIRS)) && mkdir -p $(WS)/src \
		&& for d in $$dirs; do ln -sfn ../../$$d $(WS)/src/$${d##*/}; done
	touch $@

$(WS)/.intent_node: $(WS)/.src src/intent/intent_node/setup.py src/intent/intent_node/package.xml
	$(PRUNE_LINKS)
	source $(ROS) && cd $(WS) && colcon build --symlink-install --packages-select intent_node
	touch $@

$(WS)/.mission_node: $(WS)/.src src/mission/mission_node/setup.py src/mission/mission_node/package.xml
	$(PRUNE_LINKS)
	source $(ROS) && cd $(WS) && colcon build --symlink-install --packages-select mission_node
	touch $@

$(WS)/.controller_node: $(WS)/.src src/controller/controller_node/setup.py \
		src/controller/controller_node/package.xml
	$(PRUNE_LINKS)
	source $(ROS) && cd $(WS) && colcon build --symlink-install --packages-select controller_node
	touch $@

# Built here for its tests (make test); it runs in its container only
# (make real). Its ESP backend needs sd_protocol: the venv has it (test-deps).
$(WS)/.flight_interface: $(WS)/.src src/flight_interface/flight_interface/setup.py \
		src/flight_interface/flight_interface/package.xml
	$(PRUNE_LINKS)
	source $(ROS) && cd $(WS) && colcon build --symlink-install --packages-select flight_interface
	touch $@

# The modes (launch + parameter files, no code). Rebuilt when a file is added
# or removed; with --symlink-install, edits to existing ones apply at once.
# After the nodes it starts: colcon wants every package it depends on that is
# in the workspace built first.
BRINGUP := src/bringup/ground_bringup
$(WS)/.ground_bringup: $(WS)/.src $(WS)/.gesture_node $(WS)/.intent_node $(WS)/.mission_node \
		$(WS)/.controller_node $(WS)/.flight_interface $(BRINGUP)/setup.py $(BRINGUP)/package.xml \
		$(wildcard $(BRINGUP)/launch/*.launch.py $(BRINGUP)/config/*.yaml)
	$(PRUNE_LINKS)
	source $(ROS) && cd $(WS) && colcon build --symlink-install --packages-select ground_bringup
	touch $@

$(WS)/.gesture_node: $(WS)/.src $(VENV)/.installed src/gesture/gesture_node/setup.py src/gesture/gesture_node/package.xml
	$(PRUNE_LINKS)
	source $(ROS) && source $(VENV)/bin/activate && cd $(WS) \
		&& python3 -m colcon build --symlink-install --packages-select gesture_node
	@head -1 $(WS)/install/gesture_node/lib/gesture_node/gesture_node | grep -q '$(VENV)' \
		|| { echo "gesture_node was not built with $(VENV)'s Python"; exit 1; }
	touch $@

no-ros:
	@echo "ROS 2 Jazzy not found ($(ROS)): skipping venv and $(WS), running Docker only"

# ---------- tests ----------

# What the test suites need in the venv, beyond what make venv installs:
#  * sd_protocol: a .pth file pointing at src/sd_protocol_py (always the
#    current source, nothing written into the repo: no egg-info);
#  * drone/tests/tools' requirements (esptool, pyserial), but not pytest: the
#    system's pytest stays, the one ROS's launch_testing plugins work with;
#  * pytest and pytest-cov only where the system has none (a PC without ROS).
TEST_DEPS := $(VENV)/.test-deps
$(TEST_DEPS): $(VENV)/.installed ../tools/bench/requirements.txt
	$(VENV)/bin/pip install -r <(grep -vE '^[[:space:]]*pytest\b' ../tools/bench/requirements.txt)
	$(VENV)/bin/python -c 'import pytest' 2>/dev/null || $(VENV)/bin/pip install pytest
	$(VENV)/bin/python -c 'import pytest_cov' 2>/dev/null || $(VENV)/bin/pip install pytest-cov
	site=$$($(VENV)/bin/python -c 'import sysconfig; print(sysconfig.get_path("purelib"))') \
		&& echo "$(abspath src/sd_protocol_py)" >"$$site/seame_sd_protocol.pth"
	$(VENV)/bin/python -c 'import sd_protocol'
	touch $@

test-deps: $(TEST_DEPS)

# Every suite, one summary row each (tools/run_tests.sh). With Jazzy the
# workspace is built first; without it the ROS suites show SKIP.
test: $(NATIVE) test-deps
	@$(RUN_TESTS) test

# The suites that need no ROS (the package tests' ROS cases are skipped)
unit: test-deps
	@$(RUN_TESTS) unit

# colcon test only, one row per package (as CI's first ground-ros2 step)
pkg-test: ws test-deps
	@$(RUN_TESTS) pkg

# drone/tests/ros only: pipeline, contracts, monitor, entrypoint
e2e-test: ws test-deps
	@$(RUN_TESTS) e2e

# Betaflight SITL (as CI's betaflight-sitl), not part of make test: the first
# run clones and builds Betaflight
test-sitl: test-deps
	@$(RUN_TESTS) sitl

# Its summary comes from tools/smoke_test.sh itself
smoke:
	@mkdir -p $(TESTLOG)
	@tools/smoke_test.sh 2>&1 | tee $(TESTLOG)/smoke.log; exit $${PIPESTATUS[0]}

# ---------- Docker ----------

# /flight/state must have exactly one publisher (ADR-025 §1). A running
# flight_interface container, from any compose project and started with up
# or run, blocks every rule that starts another one (fake_flight_state,
# flight_interface_sim), and every rule that would stop the chain it flies with.
# Only running containers count; there is no override. A docker ps that fails
# also refuses: no check, no start.
# flight_interface_sim (make sim, sim-smoke, sim-hold) is not counted on
# purpose: every mode rule stops the running stack first ($(STOP_COMPOSE)
# down), so a sim flight_interface is replaced, never doubled, and stopping
# it stops only the simulation. Counting it would also refuse make sim-down,
# the way to stop the sim (PR #253 review).
no-flight-interface:
	@names=$$(docker ps --filter label=com.docker.compose.service=flight_interface \
		--format '{{.Names}}') || { echo "make: cannot list Docker containers," \
		"so cannot check that flight_interface is not running"; exit 1; }; \
	if [[ -n $$names ]]; then \
		echo "make $(or $(MAKECMDGOALS),all): refused, flight_interface is running:" $$names; \
		echo "  This rule would start a second /flight/state publisher, or stop the"; \
		echo "  gesture -> intent -> mission chain it flies with (ADR-025)."; \
		echo "  If nobody is using it (ask the safety pilot), stop it first:"; \
		echo "    docker stop" $$names; \
		echo "  docker stop deactivates it cleanly: one last zero setpoint, then BYE."; \
		exit 1; \
	fi

# PSK_FILE from SECRETS: only the PSK, mode 600, written atomically. Always
# rewritten from SECRETS, the one source. Never prints the key.
define PSK_FILE_PY
import json, os, re, sys

src, dst = sys.argv[1], sys.argv[2]
try:
    with open(src) as f:
        psk = str(json.load(f).get("psk", "")).strip().lower()
except (OSError, ValueError, AttributeError) as e:
    sys.exit(f"make psk-file: cannot read the PSK from {src}: {e}")
if not re.fullmatch(r"[0-9a-f]{64}", psk):
    sys.exit(f"make psk-file: no valid PSK (64 hex characters) in {src}; "
             "create one with: python3 drone/tools/bench/ground_link.py --gen-psk")
text = json.dumps({"psk": psk}) + "\n"
try:
    with open(dst) as f:
        old = f.read()
except OSError:
    old = None
os.makedirs(os.path.dirname(dst) or ".", mode=0o700, exist_ok=True)
if old == text:
    os.chmod(dst, 0o600)
    print(f"{dst}: up to date (mode 600)")
    sys.exit()
tmp = dst + ".tmp"
fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
with os.fdopen(fd, "w") as f:
    f.write(text)
os.chmod(tmp, 0o600)
os.replace(tmp, dst)
print(f"{dst}: written (mode 600)")
if old is not None:
    print("The PSK changed. A running flight_interface container still sees "
          "the old file: stop it and start it again.")
endef
export PSK_FILE_PY

psk-file:
	@python3 -c "$$PSK_FILE_PY" "$(SECRETS)" "$(PSK_FILE)"

# env/<mode>.env filled in (from env.example/, values from the safety pilot)
env-demo env-sim env-real:
	@tools/env_check.sh $(@:env-%=%)

# The ros2 CLI's daemon on the host caches the ROS graph, one per domain: a
# stale one lists nodes from an earlier run. Stopped for the mode's domain
# before the mode starts; nothing to do without ROS 2 on the host.
# $(call stop_ros_daemon,MODE)
stop_ros_daemon = if [[ -f $(ROS) ]]; then \
	d=$$(grep -E '^[[:space:]]*GROUND_DOMAIN_ID=' env/$(1).env | tail -n 1 | cut -d= -f2); \
	( source $(ROS) && ROS_DOMAIN_ID=$$d ros2 daemon stop ) >/dev/null 2>&1 || true; fi

# Every start rule starts from scratch: the other modes stopped (demo and sim
# stop each other; a running flight_interface makes them refuse instead),
# the containers recreated, the host's ROS daemon stopped. The build cache is
# kept: only make fclean / make re remove it.
# logs/ made here, not by Docker (as root), so it stays the user's.
up: no-flight-interface env-demo
	-xhost +local: >/dev/null
	mkdir -p logs
	@$(call stop_ros_daemon,demo)
	$(STOP_COMPOSE) down
	$(DEMO_COMPOSE) up --build --force-recreate

down: no-flight-interface
	$(STOP_COMPOSE) down

clean: no-flight-interface
	$(STOP_COMPOSE) down --rmi all --remove-orphans

# ---------- Real mode: the drone (ADR-025) ----------

REAL_COMPOSE := $(COMPOSE) --env-file env/real.env
# The real chain; echo is started apart, in the foreground (the live table)
REAL_CHAIN   := gesture intent mission controller
# flight_interface is started with `run` (so the dry run can change its
# command), under this name, with --rm: docker stop also removes it
FI_NAME      := ground-flight_interface
FI_EXE       := /ws/install/flight_interface/lib/flight_interface/flight_interface
# The ESP's address: ground_bringup's config/real.yaml, the one source
ESP_HOST      = $(shell sed -n 's/^[[:space:]]*esp_host:[[:space:]]*\([0-9.]*\).*/\1/p' \
	$(BRINGUP)/config/real.yaml | head -n 1)

# make real: the whole real stack, flight_interface (backend esp) unconfigured.
# Checks first: env/real.env filled in, the PSK file, the ESP answering ping.
# It never configures, activates or ARMs flight_interface (REQ-SAF-023): it
# prints the operator steps, then shows the live table. Ctrl+C closes only
# the table; the stack keeps running until make real-down.
real: FI_ARGS =
real: ECHO_ARGS =
real: no-flight-interface env-real real-check
	$(REAL_START)

# make real-dry: the same stack and operator steps without the drone. Why it
# is needed: the real mode's steps (configure, activate, ARM, E-stop) and its
# chain can then be rehearsed, and checked after a change, at a desk, with no
# drone, no PSK and nobody holding the radio. flight_interface runs the
# loopback backend (-p backend:=loopback after real.yaml): nothing leaves the PC.
# The loopback never reads the PSK, so /dev/null is mounted in place of the
# PSK file (the service always mounts one): no psk.json needed.
real-dry: FI_ARGS = $(FI_EXE) --ros-args --params-file /config/real.yaml -p backend:=loopback
real-dry: export SEAME_SECRETS_FILE := /dev/null
# The live table says REAL-DRY, not REAL: the same env/real.env, no drone.
real-dry: ECHO_ARGS = -e GROUND_DRY=1
real-dry: no-flight-interface env-real
	$(REAL_START)

real-check:
	@tools/real_check.sh "$${SEAME_SECRETS_FILE:-$(PSK_FILE)}" "$(ESP_HOST)"

# The recipe of real and real-dry, never a rule of its own (so nothing starts
# the real mode without their checks). FI_ARGS empty = the service's own
# command (real.yaml, backend esp).
define REAL_START
-xhost +local: >/dev/null
mkdir -p logs
@$(call stop_ros_daemon,real)
$(STOP_COMPOSE) down
$(REAL_COMPOSE) up -d --build --force-recreate $(REAL_CHAIN)
$(REAL_COMPOSE) run -d --rm --build --name $(FI_NAME) flight_interface $(FI_ARGS)
@$(MAKE) --no-print-directory real-steps
-$(REAL_COMPOSE) run --rm $(ECHO_ARGS) echo
@if docker ps -q --filter name=^$(FI_NAME)$$ | grep -q .; then \
	echo "The real stack is still running. Stop it with: make real-down"; \
else echo "The real stack is stopped."; fi
endef

# The operator steps, printed again any time (nothing else happens). In a dry
# run (the running flight_interface has backend:=loopback, as real-dry starts
# it) R passes GROUND_DRY=1, so `$R` opens the table with the REAL-DRY title,
# not REAL: DRONE. Reads the container only; no container gives the plain R.
real-steps: DRY_ENV = $(shell docker inspect --format '{{join .Args " "}}' $(FI_NAME) 2>/dev/null | grep -q 'backend:=loopback' && echo '-e GROUND_DRY=1 ')
real-steps:
	@echo
	@echo "===== flight_interface is UNCONFIGURED. Operator steps (another terminal, in drone/ground/):"
	@echo "  Safety Rules first: props off on the bench, the safety pilot holds the radio."
	@echo "  R='docker compose --env-file env/real.env run --rm $(DRY_ENV)echo'"
	@echo "  L='ros2 lifecycle set --no-daemon --spin-time 5'   # waits for discovery (fresh container)"
	@echo '  $$R $$L /flight_interface configure'
	@echo '  $$R $$L /flight_interface activate'
	@echo '  $$R ros2 service call /flight_interface/arm_control std_srvs/srv/Trigger   # ARM: by hand only'
	@echo '  $$R ros2 service call /flight_interface/estop std_srvs/srv/Trigger         # E-stop'
	@echo '  $$R ros2 topic echo /flight/state                                            # its state'
	@echo '  $$R                                                                          # the live table again'
	@echo "  Logs: logs/flight_interface/latest.log. Stop everything: make real-down"
	@echo "  These steps again: make real-steps"
	@echo

# Stopping the real mode cuts the drone's link on purpose: the ESP's link
# failsafe takes over. So it asks first, and goes on only on the answer `stop`.
# flight_interface is stopped first (docker stop: it deactivates, one last
# zero setpoint, then BYE; --rm removes it), then the chain, then the echo
# containers started with `run` (the live table of make real / real-dry and
# the operator steps' commands): compose's down leaves `run` containers alone.
real-down:
	@echo "make real-down stops flight_interface: the drone's link is cut and the ESP's"
	@echo "failsafe takes over. The safety pilot must be ready, holding the radio."
	@read -r -p "Type stop to go on: " answer && [[ $$answer == stop ]] || \
		{ echo "make real-down: nothing stopped"; exit 1; }
	@ids=$$(docker ps -q --filter label=com.docker.compose.project=ground \
		--filter label=com.docker.compose.service=flight_interface); \
	if [[ -n $$ids ]]; then docker stop $$ids >/dev/null; fi
	GROUND_MODE=real GROUND_DOMAIN_ID=0 GROUND_DISCOVERY_RANGE=LOCALHOST \
		$(COMPOSE) --profile real down
	@ids=$$(docker ps -q --filter label=com.docker.compose.project=ground \
		--filter label=com.docker.compose.service=echo \
		--filter label=com.docker.compose.oneoff=True); \
	if [[ -n $$ids ]]; then docker stop $$ids >/dev/null; fi

# ---------- Simulation (SIMULATION ONLY, #86) ----------

# make px4-image rebuilds it (after a change to its Dockerfile); sim and
# sim-smoke build it only when it is missing, so they also work offline
# (docker build checks Docker Hub for the base image every time).
px4-image:
	docker build --target px4 -t $(PX4_IMAGE) -f src/simulation/Dockerfile .

px4-image-present:
	@docker image inspect $(PX4_IMAGE) >/dev/null 2>&1 || $(MAKE) --no-print-directory px4-image

# Every service listed by name: never fake_flight_state (pretend /flight/state). Started
# from scratch: the other modes stopped, the containers recreated.
sim: no-flight-interface env-sim px4-image-present
	-xhost +local: >/dev/null
	mkdir -p logs
	@$(call stop_ros_daemon,sim)
	$(STOP_COMPOSE) down
	$(SIM_ENV) $(SIM_COMPOSE) up --build --force-recreate $(SIM)

sim-down: no-flight-interface
	$(STOP_COMPOSE) down

# sim_smoke waits for px4 to be healthy (armed, in Offboard); its exit code is
# the result. Everything is stopped afterwards, pass or fail. The scenario
# checks the mask itself (from /flight/state): mask 8 owns only yaw, which it
# never flies.
sim-smoke: no-flight-interface env-sim px4-image-present
	@mkdir -p logs $(TESTLOG)
	@$(call stop_ros_daemon,sim)
	$(STOP_COMPOSE) down
	@$(SMOKE_ENV) $(SIM_COMPOSE) --profile sim-smoke up --build --force-recreate \
		--exit-code-from sim_smoke $(SIM_SMOKE) 2>&1 | tee $(TESTLOG)/sim-smoke.log; \
	code=$${PIPESTATUS[0]}; \
	$(STOP_COMPOSE) down; \
	echo "full output: $(TESTLOG)/sim-smoke.log, PX4: logs/px4-sitl/px4.log," \
		"flight_interface: logs/flight_interface_sim/latest.log"; \
	exit $$code

# The hold test (F26): RUNS runs, each from scratch, one after the other. It
# passes only if every run passes ("3 runs in a row"). Each run's output in
# $(TESTLOG)/sim-hold-<n>.log (its checks are printed here), its bag,
# truth.csv and summary.md in logs/sim-hold/<start time>/.
sim-hold: no-flight-interface env-sim px4-image-present
	@# RUNS=0 or a non-number would run nothing and pass (ADR-025 F26: 3 in a row)
	@[[ "$(RUNS)" =~ ^[1-9][0-9]*$$ ]] || { echo "make sim-hold: RUNS must be 1 or more, got '$(RUNS)'"; exit 1; }
	@mkdir -p logs $(TESTLOG)
	@$(call stop_ros_daemon,sim)
	@failed=0; \
	for run in $$(seq 1 $(RUNS)); do \
		$(STOP_COMPOSE) down >/dev/null 2>&1; \
		echo "===== make sim-hold: run $$run of $(RUNS)"; \
		$(SMOKE_ENV) $(SIM_COMPOSE) --profile sim-hold up --build --force-recreate \
			--exit-code-from sim_hold $(SIM_HOLD) >$(TESTLOG)/sim-hold-$$run.log 2>&1; \
		code=$$?; \
		grep -E '\| (PASS|FAIL) \||checks passed|\| [0-9-]+ [0-9:.]+ - ' $(TESTLOG)/sim-hold-$$run.log \
			| sed -E 's/^[^|]*\| [0-9-]+ [0-9:.]+ //'; \
		if [[ $$code != 0 ]]; then failed=$$((failed + 1)); fi; \
	done; \
	$(STOP_COMPOSE) down >/dev/null 2>&1; \
	echo "make sim-hold: $$(($(RUNS) - failed)) of $(RUNS) runs passed" \
		"(output: $(TESTLOG)/sim-hold-<n>.log, results: logs/sim-hold/)"; \
	[[ $$failed == 0 ]]

# logs/: everything but its .gitignore, which keeps the folder out of git.
# Docker: only this project's objects, never a host-wide prune. Containers
# with their anonymous volumes (the smoke test's `docker run` ones are named
# ground-smoke-*), then the images left, also the old untagged ones a rebuild
# leaves, and networks. Kept, as other projects may use them: the
# ros:jazzy-ros-base base image and Docker's build cache.
fclean: clean
	rm -rf $(VENV) $(WS) $(BUILD) ../.pytest_cache
	find logs -mindepth 1 -maxdepth 1 ! -name .gitignore -exec rm -rf {} +
	find src docker tools $(E2E) -path src/sd_protocol_py -prune -o \
		\( -name __pycache__ -o -name .pytest_cache -o -name '*.egg-info' \) -prune -exec rm -rf {} +
	docker ps -aq --filter name=^ground-smoke- | xargs -r docker rm -fv
	for p in $(DOCKER_PROJECTS); do \
		label=label=com.docker.compose.project=$$p; \
		docker ps -aq --filter $$label | xargs -r docker rm -fv; \
		docker image prune -af --filter $$label; \
		docker network prune -f --filter $$label; \
	done

re: fclean all

.PHONY: all venv ws no-ros test test-deps test-sitl pkg-test e2e-test unit smoke up down \
	clean fclean re \
	px4-image px4-image-present sim sim-down sim-smoke sim-hold \
	no-flight-interface psk-file env-demo env-sim env-real \
	real real-dry real-check real-steps real-down
