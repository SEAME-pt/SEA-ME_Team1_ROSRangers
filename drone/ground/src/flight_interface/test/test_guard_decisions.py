"""Unit tests (no ROS) for the #103 part 2 decisions in guard.py and backends.py:
A2 link loss without a latch, A3 operator-only RESUME, receive-time ordering,
the new /flight/state fields and the extended backend interface (A1)."""

import pytest

from flight_interface.backends import Backend, LoopbackBackend
from flight_interface.guard import (
    RESUME_MAX_GAP,
    RESUME_TIMEOUT,
    RESUME_WINDOW,
    ZERO,
    Guard,
)

STREAMING = {"esp_state": "STREAMING", "override_active": True, "mask": 8}
FAILSAFE = {"esp_state": "LINK_FAILSAFE", "override_active": False, "mask": None}
TICK = 0.02


def guard_in(state, now=0.0):
    g = Guard(tick_period=TICK)
    g.activate()
    g.on_drone_state(state, now)
    return g


def send_neutral_until(g, start, end, state=FAILSAFE):
    """The node's tick: fresh drone state, a neutral sent, resume_due() asked.
    Returns the time resume_due() first said True, or None."""
    t = start
    due_at = None
    while t <= end + 1e-9:
        g.on_drone_state(state, t)
        g.note_sent(ZERO, t)
        if g.resume_due(t) and due_at is None:
            due_at = t
        t = round(t + TICK, 6)
    return due_at


# ---------- A2: link loss gives zero and link_ok false, no latch ----------
def test_link_loss_gives_zero_and_comes_back_without_a_reset():
    g = guard_in(STREAMING)
    g.on_target(0.0, 0.0, 0.0, 0.3, now=0.0)
    assert g.command(0.0) == pytest.approx((0.0, 0.0, 0.0, 0.3))
    assert g.command(0.6) == ZERO  # no state for > 0.5 s
    assert g.flight_state(0.6)["link_ok"] is False
    assert g.estop is False  # A2: not latched
    g.on_drone_state(STREAMING, 0.7)
    g.on_target(0.0, 0.0, 0.0, 0.3, now=0.7)
    assert g.command(0.7) == pytest.approx((0.0, 0.0, 0.0, 0.3))


# ---------- receive time ----------
def test_a_state_older_than_the_one_held_is_ignored():
    g = guard_in(STREAMING, now=1.0)
    g.on_drone_state(FAILSAFE, 0.5)  # arrived late, received earlier
    assert g.flight_state(1.0)["esp_state"] == "STREAMING"
    g.on_drone_state(FAILSAFE, float("nan"))
    assert g.flight_state(1.0)["esp_state"] == "STREAMING"
    g.on_drone_state(FAILSAFE, 1.1)
    assert g.flight_state(1.1)["esp_state"] == "LINK_FAILSAFE"


# ---------- A3: RESUME only on the operator's request ----------
def test_resume_is_never_due_without_a_request():
    g = guard_in(FAILSAFE)
    assert send_neutral_until(g, 0.0, 3.0) is None


@pytest.mark.parametrize(
    "setup, why",
    [
        (lambda g: g.deactivate(), "not active"),
        (lambda g: g.trigger_estop("test"), "E-stop"),
        (lambda g: g.on_drone_state(STREAMING, 0.0), "not LINK_FAILSAFE"),
        # As the ESP (result 4, PR #183): no RESUME while the FC is silent.
        (
            lambda g: g.on_drone_state(dict(FAILSAFE, fc_link_ok=False), 0.0),
            "FC link lost",
        ),
    ],
)
def test_resume_refused(setup, why):
    g = guard_in(FAILSAFE)
    setup(g)
    ok, message = g.request_resume(0.0)
    assert not ok and why in message
    assert g.resume_pending is False


@pytest.mark.parametrize("fc_link_ok", [None, True])
def test_resume_accepted_when_the_fc_link_is_not_reported_lost(fc_link_ok):
    """Unknown (null) does not block yet: decision E6.1, applied with the
    FC_HEALTH decoding (MISSING_FROM_NODE.md)."""
    g = guard_in(dict(FAILSAFE, fc_link_ok=fc_link_ok))
    assert g.request_resume(0.0)[0] is True


def test_resume_refused_when_the_link_is_lost():
    g = guard_in(FAILSAFE)
    ok, message = g.request_resume(1.0)
    assert not ok and "link" in message


def test_resume_due_after_the_neutral_window_and_only_once():
    g = guard_in(FAILSAFE)
    ok, message = g.request_resume(0.0)
    assert ok and "after" in message
    assert g.resume_pending and g.flight_state(0.0)["resume_pending"] is True
    assert g.request_resume(0.0) == (False, "a resume is already pending")
    due_at = send_neutral_until(g, 0.0, 1.5)
    assert due_at == pytest.approx(RESUME_WINDOW, abs=1.5 * TICK)
    assert g.resume_pending is False
    assert send_neutral_until(g, 1.6, 3.0) is None  # never again by itself


def test_a_gap_in_the_setpoints_restarts_the_window():
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    assert send_neutral_until(g, 0.0, 0.4) is None
    later = 0.4 + RESUME_MAX_GAP + 0.05  # a gap: the window starts again
    due_at = send_neutral_until(g, later, later + 1.0)
    assert due_at == pytest.approx(later + RESUME_WINDOW, abs=1.5 * TICK)


def test_a_non_neutral_setpoint_restarts_the_window():
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    send_neutral_until(g, 0.0, 0.4)
    g.note_sent((0.0, 0.0, 0.0, 0.1), 0.42)
    due_at = send_neutral_until(g, 0.44, 1.5)
    assert due_at == pytest.approx(0.44 + RESUME_WINDOW, abs=1.5 * TICK)


@pytest.mark.parametrize(
    "event",
    [
        lambda g, t: g.trigger_estop("test"),
        lambda g, t: g.deactivate(),
        lambda g, t: g.on_drone_state(STREAMING, t),
        lambda g, t: g.on_drone_state(dict(FAILSAFE, fc_link_ok=False), t),
    ],
)
def test_a_pending_resume_is_dropped_when_its_conditions_go(event):
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    send_neutral_until(g, 0.0, 0.3)
    event(g, 0.32)
    assert g.resume_due(0.34) is False
    assert g.resume_pending is False


def test_a_pending_resume_is_dropped_when_the_link_is_lost():
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    assert g.resume_due(0.6) is False  # no state since 0.0
    assert g.resume_pending is False


def test_a_pending_resume_expires():
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    t = 0.0
    while t < RESUME_TIMEOUT + 0.1:  # link fresh, but nothing is sent
        g.on_drone_state(FAILSAFE, t)
        assert g.resume_due(t) is False
        t += 0.1
    assert g.resume_pending is False


def test_activate_drops_a_pending_resume():
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    g.activate()
    assert g.resume_pending is False


# ---------- /flight/state ----------
def test_flight_state_reports_failsafe_output_only_when_fresh():
    g = guard_in({**FAILSAFE, "failsafe_output": True})
    assert g.flight_state(0.1)["failsafe_output"] is True
    assert g.flight_state(0.6)["failsafe_output"] is False  # link lost: unknown
    g.on_drone_state({**FAILSAFE, "failsafe_output": "yes"}, 0.7)
    assert g.flight_state(0.7)["failsafe_output"] is False  # only a real True


# ---------- backends (A1: neutral and BYE, A3: resume) ----------
def test_loopback_records_neutral_bye_and_resume_in_order():
    b = LoopbackBackend()
    b.send_setpoint((0.1, 0, 0, 0), 0.0)
    b.send_neutral(0.1)
    b.end_session(0.1)
    assert b.request_resume(0.2) is True
    assert [what for _, what in b.log] == ["setpoint", "neutral", "bye", "resume"]
    assert b.sent[-1] == (0.1, ZERO)


def test_base_backend_new_methods_are_only_an_interface():
    b = Backend()
    assert b.take_acks() == []  # E2: a backend without answers has none
    for call in (
        lambda: b.send_neutral(0.0),
        lambda: b.end_session(0.0),
        lambda: b.request_resume(0.0),
    ):
        with pytest.raises(NotImplementedError):
            call()


# ---------- #179: FC link health from the ESP ----------
@pytest.mark.parametrize(
    "fc_link_ok, open_", [(None, True), (True, True), (False, False)]
)
def test_fc_link_lost_closes_the_gate(fc_link_ok, open_):
    g = guard_in({**STREAMING, "fc_link_ok": fc_link_ok})
    g.on_target(0.0, 0.0, 0.0, 0.3, now=0.0)
    expected = (0.0, 0.0, 0.0, 0.3) if open_ else ZERO
    assert g.command(0.0) == pytest.approx(expected)
    assert g.flight_state(0.0)["fc_link_ok"] is fc_link_ok
    # Decision K1: FC link lost -> link_ok false too, so mission_node goes IDLE.
    assert g.flight_state(0.0)["link_ok"] is open_


def test_fc_link_ok_is_null_unless_a_real_bool_or_when_stale():
    g = guard_in({**STREAMING, "fc_link_ok": "no"})
    assert g.flight_state(0.0)["fc_link_ok"] is None
    g.on_drone_state({**STREAMING, "fc_link_ok": True}, 0.1)
    assert g.flight_state(0.7)["fc_link_ok"] is None  # link lost: unknown


# ---------- A4: when the operator's ARM may go out ----------
IDLE = {"esp_state": "SESSION_IDLE", "override_active": False, "mask": None}


def test_arm_allowed_in_session_idle_with_a_known_mask():
    g = guard_in(IDLE)
    assert g.arm_allowed(8, 0.0) == (True, "ARM_CONTROL_REQUEST mask 8")


@pytest.mark.parametrize(
    "setup, mask, why",
    [
        (lambda g: None, 9, "mask must be"),
        (lambda g: None, True, "mask must be"),
        (lambda g: g.deactivate(), 8, "not active"),
        (lambda g: g.trigger_estop("test"), 8, "E-stop"),
        (lambda g: g.on_drone_state(STREAMING, 0.0), 8, "not SESSION_IDLE"),
        (lambda g: g.on_drone_state(FAILSAFE, 0.0), 8, "not SESSION_IDLE"),
        (lambda g: g.on_drone_state({**IDLE, "fc_link_ok": False}, 0.0), 8, "FC link"),
    ],
)
def test_arm_refused(setup, mask, why):
    g = guard_in(IDLE)
    setup(g)
    ok, message = g.arm_allowed(mask, 0.0)
    assert not ok and why in message


def test_arm_refused_when_the_link_is_lost():
    g = guard_in(IDLE)
    ok, message = g.arm_allowed(8, 1.0)
    assert not ok and "link" in message


def test_loopback_records_arm():
    b = LoopbackBackend()
    assert b.arm_control(11, 0.0) is True
    assert b.armed_masks == [11] and b.log == [(0.0, "arm")]
    with pytest.raises(NotImplementedError):
        Backend().arm_control(8, 0.0)


def test_loopback_hands_queued_acks_over_once():
    b = LoopbackBackend()
    b.acks.append(("ARM_CONTROL_ACK", 2, "MASK_MISMATCH"))
    assert b.take_acks() == [("ARM_CONTROL_ACK", 2, "MASK_MISMATCH")]
    assert b.take_acks() == []
