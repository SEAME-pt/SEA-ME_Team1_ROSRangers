import hashlib
import hmac

import pytest

import sd_protocol
from sd_protocol import derive_keys, equal_constant_time, hmac_sha256
from sd_protocol.crypto import derive_direction_keys, derive_session_key

TEST_KEY = bytes(range(32))  # spec §6 test key, never the real PSK


def test_rfc4231_case_1():
    key = b"\x0b" * 20
    expected = "b0344c61d8db38535ca8afceaf0bf12b881dc200c9833da726e9376c2e32cff7"
    assert hmac_sha256(key, b"Hi There").hex() == expected


def test_rfc4231_case_2():
    expected = "5bdcc146bf60754e6a042426089575c75a003f089d2739839dec58b964ec3843"
    assert hmac_sha256(b"Jefe", b"what do ya want for nothing?").hex() == expected


def test_session_key_is_the_hmac_of_sess_and_both_nonces():
    ground, esp = bytes(range(16)), bytes(range(16, 32))
    expected = hmac.new(TEST_KEY, b"sess" + ground + esp, hashlib.sha256).digest()
    assert derive_session_key(TEST_KEY, ground, esp) == expected


def test_session_key_depends_on_the_nonce_order():
    ground, esp = bytes(16), bytes([1] * 16)
    assert derive_session_key(TEST_KEY, ground, esp) != derive_session_key(
        TEST_KEY, esp, ground
    )


@pytest.mark.parametrize(
    "psk, ground, esp",
    [
        (bytes(31), bytes(16), bytes(16)),
        (TEST_KEY, bytes(15), bytes(16)),
        (TEST_KEY, bytes(16), bytes(17)),
    ],
)
def test_session_key_rejects_wrong_sizes(psk, ground, esp):
    with pytest.raises(ValueError):
        derive_session_key(psk, ground, esp)


def test_equal_constant_time():
    assert equal_constant_time(b"abcdefgh", b"abcdefgh")
    assert not equal_constant_time(b"abcdefgh", b"abcdefgx")
    assert not equal_constant_time(b"abcdefgh", b"abcdefg")


def test_direction_keys_are_hmac_of_the_session_key_with_g2e_and_e2g():
    # Same inputs and expected values as DirectionKeys in test_hmac.cpp (#150).
    session = derive_session_key(TEST_KEY, b"\x11" * 16, b"\x22" * 16)
    keys = derive_direction_keys(session)
    assert keys.ground_to_esp.hex() == (
        "bb0b20baf093c79ac77f89eb087d0e68a35bb31cf90f34a09bb2d41837b9e117"
    )
    assert keys.esp_to_ground.hex() == (
        "7353101a8458bfe9911c7269d2ec7aa4f94d13c0b2cfad27bd454e078f96b33d"
    )
    # Three different keys: a frame sealed for one direction never passes the other.
    assert len({keys.ground_to_esp, keys.esp_to_ground, session}) == 3


def test_derive_keys_equals_the_two_steps():
    # Same as DirectionKeys.DeriveKeysEqualsTheTwoSteps in test_hmac.cpp (#152).
    ground, esp = b"\x11" * 16, b"\x22" * 16
    keys = derive_keys(TEST_KEY, ground, esp)
    assert keys == derive_direction_keys(derive_session_key(TEST_KEY, ground, esp))
    assert keys.esp_to_ground.hex() == (
        "7353101a8458bfe9911c7269d2ec7aa4f94d13c0b2cfad27bd454e078f96b33d"
    )


def test_internal_steps_are_not_exported():
    assert not hasattr(sd_protocol, "derive_session_key")
    assert not hasattr(sd_protocol, "derive_direction_keys")


def test_direction_keys_need_a_32_byte_session_key():
    with pytest.raises(ValueError):
        derive_direction_keys(bytes(31))
