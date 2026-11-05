"""Receive rules of spec §4 (1 to 3) for one transport inside one session.

Frame checks, session check, the message type and its payload size
(REQ-COM-001, spec §3), the SETPOINT mode (§4 rule 4), then the sequence window
(ADR-003). Use one Receiver per transport (UDP control, TCP session): each has
its own seq (spec §1).

HELLO is not handled here: it arrives before a session exists and is tagged with
the PSK, so the link state machine checks it with decode_frame() and then calls
start_session(). Rejected frames are dropped and counted (REQ-COM-004); the
caller must not reset the link-loss timer for them (REQ-SAF-011).
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
    def __init__(self) -> None:
        self._key = b""
        self._session_id = 0
        self._in_session = False
        self._window = SeqWindow()
        self.counters = RxCounters()

    @property
    def in_session(self) -> bool:
        return self._in_session

    @property
    def session_id(self) -> int:
        return self._session_id

    @property
    def last_seq(self) -> int:
        return self._window.last

    @property
    def seq_counters(self) -> SeqCounters:
        return self._window.counters

    def start_session(self, session_id: int, session_key: bytes) -> bool:
        """Opens a session: the key and session_id every frame must match. Resets the window.

        session_id 0 is reserved for HELLO and is refused (returns False).
        """
        if len(session_key) != KEY_SIZE:
            raise ValueError(f"session key must be {KEY_SIZE} bytes")
        if session_id == 0:
            return False
        self._key = bytes(session_key)
        self._session_id = session_id
        self._in_session = True
        self._window.reset()
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

        # Type, payload size (REQ-COM-001) and SETPOINT mode (§4 rule 4), before
        # the window: a malformed frame must not use up its seq (REQ-COM-004).
        sizes = PAYLOAD_SIZES.get(frame.header.type)
        if sizes is None:
            return self._reject(RxResult.BAD_TYPE)
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
