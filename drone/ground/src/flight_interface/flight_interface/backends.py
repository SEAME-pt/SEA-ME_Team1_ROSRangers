"""Backends of the flight_interface lifecycle node (#103): where setpoints go and
where the drone state comes from.

A backend has these methods and no others the node may call:

  send_setpoint(command, now)  one setpoint (vx, vy, vz, wz), body FLU
  send_neutral(now)            one neutral setpoint (SETPOINT mode 0)
  end_session(now)             BYE: ends the session (E-stop, decision A1)
  request_resume(now)          RESUME_REQUEST, only when the operator asked (A3)
  arm_control(mask, now)       ARM_CONTROL_REQUEST, only from the operator's
                               ~/arm_control service (A4, REQ-SAF-023)
  poll(now)                    the valid drone states received since the last call
  take_acks()                  the ESP's ARM / RESUME answers since the last call
                               (read-only, decision E2)
  close()                      release sockets or ports

ARM_CONTROL_REQUEST goes out only through arm_control(), and the node calls
that only from the ~/arm_control service (decision A4): ARM is always an
operator action, never the node's (REQ-SAF-023). Tests check both: the node
calls nothing else on its own, and no other code builds the frame.

Backends: `loopback` (tests and dry runs, no drone) and `esp` (sd_protocol over
TCP/UDP to the gateway firmware, esp_backend.py). The PX4 SITL backend waits
for decision C1.
"""


class Backend:
    """Interface. Times are monotonic seconds."""

    name = "base"

    def send_setpoint(self, command, now):
        """Send one velocity setpoint (vx, vy, vz, wz), body FLU."""
        raise NotImplementedError

    def send_neutral(self, now):
        """Send one neutral setpoint: SETPOINT mode 0 on the ESP."""
        raise NotImplementedError

    def end_session(self, now):
        """End the session with BYE, so the ESP starts its ADR-004 output at once
        instead of after T_loss. A backend may open a new session later (A5),
        never with ARM."""
        raise NotImplementedError

    def request_resume(self, now):
        """Send RESUME_REQUEST. Returns True if it went out. Called by the node
        only after the operator asked and the guard said it is due (A3)."""
        raise NotImplementedError

    def arm_control(self, mask, now):
        """Send ARM_CONTROL_REQUEST with `mask`. Returns True if it went out.
        Called by the node only from the operator's ~/arm_control (A4)."""
        raise NotImplementedError

    def poll(self, now):
        """Return the valid drone states received since the last call (a list of
        dicts with esp_state, override_active, mask, airborne, and optionally
        failsafe_output and rx_time, the monotonic receive time). Invalid frames
        are dropped by the backend and never reach the node."""
        raise NotImplementedError

    def take_acks(self):
        """The ARM_CONTROL_ACK / RESUME_ACK answers received since the last
        call, oldest first, as (type name, result code, reason name), e.g.
        ("ARM_CONTROL_ACK", 2, "MASK_MISMATCH"). Read-only: the node only logs
        them (decision E2). A backend without answers returns []."""
        return []

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
        self.sent = []  # (now, command); a neutral is recorded as a zero command
        self.log = []  # (now, what): "setpoint", "neutral", "bye", "resume", "arm"
        self.armed_masks = []  # masks passed to arm_control()
        self.acks = []  # answers a test queues for take_acks()
        self.closed = False

    def send_setpoint(self, command, now):
        self.sent.append((now, tuple(command)))
        self.log.append((now, "setpoint"))

    def send_neutral(self, now):
        self.sent.append((now, (0.0, 0.0, 0.0, 0.0)))
        self.log.append((now, "neutral"))

    def end_session(self, now):
        self.log.append((now, "bye"))

    def request_resume(self, now):
        self.log.append((now, "resume"))
        return True

    def arm_control(self, mask, now):
        self.log.append((now, "arm"))
        self.armed_masks.append(mask)
        return True

    def poll(self, now):
        return [dict(self.state)] if self.connected and not self.closed else []

    def take_acks(self):
        acks, self.acks = self.acks, []
        return acks

    def close(self):
        self.closed = True


BACKENDS = ("loopback", "esp")


def make_backend(name, esp_host="192.168.4.1", esp_port=5759, psk=None, warn=None):
    """The backend named by the `backend` parameter. ValueError for others.

    `esp` needs sd_protocol (drone/ground/src/sd_protocol_py) and the PSK; it is
    imported only here, so the loopback backend runs without sd_protocol.
    `warn(message)` gets load_psk's warning about a secrets file other users
    can read (decision E3)."""
    if name == "loopback":
        return LoopbackBackend()
    if name == "esp":
        from flight_interface.esp_backend import EspBackend, load_psk

        if psk is None:
            psk = load_psk(warn=warn)
        return EspBackend(esp_host, esp_port, psk)
    raise ValueError(
        f"backend must be one of {', '.join(BACKENDS)} (px4 waits for C1), got {name!r}"
    )
