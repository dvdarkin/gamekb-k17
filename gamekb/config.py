"""config.json schema: load, validate, save. No device access.

Every validation problem is collected and reported together in one ConfigError.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal

from .layout import (
    EFFECTS,
    KEY_BY_NAME,
    KEYCODES,
    MEDIA,
    MODIFIER_KEYS,
    RGB,
    LayoutError,
    parse_shortcut,
)
from .macro import DEFAULT_DELAY_MS, MAX_DELAY_MS, MacroError, build_macro_area, macro_bytes

ActionKind = Literal["factory", "key", "shortcut", "media", "macro"]
ACTION_FIELDS = ("key", "shortcut", "media", "macro")
KEY_FIELDS = (*ACTION_FIELDS, "delay_ms", "label", "color")
TOP_FIELDS = ("lighting", "default_color", "keys")
# Handled by firmware: a keymap write is stored but ignored (dial press verified 2026-10-14).
COLOR_ONLY_KEYS = frozenset({"Mute", "Vol+", "Vol-"})  # the dial: firmware ignores remaps


class ConfigError(Exception):
    def __init__(self, errors: list[str]) -> None:
        self.errors = errors
        super().__init__("invalid config:\n" + "\n".join(f"  - {e}" for e in errors))


@dataclass(frozen=True)
class KeyAction:
    kind: ActionKind
    value: str = ""
    delay_ms: int = DEFAULT_DELAY_MS  # macros only


FACTORY = KeyAction("factory")


@dataclass(frozen=True)
class KeyConfig:
    action: KeyAction = FACTORY
    color: RGB | None = None
    label: str = ""


@dataclass(frozen=True)
class Lighting:
    mode: Literal["per-key", "effect"] = "per-key"
    brightness: int = 4
    effect: str = "static"  # effects use the firmware's built-in color cycle
    speed: int = 1


@dataclass(frozen=True)
class Config:
    lighting: Lighting = field(default_factory=Lighting)
    default_color: RGB = (0, 0, 0)
    keys: dict[str, KeyConfig] = field(default_factory=dict)


def _suggest(value: str, choices: Iterable[str]) -> str:
    for choice in choices:
        if choice.lower() == value.lower():
            return f" (did you mean {choice!r}?)"
    return ""


def _parse_int(raw: Any, lo: int, hi: int, where: str, errors: list[str]) -> int:
    if isinstance(raw, int) and not isinstance(raw, bool) and lo <= raw <= hi:
        return raw
    errors.append(f"{where} must be an integer {lo}-{hi}, got {raw!r}")
    return lo


def _parse_rgb(raw: Any, where: str, errors: list[str]) -> RGB | None:
    if (
        isinstance(raw, list)
        and len(raw) == 3
        and all(isinstance(c, int) and not isinstance(c, bool) and 0 <= c <= 255 for c in raw)
    ):
        return (raw[0], raw[1], raw[2])
    errors.append(f"{where} must be [R, G, B] with values 0-255, got {raw!r}")
    return None


def _parse_lighting(raw: Any, errors: list[str]) -> Lighting:
    if not isinstance(raw, dict):
        errors.append("lighting must be an object")
        return Lighting()
    mode = raw.get("mode")
    if mode == "per-key":
        allowed: tuple[str, ...] = ("mode", "brightness")
    elif mode == "effect":
        allowed = ("mode", "brightness", "effect", "speed")
    else:
        errors.append(f"lighting.mode must be 'per-key' or 'effect', got {mode!r}")
        return Lighting()
    for name in raw:
        if name not in allowed:
            errors.append(f"lighting.{name} is not valid for mode {mode!r}")
    brightness = _parse_int(raw.get("brightness", 4), 0, 4, "lighting.brightness", errors)
    if mode == "per-key":
        return Lighting(mode="per-key", brightness=brightness)
    effect = raw.get("effect", "static")
    choices = [e for e in EFFECTS if e != "userlight"]
    if effect not in choices:
        errors.append(f"lighting.effect {effect!r} unknown; valid: {', '.join(choices)}")
        effect = "static"
    speed = _parse_int(raw.get("speed", 1), 0, 2, "lighting.speed", errors)
    return Lighting("effect", brightness, str(effect), speed)


def _parse_action(name: str, kind: str, value: Any, errors: list[str], delay_ms: int) -> KeyAction:
    where = f"keys.{name}.{kind}"
    if kind == "macro":
        if not isinstance(value, str) or not value:
            errors.append(f"{where} must be a non-empty string")
            return FACTORY
        try:
            macro_bytes(value, delay_ms)
        except MacroError as exc:
            errors.append(f"{where}: {exc}")
            return FACTORY
        return KeyAction("macro", value, delay_ms)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where} must be a non-empty string")
        return FACTORY
    value = value.strip()
    if kind == "key":
        names = [*KEYCODES, *MODIFIER_KEYS]
        if value not in names:
            errors.append(f"{where}: unknown key {value!r}{_suggest(value, names)}")
            return FACTORY
        return KeyAction("key", value)
    if kind == "shortcut":
        try:
            parse_shortcut(value)
        except LayoutError as exc:
            errors.append(f"{where}: {exc}")
            return FACTORY
        return KeyAction("shortcut", value)
    if value not in MEDIA:
        errors.append(
            f"{where}: unknown media action {value!r}{_suggest(value, MEDIA)}; "
            f"valid: {', '.join(MEDIA)}"
        )
        return FACTORY
    return KeyAction("media", value)


def _parse_key(name: str, spec: Any, errors: list[str]) -> KeyConfig | None:
    if name not in KEY_BY_NAME:
        errors.append(
            f"unknown key {name!r}{_suggest(name, KEY_BY_NAME)}; valid keys: {', '.join(KEY_BY_NAME)}"
        )
        return None
    if not isinstance(spec, dict):
        errors.append(f"keys.{name} must be an object")
        return None
    for fld in spec:
        if fld not in KEY_FIELDS:
            errors.append(f"keys.{name}: unknown field {fld!r}; valid: {', '.join(KEY_FIELDS)}")
    delay_ms = DEFAULT_DELAY_MS
    if "delay_ms" in spec:
        if "macro" not in spec:
            errors.append(f"keys.{name}.delay_ms is only valid with macro")
        else:
            delay_ms = _parse_int(spec["delay_ms"], 0, MAX_DELAY_MS, f"keys.{name}.delay_ms", errors)
    present = [f for f in ACTION_FIELDS if f in spec]
    action = FACTORY
    if len(present) > 1:
        errors.append(
            f"keys.{name}: only one of {', '.join(ACTION_FIELDS)} allowed, got {', '.join(present)}"
        )
    elif present:
        action = _parse_action(name, present[0], spec[present[0]], errors, delay_ms)
    if action != FACTORY and name in COLOR_ONLY_KEYS:
        errors.append(f"keys.{name}: this key cannot be remapped on the pad; only 'color' and 'label' apply")
        action = FACTORY
    label = spec.get("label", "")
    if not isinstance(label, str):
        errors.append(f"keys.{name}.label must be a string")
        label = ""
    color = _parse_rgb(spec["color"], f"keys.{name}.color", errors) if "color" in spec else None
    return KeyConfig(action, color, label)


def parse_config(raw: object) -> Config:
    if not isinstance(raw, dict):
        raise ConfigError(["top level must be a JSON object"])
    errors: list[str] = []
    for name in raw:
        if name not in TOP_FIELDS:
            errors.append(f"unknown top-level field {name!r}; valid: {', '.join(TOP_FIELDS)}")
    lighting = _parse_lighting(raw.get("lighting", {"mode": "per-key"}), errors)
    default_color = _parse_rgb(raw.get("default_color", [0, 0, 0]), "default_color", errors)
    keys: dict[str, KeyConfig] = {}
    raw_keys = raw.get("keys", {})
    if not isinstance(raw_keys, dict):
        errors.append("keys must be an object mapping key name -> settings")
    else:
        for name, spec in raw_keys.items():
            kc = _parse_key(str(name), spec, errors)
            if kc is not None:
                keys[str(name)] = kc
    used = [keys[n].action for n in KEY_BY_NAME if n in keys and keys[n].action.kind == "macro"]
    distinct = list(dict.fromkeys((a.value, a.delay_ms) for a in used))
    try:
        build_macro_area([macro_bytes(text, delay) for text, delay in distinct])
    except MacroError as exc:
        errors.append(f"macros: {exc}")
    if errors:
        raise ConfigError(errors)
    return Config(lighting, default_color or (0, 0, 0), keys)


def config_to_dict(cfg: Config) -> dict[str, Any]:
    lt = cfg.lighting
    lighting: dict[str, Any] = {"mode": lt.mode, "brightness": lt.brightness}
    if lt.mode == "effect":
        lighting.update(effect=lt.effect, speed=lt.speed)
    keys: dict[str, Any] = {}
    for name in KEY_BY_NAME:  # physical order, stable diffs
        kc = cfg.keys.get(name)
        if kc is None:
            continue
        entry: dict[str, Any] = {}
        if kc.action.kind != "factory":
            entry[kc.action.kind] = kc.action.value
        if kc.action.kind == "macro" and kc.action.delay_ms != DEFAULT_DELAY_MS:
            entry["delay_ms"] = kc.action.delay_ms
        if kc.label:
            entry["label"] = kc.label
        if kc.color is not None:
            entry["color"] = list(kc.color)
        keys[name] = entry
    return {"lighting": lighting, "default_color": list(cfg.default_color), "keys": keys}


def load_config(path: Path) -> Config:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError([f"cannot read {path}: {exc}"]) from exc
    try:
        raw = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError([f"{path}: invalid JSON: {exc}"]) from exc
    return parse_config(raw)


def _inline(obj: dict[str, Any]) -> str:
    if not obj:
        return "{}"
    return "{ " + ", ".join(f"{json.dumps(k)}: {json.dumps(v)}" for k, v in obj.items()) + " }"


def format_config(cfg: Config) -> str:
    """config.json text: one line per key, names aligned, so hand edits and saves diff cleanly."""
    data = config_to_dict(cfg)
    keys: dict[str, Any] = data["keys"]
    lines = [
        "{",
        f'  "lighting": {_inline(data["lighting"])},',
        f'  "default_color": {json.dumps(data["default_color"])},',
    ]
    if keys:
        width = max(len(json.dumps(name)) for name in keys) + 1
        lines.append('  "keys": {')
        for i, (name, entry) in enumerate(keys.items()):
            comma = "," if i < len(keys) - 1 else ""
            lines.append(f"    {(json.dumps(name) + ':').ljust(width)} {_inline(entry)}{comma}")
        lines.append("  }")
    else:
        lines.append('  "keys": {}')
    lines.append("}")
    return "\n".join(lines) + "\n"


def save_config(cfg: Config, path: Path) -> None:
    path.write_text(format_config(cfg), encoding="utf-8")
