"""Plan, apply and verify a target DeviceImage against the pad.

Only differing entries are written, so an unchanged config sends nothing and an
interrupted write is finished by running it again.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Callable, Literal

from .device import Pad
from .encode import (
    DeviceImage,
    decode_colors,
    decode_keymap,
    decode_lighting,
    describe_entry,
    describe_lighting,
    format_rgb,
)
from .layout import (
    COLOR_LEN,
    ENTRY_MACRO,
    FACTORY_KEYMAP_OFFSET,
    KEYMAP_LEN,
    KEYS,
    MACRO_AREA_LEN,
    MACRO_WRITE_CHUNK,
)
from .macro import describe_macro_area, split_macro_area

_KEY_BY_POS = {k.pos: k for k in KEYS}
USERLIGHT_SETTLE_S = 0.3  # pad drops per-key colors sent right after a mode switch


@dataclass(frozen=True)
class Change:
    kind: Literal["park", "macros", "key", "lighting", "color"]
    pos: int  # 0 for lighting and macros
    label: str
    offsets: tuple[int, ...] = ()  # macro-area chunks to write


@dataclass(frozen=True)
class KeyResult:
    name: str
    action_ok: bool
    color_ok: bool
    expected: str
    actual: str


@dataclass(frozen=True)
class Report:
    keys: tuple[KeyResult, ...]
    lighting_ok: bool
    lighting_expected: str
    lighting_actual: str
    macros_ok: bool
    macros_expected: str
    macros_actual: str

    @property
    def ok(self) -> bool:
        return self.lighting_ok and self.macros_ok and all(k.action_ok and k.color_ok for k in self.keys)

    def lines(self) -> list[str]:
        out: list[str] = []
        line = f"{'ok  ' if self.macros_ok else 'FAIL'} macros   {self.macros_expected}"
        if self.macros_ok:
            out.append(line)
        elif self.macros_expected == self.macros_actual:
            out.append(f"{line}   (pad has: {self.macros_actual}, unused bytes differ; run gamekb write)")
        else:
            out.append(f"{line}   (pad has: {self.macros_actual})")
        for k in self.keys:
            good = k.action_ok and k.color_ok
            line = f"{'ok  ' if good else 'FAIL'} {k.name:8} {k.expected}"
            out.append(line if good else f"{line}   (pad has: {k.actual})")
        line = f"{'ok  ' if self.lighting_ok else 'FAIL'} lighting {self.lighting_expected}"
        out.append(line if self.lighting_ok else f"{line}   (pad has: {self.lighting_actual})")
        return out


def _ignore(_: Change) -> None:
    pass


def read_image(pad: Pad) -> DeviceImage:
    region = pad.read_region()
    return DeviceImage(
        keys=decode_keymap(pad.read_keymap()),
        colors=decode_colors(region[:COLOR_LEN]),
        lighting=decode_lighting(pad.read_lighting()),
        macros=pad.read_macros(),
    )


def read_factory_keys(pad: Pad) -> dict[int, bytes]:
    region = pad.read_region()
    return decode_keymap(region[FACTORY_KEYMAP_OFFSET : FACTORY_KEYMAP_OFFSET + KEYMAP_LEN])


def _macro_chunks(target: bytes, current: bytes) -> tuple[int, ...]:
    return tuple(
        offset
        for offset in range(0, MACRO_AREA_LEN, MACRO_WRITE_CHUNK)
        if target[offset : offset + MACRO_WRITE_CHUNK] != current[offset : offset + MACRO_WRITE_CHUNK]
    )


def plan(target: DeviceImage, current: DeviceImage) -> list[Change]:
    changes: list[Change] = []
    parked: set[int] = set()
    if target.macros != current.macros:
        # Keys already pointing at a macro slot would play whatever lands there mid-write: park them first.
        cur_slots, new_slots = split_macro_area(current.macros), split_macro_area(target.macros)
        for key in KEYS:
            old = current.keys[key.pos]
            if old == key.factory or old[0] != ENTRY_MACRO:
                continue
            if target.keys[key.pos] != old or cur_slots.get(old[1]) != new_slots.get(old[1]):
                parked.add(key.pos)
                changes.append(Change(
                    "park", key.pos,
                    f"{key.name}: {describe_entry(key, old)} -> factory (parked while macros change)",
                ))
        before, after = describe_macro_area(current.macros), describe_macro_area(target.macros)
        label = (
            f"macros: {after} (unused bytes differ; rewriting them)"
            if before == after
            else f"macros: {before} -> {after}"
        )
        changes.append(Change(
            "macros", 0, label,
            _macro_chunks(target.macros, current.macros),
        ))
    for key in KEYS:
        new, old = target.keys[key.pos], current.keys[key.pos]
        if new != old or key.pos in parked:
            changes.append(Change(
                "key", key.pos, f"{key.name}: {describe_entry(key, old)} -> {describe_entry(key, new)}"
            ))
    if not target.lighting.matches(current.lighting):
        changes.append(Change(
            "lighting", 0,
            f"lighting: {describe_lighting(current.lighting)} -> {describe_lighting(target.lighting)}",
        ))
    for key in KEYS:
        new_c, old_c = target.colors[key.pos], current.colors[key.pos]
        if new_c != old_c:
            changes.append(Change(
                "color", key.pos, f"{key.name}: color {format_rgb(old_c)} -> {format_rgb(new_c)}"
            ))
    return changes


def apply(
    pad: Pad,
    target: DeviceImage,
    changes: list[Change],
    progress: Callable[[Change], None] = _ignore,
    sleep: Callable[[float], None] = time.sleep,
) -> None:
    settle = False
    for change in changes:
        if change.kind == "macros":
            for offset in change.offsets:
                pad.write_macro_chunk(offset, target.macros[offset : offset + MACRO_WRITE_CHUNK])
        elif change.kind == "park":
            pad.write_key(change.pos, _KEY_BY_POS[change.pos].factory)
        elif change.kind == "key":
            pad.write_key(change.pos, target.keys[change.pos])
        elif change.kind == "lighting":
            lt = target.lighting
            pad.write_lighting(lt.effect, lt.brightness, lt.speed_wire, lt.color)
            settle = True
        else:
            if settle:
                sleep(USERLIGHT_SETTLE_S)
                settle = False
            pad.write_color(change.pos, target.colors[change.pos])
        progress(change)


def verify(pad: Pad, target: DeviceImage) -> Report:
    actual = read_image(pad)
    results: list[KeyResult] = []
    for key in KEYS:
        want_e, got_e = target.keys[key.pos], actual.keys[key.pos]
        want_c, got_c = target.colors[key.pos], actual.colors[key.pos]
        results.append(KeyResult(
            key.name,
            want_e == got_e,
            want_c == got_c,
            f"{describe_entry(key, want_e)}, color {format_rgb(want_c)}",
            f"{describe_entry(key, got_e)}, color {format_rgb(got_c)}",
        ))
    return Report(
        tuple(results),
        target.lighting.matches(actual.lighting),
        describe_lighting(target.lighting),
        describe_lighting(actual.lighting),
        target.macros == actual.macros,
        describe_macro_area(target.macros),
        describe_macro_area(actual.macros),
    )


def run_write(
    pad: Pad,
    target: DeviceImage,
    confirm: Callable[[list[Change]], bool],
    progress: Callable[[Change], None] = _ignore,
    sleep: Callable[[float], None] = time.sleep,
) -> tuple[list[Change], Report | None]:
    """Read, plan, confirm, apply, verify. Report is None when confirm() declines."""
    changes = plan(target, read_image(pad))
    if not confirm(changes):
        return changes, None
    apply(pad, target, changes, progress, sleep)
    return changes, verify(pad, target)
