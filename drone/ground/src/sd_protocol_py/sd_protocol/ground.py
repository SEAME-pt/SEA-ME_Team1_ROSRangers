"""The ground side of one session, without sockets (decision B2, 2026-11-10).

Shared by the flight_interface ESP backend (#103) and, after its move, the
bench tool scripts/ground_link.py, so the handshake, the sealing and the
receive checks exist once. Python only: the ESP side is sd::Link in C++.

  GroundSession  HELLO, HELLO_ACK check, keys, sealed frames, receive checks
  TcpReader      cuts the TCP byte stream into frames (OD-03)
  load_psk       the PSK from SEAME_PSK or ~/.config/seame-drone/secrets.json

`now` is monotonic seconds; it only fills the header's t_send_us, which is for
latency logging (spec §1).
"""

import json
import logging
import os
import pathlib
import re
import secrets

from sd_protocol.crypto import NONCE_SIZE, derive_keys
from sd_protocol.frame import HEADER_SIZE, Header, MsgType, encode_frame, frame_size
from sd_protocol.messages import (
    ByeReason,
    ConfigKv,
    Hello,
    Role,
    Transport,
    decode_hello_ack,
    decode_state,
    encode_config_kv,
    encode_hello,
    encode_u8_payload,
)
from sd_protocol.receiver import (
    HandshakeResult,
    Receiver,
    is_accepted,
    receive_handshake,
)
from sd_protocol.setpoint import Setpoint, encode_setpoint

SECRETS = pathlib.Path.home() / ".config" / "seame-drone" / "secrets.json"
_PSK_RE = re.compile(r"[0-9a-f]{64}")
_log = logging.getLogger(__name__)


def load_psk(path=None, environ=None, warn=None):
    """The PSK (ADR-002) as 32 bytes: SEAME_PSK, else "psk" in
    ~/.config/seame-drone/secrets.json (the same source as the gateway build).
    ValueError if there is none or it is not 64 hex characters.

    Decision E3: a secrets file that the group or other users can read gets a
    warning (not a refusal), as ssh does for keys. `warn(message)` receives it;
    without one it goes to this module's logger."""
    environ = os.environ if environ is None else environ
    text = environ.get("SEAME_PSK", "")
    if not text:
        secrets_file = pathlib.Path(path or SECRETS)
        try:
            text = json.loads(secrets_file.read_text()).get("psk", "")
        except (OSError, ValueError, AttributeError):
            text = ""
        else:
            _check_private(secrets_file, warn or _log.warning)
    text = str(text).strip().lower()
    if not _PSK_RE.fullmatch(text):
        raise ValueError(
            "no valid PSK: set SEAME_PSK or 'psk' in "
            "~/.config/seame-drone/secrets.json (64 hex characters)"
        )
    return bytes.fromhex(text)


def _check_private(path, warn):
    """Warn when `path` can be read by its group or by other users."""
    try:
        mode = path.stat().st_mode & 0o777
    except OSError:
        return
    if mode & 0o044:  # read permission for the group or others
        warn(
            f"{path} is readable by other users (mode {mode:o}): whoever reads "
            f"the PSK can open a session and send ARM; run: chmod 600 {path}"
        )


def t_send_us(now):
    """Header t_send_us (u32) from monotonic seconds."""
    return int(now * 1_000_000) & 0xFFFFFFFF


class GroundSession:
    """One ground session: HELLO, then frames sealed with ground_to_esp and
    checked with esp_to_ground by one Receiver per transport."""

    def __init__(self, psk, nonce=None):
        self.psk = psk
        self.nonce = nonce if nonce is not None else secrets.token_bytes(NONCE_SIZE)
        self.session_id = 0
        self.keys = None
        self.seq_tcp = 1  # HELLO used 1 (spec §1)
        self.seq_udp = 0
        self.rx_tcp = Receiver(Role.GROUND, Transport.TCP)
        self.rx_udp = Receiver(Role.GROUND, Transport.UDP)

    @property
    def is_open(self):
        return self.keys is not None

    @property
    def rejected(self):
        return self.rx_tcp.counters.rejected + self.rx_udp.counters.rejected

    # ---------- handshake ----------
    def hello(self, now):
        payload = encode_hello(Hello(proto=1, ground_nonce=self.nonce))
        header = Header(type=MsgType.HELLO, seq=1, t_send_us=t_send_us(now))
        return encode_frame(header, payload, self.psk)

    def take_ack(self, frame):
        """True if `frame` is this session's HELLO_ACK; then the keys are set."""
        result, f = receive_handshake(Role.GROUND, frame, self.psk)
        if result != HandshakeResult.ACCEPTED or f is None:
            return False
        ack = decode_hello_ack(f.payload)
        if ack is None:
            return False
        self.session_id = ack.session_id
        self.keys = derive_keys(self.psk, self.nonce, ack.esp_nonce)
        self.seq_tcp, self.seq_udp = 1, 0
        self.rx_tcp.start_session(self.session_id, self.keys.esp_to_ground)
        self.rx_udp.start_session(self.session_id, self.keys.esp_to_ground)
        return True

    # ---------- sending ----------
    def seal(self, msg_type, payload, seq, now):
        header = Header(
            type=msg_type,
            session_id=self.session_id,
            seq=seq,
            t_send_us=t_send_us(now),
        )
        return encode_frame(header, payload, self.keys.ground_to_esp)

    def tcp(self, msg_type, payload, now):
        """Any TCP message, with the next TCP seq."""
        self.seq_tcp += 1
        return self.seal(msg_type, payload, self.seq_tcp, now)

    def setpoint(self, now, mode=0, roll=0, pitch=0, yaw_rate=0, throttle=0):
        self.seq_udp += 1
        sp = Setpoint(
            mode=mode, roll=roll, pitch=pitch, yaw_rate=yaw_rate, throttle=throttle
        )
        return self.seal(MsgType.SETPOINT, encode_setpoint(sp), self.seq_udp, now)

    def neutral(self, now):
        """SETPOINT mode 0: neutral sticks, the heartbeat."""
        return self.setpoint(now, mode=0)

    def arm(self, mask, now):
        """ARM_CONTROL_REQUEST: only ever on an operator action (REQ-SAF-023)."""
        return self.tcp(MsgType.ARM_CONTROL_REQUEST, encode_u8_payload(mask), now)

    def resume_request(self, now):
        return self.tcp(MsgType.RESUME_REQUEST, b"", now)

    def bye(self, now, reason=ByeReason.NORMAL):
        return self.tcp(MsgType.BYE, encode_u8_payload(reason), now)

    def config_get(self, key, now):
        """CONFIG_GET: reads a value, changes nothing on the ESP."""
        return self.tcp(MsgType.CONFIG_GET, encode_config_kv(ConfigKv(key, 0)), now)

    # ---------- receiving ----------
    def receive_tcp(self, frame):
        """The accepted frame, or None (rejected: dropped and counted)."""
        result, f = self.rx_tcp.receive(frame)
        return f if is_accepted(result) else None

    def receive_udp(self, frame):
        result, f = self.rx_udp.receive(frame)
        return f if is_accepted(result) else None

    def receive_udp_state(self, frame):
        """The decoded STATE, or None for anything else or a rejected frame."""
        f = self.receive_udp(frame)
        if f is None or f.header.type != MsgType.STATE:
            return None
        return decode_state(f.payload)


class TcpReader:
    """Cuts TCP bytes into frames (OD-03). ValueError on a bad length."""

    def __init__(self):
        self.buf = b""

    def feed(self, data):
        self.buf += data
        frames = []
        while len(self.buf) >= HEADER_SIZE:
            size = frame_size(self.buf)
            if size == 0:
                raise ValueError("bad frame length from the ESP")
            if len(self.buf) < size:
                break
            frames.append(self.buf[:size])
            self.buf = self.buf[size:]
        return frames
