"""softap_window.sh against a fake sudo, nmcli and sleep: no network is touched.

Runs in PR CI (ci.yaml, hil-unit), not on the Pi. The fake `sleep`
holds the watchdog until the test opens a gate file, so the watchdog can be
tested without waiting for its real delay.
"""

import fcntl
import os
import signal
import subprocess
import time
import uuid
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("softap_window.sh")
LAB = "netplan-wlan0-SEAME"
PASSWORD = "not-a-real-password"
REJOIN = f"--wait 25 connection up {LAB}"  # the script's own re-join
WATCHDOG_REJOIN = REJOIN  # the watchdog re-joins the same way, after the script
LAB_AUTO_OFF = f"connection modify --temporary {LAB} connection.autoconnect no"
LAB_AUTO_ON = f"connection modify --temporary {LAB} connection.autoconnect yes"

# LAB_ACTIVE=0: the lab is down. FAIL_JOIN / FAIL_REJOIN: those calls fail.
# SLOW_ADD / SLOW_JOIN / SLOW_REJOIN: those calls take that many seconds; a finished
# re-join logs "rejoined"
FAKE_NMCLI = f"""#!/usr/bin/env bash
echo "$*" >> "$NMCLI_LOG"
case "$*" in
  "connection add "*) /bin/sleep "${{SLOW_ADD:-0}}" ;;
  *"show --active"*) [[ $LAB_ACTIVE == 0 ]] || echo {LAB} ;;
  *"connection up hil-softap-"*)
    /bin/sleep "${{SLOW_JOIN:-0}}"
    [[ -z $FAIL_JOIN ]] || exit 1 ;;
  "{REJOIN}")
    /bin/sleep "${{SLOW_REJOIN:-0}}"
    [[ -z $FAIL_REJOIN ]] || exit 1
    echo rejoined >> "$NMCLI_LOG" ;;
esac
exit 0
"""

# Waits for the gate. After 10 s it gives up and fails, so the watchdog
# stops instead of acting on its own, and never outlives the test
FAKE_SLEEP = """#!/usr/bin/env bash
for _ in $(seq 200); do [[ -e $SLEEP_GATE ]] && exit 0; /bin/sleep 0.05; done
exit 1
"""


@pytest.fixture
def env(tmp_path):
    run_id = f"selftest{uuid.uuid4().hex[:8]}"  # parallel runs never share one
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (tmp_path / "runner_temp").mkdir()
    (bin_dir / "nmcli").write_text(FAKE_NMCLI)
    (bin_dir / "sudo").write_text('#!/usr/bin/env bash\nexec "$@"\n')
    (bin_dir / "sleep").write_text(FAKE_SLEEP)
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    yield {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "NMCLI_LOG": str(tmp_path / "nmcli.log"),
        "SLEEP_GATE": str(tmp_path / "gate"),
        "RUNNER_TEMP": str(tmp_path / "runner_temp"),
        "HIL_WIFI_LOCK": str(tmp_path / "lock"),
        "GITHUB_RUN_ID": run_id,
        "SEAME_AP_SSID": "seame-hil-test",
        "SEAME_AP_PASS": PASSWORD,
    }
    # Stop this test's watchdog and fake sleeps
    subprocess.run(["pkill", "-f", f"hil-softap-{run_id}-"], check=False)
    subprocess.run(["pkill", "-f", str(bin_dir)], check=False)


def run(env, *args):
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        env=env,
        capture_output=True,
        text=True,
        timeout=20,
    )


def nmcli_calls(env):
    log = Path(env["NMCLI_LOG"])
    lines = log.read_text().splitlines() if log.exists() else []
    return [line for line in lines if line != "rejoined"]


def rejoined(env):
    return "rejoined" in Path(env["NMCLI_LOG"]).read_text().splitlines()


def wait_for_window(env):
    deadline = time.monotonic() + 5
    while not any("connection up hil-softap-" in c for c in nmcli_calls(env)):
        assert time.monotonic() < deadline, "never entered the window"
        time.sleep(0.05)


def open_gate_and_wait_for(env, call, after):
    """Let the watchdog run, then wait until it logs `call` after line `after`."""
    Path(env["SLEEP_GATE"]).touch()
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if call in nmcli_calls(env)[after:]:
            return nmcli_calls(env)[after:]
        time.sleep(0.05)
    pytest.fail(f"watchdog never ran {call!r}: {nmcli_calls(env)[after:]}")


def test_joins_runs_and_rejoins_the_lab(env):
    result = run(env, "10", "--", "true")
    assert result.returncode == 0, result.stderr
    calls = nmcli_calls(env)
    lab_off, add, up, delete, lab_on, rejoin = calls
    assert lab_off == LAB_AUTO_OFF  # no fallback to the lab inside the window
    assert add.startswith("connection add save no ")  # in memory: gone on reboot
    assert "autoconnect yes" in add and "ssid seame-hil-test" in add
    assert up.startswith(f"--wait 25 connection up hil-softap-{env['GITHUB_RUN_ID']}-")
    assert "passwd-file" in up
    assert delete.startswith("connection delete hil-softap-")
    assert lab_on == LAB_AUTO_ON
    assert rejoin == REJOIN
    assert not any(PASSWORD in call for call in calls), "password on the command line"
    assert list(Path(env["RUNNER_TEMP"]).iterdir()) == [], "password file left"


def test_failing_command_still_rejoins_the_lab(env):
    result = run(env, "10", "--", "false")
    assert result.returncode == 1
    assert nmcli_calls(env)[-1] == REJOIN


def test_overrunning_command_is_killed_and_lab_rejoined(env):
    start = time.monotonic()
    result = run(env, "1", "--", "/bin/sleep", "5")
    assert result.returncode == 124  # timeout's exit code
    assert time.monotonic() - start < 4
    calls = nmcli_calls(env)
    assert calls[-3].startswith("connection delete hil-softap-")
    assert calls[-2:] == [LAB_AUTO_ON, REJOIN]


def test_cancel_rejoins_first_then_stops_the_test(env):
    # A test slow to exit on TERM (here: it ignores it, so timeout's KILL ends
    # it 5 s later) must not hold up the re-join: a SIGKILL can follow a
    # cancel within about 10 s
    stubborn = ["bash", "-c", 'trap "" TERM; exec /bin/sleep 7.5']
    job = subprocess.Popen(
        ["bash", str(SCRIPT), "10", "--", *stubborn],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_window(env)
        start = time.monotonic()
        job.send_signal(signal.SIGTERM)  # what a cancel or step timeout sends
        while REJOIN not in nmcli_calls(env):
            assert time.monotonic() - start < 1, "the re-join waited for the test"
            time.sleep(0.02)
        assert job.wait(timeout=10) == 143  # after the test is gone
        assert time.monotonic() - start > 4, "restore did not wait for the test"
        gone = subprocess.run(["pgrep", "-f", "/bin/sleep 7.5"], capture_output=True)
        assert gone.returncode == 1, "the test command outlived the window"
    finally:
        job.kill()
        subprocess.run(["pkill", "-f", "/bin/sleep 7.5"], check=False)


def test_signal_during_the_join_restores_at_once(env):
    # The join can take up to 30 s: a cancel must not wait for it
    job = subprocess.Popen(
        ["bash", str(SCRIPT), "10", "--", "true"],
        env=env | {"SLOW_JOIN": "5"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_window(env)  # the join has started
        start = time.monotonic()
        job.send_signal(signal.SIGTERM)
        assert job.wait(timeout=5) == 143
        assert time.monotonic() - start < 2, "restore waited for the join"
        assert nmcli_calls(env)[-1] == REJOIN
        # The join, still running for a few seconds, must not hold the lock
        with open(env["HIL_WIFI_LOCK"], "w") as next_window:
            fcntl.flock(next_window, fcntl.LOCK_EX | fcntl.LOCK_NB)
    finally:
        job.kill()


def test_signal_before_the_join_restores_at_once(env):
    # NetworkManager can be slow to add the profile too (busy rescanning)
    job = subprocess.Popen(
        ["bash", str(SCRIPT), "10", "--", "true"],
        env=env | {"SLOW_ADD": "5"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 5
        while not any(c.startswith("connection add ") for c in nmcli_calls(env)):
            assert time.monotonic() < deadline, "never started the add"
            time.sleep(0.02)
        start = time.monotonic()
        job.send_signal(signal.SIGTERM)
        assert job.wait(timeout=5) == 143
        assert time.monotonic() - start < 2, "restore waited for the add"
        assert nmcli_calls(env)[-1] == REJOIN
    finally:
        job.kill()


def test_second_signal_does_not_cut_the_rejoin(env):
    # The runner sends SIGINT, then SIGTERM 7.5 s later: the re-join started
    # by the first one must still finish
    job = subprocess.Popen(
        ["bash", str(SCRIPT), "10", "--", "/bin/sleep", "6.5"],
        env=env | {"SLOW_REJOIN": "1"},
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        wait_for_window(env)
        job.send_signal(signal.SIGINT)
        deadline = time.monotonic() + 5
        while REJOIN not in nmcli_calls(env):
            assert time.monotonic() < deadline, "restore never started the re-join"
            time.sleep(0.02)
        job.send_signal(signal.SIGTERM)  # while the re-join is running
        assert job.wait(timeout=5) == 130
        assert rejoined(env), "the second signal cut the re-join"
    finally:
        job.kill()
        subprocess.run(["pkill", "-f", "/bin/sleep 6.5"], check=False)


def test_softap_join_failure_still_rejoins_lab(env, tmp_path):
    marker = tmp_path / "command-ran"
    result = run(env | {"FAIL_JOIN": "1"}, "10", "--", "touch", str(marker))
    assert result.returncode != 0
    assert not marker.exists(), "command ran without the SoftAP"
    calls = nmcli_calls(env)
    assert calls[-3].startswith("connection delete hil-softap-")
    assert calls[-2:] == [LAB_AUTO_ON, REJOIN]
    assert list(Path(env["RUNNER_TEMP"]).iterdir()) == [], "password file left"


def test_failed_rejoin_is_reported_and_retried_by_the_watchdog(env):
    env |= {"FAIL_REJOIN": "1", "LAB_ACTIVE": "0"}
    result = run(env, "10", "--", "true")
    assert result.returncode == 1
    assert "::error title=HIL Wi-Fi::" in result.stdout
    open_gate_and_wait_for(env, WATCHDOG_REJOIN, after=len(nmcli_calls(env)))


def test_watchdog_rejoins_when_the_job_is_killed(env):
    env |= {"LAB_ACTIVE": "0"}
    job = subprocess.Popen(
        ["bash", str(SCRIPT), "10", "--", "/bin/sleep", "8.5"],
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        start_new_session=True,
    )
    try:
        wait_for_window(env)
        os.killpg(job.pid, signal.SIGKILL)  # no EXIT trap, no restore
        job.wait()
        before = len(nmcli_calls(env))
        calls = open_gate_and_wait_for(env, WATCHDOG_REJOIN, after=before)
        assert calls[0].startswith("connection delete hil-softap-")
        assert calls[1] == LAB_AUTO_ON  # or the lab never autoconnects again
    finally:
        # timeout runs in its own process group, so killpg left it behind
        subprocess.run(["pkill", "-f", "/bin/sleep 8.5"], check=False)


def test_watchdog_waits_for_a_later_window(env):
    env |= {"FAIL_REJOIN": "1", "LAB_ACTIVE": "0"}
    assert run(env, "10", "--", "true").returncode == 1
    before = len(nmcli_calls(env))
    with open(env["HIL_WIFI_LOCK"], "w") as later_window:
        fcntl.flock(later_window, fcntl.LOCK_EX)  # a later run is in its window
        Path(env["SLEEP_GATE"]).touch()
        time.sleep(1)
        # Only its own profile goes, lock or not: a leftover autoconnect
        # profile could pull the Pi onto the SoftAP later
        calls = nmcli_calls(env)[before:]
        assert len(calls) == 1 and calls[0].startswith("connection delete hil-softap-")
    open_gate_and_wait_for(env, WATCHDOG_REJOIN, after=before)


def test_watchdog_leaves_an_active_lab_alone(env):
    assert run(env, "10", "--", "true").returncode == 0
    before = len(nmcli_calls(env))
    Path(env["SLEEP_GATE"]).touch()
    time.sleep(1)
    assert WATCHDOG_REJOIN not in nmcli_calls(env)[before:]


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("10", "true"),
        ("0", "--", "true"),
        ("51", "--", "true"),
        ("010", "--", "true"),
        ("x", "--", "true"),
    ],
)
def test_bad_arguments_touch_nothing(env, args):
    result = run(env, *args)
    assert result.returncode == 2
    assert nmcli_calls(env) == []
