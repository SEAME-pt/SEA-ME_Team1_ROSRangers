"""Local bridge settings and secrets, kept outside Git in ~/.config/seame-drone/.

bridge.json  : the active scenario (test or production), ESP and PC addresses, ports
secrets.json : Wi-Fi passwords (mode 600). Never copy this file into a repository.
"""

import getpass
import json
import pathlib

CONFIG_DIR = pathlib.Path.home() / ".config" / "seame-drone"
PROFILE = CONFIG_DIR / "bridge.json"
SECRETS = CONFIG_DIR / "secrets.json"

BRIDGE_PORT = 5760  # UDP port used on both sides (ESP local port and PC port)


def _ensure_dir():
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_DIR.chmod(0o700)


def load_profile() -> dict:
    try:
        return json.loads(PROFILE.read_text())
    except FileNotFoundError:
        return {}


def save_profile(profile: dict):
    _ensure_dir()
    PROFILE.write_text(json.dumps(profile, indent=2) + "\n")


def _load_secrets() -> dict:
    try:
        return json.loads(SECRETS.read_text())
    except FileNotFoundError:
        return {}


def get_secret(key: str, prompt: str, min_len: int = 8, ask_again: bool = False) -> str:
    """Return a stored password, or ask for it once and store it (mode 600)."""
    secrets = _load_secrets()
    if key in secrets and not ask_again:
        return secrets[key]
    while True:
        value = getpass.getpass(prompt)
        if min_len <= len(value) <= 63 and not any(c in value for c in '",\\'):
            break
        print(f"Use {min_len}-63 characters, without quotes, commas or backslashes.")
    secrets[key] = value
    _ensure_dir()
    SECRETS.write_text(json.dumps(secrets, indent=2) + "\n")
    SECRETS.chmod(0o600)
    return value


def forget_secrets():
    if SECRETS.exists():
        SECRETS.unlink()
