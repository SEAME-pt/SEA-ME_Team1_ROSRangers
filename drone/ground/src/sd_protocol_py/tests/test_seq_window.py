from sd_protocol import SeqResult, SeqWindow

# Same cases as drone/lib/protocol/tests/test_seq_window.cpp.


def test_in_order():
    w = SeqWindow()
    assert w.accept(1) is SeqResult.ACCEPTED
    assert w.accept(2) is SeqResult.ACCEPTED
    assert w.accept(3) is SeqResult.ACCEPTED
    assert w.last == 3
    assert w.counters.gaps == 0


def test_duplicate_rejected_and_counted():
    w = SeqWindow()
    w.accept(1)
    w.accept(2)
    assert w.accept(2) is SeqResult.DUPLICATE
    assert w.accept(3) is SeqResult.ACCEPTED
    assert w.last == 3
    assert w.counters.duplicates == 1


def test_older_rejected_and_counted():
    w = SeqWindow()
    w.accept(1)
    w.accept(5)
    assert w.accept(3) is SeqResult.OLD
    assert w.last == 5
    assert w.counters.old == 1


def test_gap_accepted_and_counted():
    w = SeqWindow()
    w.accept(1)
    w.accept(2)
    assert w.accept(10) is SeqResult.ACCEPTED_GAP
    assert w.last == 10
    assert w.counters.gaps == 1
    assert w.counters.missing == 7


def test_seq_zero_is_never_accepted():
    w = SeqWindow()
    assert w.accept(0) is SeqResult.OLD
    assert w.last == 0
    w.accept(4)
    assert w.accept(0) is SeqResult.OLD


def test_too_high_needs_new_session():
    w = SeqWindow()
    w.accept(1)
    assert w.accept(0x7FFFFFFF) is SeqResult.ACCEPTED_GAP
    assert w.accept(0x80000000) is SeqResult.NEED_NEW_SESSION
    assert w.accept(0xFFFFFFFF) is SeqResult.NEED_NEW_SESSION
    assert w.last == 0x7FFFFFFF
    assert w.counters.too_high == 2


def test_reset_starts_a_new_session():
    w = SeqWindow()
    w.accept(50)
    w.accept(50)
    w.reset()
    assert w.last == 0
    assert w.counters.duplicates == 0
    assert w.accept(1) is SeqResult.ACCEPTED
