"""OTA guard (#218): which build settings an OTA image may have.

After an OTA, eboot compares the bootloader sector (the first 4 KB of flash:
eboot and the image header) with the new image's and rewrites it when they
differ. A power loss during that rewrite leaves the ESP without a bootloader,
and on the drone the ESP is soldered to the FC: recovery means USB with the
ESP off the FC (ADR-012). So an OTA env must build the same sector 0 as the
drone runs:

  * flash clock 80 MHz (board_build.f_flash),
  * the same platform (a new platform can bring a new eboot),
  * the same board and flash layout (the ldscript sets the flash size).

The mode byte is not checked: the core's Updater sets an uncompressed image's
mode byte to the running one (we never upload .bin.gz).

This checks the build, not the drone: after a USB restore of a 40 MHz backup,
an 80 MHz OTA still passes and rewrites sector 0. Read the running clock first
(`bus` in late_log.py) whenever the drone may not run what dev builds.

secrets.py calls ota_refusal() for every espota env. It is a separate module
so that drone/tests/tools/test_ota_guard.py can test it without PlatformIO.
"""

OTA_FLASH_HZ = 80_000_000
OTA_PLATFORM = "espressif8266@4.2.1"
OTA_BOARD = "esp12e"
# esp12e's default, the layout the drone runs (4 MB, ~1 MB for the app).
OTA_LDSCRIPT = "eagle.flash.4m1m.ld"


def flash_hz(value):
    """board_build.f_flash as an int, or None when it is unset or not a
    number. PlatformIO writes it with or without a trailing L:
    "80000000L" -> 80000000, "80000000" -> 80000000."""
    text = str(value).strip().upper()
    if text.endswith("L"):
        text = text[:-1]
    if not text.isdigit():
        return None
    return int(text)


def ota_refusal(option):
    """`option(name)` gives a platformio.ini option of this env ("" when
    unset). Returns None when an OTA image of this env keeps the drone's
    bootloader sector, else the error to stop the build with."""
    problems = []
    hz = flash_hz(option("board_build.f_flash"))
    if hz != OTA_FLASH_HZ:
        if hz is None:
            shown = "the board default (40 MHz)"
        else:
            shown = f"{hz} Hz"
        problems.append(f"board_build.f_flash is {shown}, not {OTA_FLASH_HZ} Hz")
    platform = option("platform")
    if platform != OTA_PLATFORM:
        problems.append(f"platform is {platform or 'unset'}, not {OTA_PLATFORM}")
    board = option("board")
    if board != OTA_BOARD:
        problems.append(f"board is {board or 'unset'}, not {OTA_BOARD}")
    ldscript = option("board_build.ldscript")
    if ldscript not in ("", OTA_LDSCRIPT):
        problems.append(f"board_build.ldscript is {ldscript}, not {OTA_LDSCRIPT}")
    if not problems:
        return None
    advice = (
        "Flash it over USB (env:esp, ESP off the FC) instead, or ask the safety pilot."
    )
    if hz is not None and hz < OTA_FLASH_HZ:
        advice = "This branch predates #218: merge dev into it before any OTA."
    return (
        "ERROR: this OTA image would rewrite the drone's bootloader sector "
        f"(#218, ADR-006): {'; '.join(problems)}. {advice}"
    )
