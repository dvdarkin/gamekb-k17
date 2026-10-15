"""Physical layout and wire tables for the GAMEKB-K17 pad.

One index space: every key has a firmware position `pos`.
Its color lives at byte offset 3*pos, its keymap entry at 4*pos.
Verified on hardware 2026-10-14 (see docs/PROTOCOL.md).
"""

from __future__ import annotations

from dataclasses import dataclass

RGB = tuple[int, int, int]

VENDOR_ID = 0x0816
PRODUCT_ID = 0x0220
USAGE_PAGE = 0xFF00
USAGE = 0x02

ENTRY_KEYBOARD = 0x20  # [0x20, modifier mask, HID keycode, 0]
ENTRY_MEDIA = 0x30     # [0x30, consumer usage, 0, 0]
ENTRY_MACRO = 0x60     # [0x60, macro slot, 0x01, 0]

MACRO_AREA_LEN = 0x1000   # 06 0C / 06 0D address space
MACRO_WRITE_CHUNK = 0x3B  # max data bytes per 06 0D write (vendor alignment)

KEYMAP_SLOTS = 24                  # positions 0..23
KEYMAP_LEN = 4 * KEYMAP_SLOTS      # 96
COLOR_LEN = 3 * KEYMAP_SLOTS       # 72 (0x48)
FACTORY_KEYMAP_OFFSET = COLOR_LEN  # factory keymap follows the colors in the 06 13 region


class LayoutError(ValueError):
    """A key, modifier or shortcut name that the pad cannot express."""


@dataclass(frozen=True)
class Key:
    name: str
    pos: int
    factory: bytes  # 4-byte keymap entry as shipped
    row: float      # GUI grid row
    col: float      # GUI grid column
    width: float = 1.0

    @property
    def color_offset(self) -> int:
        return 3 * self.pos

    @property
    def keymap_offset(self) -> int:
        return 4 * self.pos


def _e(hexstr: str) -> bytes:
    return bytes.fromhex(hexstr)


KEYS: tuple[Key, ...] = (
    Key("Q", 1, _e("20001400"), 1, 1),
    Key("W", 2, _e("20001a00"), 1, 2),
    Key("E", 3, _e("20000800"), 1, 3),
    Key("R", 4, _e("20001500"), 1, 4),
    Key("Mute", 5, _e("30e20000"), 1, 6),
    Key("Tab", 6, _e("20002b00"), 2, 0),
    Key("A", 7, _e("20000400"), 2, 1),
    Key("S", 8, _e("20001600"), 2, 2),
    Key("D", 9, _e("20000700"), 2, 3),
    Key("F", 10, _e("20000900"), 2, 4),
    Key("Vol+", 11, _e("30e90000"), 2, 6),
    Key("LShift", 12, _e("20020000"), 3, 0),
    Key("Z", 13, _e("20001d00"), 3, 1),
    Key("X", 14, _e("20001b00"), 3, 2),
    Key("C", 15, _e("20000600"), 3, 3),
    Key("V", 16, _e("20000a00"), 3, 4),  # keycap V; factory output is g
    Key("Vol-", 17, _e("30ea0000"), 3, 6),
    Key("Bright+", 18, _e("306f0000"), 0, 1.5),
    Key("Bright-", 19, _e("30700000"), 0, 2.5),
    Key("Space", 22, _e("20002c00"), 4, 1, 2.0),
    Key("FN", 23, _e("2000ff00"), 4, 5),
)
KEY_BY_NAME: dict[str, Key] = {k.name: k for k in KEYS}
PLACEHOLDER_POSITIONS: tuple[int, ...] = (0, 20, 21)  # hold 13 00 00 00; never written

KEYCODES: dict[str, int] = {
    "Escape": 0x29, "Enter": 0x28, "Tab": 0x2B, "Space": 0x2C,
    "Backspace": 0x2A, "Delete": 0x4C,
    "Up": 0x52, "Down": 0x51, "Left": 0x50, "Right": 0x4F,
    "Home": 0x4A, "End": 0x4D, "PageUp": 0x4B, "PageDown": 0x4E,
    "CapsLock": 0x39, "PrintScreen": 0x46, "ScrollLock": 0x47,
    "Pause": 0x48, "Insert": 0x49,
    "F1": 0x3A, "F2": 0x3B, "F3": 0x3C, "F4": 0x3D,
    "F5": 0x3E, "F6": 0x3F, "F7": 0x40, "F8": 0x41,
    "F9": 0x42, "F10": 0x43, "F11": 0x44, "F12": 0x45,
    "F13": 0x68, "F14": 0x69, "F15": 0x6A, "F16": 0x6B,
    "F17": 0x6C, "F18": 0x6D, "F19": 0x6E, "F20": 0x6F,
    "F21": 0x70, "F22": 0x71, "F23": 0x72, "F24": 0x73,
    "1": 0x1E, "2": 0x1F, "3": 0x20, "4": 0x21, "5": 0x22,
    "6": 0x23, "7": 0x24, "8": 0x25, "9": 0x26, "0": 0x27,
    "A": 0x04, "B": 0x05, "C": 0x06, "D": 0x07, "E": 0x08,
    "F": 0x09, "G": 0x0A, "H": 0x0B, "I": 0x0C, "J": 0x0D,
    "K": 0x0E, "L": 0x0F, "M": 0x10, "N": 0x11, "O": 0x12,
    "P": 0x13, "Q": 0x14, "R": 0x15, "S": 0x16, "T": 0x17,
    "U": 0x18, "V": 0x19, "W": 0x1A, "X": 0x1B, "Y": 0x1C,
    "Z": 0x1D,
}
KEYCODE_NAMES: dict[int, str] = {v: k for k, v in KEYCODES.items()}

# Modifiers inside a shortcut string ("Ctrl+C"); bit order defines formatting order.
MODIFIERS: dict[str, int] = {
    "Ctrl": 0x01, "Shift": 0x02, "Alt": 0x04, "Win": 0x08,
    "RCtrl": 0x10, "RShift": 0x20, "RAlt": 0x40, "RWin": 0x80,
}
# A key that sends only a modifier (factory LShift = 20 02 00 00).
MODIFIER_KEYS: dict[str, int] = {
    "LCtrl": 0x01, "LShift": 0x02, "LAlt": 0x04, "LWin": 0x08,
    "RCtrl": 0x10, "RShift": 0x20, "RAlt": 0x40, "RWin": 0x80,
}
MODIFIER_KEY_BY_BIT: dict[int, str] = {v: k for k, v in MODIFIER_KEYS.items()}
# HID usages of the modifier keys (LCtrl..RWin), used for modifier events inside macros.
MODIFIER_USAGES: dict[int, int] = {
    0x01: 0xE0, 0x02: 0xE1, 0x04: 0xE2, 0x08: 0xE3,
    0x10: 0xE4, 0x20: 0xE5, 0x40: 0xE6, 0x80: 0xE7,
}

# HID consumer-page usages (entry type 0x30).
MEDIA: dict[str, int] = {
    "Mute": 0xE2, "VolumeUp": 0xE9, "VolumeDown": 0xEA,
    "PlayPause": 0xCD, "Next": 0xB5, "Prev": 0xB6, "Stop": 0xB7,
    "BrightnessUp": 0x6F, "BrightnessDown": 0x70,
}
MEDIA_NAMES: dict[int, str] = {v: k for k, v in MEDIA.items()}

EFFECTS: dict[str, int] = {
    "off": 0x00, "static": 0x01, "breath": 0x02, "colorloop": 0x03,
    "stream": 0x04, "meteor": 0x05, "trigspread": 0x06, "userlight": 0x07,
}
EFFECT_NAMES: dict[int, str] = {v: k for k, v in EFFECTS.items()}

# Config speed 0-2 -> wire bytes [9], [10] of 06 0B (speed 2 is 0x03, not 0x02).
SPEED_WIRE: dict[int, tuple[int, int]] = {0: (0x02, 0x00), 1: (0x02, 0x01), 2: (0x02, 0x03)}
SPEED_INDEX: dict[tuple[int, int], int] = {v: k for k, v in SPEED_WIRE.items()}


def parse_shortcut(text: str) -> tuple[int, int]:
    """'Ctrl+Shift+Z' -> (modifier mask, HID keycode)."""
    parts = [p.strip() for p in text.split("+")]
    if len(parts) < 2 or any(not p for p in parts):
        raise LayoutError(f"shortcut {text!r} must look like 'Mod+Key', e.g. 'Ctrl+C'")
    mask = 0
    for mod in parts[:-1]:
        bit = MODIFIERS.get(mod)
        if bit is None:
            raise LayoutError(
                f"unknown modifier {mod!r} in {text!r}; valid: {', '.join(MODIFIERS)}"
            )
        mask |= bit
    code = KEYCODES.get(parts[-1])
    if code is None:
        raise LayoutError(f"unknown key {parts[-1]!r} in {text!r}")
    return mask, code


def format_shortcut(mask: int, code: int) -> str | None:
    """Inverse of parse_shortcut; None if the keycode has no name."""
    key = KEYCODE_NAMES.get(code)
    if key is None:
        return None
    mods = [name for name, bit in MODIFIERS.items() if mask & bit]
    return "+".join([*mods, key])
