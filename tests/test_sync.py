"""Tests for gamekb.sync: plan/apply/verify against FakePad."""

from __future__ import annotations

import pytest

from gamekb.config import Config, parse_config
from gamekb.device import DeviceError, Pad
from gamekb.encode import DeviceImage, decode_keymap, encode
from gamekb.layout import ENTRY_MACRO, KEY_BY_NAME, KEYS
from gamekb.macro import split_macro_area
from gamekb.sync import Change, plan, read_factory_keys, read_image, run_write, verify
from tests.fakepad import MUTATING, FakePad

TARGET = encode(parse_config({
    "lighting": {"mode": "per-key", "brightness": 4},
    "keys": {
        "Q": {"shortcut": "Alt+T", "color": [160, 60, 255]},
        "W": {"key": "Escape"},
        "C": {"media": "PlayPause"},
    },
}))

# The real pad's live keymap before v3 (first 28 bytes of the 06 08 dump), rest zero.
V2_GARBAGE = bytes.fromhex(
    "13000000 20012a00 28202020 20200020 20202001 12000000 00200068"
) + bytes(96 - 28)


def pad_with(fake: FakePad) -> Pad:
    return Pad(fake, timeout_ms=30)


def no_sleep(_: float) -> None:
    pass


def yes(_: list[Change]) -> bool:
    return True


def mutations_after(fake: FakePad, start: int) -> list[bytes]:
    return [w for w in fake.writes[start:] if w[1] in MUTATING]


def test_plan_lists_only_differences_in_order() -> None:
    changes = plan(TARGET, read_image(pad_with(FakePad())))
    kinds = [(c.kind, c.pos) for c in changes]
    assert ("key", 1) in kinds and ("key", 2) in kinds and ("key", 15) in kinds
    assert ("key", 3) not in kinds    # E stays factory
    assert ("color", 1) in kinds
    assert ("color", 2) not in kinds  # W: default black == fresh pad black
    order = [c.kind for c in changes]
    assert order == sorted(order, key=["macros", "key", "lighting", "color"].index)
    assert "Q: factory -> shortcut Alt+T" in [c.label for c in changes]


def test_write_verifies_and_second_run_is_a_no_op() -> None:
    fake = FakePad()
    pad = pad_with(fake)
    changes, report = run_write(pad, TARGET, confirm=yes, sleep=no_sleep)
    assert changes and report is not None and report.ok
    start = len(fake.writes)
    again, report2 = run_write(pad, TARGET, confirm=yes, sleep=no_sleep)
    assert again == [] and report2 is not None and report2.ok
    assert mutations_after(fake, start) == []


def test_writes_never_overlap_neighbours() -> None:
    """Regression: an earlier tool wrote 4-byte entries at 1-byte stride and corrupted the keymap."""
    # Built directly (config forbids remapping the dial keys) so every position is exercised.
    base = encode(Config())
    target = DeviceImage({k.pos: bytes.fromhex("20004500") for k in KEYS}, base.colors, base.lighting, base.macros)
    fake = FakePad()
    _, report = run_write(pad_with(fake), target, confirm=yes, sleep=no_sleep)
    assert report is not None and report.ok
    offsets = sorted(w[3] for w in fake.writes if w[1] == 0x10)
    assert len(offsets) == 21
    assert all(b - a >= 4 for a, b in zip(offsets, offsets[1:]))


def test_corrupted_pad_converges() -> None:
    fake = FakePad(live_keymap=V2_GARBAGE)
    target = encode(Config())
    _, report = run_write(pad_with(fake), target, confirm=yes, sleep=no_sleep)
    assert report is not None and report.ok
    assert decode_keymap(fake.live_keymap()) == target.keys


def test_interrupted_write_converges_on_rerun() -> None:
    fake = FakePad()
    fake.unplug_after_writes = 2
    with pytest.raises(DeviceError):
        run_write(pad_with(fake), TARGET, confirm=yes, sleep=no_sleep)
    fake.unplug_after_writes = None  # replugged
    changes, report = run_write(pad_with(fake), TARGET, confirm=yes, sleep=no_sleep)
    assert changes and report is not None and report.ok


def test_cancel_writes_nothing() -> None:
    fake = FakePad()
    changes, report = run_write(pad_with(fake), TARGET, confirm=lambda c: False, sleep=no_sleep)
    assert changes and report is None
    assert mutations_after(fake, 0) == []


def test_verify_reports_mismatch() -> None:
    report = verify(pad_with(FakePad()), TARGET)
    assert not report.ok
    q = next(k for k in report.keys if k.name == "Q")
    assert not q.action_ok and not q.color_ok
    assert any(line.startswith("FAIL Q") for line in report.lines())
    assert any(line.startswith("ok   E") for line in report.lines())


def test_settle_delay_between_lighting_and_colors() -> None:
    slept: list[float] = []
    fake = FakePad()
    fake.lighting[7] = 0x04  # pad in "stream" mode, so switching to per-key is a real change
    run_write(pad_with(fake), TARGET, confirm=yes, sleep=slept.append)
    assert slept == [0.3]


def test_read_factory_keys_ignores_live_garbage() -> None:
    keys = read_factory_keys(pad_with(FakePad(live_keymap=V2_GARBAGE)))
    assert keys == {k.pos: k.factory for k in KEYS}


MACRO_TARGET = encode(parse_config({"keys": {
    "Space": {"macro": "yes{Enter}"},
    "W": {"macro": "no{Enter}"},
}}))


def test_macros_written_before_keys_and_verified() -> None:
    fake = FakePad()
    changes, report = run_write(pad_with(fake), MACRO_TARGET, confirm=yes, sleep=no_sleep)
    assert report is not None and report.ok
    assert changes[0].kind == "macros"
    ops = [w[1] for w in fake.writes if w[1] in (0x0D, 0x10)]
    assert ops.index(0x10) > max(i for i, op in enumerate(ops) if op == 0x0D)
    assert bytes(fake.macros) == MACRO_TARGET.macros


def test_only_changed_macro_chunks_are_written() -> None:
    fake = FakePad()
    pad = pad_with(fake)
    run_write(pad, MACRO_TARGET, confirm=yes, sleep=no_sleep)
    fewer = encode(parse_config({"keys": {"Space": {"macro": "yes{Enter}"}}}))
    start = len(fake.writes)
    _, report = run_write(pad, fewer, confirm=yes, sleep=no_sleep)
    assert report is not None and report.ok
    offsets = sorted(w[3] | w[4] << 8 for w in fake.writes[start:] if w[1] == 0x0D)
    assert offsets == [0x00, 0x3B, 0x76]  # directory + the bytes M1 occupied (0x60-0x77)
    assert bytes(fake.macros) == fewer.macros


def test_interrupted_macro_write_converges() -> None:
    fake = FakePad()
    fake.unplug_after_writes = 1
    with pytest.raises(DeviceError):
        run_write(pad_with(fake), MACRO_TARGET, confirm=yes, sleep=no_sleep)
    assert all(w[1] != 0x10 for w in fake.writes)  # no key points at half-written macros
    fake.unplug_after_writes = None
    _, report = run_write(pad_with(fake), MACRO_TARGET, confirm=yes, sleep=no_sleep)
    assert report is not None and report.ok


def test_verify_reports_macro_mismatch() -> None:
    report = verify(pad_with(FakePad()), MACRO_TARGET)
    assert not report.macros_ok and not report.ok
    assert any(line.startswith("FAIL macros") for line in report.lines())


def test_fresh_macro_area_label() -> None:
    changes = plan(encode(Config()), read_image(pad_with(FakePad())))
    labels = [c.label for c in changes if c.kind == "macros"]
    assert labels == ["macros: none (unused bytes differ; rewriting them)"]


def test_fresh_macro_area_verify_says_run_write() -> None:
    report = verify(pad_with(FakePad()), encode(Config()))
    assert not report.ok
    line = next(x for x in report.lines() if "macros" in x)
    assert "unused bytes differ; run gamekb write" in line


SWAP_FIRST = MACRO_TARGET
SWAP_SECOND = encode(parse_config({"keys": {"Space": {"macro": "no{Enter}"}}}))


def test_park_keys_before_macro_area_changes() -> None:
    fake = FakePad()
    run_write(pad_with(fake), SWAP_FIRST, confirm=yes, sleep=no_sleep)
    changes = plan(SWAP_SECOND, read_image(pad_with(fake)))
    kinds = [c.kind for c in changes]
    parked = {c.pos for c in changes if c.kind == "park"}
    assert parked == {KEY_BY_NAME["W"].pos, KEY_BY_NAME["Space"].pos}
    assert kinds[:2] == ["park", "park"] and kinds[2] == "macros"
    assert ("key", KEY_BY_NAME["Space"].pos) in [(c.kind, c.pos) for c in changes]
    assert any(c.label == "W: macro M0 -> factory (parked while macros change)" for c in changes)


def test_interrupted_slot_swap_never_plays_wrong_macro() -> None:
    probe = FakePad()
    run_write(pad_with(probe), SWAP_FIRST, confirm=yes, sleep=no_sleep)
    baseline = probe.mutations()
    before_keys = decode_keymap(probe.live_keymap())
    before_slots = split_macro_area(bytes(probe.macros))
    before_macro = {
        k.pos: before_slots[before_keys[k.pos][1]] for k in KEYS if before_keys[k.pos][0] == ENTRY_MACRO
    }
    run_write(pad_with(probe), SWAP_SECOND, confirm=yes, sleep=no_sleep)
    total = probe.mutations() - baseline
    assert total > 2
    target_slots = split_macro_area(SWAP_SECOND.macros)
    for n in range(1, total + 1):
        fake = FakePad()
        run_write(pad_with(fake), SWAP_FIRST, confirm=yes, sleep=no_sleep)
        fake.unplug_after_writes = fake.mutations() + n
        try:
            run_write(pad_with(fake), SWAP_SECOND, confirm=yes, sleep=no_sleep)
        except DeviceError:
            pass
        live_slots = split_macro_area(bytes(fake.macros))
        for pos, entry in decode_keymap(fake.live_keymap()).items():
            if entry[0] != ENTRY_MACRO:
                continue
            got = live_slots.get(entry[1])
            allowed = [before_macro.get(pos)]
            tgt = SWAP_SECOND.keys[pos]
            if tgt[0] == ENTRY_MACRO:
                allowed.append(target_slots[tgt[1]])
            assert got is not None and got in allowed, f"n={n} pos={pos}"
        fake.unplug_after_writes = None
        _, report = run_write(pad_with(fake), SWAP_SECOND, confirm=yes, sleep=no_sleep)
        assert report is not None and report.ok
