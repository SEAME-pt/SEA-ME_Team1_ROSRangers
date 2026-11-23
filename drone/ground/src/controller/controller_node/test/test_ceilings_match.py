"""The velocity ceilings and masks the controller applies (ADR-025 F9) are
mission_node's: copies that must not drift apart. The guard's attitude caps
against the controller are checked in flight_interface's test_guard.py.

Needs mission_node installed (CI's ground-ros2 has both packages); skipped
without it. smoke_test.sh deselects it inside the controller image, which has
no mission_node.
"""

import pytest

from controller_node.controller_logic import (
    CEILING_LINEAR_MPS,
    CEILING_YAW_DPS,
    MASK_AXES,
)


def test_ceilings_and_masks_match_mission_node():
    mission_logic = pytest.importorskip("mission_node.mission_logic")
    assert CEILING_LINEAR_MPS == mission_logic.CEILING_LINEAR_MPS
    assert CEILING_YAW_DPS == mission_logic.CEILING_YAW_DPS
    ours = {m: set(a) for m, a in MASK_AXES.items()}
    assert ours == {m: set(a) for m, a in mission_logic.MASK_AXES.items()}
