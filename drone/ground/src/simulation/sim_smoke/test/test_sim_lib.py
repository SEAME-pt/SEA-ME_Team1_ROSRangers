"""Tests of sim_lib.sh (the PX4 readers of sim_start.sh) against a fake
px4-listener that prints canned uORB output. Needs bash and awk only."""

import os
import subprocess
from pathlib import Path

import pytest

SIM_LIB = Path(__file__).resolve().parents[2] / "sim_lib.sh"

VEHICLE_STATUS = """TOPIC: vehicle_status
 vehicle_status
    timestamp: 123456 (0.1 seconds ago)
    arming_state: {arming}
    nav_state: {nav}
"""
LOCAL_POSITION = """TOPIC: vehicle_local_position
 vehicle_local_position
    timestamp: 123456 (0.01 seconds ago)
    z: {z}
    heading_good_for_control: {good}
"""


def fake_listener(tmp_path, outputs):
    """A px4-listener that prints outputs[topic]; for a list, one entry per
    call (the last one repeats)."""
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir(exist_ok=True)
    for topic, text in outputs.items():
        texts = text if isinstance(text, list) else [text]
        for index, body in enumerate(texts):
            (bin_dir / f"{topic}.{index}").write_text(body)
        (bin_dir / f"{topic}.count").write_text(str(len(texts)))
    script = bin_dir / "px4-listener"
    script.write_text(
        "#!/usr/bin/env bash\n"
        'dir=$(dirname "$0"); topic=$1\n'
        '[ -f "$dir/$topic.count" ] || exit 1\n'
        'n=$(cat "$dir/$topic.count"); calls=0\n'
        '[ -f "$dir/$topic.calls" ] && calls=$(cat "$dir/$topic.calls")\n'
        "index=$(( calls < n - 1 ? calls : n - 1 ))\n"
        'echo $((calls + 1)) > "$dir/$topic.calls"\n'
        'cat "$dir/$topic.$index"\n'
    )
    script.chmod(0o755)
    return bin_dir


def run(bin_dir, body):
    """Source sim_lib.sh with BIN=bin_dir, run `body`; (exit code, stdout)."""
    script = f'set -u\nBIN="{bin_dir}"\nsource "{SIM_LIB}"\n{body}\n'
    done = subprocess.run(
        ["bash", "-c", script],
        capture_output=True,
        text=True,
        timeout=10,
        env={**os.environ, "LC_ALL": "C"},
    )
    return done.returncode, done.stdout.strip()


def test_px4_field_reads_one_field(tmp_path):
    bin_dir = fake_listener(
        tmp_path, {"vehicle_status": VEHICLE_STATUS.format(arming=2, nav=14)}
    )
    assert run(bin_dir, "px4_field vehicle_status nav_state") == (0, "14")
    assert run(bin_dir, "px4_field vehicle_status missing_field") == (0, "")


def test_px4_field_without_px4_is_empty(tmp_path):
    bin_dir = fake_listener(tmp_path, {})
    assert run(bin_dir, "px4_field vehicle_status nav_state") == (0, "")


@pytest.mark.parametrize(
    "arming, nav, armed, nav_out", [(2, 14, 0, "14"), (1, 14, 1, "14"), (2, 4, 0, "4")]
)
def test_armed_and_nav_state(tmp_path, arming, nav, armed, nav_out):
    bin_dir = fake_listener(
        tmp_path, {"vehicle_status": VEHICLE_STATUS.format(arming=arming, nav=nav)}
    )
    assert run(bin_dir, "armed")[0] == armed
    assert run(bin_dir, "nav_state") == (0, nav_out)


@pytest.mark.parametrize("z, high", [(-1.59, False), (-1.6, True), (-2.0, True), (0.0, False)])
def test_up_high_needs_1_6_m(tmp_path, z, high):
    bin_dir = fake_listener(
        tmp_path, {"vehicle_local_position": LOCAL_POSITION.format(z=z, good="True")}
    )
    code, _ = run(bin_dir, "up_high")
    assert (code == 0) is high


def test_up_high_without_a_position_is_false(tmp_path):
    bin_dir = fake_listener(tmp_path, {})
    assert run(bin_dir, "up_high")[0] != 0


def test_height_is_up_positive(tmp_path):
    bin_dir = fake_listener(
        tmp_path, {"vehicle_local_position": LOCAL_POSITION.format(z=-1.753, good="True")}
    )
    assert run(bin_dir, "height") == (0, "1.75")


@pytest.mark.parametrize("good, ok", [("True", 0), ("False", 1)])
def test_heading_good(tmp_path, good, ok):
    bin_dir = fake_listener(
        tmp_path, {"vehicle_local_position": LOCAL_POSITION.format(z=-2.0, good=good)}
    )
    assert run(bin_dir, "heading_good")[0] == ok


def mode_at(stamp):
    return f"TOPIC: offboard_control_mode\n    timestamp: {stamp} (0.0{stamp % 10} seconds ago)\n"


def test_streaming_needs_two_different_timestamps(tmp_path):
    bin_dir = fake_listener(
        tmp_path, {"offboard_control_mode": [mode_at(100), mode_at(100), mode_at(200)]}
    )
    # first call: nothing to compare; second: same stamp; third: a new one
    body = "streaming; echo $?; streaming; echo $?; streaming; echo $?"
    assert run(bin_dir, body) == (0, "1\n1\n0")


def test_not_streaming_without_px4(tmp_path):
    bin_dir = fake_listener(tmp_path, {})
    assert run(bin_dir, "streaming; echo $?; streaming; echo $?") == (0, "1\n1")
