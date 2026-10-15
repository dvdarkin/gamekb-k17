"""Tests for gamekb.encode: golden bytes, decoding, round trips."""

from __future__ import annotations

import pytest

from gamekb.config import FACTORY, Config, KeyAction, parse_config
from gamekb.encode import (
    DeviceImage,
    LightingState,
    decode_action,
    decode_colors,
    decode_keymap,
    decode_lighting,
    describe_entry,
    encode,
    encode_action,
    image_to_config,
)
from gamekb.layout import COLOR_LEN, KEY_BY_NAME, KEYCODES, KEYMAP_LEN, KEYS, MEDIA, MODIFIER_KEYS

Q = KEY_BY_NAME["Q"]


@pytest.mark.parametrize(
    ("action", "hexstr"),
    [
        (KeyAction("shortcut", "Alt+T"), "20041700"),
        (KeyAction("shortcut", "Ctrl+C"), "20010600"),  # matches vendor web app capture
        (KeyAction("key", "Escape"), "20002900"),
        (KeyAction("key", "LCtrl"), "20010000"),
        (KeyAction("media", "PlayPause"), "30cd0000"),
        (FACTORY, "20001400"),
    ],
)
def test_encode_action_golden(action: KeyAction, hexstr: str) -> None:
    assert encode_action(Q, action) == bytes.fromhex(hexstr)


def test_encode_fills_every_key() -> None:
    img = encode(parse_config({
        "default_color": [1, 1, 1],
        "keys": {"Q": {"shortcut": "Ctrl+C", "color": [9, 8, 7]}},
    }))
    assert set(img.keys) == {k.pos for k in KEYS}
    assert set(img.colors) == {k.pos for k in KEYS}
    assert img.keys[1] == bytes.fromhex("20010600")
    assert img.keys[22] == KEY_BY_NAME["Space"].factory
    assert img.colors[1] == (9, 8, 7)
    assert img.colors[22] == (1, 1, 1)


def test_encode_lighting() -> None:
    per_key = encode(parse_config({"lighting": {"mode": "per-key", "brightness": 3}})).lighting
    assert per_key == LightingState(0x07, 3, (0x02, 0x00), (0, 0, 0))
    effect = encode(parse_config({"lighting": {
        "mode": "effect", "effect": "stream", "brightness": 2, "speed": 2,
    }})).lighting
    assert effect == LightingState(0x04, 2, (0x02, 0x03), (0, 0, 0))


def test_lighting_match_ignores_color_and_per_key_speed() -> None:
    # The pad never stores the 06 0B color bytes (verified 2026-10-14, all modes).
    per_key = LightingState(0x07, 4, (0x02, 0x00), (0, 0, 0))
    assert per_key.matches(LightingState(0x07, 4, (0x02, 0x00), (255, 255, 255)))
    assert not per_key.matches(LightingState(0x07, 3, (0x02, 0x00), (0, 0, 0)))
    breath = LightingState(0x02, 4, (0x02, 0x01), (0, 200, 255))
    assert breath.matches(LightingState(0x02, 4, (0x02, 0x01), (255, 255, 255)))
    assert not breath.matches(LightingState(0x02, 4, (0x02, 0x03), (0, 200, 255)))
    assert breath.matches(breath)


def test_decode_keymap_and_colors() -> None:
    keymap = bytearray(KEYMAP_LEN)
    keymap[4:8] = bytes.fromhex("20041700")
    colors = bytearray(COLOR_LEN)
    colors[0x42:0x45] = bytes([0, 255, 60])
    assert decode_keymap(bytes(keymap))[1] == bytes.fromhex("20041700")
    assert decode_colors(bytes(colors))[22] == (0, 255, 60)


def test_decode_lighting_from_probe_reply() -> None:
    reply = bytes.fromhex("aa 0a 0b 00 00 01 00 07 04 02 00 00 07 ff ff ff") + bytes(48)
    assert decode_lighting(reply) == LightingState(0x07, 4, (0x02, 0x00), (255, 255, 255))


@pytest.mark.parametrize(
    ("hexstr", "expected"),
    [
        ("20001400", FACTORY),
        ("20041700", KeyAction("shortcut", "Alt+T")),
        ("20002900", KeyAction("key", "Escape")),
        ("20020000", KeyAction("key", "LShift")),
        ("30cd0000", KeyAction("media", "PlayPause")),
        ("20202001", None),  # garbage left by overlapping writes of an earlier tool
        ("13000000", None),
        ("20000000", None),
    ],
)
def test_decode_action(hexstr: str, expected: KeyAction | None) -> None:
    assert decode_action(Q, bytes.fromhex(hexstr)) == expected


def test_every_expressible_action_round_trips() -> None:
    actions = (
        [KeyAction("key", n) for n in [*KEYCODES, *MODIFIER_KEYS]]
        + [KeyAction("media", n) for n in MEDIA]
        + [KeyAction("shortcut", "Ctrl+Shift+Alt+Win+F5")]
    )
    for action in actions:
        assert decode_action(Q, encode_action(Q, action)) in (action, FACTORY)


def test_describe_entry() -> None:
    assert describe_entry(Q, Q.factory) == "factory"
    assert describe_entry(Q, bytes.fromhex("20041700")) == "shortcut Alt+T"
    assert describe_entry(Q, bytes.fromhex("20202001")) == "raw 20 20 20 01"


def test_image_to_config_round_trip() -> None:
    cfg = parse_config({
        "lighting": {"mode": "effect", "effect": "breath", "brightness": 1, "speed": 0},
        "keys": {"Q": {"shortcut": "Ctrl+C", "color": [1, 2, 3]}, "C": {"media": "Next"}},
    })
    back, warnings = image_to_config(encode(cfg))
    assert warnings == []
    assert encode(back) == encode(cfg)


def test_image_to_config_warns_on_corrupted_entries() -> None:
    img = encode(Config())
    keys = dict(img.keys)
    keys[4] = bytes.fromhex("20202001")  # R, as found on the real pad
    back, warnings = image_to_config(DeviceImage(keys, img.colors, img.lighting, img.macros))
    assert back.keys["R"].action == FACTORY
    assert len(warnings) == 1
    assert warnings[0].startswith("R:") and "gamekb reset" in warnings[0]


def test_image_to_config_warns_on_unknown_lighting() -> None:
    img = encode(Config())
    odd = DeviceImage(img.keys, img.colors, LightingState(0x09, 4, (0x02, 0x00), (0, 0, 0)), img.macros)
    back, warnings = image_to_config(odd)
    assert back.lighting.mode == "per-key"
    assert any("lighting" in w for w in warnings)


def test_describe_lighting_shows_only_stored_fields() -> None:
    from gamekb.encode import describe_lighting

    assert describe_lighting(LightingState(0x07, 4, (0x02, 0x00), (255, 255, 255))) == "per-key, brightness 4"
    assert describe_lighting(LightingState(0x02, 3, (0x02, 0x01), (0, 0, 0))) == "effect breath, brightness 3, speed 1"


def test_image_to_config_never_emits_remaps_on_color_only_keys() -> None:
    from gamekb.config import config_to_dict

    img = encode(Config())
    keys = dict(img.keys)
    keys[5] = bytes.fromhex("30cd0000")  # dial press set to PlayPause (e.g. by the vendor app)
    back, warnings = image_to_config(DeviceImage(keys, img.colors, img.lighting, img.macros))
    assert back.keys["Mute"].action == FACTORY
    assert any(w.startswith("Mute:") for w in warnings)
    parse_config(config_to_dict(back))  # must be a config gamekb accepts

from gamekb.macro import build_macro_area, macro_bytes, split_macro_area  # noqa: E402
from tests.captures import M0_YES  # noqa: E402


def test_encode_macros_assigns_slots_in_key_order_and_dedupes() -> None:
    img = encode(parse_config({"keys": {
        "Space": {"macro": "yes{Enter}"},
        "W": {"macro": "no{Enter}"},
        "Q": {"macro": "yes{Enter}"},
    }}))
    assert img.keys[1] == bytes.fromhex("60000100")   # Q  -> M0
    assert img.keys[2] == bytes.fromhex("60010100")   # W  -> M1
    assert img.keys[22] == bytes.fromhex("60000100")  # Space shares M0
    assert split_macro_area(img.macros) == {
        0: macro_bytes("yes{Enter}", 20),
        1: macro_bytes("no{Enter}", 20),
    }


def test_encode_without_macros_has_empty_area() -> None:
    assert encode(Config()).macros == build_macro_area([])


def test_describe_macro_entry() -> None:
    assert describe_entry(Q, bytes.fromhex("60030100")) == "macro M3"


def test_image_to_config_reads_macros() -> None:
    cfg = parse_config({"keys": {"Space": {"macro": "Hello{Enter}", "delay_ms": 7}}})
    back, warnings = image_to_config(encode(cfg))
    assert warnings == []
    assert back.keys["Space"].action == KeyAction("macro", "Hello{Enter}", 7)


def test_image_to_config_normalizes_vendor_recording() -> None:
    img = encode(Config())
    keys = dict(img.keys)
    keys[22] = bytes.fromhex("60000100")
    back, warnings = image_to_config(DeviceImage(keys, img.colors, img.lighting, build_macro_area([M0_YES])))
    assert back.keys["Space"].action == KeyAction("macro", "yes{Enter}", 52)
    assert warnings == ["Space: macro M0 timings normalized to 52 ms"]


def test_image_to_config_missing_macro_falls_back() -> None:
    img = encode(Config())
    keys = dict(img.keys)
    keys[22] = bytes.fromhex("60050100")
    back, warnings = image_to_config(DeviceImage(keys, img.colors, img.lighting, img.macros))
    assert back.keys["Space"].action == FACTORY
    assert warnings[0].startswith("Space: macro M5 cannot be expressed")


def test_image_to_config_keeps_fn_remap() -> None:
    img = encode(Config())
    keys = dict(img.keys)
    keys[KEY_BY_NAME["FN"].pos] = bytes.fromhex("20002900")  # Escape
    back, warnings = image_to_config(DeviceImage(keys, img.colors, img.lighting, img.macros))
    assert back.keys["FN"].action == KeyAction("key", "Escape")
    assert warnings == []
