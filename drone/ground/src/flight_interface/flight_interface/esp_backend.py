"""ESP backend of the flight_interface node (#103 part 2): Protocol Spec v0 over
TCP + UDP port 5759 to the gateway firmware (#102).

What it does:
  * Opens a session (HELLO / HELLO_ACK over TCP, keys from the PSK) and keeps it:
    when the session is gone it opens a new one by itself (decision A5), with the
    rules of scripts/ground_link.py: no STATE for RECONNECT_AFTER while
    setpoints are going out, or no TCP while the ESP is not STREAMING. (The ESP
    sends STATE to the address of the last accepted SETPOINT, so while the node
    is inactive and sends nothing, no STATE comes back: that alone is not a
    lost session.) One attempt every RECONNECT_EVERY. A new
    session never sends ARM: the drone stays in SESSION_IDLE (REQ-SAF-023).
  * Confirms every new session at once with CONFIG_GET 0x01 (read-only). While
    an old session still exists, the ESP keeps a new one only as pending and
    drops it after 2 s unless a frame under its keys arrives (ESP Link State
    Machine §1, #156); an inactive node sends no setpoints, so without this its
    new session would expire.
  * Checks that the ESP answers (decision E4, ADR-005): a new session counts
    only once a frame under its keys came back (the CONFIG_ACK, or a STATE).
    None within HANDSHAKE_TIMEOUT means the HELLO_ACK was a replayed one (the
    keys are wrong) or the gateway lost the session: it is dropped and a new
    handshake follows, never with ARM. Before E4 an inactive node kept such a
    session forever.
  * Sends SETPOINT over UDP. Until decision C1 (who turns the velocity target
    into attitude sticks) every setpoint goes out as mode 0 (neutral): it keeps
    the link alive (the setpoint stream is the heartbeat, spec §7.2) and never
    moves the drone.
  * Reads STATE (UDP) and EVENT / ACK / BYE (TCP). Every frame is checked by an
    sd_protocol Receiver; a rejected frame is dropped and never refreshes the
    watchdog (REQ-SAF-011). failsafe_output is true after EVENT 0x0104 or
    0x0102, or while the ESP reports LINK_FAILSAFE; it is cleared by STREAMING,
    EVENT 0x0103 or an ACK OK.
  * FC link health (#179, decision D1 of 2026-11-10): EVENT 0x0201 FC_LINK_LOST
    (no valid FC reply for 250 ms) sets fc_link_ok false, 0x0202
    FC_LINK_RESTORED sets it true; None until the ESP says either. The guard
    allows no non-zero setpoint while it is false.
  * Every ARM_CONTROL_ACK / RESUME_ACK goes to take_acks() (decision E2), so
    the node can tell the operator what the ESP answered.
  * The FC values of every STATE (roll, pitch, heading, vbat) go with the drone
    state as `fc_values` (decision D2): the node publishes /fc/attitude and
    /fc/battery from them.
  * arm_control(mask) sends ARM_CONTROL_REQUEST (decision A4): the only path
    to ARM, called only from the node's ~/arm_control service, i.e. by the
    operator (REQ-SAF-023). The mask reported to the node is the one this
    session asked for and the ESP confirmed with ARM_CONTROL_ACK OK (decision
    B1); None before that and in every new session.
  * end_session() sends BYE (decision A1: the E-stop ends the session, so the
    ESP starts its ADR-004 output at once). request_resume() sends
    RESUME_REQUEST (decision A3: only when the operator asked).
  * Never blocks: non-blocking sockets, driven by poll() from the node's timer.
    Socket errors are caught and counted here, never raised to the node (an
    exception in the node's tick stops the node). The address is resolved
    and checked once, when the backend is made (configure): a bad port or
    host fails configure, and the tick never waits on DNS. One poll reads at
    most MAX_UDP_READS datagrams and MAX_TCP_READS chunks, so a flood cannot
    hold up the tick (and ~/estop, served by the same executor).

Open, not done here: airborne will come from the armed flag in FC_HEALTH
(decision B1), but FC_HEALTH (type 0x81, 5 Hz, #179 D3) has no byte layout
yet: until sd_protocol decodes it, airborne is False and FC_HEALTH is dropped
like any frame that is not a STATE (it never refreshes the watchdog).
ARM_CONTROL_ACK result 4 "FC not answering" (#179 D2) has a local name until
sd_protocol's AckResult has it (PR #183). The session code is sd_protocol.ground (decision B2),
shared with scripts/ground_link.py once that tool moves to it.
"""

import errno
import os
import select
import socket
import time

from flight_interface.backends import Backend
from sd_protocol.crypto import KEY_SIZE
from sd_protocol.frame import MsgType
from sd_protocol.ground import GroundSession, TcpReader, load_psk
from sd_protocol.messages import (
    AckResult,
    EspState,
    EventCode,
    decode_event,
    decode_u8_payload,
)

__all__ = ["EspBackend", "load_psk"]

DEFAULT_PORT = 5759  # TCP session + UDP control (Protocol Spec v0 §7.1)
HANDSHAKE_TIMEOUT = 1.0  # connect + HELLO_ACK, then the attempt is dropped
RECONNECT_AFTER = 3.0  # no STATE for this long: the session is gone (ground_link.py)
RECONNECT_EVERY = 2.0  # at most one attempt this often
SENDING_GAP = 0.5  # no SETPOINT for this long: the ground is not sending
# Reads per poll. Real traffic is ~1 datagram per 20 ms tick (STATE 25 Hz,
# FC_HEALTH 5 Hz); 256 is more than a default socket buffer holds, so a
# burst is still read in one tick, but the work per tick has a bound even
# when DDS tuning raises the kernel's buffer sizes (~1 ms here).
MAX_UDP_READS = 256
MAX_TCP_READS = 16  # of 4096 bytes

_FAILSAFE_ON = {EventCode.LINK_FAILSAFE_ENTERED, EventCode.FAILSAFE_OUTPUT_ACTIVE}
# #179 D1 (2026-11-10). Local until sd_protocol's EventCode has them (#179's PR).
FC_LINK_LOST = 0x0201
FC_LINK_RESTORED = 0x0202
ACK_FC_NOT_ANSWERING = 4  # #179 D2; AckResult.FC_NOT_ANSWERING after PR #183
# Read-only key (config_table_version) used to confirm a new session.
CONFIRM_KEY = 0x01
MASKS = (8, 11, 15)  # ADR-010 staged override masks

IDLE, CONNECTING, HELLO_SENT, OPEN = "idle", "connecting", "hello_sent", "open"


def resolve(host, port):
    """(IPv4 address, port) for esp_host / esp_port, or ValueError. Called
    once, at configure: a host name is looked up here, never in the tick
    (getaddrinfo blocks, and its error would be raised from poll())."""
    try:
        port = int(port)
    except (TypeError, ValueError):
        raise ValueError(f"esp_port must be a number, got {port!r}") from None
    if not 0 < port < 65536:
        raise ValueError(f"esp_port must be 1-65535, got {port}")
    host = str(host).strip()
    if not host:
        raise ValueError("esp_host is empty")
    try:
        found = socket.getaddrinfo(host, port, socket.AF_INET, socket.SOCK_STREAM)
    except (OSError, UnicodeError) as error:
        raise ValueError(f"esp_host {host!r} cannot be resolved: {error}") from None
    return found[0][4][0], port


def ack_reason(result):
    """The name of an ARM_CONTROL_ACK / RESUME_ACK result code."""
    if result == ACK_FC_NOT_ANSWERING:
        return "FC_NOT_ANSWERING"
    try:
        return AckResult(result).name
    except ValueError:
        return f"UNKNOWN_{result}"


class EspBackend(Backend):
    """The gateway firmware over sd_protocol. Times are monotonic seconds."""

    name = "esp"

    def __init__(self, host, port, psk):
        if len(psk) != KEY_SIZE:
            raise ValueError(f"PSK must be {KEY_SIZE} bytes")
        self.addr = resolve(host, port)
        self._psk = psk
        self.phase = IDLE
        self._tcp = None
        self._udp = None
        self._reader = None
        self._pending = None  # GroundSession waiting for its HELLO_ACK
        self._session = None  # the open session
        self._deadline = 0.0
        self._next_attempt = 0.0  # the first poll connects
        self._last_state_t = 0.0
        self._last_udp_t = None  # last SETPOINT sent
        self._waiting_since = None  # sending, and no STATE since then
        self.esp_state = None  # name from the last accepted STATE
        self.failsafe_output = False
        self.sessions_opened = 0
        self.errors = 0
        self.last_error = ""
        self.last_ack = None  # (type name, result) of the last ARM/RESUME ACK
        self._acks = []  # (type name, result, reason) not yet taken by the node
        self._confirmed = False  # E4: a frame under this session's keys came back
        self._confirm_by = 0.0
        self.sessions_unconfirmed = 0  # dropped for no answer to the first frame
        self.fc_link_ok = None  # #179: None unknown, False after FC_LINK_LOST
        self.armed_mask = None  # B1: the mask the ESP confirmed with ARM ACK OK
        self._arm_requested = None
        self.closed = False

    # ---------- the node's interface ----------
    def send_setpoint(self, command, now):
        """Until decision C1 every setpoint is sent as mode 0 (neutral)."""
        self._send_neutral(now)

    def send_neutral(self, now):
        self._send_neutral(now)

    def end_session(self, now):
        """BYE (decision A1), then the session is dropped. The next session is
        opened after RECONNECT_EVERY (decision A5), never with ARM. Without TCP
        BYE cannot go out (it is a TCP message): the ESP then sees the setpoints
        stop and enters its failsafe after T_loss."""
        if self._session is not None and self._tcp is not None:
            self._send_tcp(self._session.bye(now))
        self._drop(now, retry_after=RECONNECT_EVERY)

    def arm_control(self, mask, now):
        """ARM_CONTROL_REQUEST with `mask` (decision A4). Only the node's
        ~/arm_control service calls this: ARM is always an operator action
        (REQ-SAF-023). Returns True if it went out."""
        if mask not in MASKS or self._session is None or self._tcp is None:
            return False
        if not self._send_tcp(self._session.arm(mask, now)):
            return False
        self._arm_requested = mask
        return True

    def request_resume(self, now):
        if self._session is None or self._tcp is None:
            return False
        return self._send_tcp(self._session.resume_request(now))

    def take_acks(self):
        """The ARM_CONTROL_ACK / RESUME_ACK answers received since the last
        call, oldest first: (type name, result code, reason name)."""
        acks, self._acks = self._acks, []
        return acks

    def poll(self, now):
        if self.closed:
            return []
        self._drive_connection(now)
        states = []
        if self.phase == OPEN:
            self._read_tcp(now)
        if self.phase == OPEN:  # a BYE on TCP may have ended the session
            state = self._read_udp(now)
            if state is not None:
                states.append(state)
            self._check_confirmed(now)
        if self.phase == OPEN:
            self._check_session(now)
        return states

    def close(self):
        if not self.closed:
            self.end_session(time.monotonic())
            self.closed = True

    # ---------- diagnostics ----------
    @property
    def session_open(self):
        return self.phase == OPEN

    @property
    def session_id(self):
        return self._session.session_id if self._session is not None else 0

    @property
    def tcp_open(self):
        return self._tcp is not None

    @property
    def rx_rejected(self):
        """Frames of this session dropped by the receive checks (bad tag,
        replay, wrong size...), like the ESP's STATE.rx_rejected."""
        return self._session.rejected if self._session is not None else 0

    # ---------- connection ----------
    def _error(self, what, error):
        self.errors += 1
        self.last_error = f"{what}: {error}"

    def _drive_connection(self, now):
        if self.phase == IDLE and now >= self._next_attempt:
            self._start_connect(now)
        if self.phase == CONNECTING:
            self._finish_connect(now)
        if self.phase == HELLO_SENT:
            self._read_hello_ack(now)
        if self.phase in (CONNECTING, HELLO_SENT) and now > self._deadline:
            self._error("handshake", "no HELLO_ACK in time (wrong PSK or no gateway?)")
            self._drop(now, retry_after=RECONNECT_EVERY)

    def _start_connect(self, now):
        self._next_attempt = now + RECONNECT_EVERY
        self._deadline = now + HANDSHAKE_TIMEOUT
        tcp = None
        try:  # e.g. EMFILE: no socket left in this process
            tcp = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            tcp.setblocking(False)
            tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            err = tcp.connect_ex(self.addr)
        except OSError as error:
            err = error.errno or errno.EIO
        if err not in (0, errno.EINPROGRESS, errno.EWOULDBLOCK, errno.EALREADY):
            if tcp is not None:
                tcp.close()
            self._error("connect", os.strerror(err))
            return
        self._tcp = tcp
        self.phase = CONNECTING

    def _finish_connect(self, now):
        # poll(), not select(): select() raises for a descriptor >= 1024.
        poller = select.poll()
        poller.register(self._tcp, select.POLLOUT)
        if not poller.poll(0):
            return
        err = self._tcp.getsockopt(socket.SOL_SOCKET, socket.SO_ERROR)
        if err:
            self._error("connect", os.strerror(err))
            self._drop(now, retry_after=RECONNECT_EVERY)
            return
        self._pending = GroundSession(self._psk)
        self._reader = TcpReader()
        if self._send_tcp(self._pending.hello(now)):
            self.phase = HELLO_SENT
        else:
            self._drop(now, retry_after=RECONNECT_EVERY)

    def _read_hello_ack(self, now):
        data = self._recv_tcp(now)
        if not data or self.phase != HELLO_SENT:
            return
        try:
            frames = self._reader.feed(data)
        except ValueError as error:
            self._error("handshake", error)
            self._drop(now, retry_after=RECONNECT_EVERY)
            return
        for i, frame in enumerate(frames):
            if self._pending.take_ack(frame):
                if self._open(now):  # e.g. EVENT 0x0104 in the same read
                    self._handle_tcp_frames(frames[i + 1 :], now)
                return

    def _open(self, now):
        """The HELLO_ACK came: the session is open. False if it could not be
        (no UDP socket): then it is dropped and tried again later."""
        udp = None
        try:
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.setblocking(False)
            udp.connect(self.addr)  # STATE comes back to this address and port
        except OSError as error:
            if udp is not None:
                udp.close()
            self._error("udp", error)
            self._drop(now, retry_after=RECONNECT_EVERY)
            return False
        self._session, self._pending = self._pending, None
        self._udp = udp
        self.phase = OPEN
        self._last_state_t = now
        self._last_udp_t = None
        self._waiting_since = None
        self.esp_state = None
        self.failsafe_output = False
        self.fc_link_ok = None
        self.armed_mask = self._arm_requested = None
        self.sessions_opened += 1
        # The first frame under the new keys confirms a pending session, and
        # its CONFIG_ACK shows the ESP has the same keys (E4).
        self._confirmed = False
        self._confirm_by = now + HANDSHAKE_TIMEOUT
        self._send_tcp(self._session.config_get(CONFIRM_KEY, now))
        return True

    def _drop(self, now, retry_after=0.0):
        """Forget the session and close both sockets."""
        for sock in (self._tcp, self._udp):
            if sock is not None:
                sock.close()
        self._tcp = self._udp = None
        self._session = self._pending = self._reader = None
        self.phase = IDLE
        self.esp_state = None
        self.failsafe_output = False
        self.fc_link_ok = None
        self.armed_mask = self._arm_requested = None
        self._next_attempt = max(self._next_attempt, now + retry_after)

    def _check_confirmed(self, now):
        """E4 (ADR-005): no frame under this session's keys came back within
        HANDSHAKE_TIMEOUT of the HELLO_ACK. Either the HELLO_ACK was a replayed
        one (other keys than the ESP's) or the gateway lost the session:
        drop it and do the handshake again (never with ARM)."""
        if self._confirmed or now <= self._confirm_by:
            return
        self.sessions_unconfirmed += 1
        self._error(
            "session",
            "no answer to the first frame (replayed HELLO_ACK or lost "
            "session): new handshake",
        )
        self._drop(now)

    def _check_session(self, now):
        """A5: open a new session when this one is gone (ground_link.py rules).
        In STREAMING without TCP the session goes on over UDP (ADR-005);
        outside STREAMING the ESP ends it once TCP is closed and no UDP comes
        (ESP Link State Machine §1), and RESUME needs TCP anyway."""
        if self._last_udp_t is None or now - self._last_udp_t > SENDING_GAP:
            self._waiting_since = None  # not sending: no STATE is expected
        stale = (
            self._waiting_since is not None
            and now - max(self._waiting_since, self._last_state_t) > RECONNECT_AFTER
        )
        no_tcp = self._tcp is None and not self._maybe_streaming()
        if stale or no_tcp:
            self._drop(now)

    def _maybe_streaming(self):
        """Whether the ESP may be STREAMING in this session. Before the first
        STATE (an inactive node sends no setpoints, so it gets none) only an
        ARM of this session can have made it so."""
        if self.esp_state is not None:
            return self.esp_state == EspState.STREAMING.name
        return self.armed_mask is not None or self._arm_requested is not None

    # ---------- TCP ----------
    def _send_tcp(self, frame):
        if self._tcp is None:
            return False
        try:
            sent = self._tcp.send(frame)
        except OSError as error:
            self._error("tcp send", error)
            self._close_tcp()
            return False
        if sent != len(frame):
            # A non-blocking send can take only part of the frame when the
            # send buffer is nearly full (e.g. a Wi-Fi stall). The rest cannot
            # be sent later in order, and a cut frame breaks the ESP's framing
            # (it closes TCP), so close it here and say why.
            self._error("tcp send", f"short write, {sent} of {len(frame)} bytes")
            self._close_tcp()
            return False
        return True

    def _close_tcp(self):
        if self._tcp is not None:
            self._tcp.close()
        self._tcp = None

    def _recv_tcp(self, now):
        """Bytes waiting on TCP, b"" if none. A closed or broken connection is
        closed here; in a session the session goes on over UDP (ADR-005)."""
        chunks = []
        for _ in range(MAX_TCP_READS):  # the rest waits for the next poll
            if self._tcp is None:
                break
            try:
                data = self._tcp.recv(4096)
            except BlockingIOError:
                break
            except OSError as error:
                self._error("tcp recv", error)
                data = b""
            if not data:
                self._close_tcp()
                if self.phase != OPEN:
                    self._drop(now, retry_after=RECONNECT_EVERY)
                break
            chunks.append(data)
        return b"".join(chunks)

    def _read_tcp(self, now):
        data = self._recv_tcp(now)
        if not data:
            return
        try:
            frames = self._reader.feed(data)
        except ValueError as error:  # framing lost: close it, as the ESP does
            self._error("tcp framing", error)
            self._close_tcp()
            return
        self._handle_tcp_frames(frames, now)

    def _handle_tcp_frames(self, frames, now):
        for frame in frames:
            if self.phase != OPEN:  # a BYE ended the session: the rest is void
                return
            self._handle_tcp(frame, now)

    def _handle_tcp(self, frame, now):
        f = self._session.receive_tcp(frame)
        if f is None:
            return
        self._confirmed = True  # E4: the ESP has this session's keys
        t = f.header.type
        if t == MsgType.EVENT:
            event = decode_event(f.payload)
            if event is not None and event.code in _FAILSAFE_ON:
                self.failsafe_output = True
            elif event is not None and event.code == EventCode.LINK_RESUMED:
                self.failsafe_output = False
            elif event is not None and event.code == FC_LINK_LOST:
                self.fc_link_ok = False
            elif event is not None and event.code == FC_LINK_RESTORED:
                self.fc_link_ok = True
        elif t in (MsgType.ARM_CONTROL_ACK, MsgType.RESUME_ACK):
            result = decode_u8_payload(f.payload)
            self.last_ack = (MsgType(t).name, result)
            self._acks.append((MsgType(t).name, result, ack_reason(result)))
            if result == AckResult.OK:
                self.failsafe_output = False
            if t == MsgType.ARM_CONTROL_ACK:  # B1: the mask comes from our ARM
                # A refused ARM changes nothing on the ESP (sd::Link::handle_arm),
                # so it keeps a mask confirmed earlier in this session (e.g. a
                # second ~/arm_control before STATE showed STREAMING).
                if result == AckResult.OK and self._arm_requested is not None:
                    self.armed_mask = self._arm_requested
                self._arm_requested = None
        elif t == MsgType.BYE:  # the ESP ended the session: open a new one
            self._drop(now)

    # ---------- UDP ----------
    def _send_neutral(self, now):
        if self._session is None or self._udp is None:
            return
        try:
            self._udp.send(self._session.neutral(now))
        except OSError as error:  # e.g. ICMP refused: the ESP sees a link loss
            self._error("udp send", error)
        if self._waiting_since is None:
            self._waiting_since = now
        self._last_udp_t = now

    def _read_udp(self, now):
        """The newest accepted STATE as a drone-state dict, or None."""
        latest = None
        for _ in range(MAX_UDP_READS):  # the rest waits for the next poll
            try:
                data = self._udp.recv(512)
            except BlockingIOError:
                break
            except OSError as error:
                self._error("udp recv", error)
                break
            state = self._session.receive_udp_state(data)
            if state is not None:
                latest = state
        if latest is None:
            return None
        self._confirmed = True  # E4: an accepted STATE proves the keys too
        try:
            name = EspState(latest.esp_state).name
        except ValueError:
            return None  # not a valid state: the watchdog keeps counting
        self._last_state_t = now
        self._waiting_since = None
        self.esp_state = name
        if name == EspState.LINK_FAILSAFE.name:
            self.failsafe_output = True
        elif name == EspState.STREAMING.name:
            self.failsafe_output = False
        return {
            "esp_state": name,
            "override_active": bool(latest.override_active),
            "mask": self.armed_mask,  # B1: confirmed by ARM_CONTROL_ACK OK
            "airborne": False,  # B1: FC_HEALTH's armed flag, not decoded yet
            "failsafe_output": self.failsafe_output,
            "fc_link_ok": self.fc_link_ok,
            # D2: the FC values as the gateway copied them from its MSP polls
            # (roll / pitch 0.1 deg, heading deg, vbat 0.01 V). STATE has no
            # age for them: the node publishes them only while the FC link is
            # reported alive.
            "fc_values": {
                "roll": latest.roll,
                "pitch": latest.pitch,
                "heading": latest.heading,
                "vbat": latest.vbat,
            },
            "rx_time": now,
        }
