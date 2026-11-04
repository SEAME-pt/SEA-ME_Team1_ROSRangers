import hashlib
import hmac

import pytest

from sd_protocol import derive_session_key, equal_constant_time, hmac_sha256

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
