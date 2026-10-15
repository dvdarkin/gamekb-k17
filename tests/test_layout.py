"""Tests for gamekb.layout: positions, offsets, tables, shortcut parsing."""

from __future__ import annotations

import pytest

from gamekb.layout import (
    COLOR_LEN,
    KEY_BY_NAME,
    KEYCODES,
    KEYMAP_LEN,
    KEYS,
    MEDIA,
    MODIFIER_KEYS,
    MODIFIERS,
    PLACEHOLDER_POSITIONS,
    LayoutError,
    format_shortcut,
    parse_shortcut,
)


def test_21_physical_keys_with_unique_positions() -> None:
    assert len(KEYS) == 21
    positions = [k.pos for k in KEYS]
    assert len(set(positions)) == 21
    assert set(positions).isdisjoint(PLACEHOLDER_POSITIONS)
    assert all(1 <= p <= 23 for p in positions)


def test_offsets_follow_position() -> None:
    for k in KEYS:
        assert k.color_offset == 3 * k.pos
        assert k.keymap_offset == 4 * k.pos


def test_keymap_entries_do_not_overlap() -> None:
    spans = sorted((k.keymap_offset, k.keymap_offset + 4) for k in KEYS)
    for (_, end), (start, _) in zip(spans, spans[1:]):
        assert end <= start
    assert spans[-1][1] <= KEYMAP_LEN


def test_color_entries_fit_region() -> None:
    assert max(k.color_offset + 3 for k in KEYS) <= COLOR_LEN


def test_offsets_verified_on_hardware() -> None:
    # 2026-10-14 probe: W remap landed at keymap offset 8; colors read back at these offsets.
    assert KEY_BY_NAME["Q"].keymap_offset == 4
    assert KEY_BY_NAME["W"].keymap_offset == 8
    assert KEY_BY_NAME["Space"].keymap_offset == 88
    assert KEY_BY_NAME["Q"].color_offset == 0x03
    assert KEY_BY_NAME["Space"].color_offset == 0x42
    assert KEY_BY_NAME["FN"].color_offset == 0x45


def test_factory_entries() -> None:
    assert all(len(k.factory) == 4 for k in KEYS)
    assert KEY_BY_NAME["Q"].factory == bytes.fromhex("20001400")
    assert KEY_BY_NAME["Mute"].factory == bytes.fromhex("30e20000")
    assert KEY_BY_NAME["LShift"].factory == bytes.fromhex("20020000")
    assert KEY_BY_NAME["FN"].factory == bytes.fromhex("2000ff00")


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Ctrl+C", (0x01, 0x06)),
        ("Alt+T", (0x04, 0x17)),
        ("Ctrl+Shift+Z", (0x03, 0x1D)),
        ("Shift+Tab", (0x02, 0x2B)),
        (" Ctrl + Backspace ", (0x01, 0x2A)),
        ("RShift+3", (0x20, 0x20)),
    ],
)
def test_parse_shortcut(text: str, expected: tuple[int, int]) -> None:
    assert parse_shortcut(text) == expected


@pytest.mark.parametrize("text", ["C", "Ctrl+", "+C", "Hyper+C", "Ctrl+Nope", ""])
def test_parse_shortcut_rejects(text: str) -> None:
    with pytest.raises(LayoutError):
        parse_shortcut(text)


def test_format_shortcut() -> None:
    assert format_shortcut(0x03, 0x1D) == "Ctrl+Shift+Z"
    assert format_shortcut(0x01, 0xEE) is None


def test_tables_are_consistent() -> None:
    assert set(MODIFIER_KEYS.values()) == set(MODIFIERS.values())
    assert len(set(KEYCODES.values())) == len(KEYCODES)
    assert len(set(MEDIA.values())) == len(MEDIA)


def test_bottom_row_ends_with_v() -> None:
    # The keycap at position 16 is V (factory output is 'g'); there is no G key.
    assert KEY_BY_NAME["V"].pos == 16
    assert "G" not in KEY_BY_NAME
