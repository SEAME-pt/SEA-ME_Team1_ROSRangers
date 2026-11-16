import pytest

from sd_protocol import WrapCounter


def test_counts_up_without_wrap():
    c = WrapCounter()
    assert c.total == 0
    assert c.update(3) == 3
    assert c.update(3) == 3
    assert c.update(10) == 10
    assert c.wraps == 0


def test_a_lower_value_is_a_wrap():
    # 65537 rejects on the ESP: STATE says 65535, then 1 (spec §7.5, #149)
    c = WrapCounter()
    c.update(65535)
    assert c.update(1) == 65537
    assert c.wraps == 1
    assert c.update(0xFFFF) == 2 * 65536 - 1
    assert c.update(0) == 2 * 65536


def test_starts_at_the_first_value_and_resets():
    c = WrapCounter()
    assert c.update(40000) == 40000
    c.reset()
    assert c.total == 0
    assert c.update(5) == 5
    assert c.wraps == 0


@pytest.mark.parametrize("raw", [-1, 65536])
def test_refuses_a_value_that_is_not_u16(raw):
    with pytest.raises(ValueError):
        WrapCounter().update(raw)
