"""Config <-> device image conversion. Pure functions, no I/O."""

from __future__ import annotations

from dataclasses import dataclass

from .config import COLOR_ONLY_KEYS, FACTORY, Config, KeyAction, KeyConfig, Lighting
from .layout import (
    COLOR_LEN,
    EFFECT_NAMES,
    EFFECTS,
    ENTRY_KEYBOARD,
    ENTRY_MACRO,
    ENTRY_MEDIA,
    KEYCODE_NAMES,
    KEYCODES,
    KEYMAP_LEN,
    KEYS,
    MEDIA,
    MEDIA_NAMES,
    MODIFIER_KEY_BY_BIT,
    MODIFIER_KEYS,
    RGB,
    SPEED_INDEX,
    SPEED_WIRE,
    Key,
    format_shortcut,
    parse_shortcut,
)
from .macro import build_macro_area, macro_bytes, macro_to_text, split_macro_area


@dataclass(frozen=True)
class LightingState:
    """Lighting as the pad stores it (mirrors the 06 0B command / 06 0A reply layout)."""

    effect: int
    brightness: int
    speed_wire: tuple[int, int]
    color: RGB

    def matches(self, actual: LightingState) -> bool:
        """Equal in every field the pad keeps. The pad never stores the color bytes,
        and speed has no meaning in UserLight (verified 2026-10-14)."""
        if (self.effect, self.brightness) != (actual.effect, actual.brightness):
            return False
        return self.effect == EFFECTS["userlight"] or self.speed_wire == actual.speed_wire


@dataclass(frozen=True)
class DeviceImage:
    """Everything `write` controls, keyed by Key.pos, plus the 4096-byte macro area."""

    keys: dict[int, bytes]
    colors: dict[int, RGB]
    lighting: LightingState
    macros: bytes


def encode_action(key: Key, action: KeyAction) -> bytes:
    if action.kind == "factory":
        return key.factory
    if action.kind == "key":
        if action.value in MODIFIER_KEYS:
            return bytes([ENTRY_KEYBOARD, MODIFIER_KEYS[action.value], 0, 0])
        return bytes([ENTRY_KEYBOARD, 0, KEYCODES[action.value], 0])
    if action.kind == "shortcut":
        mask, code = parse_shortcut(action.value)
        return bytes([ENTRY_KEYBOARD, mask, code, 0])
    if action.kind == "macro":
        raise ValueError("macro actions are encoded by encode(), which assigns slots")
    return bytes([ENTRY_MEDIA, MEDIA[action.value], 0, 0])


def encode_lighting(lighting: Lighting) -> LightingState:
    if lighting.mode == "per-key":
        return LightingState(EFFECTS["userlight"], lighting.brightness, SPEED_WIRE[0], (0, 0, 0))
    return LightingState(EFFECTS[lighting.effect], lighting.brightness, SPEED_WIRE[lighting.speed], (0, 0, 0))


def encode(cfg: Config) -> DeviceImage:
    keys: dict[int, bytes] = {}
    colors: dict[int, RGB] = {}
    slots: dict[tuple[str, int], int] = {}
    blobs: list[bytes] = []
    for key in KEYS:
        kc = cfg.keys.get(key.name, KeyConfig())
        action = kc.action
        if action.kind == "macro":
            ident = (action.value, action.delay_ms)
            if ident not in slots:
                slots[ident] = len(blobs)
                blobs.append(macro_bytes(action.value, action.delay_ms))
            keys[key.pos] = bytes([ENTRY_MACRO, slots[ident], 0x01, 0x00])
        else:
            keys[key.pos] = encode_action(key, action)
        colors[key.pos] = kc.color if kc.color is not None else cfg.default_color
    return DeviceImage(keys, colors, encode_lighting(cfg.lighting), build_macro_area(blobs))


def decode_keymap(raw: bytes) -> dict[int, bytes]:
    if len(raw) < KEYMAP_LEN:
        raise ValueError(f"keymap needs {KEYMAP_LEN} bytes, got {len(raw)}")
    return {k.pos: bytes(raw[k.keymap_offset : k.keymap_offset + 4]) for k in KEYS}


def decode_colors(raw: bytes) -> dict[int, RGB]:
    if len(raw) < COLOR_LEN:
        raise ValueError(f"color table needs {COLOR_LEN} bytes, got {len(raw)}")
    return {
        k.pos: (raw[k.color_offset], raw[k.color_offset + 1], raw[k.color_offset + 2])
        for k in KEYS
    }


def decode_lighting(reply: bytes) -> LightingState:
    return LightingState(reply[7], reply[8], (reply[9], reply[10]), (reply[13], reply[14], reply[15]))


def decode_action(key: Key, entry: bytes) -> KeyAction | None:
    """Inverse of encode_action; None if config.json cannot express the entry."""
    if entry == key.factory:
        return FACTORY
    if len(entry) != 4 or entry[3] != 0:
        return None
    kind, a, b = entry[0], entry[1], entry[2]
    if kind == ENTRY_KEYBOARD:
        if b == 0:
            mod_name = MODIFIER_KEY_BY_BIT.get(a)
            return KeyAction("key", mod_name) if mod_name else None
        if a == 0:
            key_name = KEYCODE_NAMES.get(b)
            return KeyAction("key", key_name) if key_name else None
        shortcut = format_shortcut(a, b)
        return KeyAction("shortcut", shortcut) if shortcut else None
    if kind == ENTRY_MEDIA and b == 0:
        media = MEDIA_NAMES.get(a)
        return KeyAction("media", media) if media else None
    return None


def describe_entry(key: Key, entry: bytes) -> str:
    if entry == key.factory:
        return "factory"
    if entry[0] == ENTRY_MACRO:
        return f"macro M{entry[1]}"
    action = decode_action(key, entry)
    return f"{action.kind} {action.value}" if action else f"raw {entry.hex(' ')}"


def format_rgb(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def describe_lighting(state: LightingState) -> str:
    if state.effect == EFFECTS["userlight"]:
        return f"per-key, brightness {state.brightness}"
    effect = EFFECT_NAMES.get(state.effect, f"{state.effect:#04x}")
    speed = SPEED_INDEX.get(state.speed_wire, state.speed_wire)
    return f"effect {effect}, brightness {state.brightness}, speed {speed}"


def _lighting_from_state(state: LightingState) -> tuple[Lighting, list[str]]:
    warnings: list[str] = []
    brightness = min(max(state.brightness, 0), 4)
    if brightness != state.brightness:
        warnings.append(f"lighting: brightness {state.brightness} out of range; using {brightness}")
    if state.effect == EFFECTS["userlight"]:
        return Lighting("per-key", brightness), warnings
    effect = EFFECT_NAMES.get(state.effect)
    speed = SPEED_INDEX.get(state.speed_wire)
    if effect is None or speed is None:
        warnings.append(
            f"lighting: unrecognised effect {state.effect:#04x} / speed {state.speed_wire}; "
            "using per-key mode"
        )
        return Lighting("per-key", brightness), warnings
    return Lighting("effect", brightness, effect, speed), warnings


def _decode_macro_entry(
    key: Key, entry: bytes, macros: dict[int, bytes | None], warnings: list[str]
) -> KeyAction:
    slot = entry[1]
    raw = macros.get(slot)
    decoded = macro_to_text(raw) if raw is not None else None
    if decoded is None:
        warnings.append(f"{key.name}: macro M{slot} cannot be expressed; using factory action")
        return FACTORY
    text, delay_ms, normalized = decoded
    if normalized:
        warnings.append(f"{key.name}: macro M{slot} timings normalized to {delay_ms} ms")
    return KeyAction("macro", text, delay_ms)


def image_to_config(image: DeviceImage) -> tuple[Config, list[str]]:
    warnings: list[str] = []
    keys: dict[str, KeyConfig] = {}
    macros = split_macro_area(image.macros)
    for key in KEYS:
        entry = image.keys[key.pos]
        action: KeyAction | None
        if entry != key.factory and entry[0] == ENTRY_MACRO:
            action = _decode_macro_entry(key, entry, macros, warnings)
        else:
            action = decode_action(key, entry)
        if action is None:
            warnings.append(
                f"{key.name}: unrecognised entry {entry.hex(' ')}; using factory action "
                "(run 'gamekb reset' to clean the pad)"
            )
            action = FACTORY
        elif action != FACTORY and key.name in COLOR_ONLY_KEYS:
            warnings.append(
                f"{key.name}: pad holds {entry.hex(' ')} but this key cannot be remapped; "
                "using factory action (run 'gamekb reset' to clean the pad)"
            )
            action = FACTORY
        keys[key.name] = KeyConfig(action=action, color=image.colors[key.pos])
    lighting, lighting_warnings = _lighting_from_state(image.lighting)
    return Config(lighting=lighting, default_color=(0, 0, 0), keys=keys), warnings + lighting_warnings
