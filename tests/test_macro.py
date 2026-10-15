"""Tests for gamekb.macro."""

from __future__ import annotations

import re

import pytest

from gamekb.macro import MacroError, Stroke, build_macro_area, macro_bytes, parse_macro
from tests.captures import M0_YES, M1_NO


def test_yes_enter_golden() -> None:
    assert macro_bytes("yes{Enter}", 20) == bytes.fromhex(
        "14 00 42 1c 14 00 02 1c 14 00 42 08 14 00 02 08 14 00 42 16 14 00 02 16 14 00 42 28 00 00 82 28"
    )


def test_newline_is_enter_and_tab_is_tab() -> None:
    assert parse_macro("yes\n") == parse_macro("yes{Enter}")
    assert parse_macro("a\tb") == parse_macro("a{Tab}b")


@pytest.mark.parametrize(
    ("text", "strokes"),
    [
        ("A", [Stroke((0xE1,), 0x04)]),
        ("!", [Stroke((0xE1,), 0x1E)]),
        ("a b", [Stroke((), 0x04), Stroke((), 0x2C), Stroke((), 0x05)]),
        ("{Ctrl+Shift+Z}", [Stroke((0xE0, 0xE1), 0x1D)]),
        ("{LCtrl}", [Stroke((), 0xE0)]),
        ("{F5}", [Stroke((), 0x3E)]),
        ("{{x}}", [Stroke((0xE1,), 0x2F), Stroke((), 0x1B), Stroke((0xE1,), 0x30)]),
        ("a{wait 500}b{wait 1}{wait 2}c", [Stroke((), 0x04, 500), Stroke((), 0x05, 3), Stroke((), 0x06)]),
    ],
)
def test_parse_macro(text: str, strokes: list[Stroke]) -> None:
    assert parse_macro(text) == strokes


def test_shortcut_event_order() -> None:
    # Ctrl down, Shift down, Z down, Z up, Shift up, Ctrl up (last)
    assert macro_bytes("{Ctrl+Shift+Z}", 5) == bytes.fromhex(
        "05 00 42 e0 05 00 42 e1 05 00 42 1d 05 00 02 1d 05 00 02 e1 00 00 82 e0"
    )


def test_wait_adds_to_previous_strokes_last_event() -> None:
    assert macro_bytes("a{wait 100}b", 10) == bytes.fromhex("0a 00 42 04 6e 00 02 04 0a 00 42 05 00 00 82 05")


@pytest.mark.parametrize(
    ("text", "fragment"),
    [
        ("", "empty"),
        ("café", "cannot be typed"),
        ("{Enter", "unclosed"),
        ("a}b", "unmatched"),
        ("{Nope}", "unknown token {Nope}"),
        ("{enter}", "did you mean {Enter}"),
        ("{Hyper+C}", "unknown modifier"),
        ("{wait 5}a", "cannot come first"),
        ("a{wait x}", "{wait 500}"),
        ("a{wait ²}b", "{wait 500}"),
        ("a{wait 5}", "at the end"),
        ("a{wait 70000}b", "65535"),
        ("a{wait 65535}b", "65535"),
    ],
)
def test_parse_errors(text: str, fragment: str) -> None:
    with pytest.raises(MacroError, match=re.escape(fragment)):
        macro_bytes(text, 20)


def test_area_matches_vendor_captures() -> None:
    one = build_macro_area([M0_YES])
    assert one[:4] == bytes.fromhex("40 00 ff ff")
    assert one[4:0x40] == b"\xff" * 0x3C
    assert one[0x40:0x60] == M0_YES
    assert one[0x60:] == bytes(0x1000 - 0x60)
    two = build_macro_area([M0_YES, M1_NO])
    assert two[:4] == bytes.fromhex("40 00 60 00")
    assert two[0x60:0x78] == M1_NO
    assert len(two) == 0x1000


def test_empty_area() -> None:
    assert build_macro_area([]) == b"\xff" * 0x40 + bytes(0x1000 - 0x40)


def test_area_limits() -> None:
    with pytest.raises(MacroError, match="at most 16"):
        build_macro_area([M1_NO] * 17)
    with pytest.raises(MacroError, match="4032"):
        build_macro_area([bytes(4036)])
from gamekb.macro import (  # noqa: E402
    Event,
    decode_events,
    describe_macro_area,
    events_to_strokes,
    macro_to_text,
    split_macro_area,
)


def test_split_area() -> None:
    assert split_macro_area(build_macro_area([M0_YES, M1_NO])) == {0: M0_YES, 1: M1_NO}
    assert split_macro_area(build_macro_area([])) == {}


def test_split_area_factory_zeros_is_empty() -> None:
    assert split_macro_area(bytes(0x1000)) == {}


def test_split_area_malformed() -> None:
    bad_ptr = bytearray(build_macro_area([M0_YES]))
    bad_ptr[0:2] = (0x41).to_bytes(2, "little")
    assert split_macro_area(bytes(bad_ptr)) == {0: None}
    no_end = bytearray(build_macro_area([M0_YES]))
    no_end[0x5E] = 0x02  # clear the last-event flag: data runs off the end
    assert split_macro_area(bytes(no_end)) == {0: None}


def test_decode_events_from_capture() -> None:
    events = decode_events(M0_YES)
    assert events is not None
    assert [e.delay_ms for e in events] == [86, 278, 88, 221, 67, 602, 52, 0]
    assert [e.press for e in events] == [True, False] * 4
    assert decode_events(bytes.fromhex("00 00 43 04")) is None      # unknown flags
    assert decode_events(bytes.fromhex("00 00 82 04 00 00 82 05")) is None  # last flag too early
    assert decode_events(bytes.fromhex("00 00 42 04")) is None      # no last flag


@pytest.mark.parametrize(
    "text",
    ["yes{Enter}", "Hello, World!{Enter}", "{Ctrl+A}{Ctrl+C}", "a{wait 500}b", "{{json}}", "{LShift}x",
     "git status\n", "{Ctrl+Shift+Z}"],
)
def test_text_round_trip(text: str) -> None:
    raw = macro_bytes(text, 15)
    decoded = macro_to_text(raw)
    assert decoded is not None
    back, delay, normalized = decoded
    assert (delay, normalized) == (15, False)
    assert macro_bytes(back, delay) == raw


def test_vendor_recording_is_normalized() -> None:
    assert macro_to_text(M0_YES) == ("yes{Enter}", 52, True)
    assert macro_to_text(M1_NO) == ("no{Enter}", 60, True)


def test_unexpressible_macros() -> None:
    release_first = [Event(0, False, 0x04), Event(0, True, 0x04)]
    assert events_to_strokes(release_first) is None
    unreleased = bytes.fromhex("05 00 42 04 00 00 82 05")  # a down, b up
    assert macro_to_text(unreleased) is None


def test_describe_macro_area() -> None:
    assert describe_macro_area(build_macro_area([])) == "none"
    area = build_macro_area([macro_bytes("yes{Enter}", 20), M1_NO])
    assert describe_macro_area(area) == "M0 'yes{Enter}', M1 'no{Enter}' (recorded timings)"
