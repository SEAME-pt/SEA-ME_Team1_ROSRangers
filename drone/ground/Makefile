# Ground nodes: native venv + colcon workspace, and the Docker containers.
#
#   make          venv + ros2_ws (only if ROS 2 Jazzy is installed), then
#                 docker compose up --build (gesture, intent, echo)
#   make down     stop the containers
#   make clean    down + remove this project's Docker images
#   make fclean   clean + remove the venv, the workspace and Python caches,
#                 leaving drone/ground/ as it was cloned
#   make re       fclean, then all
#   make test     every test: colcon test (unit + node) and drone/tests/ros
#                 (pipeline). Without Jazzy: the unit tests only (= make unit)
#   make unit     tests that need no ROS (ROS tests are skipped)
#   make smoke    Docker smoke test: builds the images, runs the package tests
#                 inside them, gesture -> intent across containers, clean stops
#
# The native workspace follows docs/PraticalPlan/06 Gestures/00 Gesture Pipeline.md
# (Step 0), but lives here instead of ~/ros2_ws and ~/gesture-venv:
#   gesture_node is built with the venv's Python (MediaPipe, OpenCV),
#   intent_node with the system Python.

SHELL   := /bin/bash
COMPOSE := docker compose -f docker-compose.yml
ROS     := /opt/ros/jazzy/setup.bash

VENV    := gesture-venv
WS      := ros2_ws
PKGS    := gesture_node intent_node
E2E     := ../tests/ros

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

ws: $(WS)/.gesture_node $(WS)/.intent_node

# Symlinks, not copies, so --symlink-install picks up .py edits without a rebuild
$(WS)/.src:
	mkdir -p $(WS)/src
	$(foreach p,$(PKGS),ln -sfn ../../src/$(p) $(WS)/src/$(p);)
	touch $@

$(WS)/.intent_node: $(WS)/.src src/intent_node/setup.py src/intent_node/package.xml
	source $(ROS) && cd $(WS) && colcon build --symlink-install --packages-select intent_node
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

test: $(TEST)

# Same as CI's ground-ros2 job. The venv's Python runs them so gesture_node
# finds MediaPipe; it also sees the system pytest and ROS.
ros-test: ws
	source $(ROS) && source $(WS)/install/setup.bash && source $(VENV)/bin/activate \
		&& cd $(WS) \
		&& python3 -m colcon test --packages-select $(PKGS) --event-handlers console_direct+ \
		&& colcon test-result --verbose
	source $(ROS) && source $(WS)/install/setup.bash && source $(VENV)/bin/activate \
		&& python3 -m pytest $(E2E) -v

unit:
	cd src/intent_node && python3 -m unittest discover -s test -v
	cd src/gesture_node && python3 -m unittest discover -s test -v
	python3 -m unittest discover -s $(E2E) -v

smoke:
	docker/smoke_test.sh

# ---------- Docker ----------

up:
	-xhost +local: >/dev/null
	$(COMPOSE) up --build

down:
	$(COMPOSE) down

clean:
	$(COMPOSE) down --rmi all --remove-orphans

fclean: clean
	rm -rf $(VENV) $(WS) ../.pytest_cache
	find src $(E2E) \( -name __pycache__ -o -name .pytest_cache -o -name '*.egg-info' \) -prune -exec rm -rf {} +

re: fclean all

.PHONY: all venv ws no-ros test ros-test unit smoke up down clean fclean re
