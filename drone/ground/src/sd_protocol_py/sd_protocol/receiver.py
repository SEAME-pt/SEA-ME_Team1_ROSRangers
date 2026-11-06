"""Receive rules of spec §4 for one side and one transport inside one session.

Order (spec §4, v1.3): frame checks (rule 1), session (rule 2), message type,
transport (§2), direction (§2: only what the other side sends, #152), payload
size (§3, REQ-COM-001), SETPOINT mode (rule 4), then the sequence window
(rule 3, ADR-003). A frame refused before the window changes no state. Use one
Receiver per transport (UDP control, TCP session): each has its own seq, and
each accepts only the types of its transport, so a frame captured on one cannot
be replayed on the other.

HELLO and HELLO_ACK are not handled by a Receiver: they arrive before a session
exists and are tagged with the PSK, so they go to receive_handshake() (spec §1).
Rejected frames are dropped and counted (REQ-COM-004); the caller must not
reset the link-loss timer for them (REQ-SAF-011).
"""

from dataclasses import dataclass
from enum import IntEnum

from sd_protocol.crypto import KEY_SIZE
from sd_protocol.frame import Frame, MsgType, RxError, decode_frame
from sd_protocol.messages import (
    CONFIG_ACK_SIZE,
    CONFIG_KV_SIZE,
    EVENT_MAX_SIZE,
    EVENT_MIN_SIZE,
    HELLO_ACK_SIZE,
    HELLO_SIZE,
    STATE_SIZE,
    Role,
    Transport,
    decode_hello_ack,
    receives,
    transport_of,
)
from sd_protocol.seq_window import SeqCounters, SeqResult, SeqWindow
from sd_protocol.setpoint import SETPOINT_SIZE


class RxResult(IntEnum):
    """Result of Receiver.receive. Only ACCEPTED and ACCEPTED_GAP may be used."""

    ACCEPTED = 0
    ACCEPTED_GAP = 1  # accepted; some seq numbers were skipped (counted, not an error)
    NO_SESSION = 2  # no session is open
    TRUNCATED = 3
    BAD_MAGIC = 4
    BAD_VERSION = 5
    BAD_LENGTH = 6
    BAD_TAG = 7
    WRONG_SESSION = 8  # session_id is not the current session (spec §4 rule 2)
    DUPLICATE_SEQ = 9
    OLD_SEQ = 10
    SEQ_TOO_HIGH = 11  # seq >= 2^31: the sender must open a new session
    BAD_TYPE = 12  # message type not in spec §3 (REQ-COM-001)
    BAD_PAYLOAD_SIZE = 13  # payload_len does not match the type (spec §3, REQ-COM-001)
    BAD_MODE = 14  # SETPOINT mode other than 0 or 1 (spec §4 rule 4, REQ-COM-002)
    WRONG_TRANSPORT = 15  # a TCP type on the UDP Receiver or the other way (spec §2)
    WRONG_DIRECTION = 16  # a type this side never receives: its own, sent back (§2)


def is_accepted(result: RxResult) -> bool:
    return result in (RxResult.ACCEPTED, RxResult.ACCEPTED_GAP)


@dataclass
class RxCounters:
    accepted: int = 0
    rejected: int = 0  # every reject below; reported as STATE.rx_rejected
    no_session: int = 0
    truncated: int = 0
    bad_magic: int = 0
    bad_version: int = 0
    bad_length: int = 0
    bad_tag: int = 0
    wrong_session: int = 0
    duplicate_seq: int = 0
    old_seq: int = 0
    seq_too_high: int = 0
    bad_type: int = 0
    bad_payload_size: int = 0
    bad_mode: int = 0
    wrong_transport: int = 0
    wrong_direction: int = 0


_FRAME_REJECTS = {
    RxError.TRUNCATED: RxResult.TRUNCATED,
    RxError.BAD_MAGIC: RxResult.BAD_MAGIC,
    RxError.BAD_VERSION: RxResult.BAD_VERSION,
    RxError.BAD_LENGTH: RxResult.BAD_LENGTH,
    RxError.BAD_TAG: RxResult.BAD_TAG,
}

# Spec §3: payload size per type, as (min, max). Any other size is rejected.
PAYLOAD_SIZES = {
    MsgType.HELLO: (HELLO_SIZE, HELLO_SIZE),
    MsgType.HELLO_ACK: (HELLO_ACK_SIZE, HELLO_ACK_SIZE),
    MsgType.ARM_CONTROL_REQUEST: (1, 1),
    MsgType.ARM_CONTROL_ACK: (1, 1),
    MsgType.RESUME_REQUEST: (0, 0),
    MsgType.RESUME_ACK: (1, 1),
    MsgType.BYE: (1, 1),
    MsgType.CONFIG_GET: (CONFIG_KV_SIZE, CONFIG_KV_SIZE),
    MsgType.CONFIG_SET: (CONFIG_KV_SIZE, CONFIG_KV_SIZE),
    MsgType.CONFIG_ACK: (CONFIG_ACK_SIZE, CONFIG_ACK_SIZE),
    MsgType.EVENT: (EVENT_MIN_SIZE, EVENT_MAX_SIZE),
    MsgType.SETPOINT: (SETPOINT_SIZE, SETPOINT_SIZE),
    MsgType.STATE: (STATE_SIZE, STATE_SIZE),
}

SETPOINT_MODES = (0, 1)  # 0 neutral, 1 attitude (spec §3)

_SEQ_RESULTS = {
    SeqResult.ACCEPTED: RxResult.ACCEPTED,
    SeqResult.ACCEPTED_GAP: RxResult.ACCEPTED_GAP,
    SeqResult.DUPLICATE: RxResult.DUPLICATE_SEQ,
    SeqResult.OLD: RxResult.OLD_SEQ,
    SeqResult.NEED_NEW_SESSION: RxResult.SEQ_TOO_HIGH,
}


class Receiver:
    def __init__(self, role: Role, transport: Transport) -> None:
        """The role and the transport are fixed for the Receiver's lifetime (#152)."""
        self._role = Role(role)  # ValueError for anything else
        self._transport = Transport(transport)
        self._key = b""
        self._session_id = 0
        self._in_session = False
        self._window = SeqWindow()
        self.counters = RxCounters()

    @property
    def role(self) -> Role:
        return self._role

    @property
    def in_session(self) -> bool:
        return self._in_session

    @property
    def session_id(self) -> int:
        return self._session_id

    @property
    def transport(self) -> Transport:
        return self._transport

    @property
    def last_seq(self) -> int:
        return self._window.last

    @property
    def seq_counters(self) -> SeqCounters:
        return self._window.counters

    def start_session(self, session_id: int, rx_key: bytes) -> bool:
        """Opens a session: the key and session_id every frame must match.

        `rx_key` is this side's receiving direction key from derive_keys()
        (spec §1, #150, #152): ground_to_esp on the ESP, esp_to_ground on the
        ground; never the session key itself. The window starts at 1 on TCP
        (HELLO and HELLO_ACK used seq 1, spec §1) and at 0 on UDP.
        session_id 0 is reserved for HELLO and is refused (returns False); then
        nothing changes, and a session that was open stays open. Never open the
        same (session_id, key) twice: the window restarts, so every frame of that
        session would be accepted again.
        """
        if len(rx_key) != KEY_SIZE:
            raise ValueError(f"key must be {KEY_SIZE} bytes")
        if session_id == 0:
            return False
        self._key = bytes(rx_key)
        self._session_id = session_id
        self._in_session = True
        self._window.reset(1 if self._transport is Transport.TCP else 0)
        return True

    def end_session(self) -> None:
        self._key = b""
        self._session_id = 0
        self._in_session = False
        self._window.reset()

    def _reject(self, result: RxResult) -> tuple[RxResult, None]:
        self.counters.rejected += 1
        name = result.name.lower()
        setattr(self.counters, name, getattr(self.counters, name) + 1)
        return result, None

    def receive(self, buf: bytes) -> tuple[RxResult, Frame | None]:
        """Returns (ACCEPTED or ACCEPTED_GAP, frame) or (the reason, None)."""
        if not self._in_session:
            return self._reject(RxResult.NO_SESSION)

        # Rule 1: length, magic, version, tag.
        error, frame = decode_frame(buf, self._key)
        if error is not RxError.OK:
            return self._reject(_FRAME_REJECTS[error])

        # Rule 2: the current session only.
        if frame.header.session_id != self._session_id:
            return self._reject(RxResult.WRONG_SESSION)

        # Type, transport (§2), payload size (REQ-COM-001) and SETPOINT mode
        # (§4 rule 4), before the window: a malformed frame must not use up its
        # seq (REQ-COM-004).
        sizes = PAYLOAD_SIZES.get(frame.header.type)
        if sizes is None:
            return self._reject(RxResult.BAD_TYPE)
        # Both transports use the same direction key: without this, a frame
        # captured on TCP could be replayed on UDP, or the other way.
        if transport_of(frame.header.type) is not self._transport:
            return self._reject(RxResult.WRONG_TRANSPORT)
        # Direction (#152): only what the other side sends. A node's own frame
        # sent back to it is refused here even if both ends got the keys wrong.
        if not receives(self._role, frame.header.type):
            return self._reject(RxResult.WRONG_DIRECTION)
        if not sizes[0] <= len(frame.payload) <= sizes[1]:
            return self._reject(RxResult.BAD_PAYLOAD_SIZE)
        if (
            frame.header.type == MsgType.SETPOINT
            and frame.payload[0] not in SETPOINT_MODES
        ):
            return self._reject(RxResult.BAD_MODE)

        # Rule 3: newer than the last accepted message (ADR-003).
        result = _SEQ_RESULTS[self._window.accept(frame.header.seq)]
        if not is_accepted(result):
            return self._reject(result)
        self.counters.accepted += 1
        return result, frame


class HandshakeResult(IntEnum):
    """Result of receive_handshake (spec §1, v1.3). Only ACCEPTED may be used."""

    ACCEPTED = 0
    TRUNCATED = 1
    BAD_MAGIC = 2
    BAD_VERSION = 3
    BAD_LENGTH = 4
    BAD_TAG = 5  # not tagged with the PSK
    WRONG_DIRECTION = 6  # not the one handshake type this side receives
    BAD_PAYLOAD_SIZE = 7  # HELLO is 17 bytes, HELLO_ACK 32
    # HELLO: session_id not 0; HELLO_ACK: 0, or header and payload session_id differ
    WRONG_SESSION = 8
    BAD_SEQ = 9  # seq is not 1


_HANDSHAKE_FRAME_REJECTS = {
    RxError.TRUNCATED: HandshakeResult.TRUNCATED,
    RxError.BAD_MAGIC: HandshakeResult.BAD_MAGIC,
    RxError.BAD_VERSION: HandshakeResult.BAD_VERSION,
    RxError.BAD_LENGTH: HandshakeResult.BAD_LENGTH,
    RxError.BAD_TAG: HandshakeResult.BAD_TAG,
}


def receive_handshake(
    role: Role, buf: bytes, psk: bytes
) -> tuple[HandshakeResult, Frame | None]:
    """Checks one handshake frame for `role` before any session (spec §1, #152).

    The ESP accepts only HELLO (session_id 0) and the ground only HELLO_ACK
    (header session_id = payload session_id, not 0), both tagged with the PSK
    and with seq 1, so a reflected or out-of-place handshake frame does nothing.
    The ESP sends every HELLO here, also during a session. Checked in this
    order: tag, type, payload size, session_id, seq. Returns (ACCEPTED, frame)
    or (the reason, None). Not counted: the caller (#102) counts or rate-limits
    handshake attempts.
    """
    role = Role(role)
    error, frame = decode_frame(buf, psk)
    if error is not RxError.OK:
        return _HANDSHAKE_FRAME_REJECTS[error], None
    expected = MsgType.HELLO if role is Role.ESP else MsgType.HELLO_ACK
    if frame.header.type != expected:
        return HandshakeResult.WRONG_DIRECTION, None
    if len(frame.payload) != (HELLO_SIZE if role is Role.ESP else HELLO_ACK_SIZE):
        return HandshakeResult.BAD_PAYLOAD_SIZE, None
    session_id = frame.header.session_id
    if role is Role.ESP:
        if session_id != 0:
            return HandshakeResult.WRONG_SESSION, None
    elif session_id == 0 or decode_hello_ack(frame.payload).session_id != session_id:
        return HandshakeResult.WRONG_SESSION, None
    if frame.header.seq != 1:
        return HandshakeResult.BAD_SEQ, None
    return HandshakeResult.ACCEPTED, frame
