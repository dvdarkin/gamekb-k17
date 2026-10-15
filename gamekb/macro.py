"""Firmware macros: text <-> strokes <-> 4-byte events <-> the 4 KB macro area. Pure, no I/O.

Format verified 2026-10-14 from vendor web app captures (see docs/PROTOCOL.md, "Macros").
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .layout import (
    KEYCODE_NAMES,
    KEYCODES,
    MACRO_AREA_LEN,
    MODIFIER_KEY_BY_BIT,
    MODIFIER_KEYS,
    MODIFIER_USAGES,
    MODIFIERS,
    LayoutError,
    parse_shortcut,
)

MACRO_SLOTS = 16
MACRO_DATA_OFFSET = 0x40
MAX_MACRO_BYTES = MACRO_AREA_LEN - MACRO_DATA_OFFSET  # 4032
DEFAULT_DELAY_MS = 20
MAX_DELAY_MS = 0xFFFF
FLAG_PRESS = 0x42
FLAG_RELEASE = 0x02
FLAG_LAST = 0x80

ENTER = KEYCODES["Enter"]
TAB = KEYCODES["Tab"]
LSHIFT = MODIFIER_USAGES[0x02]
USAGE_TO_BIT: dict[int, int] = {usage: bit for bit, usage in MODIFIER_USAGES.items()}

# US layout: unshifted and shifted punctuation -> HID keycode.
_UNSHIFTED = {"-": 0x2D, "=": 0x2E, "[": 0x2F, "]": 0x30, "\\": 0x31, ";": 0x33, "'": 0x34,
              "`": 0x35, ",": 0x36, ".": 0x37, "/": 0x38}
_SHIFTED = {"!": 0x1E, "@": 0x1F, "#": 0x20, "$": 0x21, "%": 0x22, "^": 0x23, "&": 0x24,
            "*": 0x25, "(": 0x26, ")": 0x27, "_": 0x2D, "+": 0x2E, "{": 0x2F, "}": 0x30,
            "|": 0x31, ":": 0x33, '"': 0x34, "~": 0x35, "<": 0x36, ">": 0x37, "?": 0x38}


def _char_table() -> dict[str, tuple[bool, int]]:
    table: dict[str, tuple[bool, int]] = {" ": (False, KEYCODES["Space"])}
    for c in "abcdefghijklmnopqrstuvwxyz":
        table[c] = (False, KEYCODES[c.upper()])
        table[c.upper()] = (True, KEYCODES[c.upper()])
    for c in "0123456789":
        table[c] = (False, KEYCODES[c])
    for c, code in _UNSHIFTED.items():
        table[c] = (False, code)
    for c, code in _SHIFTED.items():
        table[c] = (True, code)
    return table


CHAR_KEYS: dict[str, tuple[bool, int]] = _char_table()  # char -> (needs shift, keycode)
TEXT_FOR: dict[tuple[bool, int], str] = {v: k for k, v in CHAR_KEYS.items()}


class MacroError(ValueError):
    """Macro text or data the pad cannot store."""


@dataclass(frozen=True)
class Stroke:
    mods: tuple[int, ...]  # modifier usages (E0-E7) held around the key, in press order
    key: int               # HID usage pressed and released
    wait_ms: int = 0       # extra delay after this stroke ({wait N})


@dataclass(frozen=True)
class Event:
    delay_ms: int  # wait after this event
    press: bool
    keycode: int


def _char_stroke(ch: str) -> Stroke:
    if ch == "\n":
        return Stroke((), ENTER)
    if ch == "\t":
        return Stroke((), TAB)
    entry = CHAR_KEYS.get(ch)
    if entry is None:
        raise MacroError(f"character {ch!r} cannot be typed (US layout, printable ASCII only)")
    shift, code = entry
    return Stroke((LSHIFT,), code) if shift else Stroke((), code)


def _suggest_token(token: str) -> str:
    for name in [*KEYCODES, *MODIFIER_KEYS]:
        if name.lower() == token.lower():
            return f" (did you mean {{{name}}}?)"
    return ""


def _token_stroke(token: str) -> Stroke:
    if token in KEYCODES:
        return Stroke((), KEYCODES[token])
    if token in MODIFIER_KEYS:
        return Stroke((), MODIFIER_USAGES[MODIFIER_KEYS[token]])
    if "+" in token:
        try:
            mask, code = parse_shortcut(token)
        except LayoutError as exc:
            raise MacroError(f"{{{token}}}: {exc}") from exc
        mods = tuple(MODIFIER_USAGES[bit] for bit in MODIFIERS.values() if mask & bit)
        return Stroke(mods, code)
    raise MacroError(f"unknown token {{{token}}}{_suggest_token(token)}")


def parse_macro(text: str) -> list[Stroke]:
    if not text:
        raise MacroError("macro is empty")
    strokes: list[Stroke] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch == "{":
            if text.startswith("{{", i):
                strokes.append(_char_stroke("{"))
                i += 2
                continue
            end = text.find("}", i)
            if end < 0:
                raise MacroError(f"unclosed '{{' at position {i}")
            token = text[i + 1 : end].strip()
            i = end + 1
            parts = token.split()
            if parts and parts[0].lower() == "wait":
                if len(parts) != 2 or not (parts[1].isascii() and parts[1].isdigit()):
                    raise MacroError(f"bad token {{{token}}}; write e.g. {{wait 500}}")
                ms = int(parts[1])
                if ms > MAX_DELAY_MS:
                    raise MacroError(f"{{{token}}}: wait must be 0-{MAX_DELAY_MS} ms")
                if not strokes:
                    raise MacroError("{wait N} cannot come first")
                last = strokes[-1]
                strokes[-1] = Stroke(last.mods, last.key, last.wait_ms + ms)
                continue
            strokes.append(_token_stroke(token))
            continue
        if ch == "}":
            if text.startswith("}}", i):
                strokes.append(_char_stroke("}"))
                i += 2
                continue
            raise MacroError(f"unmatched '}}' at position {i}; write '}}}}' for a literal brace")
        strokes.append(_char_stroke(ch))
        i += 1
    if strokes[-1].wait_ms:
        raise MacroError("{wait N} at the end has no effect")
    return strokes


def strokes_to_events(strokes: list[Stroke], delay_ms: int) -> list[Event]:
    if not 0 <= delay_ms <= MAX_DELAY_MS:
        raise MacroError(f"delay_ms must be 0-{MAX_DELAY_MS}")
    events: list[Event] = []
    for s in strokes:
        group = (
            [Event(delay_ms, True, m) for m in s.mods]
            + [Event(delay_ms, True, s.key), Event(delay_ms, False, s.key)]
            + [Event(delay_ms, False, m) for m in reversed(s.mods)]
        )
        total = delay_ms + s.wait_ms
        if total > MAX_DELAY_MS:
            raise MacroError(f"delay after a key ({total} ms) exceeds {MAX_DELAY_MS} ms")
        group[-1] = Event(total, False, group[-1].keycode)
        events += group
    events[-1] = Event(0, events[-1].press, events[-1].keycode)
    return events


def encode_events(events: list[Event]) -> bytes:
    out = bytearray()
    for i, e in enumerate(events):
        flags = FLAG_PRESS if e.press else FLAG_RELEASE
        if i == len(events) - 1:
            flags |= FLAG_LAST
        out += bytes([e.delay_ms & 0xFF, e.delay_ms >> 8, flags, e.keycode])
    return bytes(out)


def macro_bytes(text: str, delay_ms: int) -> bytes:
    return encode_events(strokes_to_events(parse_macro(text), delay_ms))


def build_macro_area(macros: list[bytes]) -> bytes:
    """Slot list (M0 first) -> the 4096-byte area exactly as the vendor app lays it out."""
    if len(macros) > MACRO_SLOTS:
        raise MacroError(f"at most {MACRO_SLOTS} macros, got {len(macros)}")
    total = sum(len(m) for m in macros)
    if total > MAX_MACRO_BYTES:
        raise MacroError(f"macros need {total} bytes; the pad holds {MAX_MACRO_BYTES}")
    area = bytearray(MACRO_AREA_LEN)
    area[:MACRO_DATA_OFFSET] = b"\xff" * MACRO_DATA_OFFSET
    ptr = MACRO_DATA_OFFSET
    for slot, raw in enumerate(macros):
        area[2 * slot : 2 * slot + 2] = ptr.to_bytes(2, "little")
        area[ptr : ptr + len(raw)] = raw
        ptr += len(raw)
    return bytes(area)


MODIFIER_NAME_BY_BIT: dict[int, str] = {bit: name for name, bit in MODIFIERS.items()}


def split_macro_area(area: bytes) -> dict[int, bytes | None]:
    """Raw bytes per used slot; None if the slot's pointer or data is malformed."""
    result: dict[int, bytes | None] = {}
    for slot in range(MACRO_SLOTS):
        ptr = area[2 * slot] | area[2 * slot + 1] << 8
        if ptr in (0xFFFF, 0x0000):  # vendor empty / factory-fresh
            continue
        if ptr < MACRO_DATA_OFFSET or ptr >= len(area) or (ptr - MACRO_DATA_OFFSET) % 4:
            result[slot] = None
            continue
        end = ptr
        while end + 4 <= len(area):
            end += 4
            if area[end - 2] & FLAG_LAST:
                break
        else:
            result[slot] = None
            continue
        result[slot] = bytes(area[ptr:end])
    return result


def decode_events(raw: bytes) -> list[Event] | None:
    if not raw or len(raw) % 4:
        return None
    events: list[Event] = []
    count = len(raw) // 4
    for i in range(count):
        lo, hi, flags, keycode = raw[4 * i : 4 * i + 4]
        last = bool(flags & FLAG_LAST)
        if last != (i == count - 1):
            return None
        kind = flags & ~FLAG_LAST
        if kind not in (FLAG_PRESS, FLAG_RELEASE):
            return None
        events.append(Event(lo | hi << 8, kind == FLAG_PRESS, keycode))
    return events


def events_to_strokes(events: list[Event]) -> list[Stroke] | None:
    """Group events into strokes exactly as strokes_to_events generates them."""
    strokes: list[Stroke] = []
    i, n = 0, len(events)
    while i < n:
        e = events[i]
        if not e.press:
            return None
        if i + 1 < n and not events[i + 1].press and events[i + 1].keycode == e.keycode:
            strokes.append(Stroke((), e.keycode))
            i += 2
            continue
        mods: list[int] = []
        while i < n and events[i].press and events[i].keycode in USAGE_TO_BIT:
            mods.append(events[i].keycode)
            i += 1
        if not mods or i >= n or not events[i].press:
            return None
        key = events[i].keycode
        i += 1
        if i >= n or events[i].press or events[i].keycode != key:
            return None
        i += 1
        for m in reversed(mods):
            if i >= n or events[i].press or events[i].keycode != m:
                return None
            i += 1
        strokes.append(Stroke(tuple(mods), key))
    return strokes


def _escape(ch: str) -> str:
    return ch * 2 if ch in "{}" else ch


def strokes_to_text(strokes: list[Stroke]) -> str | None:
    parts: list[str] = []
    for s in strokes:
        if not s.mods:
            if s.key in USAGE_TO_BIT:
                part = "{" + MODIFIER_KEY_BY_BIT[USAGE_TO_BIT[s.key]] + "}"
            elif (False, s.key) in TEXT_FOR:
                part = TEXT_FOR[(False, s.key)]
            elif s.key in KEYCODE_NAMES:
                part = "{" + KEYCODE_NAMES[s.key] + "}"
            else:
                return None
        elif s.mods == (LSHIFT,) and (True, s.key) in TEXT_FOR:
            part = _escape(TEXT_FOR[(True, s.key)])
        else:
            if list(s.mods) != sorted(set(s.mods)) or any(m not in USAGE_TO_BIT for m in s.mods):
                return None  # parse_macro always emits modifiers once, in bit order
            key_name = KEYCODE_NAMES.get(s.key)
            if key_name is None:
                return None
            names = [MODIFIER_NAME_BY_BIT[USAGE_TO_BIT[m]] for m in s.mods]
            part = "{" + "+".join([*names, key_name]) + "}"
        if s.wait_ms:
            part += f"{{wait {s.wait_ms}}}"
        parts.append(part)
    return "".join(parts)


def macro_to_text(raw: bytes) -> tuple[str, int, bool] | None:
    """(text, delay_ms, normalized). Structure is exact; uneven timings are normalized."""
    events = decode_events(raw)
    if events is None:
        return None
    strokes = events_to_strokes(events)
    if strokes is None:
        return None
    gaps = [e.delay_ms for e in events[:-1]]
    counts = Counter(gaps)
    base = min(counts, key=lambda d: (-counts[d], d))  # most common, ties -> smallest
    normalized = events[-1].delay_ms != 0
    timed: list[Stroke] = []
    index = 0
    for s in strokes:
        size = 2 + 2 * len(s.mods)
        group = events[index : index + size]
        index += size
        if any(e.delay_ms != base for e in group[:-1]):
            normalized = True
        wait = 0
        if index < len(events):
            final = group[-1].delay_ms
            if final < base:
                normalized = True
            else:
                wait = final - base
        timed.append(Stroke(s.mods, s.key, wait))
    if normalized:
        timed = [Stroke(s.mods, s.key) for s in timed]
    text = strokes_to_text(timed)
    return None if text is None else (text, base, normalized)


def describe_macro_area(area: bytes) -> str:
    parts: list[str] = []
    for slot, raw in sorted(split_macro_area(area).items()):
        decoded = macro_to_text(raw) if raw is not None else None
        if decoded is None:
            parts.append(f"M{slot} <not expressible>")
        else:
            text, _delay, normalized = decoded
            parts.append(f"M{slot} {text!r}" + (" (recorded timings)" if normalized else ""))
    return ", ".join(parts) if parts else "none"
