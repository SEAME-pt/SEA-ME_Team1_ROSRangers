# ruff: noqa: F821  (Import and env are provided by PlatformIO)
"""Pre-build script: Wi-Fi and OTA secrets from ~/.config/seame-drone/.

Same files as scripts/bridge_config.py, so nothing secret lives in the repo.
Without that folder (CI), set SEAME_AP_SSID, SEAME_AP_PASS and SEAME_OTA_PASS.
"""

import json
import os
import pathlib

Import("env")

CONFIG_DIR = pathlib.Path.home() / ".config" / "seame-drone"


def load(name):
    path = CONFIG_DIR / name
    return json.loads(path.read_text()) if path.exists() else {}


profile, secrets = load("bridge.json"), load("secrets.json")
if profile and profile.get("scenario") != "production":
    env.Exit("ERROR: bridge.json is the TEST scenario. Run setup_production.py first.")

ssid = os.environ.get("SEAME_AP_SSID") or profile.get("ssid", "")
if not ssid:
    # An image with no SSID has no network to join, so OTA recovery is lost.
    env.Exit("ERROR: AP_SSID missing. Run setup_production.py or set SEAME_AP_SSID.")
values = {
    "AP_SSID": ssid,
    "AP_PASS": os.environ.get("SEAME_AP_PASS") or secrets.get(f"wifi:{ssid}", ""),
    "OTA_PASS": os.environ.get("SEAME_OTA_PASS") or secrets.get("ota", ""),
}
for name, value in values.items():
    if name != "AP_SSID" and len(value) < 8:
        env.Exit(f"ERROR: {name} missing or shorter than 8 characters.")

env.Append(
    CPPDEFINES=[(k, env.StringifyMacro(v)) for k, v in values.items()]
    + [("AP_CHANNEL", int(profile.get("channel", 6)))]
)
if env.GetProjectOption("upload_protocol", "") == "espota":
    # UPLOAD_FLAGS, not UPLOADERFLAGS: the platform builder runs after this
    # pre: script and replaces UPLOADERFLAGS for espota.
    env.Append(UPLOAD_FLAGS=[f"--auth={values['OTA_PASS']}"])
