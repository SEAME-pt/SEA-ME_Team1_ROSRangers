"""Minimal MSP v1 client (Betaflight) over a serial port or UDP.

`Client.request` sends read-only requests only. The single write command
(MSP_SET_RAW_RC, for the channel-order bench test) goes through `Client.write`,
which refuses to run unless the client was created with `allow_write=True`.
"""

import socket
import struct
import time

import serial

# MSP v1 command IDs (Betaflight msp_protocol.h)
MSP_API_VERSION = 1
MSP_FC_VARIANT = 2
MSP_FC_VERSION = 3
MSP_BOARD_INFO = 4
MSP_STATUS = 101
MSP_RC = 105
MSP_ATTITUDE = 108
MSP_ANALOG = 110
MSP_SET_RAW_RC = 200

READ_ONLY_COMMANDS = {
    MSP_API_VERSION,
    MSP_FC_VARIANT,
    MSP_FC_VERSION,
    MSP_BOARD_INFO,
    MSP_STATUS,
    MSP_RC,
    MSP_ATTITUDE,
    MSP_ANALOG,
}
WRITE_COMMANDS = {MSP_SET_RAW_RC}

# Betaflight keeps rcData as roll, pitch, yaw, throttle, then AUX1...
RC_NAMES = ("roll", "pitch", "yaw", "throttle")


def checksum(data: bytes) -> int:
    c = 0
    for b in data:
        c ^= b
    return c


def encode_request(cmd: int, payload: bytes = b"") -> bytes:
    """Build an MSP v1 request: '$M<' size cmd payload checksum."""
    if not 0 <= cmd <= 255:
        raise ValueError("MSP v1 command must be 0..255")
    if len(payload) > 255:
        raise ValueError("MSP v1 payload must be at most 255 bytes")
    body = bytes([len(payload), cmd]) + payload
    return b"$M<" + body + bytes([checksum(body)])


def encode_response(cmd: int, payload: bytes = b"", error: bool = False) -> bytes:
    """Build an MSP v1 response, as the flight controller would. Used by tests and fake-fc."""
    body = bytes([len(payload), cmd]) + payload
    return (b"$M!" if error else b"$M>") + body + bytes([checksum(body)])


class Frame:
    def __init__(self, direction: str, cmd: int, payload: bytes):
        self.direction = direction  # '<' request, '>' response, '!' error
        self.cmd = cmd
        self.payload = payload

    def __repr__(self):
        return (
            f"Frame({self.direction!r}, cmd={self.cmd}, payload={self.payload.hex()})"
        )


class Parser:
    """Byte-by-byte MSP v1 parser. Bad checksums are counted and dropped."""

    def __init__(self):
        self.bad_checksums = 0
        self._reset()

    def _reset(self):
        self._state = 0
        self._dir = ""
        self._size = 0
        self._cmd = 0
        self._buf = bytearray()

    def feed(self, data: bytes):
        frames = []
        for b in data:
            s = self._state
            if s == 0:
                self._state = 1 if b == ord("$") else 0
            elif s == 1:
                self._state = 2 if b == ord("M") else (1 if b == ord("$") else 0)
            elif s == 2:
                if b in (ord("<"), ord(">"), ord("!")):
                    self._dir = chr(b)
                    self._state = 3
                else:
                    self._reset()
                    self._state = 1 if b == ord("$") else 0
            elif s == 3:
                self._size = b
                self._state = 4
            elif s == 4:
                self._cmd = b
                self._state = 5 if self._size else 6
            elif s == 5:
                self._buf.append(b)
                if len(self._buf) == self._size:
                    self._state = 6
            elif s == 6:
                body = bytes([self._size, self._cmd]) + bytes(self._buf)
                if checksum(body) == b:
                    frames.append(Frame(self._dir, self._cmd, bytes(self._buf)))
                else:
                    self.bad_checksums += 1
                self._reset()
        return frames


# ---------- payload decoders ----------


def decode(cmd: int, p: bytes) -> dict:
    if cmd == MSP_API_VERSION and len(p) >= 3:
        return {"protocol": p[0], "api": f"{p[1]}.{p[2]}"}
    if cmd == MSP_FC_VARIANT and len(p) >= 4:
        return {"variant": p[:4].decode(errors="replace")}
    if cmd == MSP_FC_VERSION and len(p) >= 3:
        return {"version": f"{p[0]}.{p[1]}.{p[2]}"}
    if cmd == MSP_BOARD_INFO and len(p) >= 4:
        return {"board": p[:4].decode(errors="replace")}
    if cmd == MSP_STATUS and len(p) >= 10:
        return decode_status(p)
    if cmd == MSP_RC:
        return {"channels": list(struct.unpack_from(f"<{len(p) // 2}H", p))}
    if cmd == MSP_ATTITUDE and len(p) >= 6:
        roll, pitch, yaw = struct.unpack_from("<hhh", p)
        return {"roll_deg": roll / 10, "pitch_deg": pitch / 10, "yaw_deg": yaw}
    if cmd == MSP_ANALOG and len(p) >= 1:
        out = {"vbat_v": p[0] / 10}
        if len(p) >= 5:
            out["rssi"] = struct.unpack_from("<H", p, 3)[0]  # 0..1023
        return out
    return {"raw": p.hex()}


def decode_status(p: bytes) -> dict:
    """MSP_STATUS. Betaflight 4.x layout; the fields after the first 10 bytes are optional."""
    cycle, i2c_err, sensors, flags = struct.unpack_from("<HHHI", p)
    out = {
        "cycle_time_us": cycle,
        "i2c_errors": i2c_err,
        "sensors_mask": sensors,
        "flight_mode_flags": flags,
        "armed": bool(flags & 1),
    }
    if len(p) >= 13:
        out["cpu_load"] = struct.unpack_from("<H", p, 11)[0]
    # 13: gyro cycle time (u16), 15: count of extra flight-mode bytes, then those bytes
    if len(p) >= 16:
        i = 16 + p[15]
        if len(p) >= i + 5:
            out["arming_disable_flags"] = struct.unpack_from("<I", p, i + 1)[0]
        if len(p) >= i + 6:
            out["reboot_required"] = bool(p[i + 5] & 1)
    return out


def encode_rc(channels) -> bytes:
    """MSP_SET_RAW_RC / MSP_RC payload: one little-endian u16 per channel."""
    return struct.pack(f"<{len(channels)}H", *channels)


# ---------- transports ----------


class SerialTransport:
    def __init__(self, port: str, baud: int = 115200):
        self.ser = serial.Serial(port, baud, timeout=0.001)

    def send(self, data: bytes):
        self.ser.write(data)

    def recv(self) -> bytes:
        return self.ser.read(512)

    def close(self):
        self.ser.close()


class UdpTransport:
    """The ESP (AT+SAVETRANSLINK, UDP) sends to a fixed PC port and listens on its own port."""

    def __init__(self, host: str, port: int, local_port: int):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.bind(("0.0.0.0", local_port))
        self.sock.settimeout(0.001)

    def send(self, data: bytes):
        self.sock.sendto(data, self.addr)

    def recv(self) -> bytes:
        try:
            return self.sock.recv(2048)
        except socket.timeout:
            return b""

    def close(self):
        self.sock.close()


class Client:
    def __init__(
        self,
        transport,
        trace: bool = False,
        allow_write: bool = False,
        loopback: bool = False,
    ):
        self.t = transport
        self.parser = Parser()
        self.trace = trace
        self.allow_write = allow_write
        self.loopback = (
            loopback  # TX wired to RX on the ESP: our own request comes back
        )

    def is_answer(self, f: Frame) -> bool:
        return f.direction in (">!<" if self.loopback else ">!")

    def request(self, cmd: int, timeout: float = 0.5):
        """Send one read-only request. Returns (Frame or None, round-trip seconds)."""
        if cmd not in READ_ONLY_COMMANDS:
            raise ValueError(f"MSP command {cmd} is not in the read-only list")
        return self._exchange(cmd, b"", timeout)

    def write(self, cmd: int, payload: bytes, timeout: float = 0.5):
        """Send one write command. Only for clients created with allow_write=True."""
        if not self.allow_write or cmd not in WRITE_COMMANDS:
            raise ValueError(f"MSP write {cmd} is not allowed on this client")
        return self._exchange(cmd, payload, timeout)

    def _exchange(self, cmd: int, payload: bytes, timeout: float):
        request = encode_request(cmd, payload)
        self.t.send(request)
        if self.trace:
            print(f"PC -> ESP/FC: {request.hex(' ')}", flush=True)
        t0 = time.perf_counter()
        while time.perf_counter() - t0 < timeout:
            for f in self.parser.feed(self.t.recv()):
                if f.cmd == cmd and self.is_answer(f):
                    if self.trace:
                        frame = encode_response(
                            f.cmd, f.payload, error=f.direction == "!"
                        )
                        print(f"PC <- ESP/FC: {frame.hex(' ')}", flush=True)
                    return f, time.perf_counter() - t0
        if self.trace:
            print("PC <- ESP/FC: timeout", flush=True)
        return None, None

    def close(self):
        self.t.close()
