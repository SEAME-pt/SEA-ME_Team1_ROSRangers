"""Unit tests (no ROS) for the #103 part 2 decisions in guard.py and backends.py:
A2 link loss without a latch, A3 / A4 operator-only RESUME and ARM held for the
K6 neutral window (1.5 s, PR #191), E6.1 unknown FC link = not OK,
receive-time ordering, the new /flight/state fields and the extended backend
interface (A1)."""

import pytest

from flight_interface.backends import Backend, LoopbackBackend
from flight_interface.guard import (
    ARM,
    NEUTRAL_MAX_GAP,
    NEUTRAL_WINDOW,
    REQUEST_TIMEOUT,
    RESUME,
    ZERO,
    Guard,
)

STREAMING = {
    "esp_state": "STREAMING",
    "override_active": True,
    "mask": 8,
    "fc_link_ok": True,
}
FAILSAFE = {
    "esp_state": "LINK_FAILSAFE",
    "override_active": False,
    "mask": None,
    "fc_link_ok": True,
}
TICK = 0.02


def guard_in(state, now=0.0):
    g = Guard(tick_period=TICK)
    g.activate()
    g.on_drone_state(state, now)
    return g


def send_neutral_until(g, start, end, state=FAILSAFE, seen=None):
    """The node's tick: fresh drone state, a neutral sent, request_due() asked.
    Returns the time request_due() first gave something, or None. `seen`
    collects what it gave."""
    t = start
    due_at = None
    while t <= end + 1e-9:
        g.on_drone_state(state, t)
        g.note_sent(ZERO, t)
        due = g.request_due(t)
        if due is not None:
            if seen is not None:
                seen.append(due)
            if due_at is None:
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
        # E6.1: unknown is not OK.
        (
            lambda g: g.on_drone_state(dict(FAILSAFE, fc_link_ok=None), 0.0),
            "not known",
        ),
    ],
)
def test_resume_refused(setup, why):
    g = guard_in(FAILSAFE)
    setup(g)
    ok, message = g.request_resume(0.0)
    assert not ok and why in message
    assert g.resume_pending is False


@pytest.mark.parametrize(
    "fc_link_ok, ok", [(True, True), (None, False), (False, False)]
)
def test_resume_needs_the_fc_link_reported_alive(fc_link_ok, ok):
    """E6.1 (applied with the FC_HEALTH decoding): only true is OK."""
    g = guard_in(dict(FAILSAFE, fc_link_ok=fc_link_ok))
    assert g.request_resume(0.0)[0] is ok


def test_resume_refused_when_the_link_is_lost():
    g = guard_in(FAILSAFE)
    ok, message = g.request_resume(1.0)
    assert not ok and "link" in message


def test_resume_due_after_the_neutral_window_and_only_once():
    g = guard_in(FAILSAFE)
    ok, message = g.request_resume(0.0)
    assert ok and "after" in message
    assert g.resume_pending and g.flight_state(0.0)["resume_pending"] is True
    assert g.request_resume(0.0) == (False, "an operator RESUME is already pending")
    seen = []
    due_at = send_neutral_until(g, 0.0, 2.0, seen=seen)
    assert due_at == pytest.approx(NEUTRAL_WINDOW, abs=1.5 * TICK)
    assert seen == [(RESUME, None, None)]  # once; FAILSAFE has no session_id
    assert g.resume_pending is False
    assert send_neutral_until(g, 2.1, 4.0) is None  # never again by itself


def test_the_window_is_the_k6_value_of_pr_191():
    """K6 as changed by the safety pilot on PR #191 (2026-11-13): 1.5 s
    window, 3 s timeout. The ESP needs 1 s of RC acks (#188), so 0.6 s
    was refused with result 4 on the bench."""
    assert NEUTRAL_WINDOW == 1.5 and REQUEST_TIMEOUT == 3.0
    assert NEUTRAL_MAX_GAP == 0.1


def test_a_gap_in_the_setpoints_restarts_the_window():
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    assert send_neutral_until(g, 0.0, 0.4) is None
    later = 0.4 + NEUTRAL_MAX_GAP + 0.05  # a gap: the window starts again
    due_at = send_neutral_until(g, later, later + 2.0)
    assert due_at == pytest.approx(later + NEUTRAL_WINDOW, abs=1.5 * TICK)


def test_a_non_neutral_setpoint_restarts_the_window():
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    send_neutral_until(g, 0.0, 0.4)
    g.note_sent((0.0, 0.0, 0.0, 0.1), 0.42)
    due_at = send_neutral_until(g, 0.44, 2.5)
    assert due_at == pytest.approx(0.44 + NEUTRAL_WINDOW, abs=1.5 * TICK)


@pytest.mark.parametrize(
    "event",
    [
        lambda g, t: g.trigger_estop("test"),
        lambda g, t: g.deactivate(),
        lambda g, t: g.on_drone_state(STREAMING, t),
        lambda g, t: g.on_drone_state(dict(FAILSAFE, fc_link_ok=False), t),
        lambda g, t: g.on_drone_state(dict(FAILSAFE, fc_link_ok=None), t),
        lambda g, t: g.on_drone_state(dict(FAILSAFE, session_id=7), t),
    ],
)
def test_a_pending_resume_is_dropped_when_its_conditions_go(event):
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    send_neutral_until(g, 0.0, 0.3)
    event(g, 0.32)
    assert g.request_due(0.34) is None
    assert g.resume_pending is False


def test_a_pending_resume_is_dropped_when_the_link_is_lost():
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    assert g.request_due(0.6) is None  # no state since 0.0
    assert g.resume_pending is False


def test_a_pending_resume_expires():
    g = guard_in(FAILSAFE)
    g.request_resume(0.0)
    t = 0.0
    while t < REQUEST_TIMEOUT + 0.1:  # link fresh, but nothing is sent
        g.on_drone_state(FAILSAFE, t)
        assert g.request_due(t) is None
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
    assert b.send_setpoint((0.1, 0, 0, 0), 0.0) is True
    assert b.send_neutral(0.1) is True
    assert b.end_session(0.1) is True
    assert b.request_resume(0.2, None) is True
    assert [what for _, what in b.log] == ["setpoint", "neutral", "bye", "resume"]
    assert b.sent[-1] == (0.1, ZERO)


def test_base_backend_new_methods_are_only_an_interface():
    b = Backend()
    assert b.take_acks() == []  # E2: a backend without answers has none
    for call in (
        lambda: b.send_neutral(0.0),
        lambda: b.end_session(0.0),
        lambda: b.request_resume(0.0, 1),
    ):
        with pytest.raises(NotImplementedError):
            call()


# ---------- #179: FC link health from the ESP ----------
@pytest.mark.parametrize(
    "fc_link_ok, open_", [(None, False), (True, True), (False, False)]
)
def test_fc_link_lost_closes_the_gate(fc_link_ok, open_):
    g = guard_in({**STREAMING, "fc_link_ok": fc_link_ok})
    g.on_target(0.0, 0.0, 0.0, 0.3, now=0.0)
    expected = (0.0, 0.0, 0.0, 0.3) if open_ else ZERO
    assert g.command(0.0) == pytest.approx(expected)
    assert g.flight_state(0.0)["fc_link_ok"] is fc_link_ok
    # Decision K1: FC link lost -> link_ok false too, so mission_node goes IDLE.
    # E6.1: unknown (None) counts as lost.
    assert g.flight_state(0.0)["link_ok"] is open_


def test_fc_link_ok_is_null_unless_a_real_bool_or_when_stale():
    g = guard_in({**STREAMING, "fc_link_ok": "no"})
    assert g.flight_state(0.0)["fc_link_ok"] is None
    g.on_drone_state({**STREAMING, "fc_link_ok": True}, 0.1)
    assert g.flight_state(0.7)["fc_link_ok"] is None  # link lost: unknown


# ---------- A4, K6: the operator's ARM, held for the neutral window ----------
IDLE = {
    "esp_state": "SESSION_IDLE",
    "override_active": False,
    "mask": None,
    "fc_link_ok": True,
    "session_id": 5,
}


def test_arm_is_held_and_due_after_the_neutral_window_once():
    g = guard_in(IDLE)
    ok, message = g.request_arm(8, 0.0)
    assert ok and message.startswith("ARM_CONTROL_REQUEST mask 8 goes out after 1.5 s")
    assert g.arm_pending and g.flight_state(0.0)["arm_pending"] is True
    assert g.request_arm(8, 0.0) == (False, "an operator ARM is already pending")
    assert g.request_resume(0.0)[0] is False  # one request at a time
    seen = []
    due_at = send_neutral_until(g, 0.0, 2.5, state=IDLE, seen=seen)
    assert due_at == pytest.approx(NEUTRAL_WINDOW, abs=1.5 * TICK)
    assert seen == [(ARM, 8, 5)]  # K6: with the session it was asked in
    assert g.arm_pending is False


def test_arm_is_never_due_without_a_request():
    g = guard_in(IDLE)
    assert send_neutral_until(g, 0.0, 4.0, state=IDLE) is None


@pytest.mark.parametrize(
    "event",
    [
        lambda g, t: g.trigger_estop("test"),
        lambda g, t: g.deactivate(),
        lambda g, t: g.on_drone_state(dict(IDLE, esp_state="STREAMING"), t),
        lambda g, t: g.on_drone_state(dict(IDLE, fc_link_ok=False), t),
        lambda g, t: g.on_drone_state(dict(IDLE, fc_link_ok=None), t),
        # REQ-SAF-023: a new session never inherits an operator's ARM.
        lambda g, t: g.on_drone_state(dict(IDLE, session_id=6), t),
    ],
)
def test_a_pending_arm_is_dropped_when_its_conditions_go(event):
    g = guard_in(IDLE)
    g.request_arm(8, 0.0)
    send_neutral_until(g, 0.0, 0.5, state=IDLE)
    event(g, 0.52)
    assert g.request_due(0.54) is None
    assert g.arm_pending is False
    assert send_neutral_until(g, 0.56, 4.0, state=IDLE) is None


def test_a_pending_arm_expires_without_setpoints():
    g = guard_in(IDLE)
    g.request_arm(8, 0.0)
    t = 0.0
    while t < REQUEST_TIMEOUT + 0.1:
        g.on_drone_state(IDLE, t)
        assert g.request_due(t) is None
        t += 0.1
    assert g.arm_pending is False


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
        (lambda g: g.on_drone_state({**IDLE, "fc_link_ok": None}, 0.0), 8, "not known"),
    ],
)
def test_arm_refused(setup, mask, why):
    g = guard_in(IDLE)
    setup(g)
    ok, message = g.request_arm(mask, 0.0)
    assert not ok and why in message
    assert g.arm_pending is False


def test_arm_refused_when_the_link_is_lost():
    g = guard_in(IDLE)
    ok, message = g.request_arm(8, 1.0)
    assert not ok and "link" in message


def test_loopback_records_arm():
    b = LoopbackBackend()
    assert b.arm_control(11, 0.0, None) is True
    assert b.armed_masks == [11] and b.log == [(0.0, "arm")]
    with pytest.raises(NotImplementedError):
        Backend().arm_control(8, 0.0, 1)


def test_loopback_hands_queued_acks_over_once():
    b = LoopbackBackend()
    b.acks.append(("ARM_CONTROL_ACK", 2, "MASK_MISMATCH"))
    assert b.take_acks() == [("ARM_CONTROL_ACK", 2, "MASK_MISMATCH")]
    assert b.take_acks() == []


# ---------- D1, #149: armed and the ESP's reject count on /flight/state ----------
@pytest.mark.parametrize("armed", [True, False, None, "yes"])
def test_armed_is_reported_true_false_or_null(armed):
    g = guard_in({**STREAMING, "armed": armed, "airborne": armed is True})
    state = g.flight_state(0.0)
    assert state["armed"] is (armed if isinstance(armed, bool) else None)
    assert state["airborne"] is (armed is True)  # D1: airborne = armed


def test_esp_rx_rejected_only_as_a_real_int():
    g = guard_in({**STREAMING, "esp_rx_rejected": 70000})
    assert g.flight_state(0.0)["esp_rx_rejected"] == 70000
    g.on_drone_state({**STREAMING, "esp_rx_rejected": True}, 0.1)
    assert g.flight_state(0.1)["esp_rx_rejected"] is None
    assert g.flight_state(0.7)["esp_rx_rejected"] is None  # link lost


def test_loopback_reports_the_fc_link_alive():
    """E6.1 (b): so a dry run still shows link_ok true while active."""
    g = Guard(tick_period=TICK)
    g.activate()
    g.on_drone_state(LoopbackBackend().poll(0.0)[0], 0.0)
    assert g.flight_state(0.0)["link_ok"] is True
