"""HMAC-SHA256, session and direction keys, constant-time compare (ADR-002, spec §1)."""

import hashlib
import hmac
from dataclasses import dataclass

KEY_SIZE = 32
NONCE_SIZE = 16


def hmac_sha256(key: bytes, data: bytes) -> bytes:
    """HMAC-SHA256 (RFC 2104) of `data`, 32 bytes."""
    return hmac.new(key, data, hashlib.sha256).digest()


def derive_session_key(psk: bytes, ground_nonce: bytes, esp_nonce: bytes) -> bytes:
    """session key = HMAC-SHA256(PSK, "sess" || ground_nonce || esp_nonce)."""
    if len(psk) != KEY_SIZE:
        raise ValueError(f"PSK must be {KEY_SIZE} bytes")
    if len(ground_nonce) != NONCE_SIZE or len(esp_nonce) != NONCE_SIZE:
        raise ValueError(f"nonces must be {NONCE_SIZE} bytes")
    return hmac_sha256(psk, b"sess" + ground_nonce + esp_nonce)


@dataclass(frozen=True)
class DirectionKeys:
    """One key per direction (ADR-002 entry of 2026-11-05, #150)."""

    ground_to_esp: bytes  # ground -> ESP frames: the ESP's Receivers use it
    esp_to_ground: bytes  # ESP -> ground frames: the ground's Receivers use it


def derive_direction_keys(session_key: bytes) -> DirectionKeys:
    """ground_to_esp = HMAC-SHA256(session key, "g2e"), esp_to_ground = ... "e2g".

    Each side seals with its sending key and checks with its receiving key, so a
    frame sent back to its own sender (reflection) fails the tag. HELLO and
    HELLO_ACK stay tagged with the PSK; the session key itself tags nothing.
    """
    if len(session_key) != KEY_SIZE:
        raise ValueError(f"session key must be {KEY_SIZE} bytes")
    return DirectionKeys(
        hmac_sha256(session_key, b"g2e"), hmac_sha256(session_key, b"e2g")
    )


def equal_constant_time(a: bytes, b: bytes) -> bool:
    """Compares in time that does not depend on where the bytes differ (spec §4 rule 1)."""
    return hmac.compare_digest(a, b)
