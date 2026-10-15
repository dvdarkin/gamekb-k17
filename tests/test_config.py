"""Tests for gamekb.config: parsing, validation, round trips."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from gamekb.config import (
    FACTORY,
    Config,
    ConfigError,
    KeyAction,
    KeyConfig,
    Lighting,
    config_to_dict,
    load_config,
    parse_config,
    save_config,
)


def errors_of(raw: object) -> list[str]:
    with pytest.raises(ConfigError) as info:
        parse_config(raw)
    return info.value.errors


def test_empty_object_is_all_factory() -> None:
    cfg = parse_config({})
    assert cfg == Config()
    assert cfg.lighting == Lighting(mode="per-key", brightness=4)


def test_full_example_parses() -> None:
    cfg = parse_config({
        "lighting": {"mode": "per-key", "brightness": 3},
        "default_color": [1, 2, 3],
        "keys": {
            "Q": {"shortcut": "Alt+T", "label": "Thinking", "color": [160, 60, 255]},
            "E": {"key": "Enter"},
            "Tab": {"media": "PlayPause"},
            "LShift": {"key": "LCtrl"},
            "W": {"color": [50, 50, 55]},
        },
    })
    assert cfg.lighting.brightness == 3
    assert cfg.default_color == (1, 2, 3)
    assert cfg.keys["Q"] == KeyConfig(KeyAction("shortcut", "Alt+T"), (160, 60, 255), "Thinking")
    assert cfg.keys["E"].action == KeyAction("key", "Enter")
    assert cfg.keys["Tab"].action == KeyAction("media", "PlayPause")
    assert cfg.keys["LShift"].action == KeyAction("key", "LCtrl")
    assert cfg.keys["W"] == KeyConfig(FACTORY, (50, 50, 55), "")


def test_effect_lighting() -> None:
    cfg = parse_config({"lighting": {
        "mode": "effect", "effect": "stream", "brightness": 3, "speed": 2,
    }})
    assert cfg.lighting == Lighting("effect", 3, "stream", 2)


def test_all_errors_reported_at_once() -> None:
    errs = errors_of({
        "hotkey": "Ctrl+Shift+ScrollLock",
        "lighting": {"mode": "per-key", "brightness": 9},
        "keys": {
            "Q": {"shortcut": "Hyper+C"},
            "Nope": {},
            "E": {"key": "Enter", "shortcut": "Ctrl+C"},
        },
    })
    assert len(errs) == 5
    joined = "\n".join(errs)
    for fragment in ("hotkey", "brightness", "Hyper", "Nope", "only one of"):
        assert fragment in joined


def test_macro_action() -> None:
    cfg = parse_config({"keys": {
        "Space": {"macro": "yes{Enter}"},
        "W": {"macro": " git\n", "delay_ms": 5},
    }})
    assert cfg.keys["Space"].action == KeyAction("macro", "yes{Enter}", 20)
    assert cfg.keys["W"].action == KeyAction("macro", " git\n", 5)  # macro text is never stripped


def test_macro_size_limit() -> None:
    errs = errors_of({"keys": {"Space": {"macro": "a" * 505}}})  # 1010 events = 4040 bytes
    assert any("4032" in e for e in errs), errs


def test_macro_dict_round_trip() -> None:
    raw: dict[str, Any] = {
        "lighting": {"mode": "per-key", "brightness": 4},
        "default_color": [0, 0, 0],
        "keys": {"W": {"macro": "no\n", "delay_ms": 5}, "Space": {"macro": "yes{Enter}"}},
    }
    assert config_to_dict(parse_config(raw)) == raw


@pytest.mark.parametrize(
    ("raw", "fragment"),
    [
        ({"keys": {"space": {}}}, "did you mean 'Space'"),
        ({"keys": {"Q": {"key": "escape"}}}, "did you mean 'Escape'"),
        ({"keys": {"Q": {"key": "TBD"}}}, "unknown key 'TBD'"),
        ({"keys": {"Q": {"shortcut": ""}}}, "non-empty string"),
        ({"keys": {"Q": {"color": [256, 0, 0]}}}, "keys.Q.color"),
        ({"keys": {"Q": {"color": "red"}}}, "keys.Q.color"),
        ({"keys": {"Q": {"colour": [1, 2, 3]}}}, "unknown field 'colour'"),
        ({"keys": {"Q": "Escape"}}, "keys.Q must be an object"),
        ({"keys": {"Q": {"media": "Louder"}}}, "unknown media action"),
        ({"keys": {"Space": {"macro": "{Nope}"}}}, "keys.Space.macro: unknown token {Nope}"),
        ({"keys": {"Space": {"macro": ""}}}, "keys.Space.macro must be a non-empty string"),
        ({"keys": {"Space": {"key": "A", "delay_ms": 5}}}, "keys.Space.delay_ms is only valid with macro"),
        ({"keys": {"Space": {"macro": "a", "delay_ms": -1}}}, "keys.Space.delay_ms"),
        ({"keys": {"Mute": {"macro": "a"}}}, "cannot be remapped"),
        ({"lighting": {"mode": "effect", "effect": "userlight"}}, "lighting.effect"),
        ({"lighting": {"mode": "per-key", "effect": "stream"}}, "not valid for mode"),
        ({"lighting": {"mode": "rainbow"}}, "lighting.mode"),
        # effects use the firmware's built-in color cycle; there is no effect color
        ({"lighting": {"mode": "effect", "color": [1, 2, 3]}}, "lighting.color is not valid for mode 'effect'"),
        ({"lighting": {"mode": "per-key", "brightness": True}}, "lighting.brightness"),
        ({"keys": []}, "keys must be an object"),
        ([], "top level"),
    ],
)
def test_specific_errors(raw: object, fragment: str) -> None:
    errs = errors_of(raw)
    assert any(fragment in e for e in errs), errs


def test_dict_round_trip() -> None:
    raw: dict[str, Any] = {
        "lighting": {"mode": "effect", "brightness": 2, "effect": "breath", "speed": 0},
        "default_color": [9, 9, 9],
        "keys": {
            "Q": {"shortcut": "Ctrl+C", "label": "copy", "color": [4, 5, 6]},
            "Space": {"media": "Mute"},
        },
    }
    assert config_to_dict(parse_config(raw)) == raw


def test_load_and_save(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    cfg = parse_config({"keys": {"A": {"key": "Escape"}}})
    save_config(cfg, path)
    assert load_config(path) == cfg


def test_load_reports_bad_json_and_missing_file(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(ConfigError, match="invalid JSON"):
        load_config(bad)
    with pytest.raises(ConfigError, match="cannot read"):
        load_config(tmp_path / "missing.json")


def test_hardware_keys_are_color_only() -> None:
    errs = errors_of({"keys": {"Mute": {"media": "PlayPause"}}})
    assert errs == ["keys.Mute: this key cannot be remapped on the pad; only 'color' and 'label' apply"]
    assert parse_config({"keys": {"Mute": {"color": [1, 2, 3], "label": "dial"}}}).keys["Mute"].color == (1, 2, 3)


def test_bright_and_fn_keys_accept_actions() -> None:
    # 2026-10-14 hardware check: Bright+, Bright- and FN remaps take effect (only the dial ignores them).
    cfg = parse_config({"keys": {
        "Bright+": {"shortcut": "Shift+Tab"},
        "Bright-": {"key": "Escape"},
        "FN": {"macro": "continue{Enter}"},
    }})
    assert cfg.keys["Bright+"].action == KeyAction("shortcut", "Shift+Tab")
    assert cfg.keys["FN"].action == KeyAction("macro", "continue{Enter}")
    for dial in ("Mute", "Vol+", "Vol-"):
        assert errors_of({"keys": {dial: {"key": "Escape"}}})


def test_save_writes_one_key_per_line(tmp_path: Path) -> None:
    path = tmp_path / "config.json"
    raw = {"keys": {"Q": {"shortcut": "Alt+T", "label": "think", "color": [1, 2, 3]}, "Space": {"key": "Enter"}}}
    save_config(parse_config(raw), path)
    lines = path.read_text(encoding="utf-8").splitlines()
    assert '  "default_color": [0, 0, 0],' in lines
    assert '    "Q":     { "shortcut": "Alt+T", "label": "think", "color": [1, 2, 3] },' in lines
    assert '    "Space": { "key": "Enter" }' in lines
    assert load_config(path) == parse_config(raw)


def test_shipped_files_are_in_save_format(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parent.parent
    for path in [root / "config.json", *sorted((root / "profiles").glob("*.json"))]:
        out = tmp_path / path.name
        save_config(load_config(path), out)
        assert out.read_text(encoding="utf-8") == path.read_text(encoding="utf-8"), path.name
