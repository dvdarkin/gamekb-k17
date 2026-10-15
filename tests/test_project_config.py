"""The shipped config.json must stay valid."""

from __future__ import annotations

from pathlib import Path

from gamekb.config import KeyAction, load_config
from gamekb.encode import encode

ROOT = Path(__file__).resolve().parent.parent


def test_shipped_config_is_valid() -> None:
    cfg = load_config(ROOT / "config.json")
    assert cfg.keys["Q"].action == KeyAction("shortcut", "Alt+T")
    assert cfg.keys["Space"].color == (0, 255, 60)
    assert cfg.keys["Space"].action == KeyAction("macro", "yes{Enter}")
    encode(cfg)


def test_shipped_profiles_are_valid() -> None:
    from gamekb.profiles import list_profiles

    directory = ROOT / "profiles"
    assert {"claude", "default"} <= set(list_profiles(directory))
    for name in list_profiles(directory):
        encode(load_config(directory / f"{name}.json"))


def test_claude_profile_destructive_keys_wait_for_enter() -> None:
    cfg = load_config(ROOT / "profiles" / "claude.json")
    assert cfg.keys["Tab"].action == KeyAction("macro", "/clear")
    assert cfg.keys["LShift"].action == KeyAction("macro", "/compact ")
    assert cfg.keys["V"].action == KeyAction("key", "Enter")
    assert cfg.keys["FN"].action == KeyAction("macro", "continue{Enter}")
