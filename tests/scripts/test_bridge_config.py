import pathlib
import stat
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "scripts"))

import bridge_config as cfg  # noqa: E402


def use_tmp(monkeypatch, tmp_path):
    monkeypatch.setattr(cfg, "CONFIG_DIR", tmp_path / "cfg")
    monkeypatch.setattr(cfg, "PROFILE", tmp_path / "cfg" / "bridge.json")
    monkeypatch.setattr(cfg, "SECRETS", tmp_path / "cfg" / "secrets.json")


def test_secret_asked_once_and_private(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    answers = iter(["short", 'has"quote', "goodpassword"])
    monkeypatch.setattr(cfg.getpass, "getpass", lambda prompt: next(answers))
    assert cfg.get_secret("wifi:X", "pw: ") == "goodpassword"
    monkeypatch.setattr(
        cfg.getpass, "getpass", lambda prompt: (_ for _ in ()).throw(AssertionError)
    )
    assert cfg.get_secret("wifi:X", "pw: ") == "goodpassword"
    assert stat.S_IMODE(cfg.SECRETS.stat().st_mode) == 0o600
    assert stat.S_IMODE(cfg.CONFIG_DIR.stat().st_mode) == 0o700


def test_profile_roundtrip(monkeypatch, tmp_path):
    use_tmp(monkeypatch, tmp_path)
    assert cfg.load_profile() == {}
    cfg.save_profile({"scenario": "test", "esp_ip": "10.20.0.5"})
    assert cfg.load_profile()["esp_ip"] == "10.20.0.5"
