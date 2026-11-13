# Ground nodes: native venv + colcon workspace, and the Docker containers.
#
#   make          venv + ros2_ws (only if ROS 2 Jazzy is installed), then
#                 docker compose up --build (gesture, intent, echo, mission,
#                 flight = pretend /flight/state): echo's live table in this
#                 terminal, each node's output in logs/<service>/latest.log
#   make down     stop the containers
#   make clean    down + remove this project's Docker images
#   make fclean   clean + remove the venv, the workspace, logs/, Python caches and
#                 the rest of this project's Docker objects (make smoke's too),
#                 leaving drone/ground/ as it was cloned. The rest of Docker on
#                 this PC is not touched
#   make re       fclean, then all
#   make test     every test: colcon test (unit + node) and drone/tests/ros
#                 (pipeline). Without Jazzy: the unit tests only (= make unit)
#   make unit     tests that need no ROS (ROS tests are skipped)
#   make smoke    Docker smoke test: builds the images, runs the package tests
#                 inside them, gesture -> intent -> mission across containers,
#                 the echo monitor, clean stops, node logs (in logs/smoke)
#
# SIMULATION ONLY (#86): PX4 SITL + Gazebo instead of the pretend /flight/state.
# The first run builds the PX4 image (long, several GB); later runs reuse it.
#   make sim        PX4 SITL + Gazebo (headless) + XRCE agent + px4_offboard_bridge,
#                   gesture (camera), intent, mission, echo. PX4 arms, takes off
#                   and switches to Offboard by itself; then ENGAGE (both open
#                   hands) and fly. Never flight (the bridge publishes /flight/state)
#   make sim-smoke  headless test, no camera: scripted gestures (ENGAGE, right,
#                   climb, intent_node killed) and the checks of #86, then stops.
#                   Rosbag + summary.md in logs/sim-smoke/<start time>/
#   make sim-down   stop the simulation
#   make px4-image  rebuild the PX4 image (after a change to its Dockerfile)
#   Options: MASK (axes the ground owns, default 15): 8|11|15 for make sim,
#   11|15 for make sim-smoke (it flies right and up only, so under mask 8
#   nothing moves and the checks cannot pass). GUI=0 (no Gazebo window,
#   make sim only; sim-smoke is always headless). Logs: logs/px4/ (sim start-up, bridge),
#   logs/px4-sitl/ (PX4, agent), logs/<node>/ as above.
#
# Every test target ends with a summary (passed/total per package or file)
# and keeps its full output in logs/test/<target>.log.
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
PKGS    := gesture_node intent_node mission_node
E2E     := ../tests/ros
TESTLOG := logs/test

# Simulation (#86). The PX4 image (drone/simulation/px4/Dockerfile) is built
# with plain docker build, not by compose, so make clean and fclean keep it:
# rebuilding it takes a long time. Remove it by hand: docker rmi $(PX4_IMAGE)
PX4_IMAGE    := seame-px4-sim:jazzy-1.17
SIM_PROFILES := --profile sim --profile sim-smoke
SIM          := px4 gesture intent mission echo
SIM_SMOKE    := px4 mission sim_smoke
MASK         ?= 15
GUI          ?= 1
# Host group of the GPU render node, so the px4 container (uid 1000) can use it
RENDER_GID   := $(shell stat -c %g /dev/dri/renderD128 2>/dev/null || echo 0)
SIM_ENV      := MASK=$(MASK) GUI=$(GUI) RENDER_GID=$(RENDER_GID)
SMOKE_ENV    := MASK=$(MASK) GUI=0 RENDER_GID=$(RENDER_GID)

# Compose projects: ground (make, make up: named after this folder) and
# ground-smoke (make smoke). Compose labels their containers, images and
# networks with the name, so fclean removes only those.
DOCKER_PROJECTS := ground ground-smoke

# Teammates without Jazzy only get the containers and the unit tests
ifneq ($(wildcard $(ROS)),)
NATIVE  := ws
TEST    := ros-test
else
NATIVE  := no-ros
TEST    := unit
endif

all: $(NATIVE) up

# ---------- native venv + workspace ----------

venv: $(VENV)/.installed

$(VENV)/.installed: src/gesture_node/requirements.txt
	python3.12 -m venv --system-site-packages $(VENV)
	$(VENV)/bin/pip install --upgrade pip
	$(VENV)/bin/pip install -r $<
	touch $@

ws: $(WS)/.gesture_node $(WS)/.intent_node $(WS)/.mission_node

# Symlinks, not copies, so --symlink-install picks up .py edits without a rebuild.
# Redone when PKGS changes, so an existing workspace gets new packages too.
$(WS)/.src: Makefile
	mkdir -p $(WS)/src
	$(foreach p,$(PKGS),ln -sfn ../../src/$(p) $(WS)/src/$(p);)
	touch $@

$(WS)/.intent_node: $(WS)/.src src/intent_node/setup.py src/intent_node/package.xml
	source $(ROS) && cd $(WS) && colcon build --symlink-install --packages-select intent_node
	touch $@

$(WS)/.mission_node: $(WS)/.src src/mission_node/setup.py src/mission_node/package.xml
	source $(ROS) && cd $(WS) && colcon build --symlink-install --packages-select mission_node
	touch $@

$(WS)/.gesture_node: $(WS)/.src $(VENV)/.installed src/gesture_node/setup.py src/gesture_node/package.xml
	source $(ROS) && source $(VENV)/bin/activate && cd $(WS) \
		&& python3 -m colcon build --symlink-install --packages-select gesture_node
	@head -1 $(WS)/install/gesture_node/lib/gesture_node/gesture_node | grep -q '$(VENV)' \
		|| { echo "gesture_node was not built with $(VENV)'s Python"; exit 1; }
	touch $@

no-ros:
	@echo "ROS 2 Jazzy not found ($(ROS)): skipping venv and $(WS), running Docker only"

# ---------- tests ----------

# Bash helpers for the summaries, loaded with `eval "$$SUMMARY_SH"`.
#   row NAME CODE PASSED TOTAL [NOTE]  one line: PASS/FAIL, passed/total, note
#   n_before WORD TEXT                 "3 failed" -> 3 (0 if absent)
#   n_after KEY TEXT                   "skipped=3" -> 3 (0 if absent)
#   notes N LABEL ...                  "3 skipped, 1 failed" (non-zero counts)
#   summary TITLE CODE ROW...          the block at the end; returns CODE
define SUMMARY_SH
row() {
    local status=PASS
    (($$2)) && status=FAIL
    printf '  %-34s %s %5s/%-5s %s\n' "$$1" "$$status" "$$3" "$$4" "$${5:-}"
}
n_before() {
    local n
    n=$$(grep -oE "[0-9]+ $$1" <<<"$$2" | grep -oE '^[0-9]+' | head -n 1)
    echo "$${n:-0}"
}
n_after() {
    local n
    n=$$(grep -oE "$$1=[0-9]+" <<<"$$2" | grep -oE '[0-9]+$$' | head -n 1)
    echo "$${n:-0}"
}
notes() {
    local out=()
    while (($$# >= 2)); do
        (($$1 > 0)) && out+=("$$1 $$2")
        shift 2
    done
    local IFS=,
    sed 's/,/, /g' <<<"$${out[*]}"
}
summary() {
    local title=$$1 code=$$2
    shift 2
    echo
    echo "=================== summary: $$title ==================="
    printf '%s\n' "$$@"
    ((code)) && echo "FAILED" || echo "ALL PASSED"
    return "$$code"
}
endef
export SUMMARY_SH

test: $(TEST)

# Same as CI's ground-ros2 job. The venv's Python runs them so gesture_node
# finds MediaPipe; it also sees the system pytest and ROS. Both parts run even
# if the first fails, each with its own summary.
ros-test: ws
	@$(MAKE) --no-print-directory pkg-test; a=$$?; \
	$(MAKE) --no-print-directory e2e-test; b=$$?; \
	exit $$((a || b))

# The packages' tests (unit + node), the way ROS runs them. One row per package.
pkg-test: ws
	@eval "$$SUMMARY_SH"; mkdir -p $(TESTLOG); \
	( source $(ROS) && source $(WS)/install/setup.bash && source $(VENV)/bin/activate \
		&& cd $(WS) \
		&& { colcon test-result --delete-yes >/dev/null || true; } \
		&& python3 -m colcon test --packages-select $(PKGS) --event-handlers console_direct+ \
		&& colcon test-result --verbose ) 2>&1 | tee $(TESTLOG)/pkg-test.log; \
	code=$${PIPESTATUS[0]}; rows=(); \
	for p in $(PKGS); do \
		line=$$(cd $(WS) && colcon test-result --all 2>/dev/null | grep "^build/$$p/"); \
		if [[ -z $$line ]]; then rows+=("$$(row $$p 1 0 '?' 'no results (crash or not built)')"); code=1; continue; fi; \
		t=$$(n_before tests "$$line"); e=$$(n_before errors "$$line"); \
		f=$$(n_before failures "$$line"); k=$$(n_before skipped "$$line"); \
		rows+=("$$(row $$p $$((e + f)) $$((t - e - f - k)) $$t "$$(notes $$k skipped $$f failed $$e errors)")"); \
	done; \
	summary "make pkg-test (colcon: unit + node tests)" $$code "$${rows[@]}"

# drone/tests/ros: pipeline, contracts, monitor, entrypoint. One row per file.
e2e-test: ws
	@eval "$$SUMMARY_SH"; mkdir -p $(TESTLOG); \
	( source $(ROS) && source $(WS)/install/setup.bash && source $(VENV)/bin/activate \
		&& python3 -m pytest $(E2E) -v ) 2>&1 | tee $(TESTLOG)/e2e-test.log; \
	code=$${PIPESTATUS[0]}; rows=(); \
	for f in $$(grep -oE '^[^ ]+\.py::' $(TESTLOG)/e2e-test.log | sort -u); do \
		lines=$$(grep -F "$$f" $(TESTLOG)/e2e-test.log | grep -E ' (PASSED|FAILED|ERROR|SKIPPED)'); \
		p=$$(grep -c ' PASSED' <<<"$$lines"); x=$$(grep -cE ' (FAILED|ERROR)' <<<"$$lines"); \
		k=$$(grep -c ' SKIPPED' <<<"$$lines"); \
		rows+=("$$(row "$$(basename "$${f%::}")" $$x $$p $$((p + x + k)) "$$(notes $$k skipped $$x failed)")"); \
	done; \
	last=$$(grep -E '^=+ .*[0-9]+ (passed|failed|error)' $(TESTLOG)/e2e-test.log | tail -n 1); \
	p=$$(n_before passed "$$last"); x=$$(( $$(n_before failed "$$last") + $$(n_before 'errors?' "$$last") )); \
	k=$$(n_before skipped "$$last"); \
	rows+=("$$(row total $$code $$p $$((p + x + k)) "$$(notes $$k skipped $$x 'failed or errors')")"); \
	summary "make e2e-test (drone/tests/ros)" $$code "$${rows[@]}"

# Every suite runs even if one fails. One row per package, plus drone/tests/ros.
unit:
	@eval "$$SUMMARY_SH"; mkdir -p $(TESTLOG); : >$(TESTLOG)/unit.log; code=0; rows=(); \
	for dir in $(addprefix src/,$(PKGS)) $(E2E); do \
		( if [[ $$dir == $(E2E) ]]; then python3 -m unittest discover -s $$dir -v; \
			else cd $$dir && python3 -m unittest discover -s test -v; fi ) 2>&1 | tee $(TESTLOG)/unit.part; \
		c=$${PIPESTATUS[0]}; ((c)) && code=1; cat $(TESTLOG)/unit.part >>$(TESTLOG)/unit.log; \
		ran=$$(grep -oE '^Ran [0-9]+' $(TESTLOG)/unit.part | grep -oE '[0-9]+'); ran=$${ran:-0}; \
		end=$$(grep -E '^(OK|FAILED)' $(TESTLOG)/unit.part | tail -n 1); \
		k=$$(n_after skipped "$$end"); f=$$(n_after failures "$$end"); e=$$(n_after errors "$$end"); \
		name=$${dir#src/}; [[ $$dir == $(E2E) ]] && name=drone/tests/ros; \
		rows+=("$$(row $$name $$c $$((ran - k - f - e)) $$ran "$$(notes $$k skipped $$f failed $$e errors)")"); \
	done; \
	rm -f $(TESTLOG)/unit.part; \
	summary "make unit (no ROS needed)" $$code "$${rows[@]}"

# Its summary comes from docker/smoke_test.sh itself
smoke:
	@mkdir -p $(TESTLOG)
	@docker/smoke_test.sh 2>&1 | tee $(TESTLOG)/smoke.log; exit $${PIPESTATUS[0]}

# ---------- Docker ----------

# logs/ made here, not by Docker (as root), so it stays the user's.
# A running simulation is stopped first: its bridge also publishes /flight/state.
up:
	-xhost +local: >/dev/null
	mkdir -p logs
	$(COMPOSE) $(SIM_PROFILES) stop px4 sim_smoke
	$(COMPOSE) up --build

down:
	$(COMPOSE) $(SIM_PROFILES) down

clean:
	$(COMPOSE) $(SIM_PROFILES) down --rmi all --remove-orphans

# ---------- Simulation (SIMULATION ONLY, #86) ----------

# make px4-image rebuilds it (after a change to its Dockerfile); sim and
# sim-smoke build it only when it is missing, so they also work offline
# (docker build checks Docker Hub for the base image every time).
px4-image:
	docker build -t $(PX4_IMAGE) ../simulation/px4

px4-image-present:
	@docker image inspect $(PX4_IMAGE) >/dev/null 2>&1 || $(MAKE) --no-print-directory px4-image

# Every service listed by name: never flight (pretend /flight/state). Started
# from scratch: the other modes stopped, the containers recreated.
sim: px4-image-present
	-xhost +local: >/dev/null
	mkdir -p logs
	$(COMPOSE) $(SIM_PROFILES) down
	$(SIM_ENV) $(COMPOSE) --profile sim up --build --force-recreate $(SIM)

sim-down:
	$(COMPOSE) $(SIM_PROFILES) down

# sim_smoke waits for px4 to be healthy (armed, in Offboard); its exit code is
# the result. Everything is stopped afterwards, pass or fail.
# Checked before the PX4 image is built: mask 8 owns only yaw, which the
# scenario never flies, so its checks could never pass.
sim-smoke-mask:
	@[[ "$(MASK)" == 11 || "$(MASK)" == 15 ]] || \
		{ echo "make sim-smoke: MASK must be 11 or 15, got '$(MASK)'"; exit 2; }

sim-smoke: sim-smoke-mask px4-image-present
	@mkdir -p logs $(TESTLOG)
	$(COMPOSE) $(SIM_PROFILES) down
	@$(SMOKE_ENV) $(COMPOSE) --profile sim-smoke up --build --force-recreate \
		--exit-code-from sim_smoke $(SIM_SMOKE) 2>&1 | tee $(TESTLOG)/sim-smoke.log; \
	code=$${PIPESTATUS[0]}; \
	$(COMPOSE) $(SIM_PROFILES) down; \
	echo "full output: $(TESTLOG)/sim-smoke.log, PX4: logs/px4-sitl/px4.log"; \
	exit $$code

# logs/: everything but its .gitignore, which keeps the folder out of git.
# Docker: only this project's objects, never a host-wide prune. Containers
# with their anonymous volumes (the smoke test's `docker run` ones are named
# ground-smoke-*), then the images left, also the old untagged ones a rebuild
# leaves, and networks. Kept, as other projects may use them: the
# ros:jazzy-ros-base base image and Docker's build cache.
fclean: clean
	rm -rf $(VENV) $(WS) ../.pytest_cache
	find logs -mindepth 1 -maxdepth 1 ! -name .gitignore -exec rm -rf {} +
	find src docker $(E2E) -path src/sd_protocol_py -prune -o \
		\( -name __pycache__ -o -name .pytest_cache -o -name '*.egg-info' \) -prune -exec rm -rf {} +
	docker ps -aq --filter name=^ground-smoke- | xargs -r docker rm -fv
	for p in $(DOCKER_PROJECTS); do \
		label=label=com.docker.compose.project=$$p; \
		docker ps -aq --filter $$label | xargs -r docker rm -fv; \
		docker image prune -af --filter $$label; \
		docker network prune -f --filter $$label; \
	done

re: fclean all

.PHONY: all venv ws no-ros test ros-test pkg-test e2e-test unit smoke up down clean fclean re \
	px4-image px4-image-present sim sim-down sim-smoke sim-smoke-mask
