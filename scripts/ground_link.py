#!/usr/bin/env python3
"""Bench ground for the gateway firmware (#102): one Protocol Spec v0 session.

Opens a session over TCP (HELLO / HELLO_ACK), streams SETPOINT at 50 Hz over
UDP, and shows STATE, EVENTs and replies as they arrive. Every frame from the
ESP is checked with the same Receiver rules as the ESP's own (sd_protocol).

It is the Sprint 2 fault-injection demo tool: each key below is one fault, and
each must end in the defined safe state (ESP Link State Machine).

    a        ARM_CONTROL_REQUEST with --mask (operator action only, REQ-SAF-023)
    r        RESUME_REQUEST (needs 500 ms of neutral first: press n, wait)
    n        neutral sticks (SETPOINT mode 0, the default)
    m R      move: mode 1 with roll R (-1000..1000), the rest at 0
    s        stop UDP: simulated link loss; shows the failsafe time
    u        UDP on again
    g        garbage: bad tag, replayed seq, old seq, bad mode, random bytes
    h        replay the recorded HELLO on a second TCP connection
    c KEY    CONFIG_GET of KEY (hex, e.g. c 40)
    k        kill: close everything without BYE, as a crashed ground node
    b / q    BYE, then quit

PSK (ADR-002): SEAME_PSK, or "psk" in ~/.config/seame-drone/secrets.json, the
same one the gateway is built with. `--gen-psk` creates it there.

Safety: props off on the bench (Safety Rules #1, #13). The radio always wins:
keep a finger on SA. This tool never sends ARM by itself.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import secrets
import select
import socket
import sys
import time

REPO = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "drone" / "ground" / "src" / "sd_protocol_py"))

from sd_protocol.crypto import derive_keys  # noqa: E402
from sd_protocol.frame import (  # noqa: E402
    HEADER_SIZE,
    Header,
    MsgType,
    encode_frame,
    frame_size,
)
from sd_protocol.messages import (  # noqa: E402
    ConfigKv,
    EspState,
    EventCode,
    Hello,
    Role,
    Transport,
    decode_config_ack,
    decode_event,
    decode_hello_ack,
    decode_state,
    decode_u8_payload,
    encode_config_kv,
    encode_hello,
    encode_u8_payload,
)
from sd_protocol.receiver import (  # noqa: E402
    HandshakeResult,
    Receiver,
    is_accepted,
    receive_handshake,
)
from sd_protocol.setpoint import Setpoint, encode_setpoint  # noqa: E402

CONFIG_DIR = pathlib.Path.home() / ".config" / "seame-drone"
SECRETS = CONFIG_DIR / "secrets.json"
NONCE_SIZE = 16


def now_us() -> int:
    return time.monotonic_ns() // 1000 & 0xFFFFFFFF


PSK_RE = re.compile(r"[0-9a-f]{64}")


def parse_psk(text: str) -> bytes:
    text = text.strip().lower()
    if not PSK_RE.fullmatch(text):
        raise SystemExit(
            "No valid PSK: set SEAME_PSK or run `ground_link.py --gen-psk` (64 hex characters)."
        )
    return bytes.fromhex(text)


def read_secrets(path: pathlib.Path) -> dict:
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        raise SystemExit(f"{path} is not valid JSON: {e}") from None


def load_psk(path: pathlib.Path = SECRETS) -> bytes:
    return parse_psk(os.environ.get("SEAME_PSK") or read_secrets(path).get("psk", ""))


def gen_psk(path: pathlib.Path = SECRETS) -> str:
    """Adds a new PSK to secrets.json, never replacing one. The file keeps the
    Wi-Fi and OTA secrets too, so it is rewritten atomically, mode 600."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.parent.chmod(0o700)
    data = read_secrets(path)
    if data.get("psk"):
        raise SystemExit(f"{path} already has a PSK; delete it first to replace it.")
    data["psk"] = secrets.token_hex(32)
    tmp = path.with_suffix(".tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as fh:
        fh.write(json.dumps(data, indent=2) + "\n")
    os.replace(tmp, path)
    return data["psk"]


def describe_event(code: int) -> str:
    try:
        return EventCode(code).name
    except ValueError:
        return f"0x{code:04x}"


class GroundLink:
    """The protocol side of one ground session, without sockets (testable)."""

    def __init__(self, psk: bytes, nonce: bytes | None = None) -> None:
        self.psk = psk
        self.nonce = nonce if nonce is not None else secrets.token_bytes(NONCE_SIZE)
        self.session_id = 0
        self.keys = None
        self.seq_tcp = 1  # HELLO used 1 (spec §1)
        self.seq_udp = 0
        self.rx_tcp = Receiver(Role.GROUND, Transport.TCP)
        self.rx_udp = Receiver(Role.GROUND, Transport.UDP)
        self.last_setpoint: bytes = b""

    def hello(self) -> bytes:
        payload = encode_hello(Hello(proto=1, ground_nonce=self.nonce))
        return encode_frame(
            Header(type=MsgType.HELLO, seq=1, t_send_us=now_us()), payload, self.psk
        )

    def take_ack(self, frame: bytes) -> bool:
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

    def _seal(self, msg_type: int, payload: bytes, seq: int) -> bytes:
        header = Header(
            type=msg_type, session_id=self.session_id, seq=seq, t_send_us=now_us()
        )
        return encode_frame(header, payload, self.keys.ground_to_esp)

    def tcp(self, msg_type: int, payload: bytes = b"") -> bytes:
        self.seq_tcp += 1
        return self._seal(msg_type, payload, self.seq_tcp)

    def arm(self, mask: int) -> bytes:
        return self.tcp(MsgType.ARM_CONTROL_REQUEST, encode_u8_payload(mask))

    def setpoint(
        self, mode: int, roll: int = 0, pitch: int = 0, yaw: int = 0, throttle: int = 0
    ) -> bytes:
        self.seq_udp += 1
        sp = Setpoint(
            mode=mode, roll=roll, pitch=pitch, yaw_rate=yaw, throttle=throttle
        )
        self.last_setpoint = self._seal(
            MsgType.SETPOINT, encode_setpoint(sp), self.seq_udp
        )
        return self.last_setpoint

    def garbage(self) -> list[tuple[str, bytes]]:
        """Frames the ESP must drop and count, none of which may reset T_loss."""
        out = []
        sent = self.last_setpoint  # already accepted by the ESP
        fresh = self.setpoint(0)  # never sent with a valid tag
        out.append(("bad tag", fresh[:-1] + bytes([fresh[-1] ^ 1])))
        if sent:
            out.append(("replay (duplicate seq)", sent))
        old = self._seal(MsgType.SETPOINT, encode_setpoint(Setpoint(0, 0, 0, 0, 0)), 1)
        out.append(("old seq", old))
        bad_mode = bytearray(encode_setpoint(Setpoint(0, 0, 0, 0, 0)))
        bad_mode[0] = 7
        self.seq_udp += 1
        out.append(
            ("bad mode", self._seal(MsgType.SETPOINT, bytes(bad_mode), self.seq_udp))
        )
        out.append(("random bytes", secrets.token_bytes(38)))
        return out

    def describe_tcp(self, frame: bytes) -> str:
        result, f = self.rx_tcp.receive(frame)
        if not is_accepted(result) or f is None:
            return f"rejected TCP frame: {result.name}"
        t = f.header.type
        if t == MsgType.EVENT:
            e = decode_event(f.payload)
            return f"EVENT {describe_event(e.code)}" if e else "EVENT (bad payload)"
        if t in (MsgType.ARM_CONTROL_ACK, MsgType.RESUME_ACK, MsgType.BYE):
            v = decode_u8_payload(f.payload)
            return f"{MsgType(t).name} {v}"
        if t == MsgType.CONFIG_ACK:
            a = decode_config_ack(f.payload)
            return (
                f"CONFIG_ACK key 0x{a.key:02x} result {a.result} value {a.value}"
                if a
                else "CONFIG_ACK (bad payload)"
            )
        return f"{MsgType(t).name}"

    def state(self, frame: bytes):
        result, f = self.rx_udp.receive(frame)
        if not is_accepted(result) or f is None or f.header.type != MsgType.STATE:
            return None
        return decode_state(f.payload)


def state_line(st) -> str:
    try:
        name = EspState(st.esp_state).name
    except ValueError:
        name = str(st.esp_state)
    return (
        f"state={name} last_ctrl_seq={st.last_ctrl_seq} rx_rejected={st.rx_rejected} "
        f"msp_errors={st.msp_errors} roll={st.roll / 10:.1f} pitch={st.pitch / 10:.1f} "
        f"vbat={st.vbat / 100:.2f}V"
    )


class TcpReader:
    """Cuts TCP bytes into frames (OD-03)."""

    def __init__(self) -> None:
        self.buf = b""

    def feed(self, data: bytes) -> list[bytes]:
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


def log(t0: float, text: str) -> None:
    print(f"[{time.monotonic() - t0:8.3f}] {text}", flush=True)


def take_ack(link: GroundLink, frames: list[bytes]) -> list[bytes] | None:
    """Finds the HELLO_ACK among `frames`. Returns the frames after it (they
    came in the same TCP read, e.g. an EVENT), or None if there is none."""
    for i, frame in enumerate(frames):
        if link.take_ack(frame):
            return frames[i + 1 :]
    return None


STATE_STALE_S = 1.0  # no STATE for this long: say so, never reprint old values
RECONNECT_AFTER_S = 3.0  # no STATE for this long: the session is gone, open a new one
RECONNECT_EVERY_S = 2.0


def needs_new_session(stale_s: float, tcp_open: bool, esp_state: int | None) -> bool:
    """When the tool must open a new session (bench test ESP-S2-08):
    - no STATE for RECONNECT_AFTER_S: the session is gone or unreachable;
    - no TCP while the drone is not STREAMING: ARM and RESUME need TCP, so
      nothing could leave LINK_FAILSAFE or SESSION_IDLE (State Machine §1.1).
    In STREAMING without TCP the session keeps flying on UDP (ADR-005)."""
    if stale_s > RECONNECT_AFTER_S:
        return True
    return not tcp_open and esp_state is not None and esp_state != EspState.STREAMING


def connect(args, link: GroundLink, t0: float, fatal: bool = True, timeout: float = 3):
    """Opens a session: (tcp, reader, frames that came after HELLO_ACK). On
    failure it exits, or returns None when `fatal` is False (reconnect)."""
    try:
        tcp = socket.create_connection((args.host, args.port), timeout=timeout)
        tcp.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        hello = link.hello()
        tcp.sendall(hello)
        reader = TcpReader()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            data = tcp.recv(512)
            if not data:
                break
            rest = take_ack(link, reader.feed(data))
            if rest is not None:
                log(t0, f"session 0x{link.session_id:08x} open")
                tcp.setblocking(False)
                link.recorded_hello = hello
                return tcp, reader, rest
        tcp.close()
        why = "no HELLO_ACK: wrong PSK, or not the gateway firmware?"
    except (OSError, ValueError) as e:
        why = f"cannot open a session with {args.host}:{args.port}: {e}"
    if fatal:
        raise SystemExit(why)
    return None


class Stdin:
    """Lines from stdin without blocking; works for a terminal and a pipe."""

    def __init__(self) -> None:
        self.fd = sys.stdin.fileno()
        self.buf = b""
        self.open = True

    def lines(self) -> list[str]:
        data = os.read(self.fd, 4096)
        if not data:
            self.open = False  # the pipe ended: stop watching it
        self.buf += data
        *done, self.buf = self.buf.split(b"\n")
        return [line.decode(errors="replace") for line in done]


def run(args) -> None:
    link = GroundLink(load_psk())
    t0 = time.monotonic()
    tcp, reader, early = connect(args, link, t0)
    for frame in early:  # sent right after HELLO_ACK, e.g. EVENT 0x0104
        log(t0, link.describe_tcp(frame))
    udp = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    udp.setblocking(False)
    dest = (args.host, args.port)
    stdin = Stdin()
    mode, roll = 0, 0
    udp_on = True
    last_sent = None  # time of the last SETPOINT sent
    stopped_at = None  # the last SETPOINT before `s`
    next_sp = time.monotonic()
    next_print = 0.0
    last_state = None
    last_state_t = time.monotonic()  # grace period right after the handshake
    udp_error = ""  # logged once per change, not 50 times a second
    next_reconnect = 0.0
    unreachable_logged = False
    replay, replay_reader = None, TcpReader()
    print(__doc__.split("\n\n")[2], flush=True)

    def send_tcp(frame: bytes, what: str) -> None:
        if tcp is None:
            log(
                t0, f"not sent ({what}): no TCP; the session runs on UDP only (ADR-005)"
            )
            return
        try:
            tcp.sendall(frame)
            log(t0, f"sent {what}")
        except OSError as e:
            log(t0, f"TCP send failed ({what}): {e}")

    while True:
        now = time.monotonic()
        if udp_on and now >= next_sp:
            try:
                udp.sendto(link.setpoint(mode, roll=roll), dest)
                last_sent = now
                if udp_error:
                    log(t0, "UDP sending again")
                    udp_error = ""
            except OSError as e:
                if str(e) != udp_error:
                    log(t0, f"UDP send failed: {e} (the drone sees a link loss)")
                    udp_error = str(e)
            next_sp += 1 / args.rate
            if next_sp < now:
                next_sp = now + 1 / args.rate
        socks = [udp] + [x for x in (tcp, replay) if x is not None]
        if stdin.open:
            socks.append(stdin.fd)
        wait = 0.02 if not udp_on else max(0.0, min(next_sp - time.monotonic(), 0.02))
        ready, _, _ = select.select(socks, [], [], wait)
        if udp in ready:
            try:
                while True:
                    st = link.state(udp.recv(512))
                    if st is not None:
                        if time.monotonic() - last_state_t > STATE_STALE_S and tcp:
                            # Back after an outage: is TCP still alive? A dead
                            # connection answers this with a reset.
                            send_tcp(
                                link.tcp(
                                    MsgType.CONFIG_GET, encode_config_kv(ConfigKv(1, 0))
                                ),
                                "CONFIG_GET 0x01 (checking TCP after the outage)",
                            )
                        last_state = st
                        last_state_t = time.monotonic()
            except (BlockingIOError, ConnectionRefusedError):
                pass
        if tcp is not None and tcp in ready:
            try:
                data = tcp.recv(4096)
                frames = reader.feed(data)
            except BlockingIOError:
                data, frames = None, []
            except (OSError, ValueError) as e:
                data, frames = b"", []
                log(t0, f"TCP error: {e}")
            if data == b"":
                # ADR-005: a TCP drop does not end the session; UDP goes on.
                log(t0, "TCP closed: the session continues on UDP only (ADR-005)")
                tcp.close()
                tcp = None
            for frame in frames:
                text = link.describe_tcp(frame)
                if stopped_at is not None and "LINK_FAILSAFE_ENTERED" in text:
                    ms = (time.monotonic() - stopped_at) * 1000
                    text += f"  ({ms:.0f} ms after the last SETPOINT)"
                    stopped_at = None
                log(t0, text)
        if replay is not None and replay in ready:
            try:
                data = replay.recv(512)
            except OSError:
                data = b""
            if data:
                for frame in replay_reader.feed(data):
                    if frame[3] == MsgType.HELLO_ACK:
                        log(
                            t0,
                            "replay got a HELLO_ACK: a pending session nobody can confirm (expected)",
                        )
            else:
                log(t0, "replay connection closed by the ESP")
                replay.close()
                replay = None
        if now >= next_print:
            age = now - last_state_t
            if last_state is not None and age < STATE_STALE_S:
                log(t0, state_line(last_state))
            elif udp_on and age >= STATE_STALE_S:  # not before the first STATE
                log(t0, f"no STATE for {age:.1f} s: link down or session ended")
            next_print = now + args.print_every
        # The session is gone (for example the Wi-Fi dropped and the ESP ended
        # it): open a new one. Sticks go back to neutral, and ARM stays the
        # operator's action (REQ-SAF-023). Not during `s`, which is on purpose.
        esp_state = last_state.esp_state if last_state is not None else None
        if (
            udp_on
            and needs_new_session(now - last_state_t, tcp is not None, esp_state)
            and now >= next_reconnect
        ):
            next_reconnect = now + RECONNECT_EVERY_S
            fresh = GroundLink(link.psk)
            opened = connect(args, fresh, t0, fatal=False, timeout=1)
            if opened is None and not unreachable_logged:
                log(
                    t0,
                    "opening a new session: drone not reachable yet, retrying every 2 s",
                )
                unreachable_logged = True
            if opened is not None:
                unreachable_logged = False
                if tcp is not None:
                    tcp.close()
                link = fresh
                tcp, reader, early = opened
                for frame in early:
                    log(t0, link.describe_tcp(frame))
                mode, roll = 0, 0
                stopped_at = None
                last_state_t = time.monotonic()
                log(t0, "new session: sticks neutral; type a to ARM again")
        if stdin.open and stdin.fd in ready:
            for line in stdin.lines():
                words = line.split()
                if not words:
                    continue
                cmd = words[0]
                if cmd == "a":
                    send_tcp(
                        link.arm(args.mask), f"ARM_CONTROL_REQUEST mask {args.mask}"
                    )
                elif cmd == "r":
                    send_tcp(link.tcp(MsgType.RESUME_REQUEST), "RESUME_REQUEST")
                elif cmd == "n":
                    mode, roll = 0, 0
                    log(t0, "neutral sticks")
                elif cmd == "m":
                    mode, roll = 1, int(words[1]) if len(words) > 1 else 300
                    log(t0, f"mode 1, roll {roll}")
                elif cmd == "s":
                    udp_on = False
                    stopped_at = last_sent
                    log(t0, "UDP stopped (link loss)")
                elif cmd == "u":
                    udp_on = True
                    stopped_at = None
                    next_sp = time.monotonic()
                    log(t0, "UDP on")
                elif cmd == "g":
                    for name, frame in link.garbage():
                        udp.sendto(frame, dest)
                        log(t0, f"sent garbage: {name}")
                elif cmd == "h":
                    if replay is not None:
                        replay.close()
                    try:
                        replay = socket.create_connection(
                            (args.host, args.port), timeout=3
                        )
                        replay.sendall(link.recorded_hello)
                        replay.setblocking(False)
                        replay_reader = TcpReader()
                        log(t0, "replayed the recorded HELLO on a second connection")
                    except OSError as e:
                        replay = None
                        log(t0, f"replay connection refused: {e}")
                elif cmd == "c" and len(words) > 1:
                    key = int(words[1], 16)
                    frame = link.tcp(
                        MsgType.CONFIG_GET, encode_config_kv(ConfigKv(key, 0))
                    )
                    send_tcp(frame, f"CONFIG_GET 0x{key:02x}")
                elif cmd == "k":
                    log(t0, "killed: sockets closed without BYE")
                    os._exit(0)
                elif cmd in ("b", "q"):
                    send_tcp(link.tcp(MsgType.BYE, encode_u8_payload(0)), "BYE")
                    return
                else:
                    log(t0, f"unknown command {cmd!r}")


def main() -> None:
    p = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    p.add_argument("--host", default="192.168.4.1")
    p.add_argument("--port", type=int, default=5759)
    p.add_argument(
        "--rate", type=float, default=50.0, help="SETPOINT rate in Hz (spec: 50)"
    )
    p.add_argument(
        "--mask", type=int, default=8, help="override mask sent with ARM (ADR-010)"
    )
    p.add_argument(
        "--print-every", type=float, default=0.5, help="STATE print period in s"
    )
    p.add_argument(
        "--gen-psk", action="store_true", help="create a PSK in secrets.json and exit"
    )
    args = p.parse_args()
    if args.gen_psk:
        gen_psk()
        print(f"PSK stored in {SECRETS}. Rebuild and flash esp_gateway with it.")
        return
    run(args)


if __name__ == "__main__":
    main()
