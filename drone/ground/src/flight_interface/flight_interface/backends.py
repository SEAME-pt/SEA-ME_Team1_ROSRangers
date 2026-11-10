"""Backends of the flight_interface lifecycle node (#103): where setpoints go and
where the drone state comes from.

A backend has three methods: send_setpoint(), poll() and close(). There is
deliberately no way to send ARM_CONTROL_REQUEST through it (REQ-SAF-023):
arming is always an operator action, never the node's.

Today only `loopback` exists (tests and dry runs, no drone). The ESP backend
(sd_protocol over TCP/UDP to the gateway firmware, #102) and the PX4 SITL
backend (ADR-019) come in later parts of #103.
"""


class Backend:
    """Interface. Times are monotonic seconds."""

    name = "base"

    def send_setpoint(self, command, now):
        """Send one velocity setpoint (vx, vy, vz, wz), body FLU."""
        raise NotImplementedError

    def poll(self, now):
        """Return the valid drone states received since the last call (a list of
        dicts with esp_state, override_active, mask, airborne). Invalid frames
        are dropped by the backend and never reach the node."""
        raise NotImplementedError

    def close(self):
        """Release sockets or ports. Called on cleanup and shutdown."""


class LoopbackBackend(Backend):
    """No drone: keeps what was sent and reports a fixed drone state.

    The reported state is conservative by default (SESSION_IDLE, no override,
    no mask), so a dry run never lets mission_node engage. Set `state` to
    something else on purpose for a test.
    """

    name = "loopback"

    def __init__(self, state=None, connected=True):
        self.state = dict(
            state
            or {
                "esp_state": "SESSION_IDLE",
                "override_active": False,
                "mask": None,
                "airborne": False,
            }
        )
        self.connected = connected
        self.sent = []  # (now, command)
        self.closed = False

    def send_setpoint(self, command, now):
        self.sent.append((now, tuple(command)))

    def poll(self, now):
        return [dict(self.state)] if self.connected and not self.closed else []

    def close(self):
        self.closed = True


def make_backend(name):
    """The backend named by the `backend` parameter. ValueError for others."""
    if name == "loopback":
        return LoopbackBackend()
    raise ValueError(
        f"backend must be 'loopback' (esp and px4 come later in #103), got {name!r}"
    )
