# The base every ground image builds FROM (#186): ROS 2 Jazzy, the non-root
# user `ros`, /ws and the entrypoint. Only what every image needs, so a change
# to one package never rebuilds the others (sd_protocol_py and pip packages
# stay in the images that use them). Every package's ROS dependencies already
# come with ros-base (pytest included), so no rosdep.
#
# Build context is drone/ground/. compose builds it as the `base` service and
# hands it to the others as the build context `base` (`FROM base`), so
# `docker compose build` always builds it first.
#
# The user has the host user's uid and gid (GROUND_UID / GROUND_GID, set by the
# Makefile and tools/smoke_test.sh; 1000 by default), so the files it writes to
# logs/ belong to the host user, and it can read the host's psk.json (mode 600).
FROM ros:jazzy-ros-base

ARG GROUND_UID=1000
ARG GROUND_GID=1000

# The image already has a user `ubuntu` with uid and gid 1000: free the ids
# first. A video group member, for the camera.
RUN set -eux; \
    user=$(getent passwd "${GROUND_UID}" | cut -d: -f1); \
    if [ -n "$user" ]; then userdel -r "$user" 2>/dev/null || userdel "$user"; fi; \
    group=$(getent group "${GROUND_GID}" | cut -d: -f1); \
    if [ -n "$group" ]; then groupdel "$group"; fi; \
    groupadd -g "${GROUND_GID}" ros; \
    useradd -m -u "${GROUND_UID}" -g "${GROUND_GID}" -G video -s /bin/bash ros; \
    mkdir -p /ws; \
    chown ros:ros /ws

# Ubuntu 24.04 marks the system Python as externally managed (PEP 668). The
# containers are throwaway, so the images that pip install go straight into it.
ENV PIP_BREAK_SYSTEM_PACKAGES=1 \
    PIP_NO_CACHE_DIR=1

COPY docker/entrypoint.sh /entrypoint.sh

USER ros
WORKDIR /ws
ENTRYPOINT ["/entrypoint.sh"]
