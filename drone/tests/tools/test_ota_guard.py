"""Unit tests of drone/firmware/esp8266/ota_guard.py (#218): which OTA envs
secrets.py lets build. No PlatformIO, no ESP."""

import pathlib
import sys

sys.path.insert(
    0,
    str(pathlib.Path(__file__).resolve().parents[3] / "drone" / "firmware" / "esp8266"),
)

import ota_guard  # noqa: E402

# What platformio.ini gives an OTA env on dev since #218.
GOOD = {
    "board_build.f_flash": "80000000L",
    "platform": "espressif8266@4.2.1",
    "board": "esp12e",
    "board_build.ldscript": "",
}


def option(overrides=None):
    values = dict(GOOD, **(overrides or {}))
    return lambda name: values.get(name, "")


def test_flash_hz_reads_both_spellings():
    assert ota_guard.flash_hz("80000000L") == 80_000_000
    assert ota_guard.flash_hz("80000000") == 80_000_000
    assert ota_guard.flash_hz(" 40000000l ") == 40_000_000
    assert ota_guard.flash_hz("") is None
    assert ota_guard.flash_hz("80MHz") is None


def test_the_dev_ota_env_passes():
    assert ota_guard.ota_refusal(option()) is None


def test_80_mhz_without_the_l_passes():
    assert ota_guard.ota_refusal(option({"board_build.f_flash": "80000000"})) is None


def test_the_esp12e_ldscript_written_out_passes():
    ldscript = {"board_build.ldscript": "eagle.flash.4m1m.ld"}
    assert ota_guard.ota_refusal(option(ldscript)) is None


def test_a_40_mhz_env_is_refused_and_told_to_merge_dev():
    refusal = ota_guard.ota_refusal(option({"board_build.f_flash": "40000000L"}))
    assert "40000000 Hz, not 80000000 Hz" in refusal
    assert "merge dev" in refusal


def test_no_clock_means_the_board_default_and_is_refused():
    refusal = ota_guard.ota_refusal(option({"board_build.f_flash": ""}))
    assert "the board default (40 MHz)" in refusal
    assert "over USB" in refusal


def test_a_platform_bump_is_refused():
    refusal = ota_guard.ota_refusal(option({"platform": "espressif8266@4.3.0"}))
    assert "platform is espressif8266@4.3.0" in refusal
    assert "merge dev" not in refusal


def test_another_board_or_flash_layout_is_refused():
    assert "board is nodemcuv2" in ota_guard.ota_refusal(option({"board": "nodemcuv2"}))
    layout = {"board_build.ldscript": "eagle.flash.4m2m.ld"}
    assert "eagle.flash.4m2m.ld" in ota_guard.ota_refusal(option(layout))


def test_every_problem_is_named():
    refusal = ota_guard.ota_refusal(
        option({"board_build.f_flash": "40000000L", "platform": "espressif8266@4.3.0"})
    )
    assert "f_flash" in refusal and "platform" in refusal
