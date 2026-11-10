"""Fake gateway ESP for the flight_interface tests (#103): Protocol Spec v0 on
localhost TCP + UDP (one port number for both, like the gateway).

Built on sd_protocol with Role.ESP, so every frame from the ground is checked
like the firmware checks it. It is a small model of the ESP Link State Machine
(NO_SESSION, SESSION_IDLE, STREAMING, LINK_FAILSAFE), enough for the ground
tests: HELLO / HELLO_ACK, ARM and RESUME with their ACKs, BYE both ways, T_loss,
the resume condition, EVENT 0x0102 / 0x0103 / 0x0104 and STATE at 25 Hz.
A HELLO while a session exists opens a PENDING session, as the gateway does
(ESP Link State Machine §1, #156): it takes over only when its first valid
frame arrives (BYE reason 3 to the old one), and is dropped after 2 s
without one. CONFIG_GET is answered (value 1 for any key).

No thread: the test calls step(now) with its own clock, so timing is exact.

`received` records EVERY frame that arrives, accepted or not, as
(transport, msg_type, session_id): the REQ-SAF-023 tests check that no
ARM_CONTROL_REQUEST ever reached the ESP, whatever its tag or session.
"""

import secrets
import socket

from sd_protocol.crypto import NONCE_SIZE, derive_keys
from sd_protocol.frame import HEADER_SIZE, Header, MsgType, encode_frame, frame_size
from sd_protocol.messages import (
    FW_VERSION_SIZE,
    AckResult,
    ByeReason,
    ConfigAck,
    EspState,
    Event,
    EventCode,
    HelloAck,
    Role,
    State,
    Transport,
    decode_config_kv,
    decode_hello,
    encode_config_ack,
    encode_event,
    encode_hello_ack,
    encode_state,
    encode_u8_payload,
)
from sd_protocol.receiver import (
    HandshakeResult,
    Receiver,
    is_accepted,
    receive_handshake,
)
from sd_protocol.setpoint import decode_setpoint

T_FRESH = 0.15
T_LOSS = 0.25
RESUME_NEUTRAL = 0.5
STATE_PERIOD = 0.04  # 25 Hz
PENDING_MAX = 2.0

TEST_PSK = bytes(range(32))


def _raw_type(frame):
    return frame[3] if len(frame) > 3 else None


def _raw_session(frame):
    return int.from_bytes(frame[8:12], "little") if len(frame) >= 12 else None


class FakeEsp:
    def __init__(self, psk=TEST_PSK, host="127.0.0.1"):
        self.psk = psk
        for _ in range(50):  # the same free port number for UDP and TCP
            udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            udp.bind((host, 0))
            port = udp.getsockname()[1]
            listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                listener.bind((host, port))
                break
            except OSError:
                udp.close()
                listener.close()
        else:  # pragma: no cover
            raise RuntimeError("no free port for the fake ESP")
        listener.listen(8)
        listener.setblocking(False)
        udp.setblocking(False)
        self.addr = (host, port)
        self._listener, self._udp = listener, udp
        self._conns = {}  # socket -> receive buffer
        self.received = []  # (transport, msg_type, session_id) of every frame
        self.hellos = 0
        self.setpoint_modes = []  # mode of every accepted SETPOINT
        self.byes_received = 0
        self.switches = 0  # pending sessions confirmed
        self.pending_expired = 0
        self._pending = None  # dict while a pending session waits
        self.send_state = True
        self.accept_udp = True
        self._reset_session()
        self.state = EspState.NO_SESSION
        self.failsafe_output = False
        self._next_state_t = 0.0

    # ---------- what the tests read ----------
    def count(self, msg_type):
        return sum(1 for _, t, _ in self.received if t == msg_type)

    @property
    def arm_requests(self):
        return self.count(MsgType.ARM_CONTROL_REQUEST)

    def setpoints(self):
        return self.count(MsgType.SETPOINT)

    # ---------- fault controls ----------
    def close_tcp(self):
        """The session's TCP connection drops (the session itself goes on)."""
        if self._session_conn is not None:
            self._close_conn(self._session_conn)

    def send_bye(self, reason=ByeReason.NORMAL):
        """The ESP ends the session."""
        if self._session_conn is not None:
            self._send_tcp(MsgType.BYE, encode_u8_payload(reason))
        self._end_session()

    def send_tcp_message(self, msg_type, payload):
        """Any ESP -> ground TCP message under the session keys."""
        self._send_tcp(msg_type, payload)

    def send_event(self, code):
        self._send_tcp(MsgType.EVENT, encode_event(Event(code=code)))

    def send_tcp_garbage(self, data=b"SD\x01\x40\x00\x00\xff\xff" + bytes(12)):
        """Bytes with a payload_len over the limit: the ground must close TCP."""
        if self._session_conn is not None:
            self._session_conn.send(data)

    def send_bad_state(self):
        """A STATE tagged with the wrong key: must be dropped, never refresh."""
        if self._keys is None or self._peer is None:
            return
        self._seq_udp += 1
        header = Header(MsgType.STATE, session_id=self.session_id, seq=self._seq_udp)
        frame = encode_frame(header, encode_state(self._state_msg()), bytes(32))
        self._udp.sendto(frame, self._peer)

    def send_udp_frame(self, msg_type, payload):
        """Any ESP -> ground UDP type under the session keys (e.g. FC_HEALTH
        0x81 of #179, which has no layout yet)."""
        if self._keys is None or self._peer is None:
            return
        self._seq_udp += 1
        self._udp.sendto(self._seal(msg_type, payload, self._seq_udp), self._peer)

    def send_state_value(self, esp_state):
        """One STATE with any esp_state byte, also an unknown one."""
        self._send_state(esp_state)

    def force_state(self, state, failsafe_output=None):
        """Put the model into a state, e.g. STREAMING as if the operator armed
        it from another tool. `now` for T_loss starts at the next setpoint."""
        self.state = EspState(state)
        if failsafe_output is not None:
            self.failsafe_output = failsafe_output
        self._last_ctrl_t = None

    def restart(self):
        """A reboot: every connection and the session are gone."""
        for conn in list(self._conns):
            self._close_conn(conn)
        self._pending = None
        self._reset_session()
        self.state = EspState.NO_SESSION
        self.failsafe_output = False

    def close(self):
        for conn in list(self._conns):
            self._close_conn(conn)
        self._listener.close()
        self._udp.close()

    # ---------- the model ----------
    def _reset_session(self):
        self.session_id = 0
        self._keys = None
        self._rx_tcp = Receiver(Role.ESP, Transport.TCP)
        self._rx_udp = Receiver(Role.ESP, Transport.UDP)
        self._seq_tcp = 1
        self._seq_udp = 0
        self._session_conn = None
        self._peer = None
        self._last_ctrl_t = None
        self._last_sp_t = None
        self._neutral_since = None

    def _end_session(self):
        if self.state in (EspState.STREAMING, EspState.LINK_FAILSAFE):
            self.failsafe_output = True  # ADR-004 output goes on (§1.1)
        conn = self._session_conn
        self._reset_session()
        self.state = EspState.NO_SESSION
        if conn is not None:
            self._close_conn(conn)

    def step(self, now):
        if self._pending is not None and now - self._pending["t"] >= PENDING_MAX:
            self._pending = None
            self.pending_expired += 1
        self._accept()
        for conn in list(self._conns):
            self._read_conn(conn, now)
        self._read_udp(now)
        if (
            self.state == EspState.STREAMING
            and self._last_ctrl_t is not None
            and now - self._last_ctrl_t >= T_LOSS
        ):
            self.state = EspState.LINK_FAILSAFE
            self.failsafe_output = True
            self.send_event(EventCode.LINK_FAILSAFE_ENTERED)
        if self._keys is not None and self.send_state and now >= self._next_state_t:
            self._send_state(self.state)
            self._next_state_t = now + STATE_PERIOD

    def _accept(self):
        while True:
            try:
                conn, _ = self._listener.accept()
            except BlockingIOError:
                return
            conn.setblocking(False)
            # No Nagle: a second small message must not wait for an ACK.
            conn.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            self._conns[conn] = b""

    def _close_conn(self, conn):
        self._conns.pop(conn, None)
        conn.close()
        if conn is self._session_conn:
            self._session_conn = None

    def _read_conn(self, conn, now):
        try:
            data = conn.recv(4096)
        except BlockingIOError:
            return
        except OSError:
            data = b""
        if not data:
            self._close_conn(conn)
            return
        buf = self._conns[conn] + data
        while len(buf) >= HEADER_SIZE:
            size = frame_size(buf)
            if size == 0:  # framing lost: close, as the gateway does
                self._close_conn(conn)
                return
            if len(buf) < size:
                break
            frame, buf = buf[:size], buf[size:]
            self.received.append((Transport.TCP, _raw_type(frame), _raw_session(frame)))
            self._handle_tcp_frame(conn, frame, now)
            if conn not in self._conns:
                return
        self._conns[conn] = buf

    def _handle_tcp_frame(self, conn, frame, now):
        if _raw_type(frame) == MsgType.HELLO:
            self._handle_hello(conn, frame, now)
            return
        pend = self._pending
        if pend is not None and conn is pend["conn"]:
            result, f = pend["rx_tcp"].receive(frame)
            if not is_accepted(result):
                return
            self._confirm_pending()
        elif conn is self._session_conn and self._keys is not None:
            result, f = self._rx_tcp.receive(frame)
            if not is_accepted(result):
                return
        else:
            self._close_conn(conn)  # a connection without a session carries HELLO only
            return
        t = f.header.type
        if t == MsgType.ARM_CONTROL_REQUEST:
            if self.state == EspState.SESSION_IDLE:
                self.state = EspState.STREAMING
                self.failsafe_output = False
                self._last_ctrl_t = now
                self._send_tcp(MsgType.ARM_CONTROL_ACK, encode_u8_payload(AckResult.OK))
            else:
                self._send_tcp(
                    MsgType.ARM_CONTROL_ACK, encode_u8_payload(AckResult.WRONG_STATE)
                )
        elif t == MsgType.RESUME_REQUEST:
            self._handle_resume(now)
        elif t == MsgType.CONFIG_GET:
            kv = decode_config_kv(f.payload)
            ack = ConfigAck(key=kv.key, result=0, value=1)
            self._send_tcp(MsgType.CONFIG_ACK, encode_config_ack(ack))
        elif t == MsgType.BYE:
            self.byes_received += 1
            self._end_session()

    def _handle_resume(self, now):
        if self.state != EspState.LINK_FAILSAFE:
            result = AckResult.WRONG_STATE
        elif (
            self._neutral_since is not None
            and now - self._neutral_since >= RESUME_NEUTRAL
            and self._last_sp_t is not None
            and now - self._last_sp_t < T_FRESH
        ):
            result = AckResult.OK
        else:
            result = AckResult.RESUME_CONDITION_NOT_MET
        self._send_tcp(MsgType.RESUME_ACK, encode_u8_payload(result))
        if result == AckResult.OK:
            self.state = EspState.STREAMING
            self.failsafe_output = False
            self._last_ctrl_t = now
            self.send_event(EventCode.LINK_RESUMED)

    def _new_session(self, ground_nonce, conn, now):
        session_id = secrets.randbits(31) + 1
        esp_nonce = secrets.token_bytes(NONCE_SIZE)
        keys = derive_keys(self.psk, ground_nonce, esp_nonce)
        rx_tcp = Receiver(Role.ESP, Transport.TCP)
        rx_udp = Receiver(Role.ESP, Transport.UDP)
        rx_tcp.start_session(session_id, keys.ground_to_esp)
        rx_udp.start_session(session_id, keys.ground_to_esp)
        ack = HelloAck(session_id, esp_nonce, b"fake-esp".ljust(FW_VERSION_SIZE, b"\0"))
        header = Header(MsgType.HELLO_ACK, session_id=session_id, seq=1)
        conn.send(encode_frame(header, encode_hello_ack(ack), self.psk))
        return {
            "id": session_id,
            "keys": keys,
            "rx_tcp": rx_tcp,
            "rx_udp": rx_udp,
            "conn": conn,
            "t": now,
        }

    def _adopt(self, session):
        self._reset_session()
        self.session_id = session["id"]
        self._keys = session["keys"]
        self._rx_tcp, self._rx_udp = session["rx_tcp"], session["rx_udp"]
        self._session_conn = session["conn"]
        self.state = EspState.SESSION_IDLE
        if self.failsafe_output:
            self.send_event(EventCode.FAILSAFE_OUTPUT_ACTIVE)

    def _handle_hello(self, conn, frame, now):
        result, f = receive_handshake(Role.ESP, frame, self.psk)
        if result != HandshakeResult.ACCEPTED:
            return
        hello = decode_hello(f.payload)
        if hello is None or hello.proto != 1:
            return
        self.hellos += 1
        session = self._new_session(hello.ground_nonce, conn, now)
        if self._keys is None:  # nothing to protect: open at once
            self._adopt(session)
        else:  # pending until its first valid frame (a later HELLO replaces it)
            self._pending = session

    def _confirm_pending(self):
        """The pending session's first valid frame: it becomes the session."""
        session, self._pending = self._pending, None
        self.switches += 1
        if self.state in (EspState.STREAMING, EspState.LINK_FAILSAFE):
            self.failsafe_output = True  # a switch from control: ADR-004 output
        old_conn = self._session_conn
        if old_conn is not None and old_conn is not session["conn"]:
            self._send_tcp(MsgType.BYE, encode_u8_payload(ByeReason.REPLACED))
            self._close_conn(old_conn)
        self._adopt(session)

    def _read_udp(self, now):
        while True:
            try:
                data, addr = self._udp.recvfrom(1024)
            except BlockingIOError:
                return
            self.received.append((Transport.UDP, _raw_type(data), _raw_session(data)))
            if not self.accept_udp:
                continue
            pend = self._pending
            if pend is not None and _raw_session(data) == pend["id"]:
                result, f = pend["rx_udp"].receive(data)
                if not is_accepted(result):
                    continue
                self._confirm_pending()
            elif self._keys is not None:
                result, f = self._rx_udp.receive(data)
                if not is_accepted(result):
                    continue
            else:
                continue
            if f.header.type != MsgType.SETPOINT:
                continue
            self._peer = addr
            sp = decode_setpoint(f.payload)
            self.setpoint_modes.append(sp.mode)
            gap_ok = self._last_sp_t is not None and now - self._last_sp_t < T_FRESH
            if sp.mode == 0:
                if self._neutral_since is None or not gap_ok:
                    self._neutral_since = now
            else:
                self._neutral_since = None
            self._last_sp_t = now
            if self.state == EspState.STREAMING:
                self._last_ctrl_t = now

    # ---------- sending ----------
    def _seal(self, msg_type, payload, seq):
        header = Header(msg_type, session_id=self.session_id, seq=seq)
        return encode_frame(header, payload, self._keys.esp_to_ground)

    def _send_tcp(self, msg_type, payload):
        if self._session_conn is None or self._keys is None:
            return
        self._seq_tcp += 1
        self._session_conn.send(self._seal(msg_type, payload, self._seq_tcp))

    def _state_msg(self, esp_state=None):
        return State(
            esp_state=int(self.state if esp_state is None else esp_state),
            override_active=1 if self.state == EspState.STREAMING else 0,
            last_ctrl_seq=0,
            last_ctrl_t_send_us=0,
            roll=0,
            pitch=0,
            heading=0,
            vbat=1200,
            flow_x=0,
            flow_y=0,
            flow_quality=0,
            flow_status=4,
            rx_rejected=0,
            msp_errors=0,
        )

    def _send_state(self, esp_state):
        if self._keys is None or self._peer is None:
            return
        self._seq_udp += 1
        payload = encode_state(self._state_msg(esp_state))
        try:
            self._udp.sendto(
                self._seal(MsgType.STATE, payload, self._seq_udp), self._peer
            )
        except OSError:  # pragma: no cover - the ground's socket is gone
            pass
