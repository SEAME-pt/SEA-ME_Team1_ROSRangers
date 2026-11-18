"""Test helpers: an in-memory FC that answers MSP, and a reader for --trace logs.

FakeFc is a transport for msp.Client (send/recv/close). Each request is
answered at once with the next payload queued for its command. A command with
no payload left gets no answer, like an FC that stopped replying. "error"
answers with an MSP error frame ('$M!').
"""

import pathlib

from flight_interface import msp

DATA = pathlib.Path(__file__).resolve().parent / "data"
REPLY_PREFIX = "PC <- ESP/FC:"


class FakeFc:
    def __init__(self, answers):
        """answers: {command: [payload, ...]}, used in order."""
        self.answers = {cmd: list(payloads) for cmd, payloads in answers.items()}
        self.inbox = bytearray()
        self.requests = []
        self.closed = False
        self._parser = msp.Parser()

    def send(self, data):
        for frame in self._parser.feed(data):
            self.requests.append(frame.cmd)
            queue = self.answers.get(frame.cmd)
            if not queue:
                continue
            payload = queue.pop(0)
            if payload == "error":
                self.inbox += msp.encode_response(frame.cmd, error=True)
            else:
                self.inbox += msp.encode_response(frame.cmd, payload)

    def recv(self):
        data, self.inbox = bytes(self.inbox), bytearray()
        return data

    def close(self):
        self.closed = True


def load_trace(name):
    """FC replies from a `msp_check --trace` log: {command: [payload, ...]}."""
    parser = msp.Parser()
    answers = {}
    for line in (DATA / name).read_text().splitlines():
        if not line.startswith(REPLY_PREFIX):
            continue
        hex_bytes = line[len(REPLY_PREFIX) :].strip()
        if hex_bytes == "timeout":
            continue
        for frame in parser.feed(bytes.fromhex(hex_bytes)):
            answers.setdefault(frame.cmd, []).append(frame.payload)
    return answers
