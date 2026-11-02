"""softap_window.sh against a fake sudo + nmcli: no network is touched."""

import os
import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).with_name("softap_window.sh")
LAB = "netplan-wlan0-SEAME"
PASSWORD = "not-a-real-password"

FAKE_NMCLI = f"""#!/usr/bin/env bash
echo "$*" >> "$NMCLI_LOG"
[[ "$*" == *"show --active"* ]] && echo {LAB}
exit 0
"""


@pytest.fixture
def env(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "nmcli").write_text(FAKE_NMCLI)
    (bin_dir / "sudo").write_text('#!/usr/bin/env bash\nexec "$@"\n')
    for tool in bin_dir.iterdir():
        tool.chmod(0o755)
    yield {
        **os.environ,
        "PATH": f"{bin_dir}:{os.environ['PATH']}",
        "NMCLI_LOG": str(tmp_path / "nmcli.log"),
        "GITHUB_RUN_ID": "selftest",
        "SEAME_AP_SSID": "seame-hil-test",
        "SEAME_AP_PASS": PASSWORD,
    }
    # The watchdog sleeps for minutes after the script returns: stop it
    subprocess.run(["pkill", "-f", "hil-softap-selftest-"], check=False)


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
    return log.read_text().splitlines() if log.exists() else []


def test_joins_runs_and_rejoins_the_lab(env):
    result = run(env, "10", "--", "true")
    assert result.returncode == 0, result.stderr
    calls = nmcli_calls(env)
    add, up, delete, rejoin = calls
    assert add.startswith("connection add") and "autoconnect no" in add
    assert "ssid seame-hil-test" in add
    assert (
        up.startswith("--wait 30 connection up hil-softap-selftest-")
        and "passwd-file" in up
    )
    assert delete.startswith("connection delete hil-softap-selftest-")
    assert rejoin == f"--wait 30 connection up {LAB}"
    assert not any(PASSWORD in call for call in calls), "password on the command line"


def test_failing_command_still_rejoins_the_lab(env):
    result = run(env, "10", "--", "false")
    assert result.returncode == 1
    assert nmcli_calls(env)[-1] == f"--wait 30 connection up {LAB}"


@pytest.mark.parametrize(
    "args",
    [
        (),
        ("10", "true"),
        ("0", "--", "true"),
        ("61", "--", "true"),
        ("x", "--", "true"),
    ],
)
def test_bad_arguments_touch_nothing(env, args):
    result = run(env, *args)
    assert result.returncode == 2
    assert nmcli_calls(env) == []
