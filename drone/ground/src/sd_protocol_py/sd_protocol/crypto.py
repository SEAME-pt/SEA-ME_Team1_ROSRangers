"""HMAC-SHA256, session key and constant-time compare (ADR-002, spec §1)."""

import hashlib
import hmac

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


def equal_constant_time(a: bytes, b: bytes) -> bool:
    """Compares in time that does not depend on where the bytes differ (spec §4 rule 1)."""
    return hmac.compare_digest(a, b)
