"""Tests for gamekb.device against the in-memory FakePad."""

from __future__ import annotations

from typing import Any

import pytest

from gamekb.device import (
    DetectResult,
    DetectStatus,
    DeviceError,
    NoReply,
    Pad,
    PadNotFound,
    WriteRejected,
    detect,
)
from tests.fakepad import FakePad, factory_keymap


def pad_with(fake: FakePad) -> Pad:
    return Pad(fake, timeout_ms=30)


def test_detect_statuses() -> None:
    other = {"usage_page": 0x01, "usage": 0x06, "path": b"kbd"}
    cfg = {"usage_page": 0xFF00, "usage": 0x02, "path": b"cfg"}

    def none(v: int, p: int) -> list[dict[str, Any]]:
        return []

    def only_keyboard(v: int, p: int) -> list[dict[str, Any]]:
        return [other]

    def both(v: int, p: int) -> list[dict[str, Any]]:
        return [other, cfg]

    assert detect(none).status is DetectStatus.NOT_SEEN
    assert detect(only_keyboard).status is DetectStatus.NO_CONFIG_INTERFACE
    ready = detect(both)
    assert ready.status is DetectStatus.READY and ready.path == b"cfg"


def test_open_reports_not_seen() -> None:
    with pytest.raises(PadNotFound, match="does not see"):
        Pad.open(lambda: DetectResult(DetectStatus.NOT_SEEN))


def test_open_reports_busy_interface(monkeypatch: pytest.MonkeyPatch) -> None:
    import hid

    class Busy:
        def open_path(self, path: bytes) -> None:
            raise OSError("open failed")

    monkeypatch.setattr(hid, "device", Busy)
    with pytest.raises(PadNotFound) as info:
        Pad.open(lambda: DetectResult(DetectStatus.READY, b"cfg"))
    assert info.value.result.status is DetectStatus.BUSY
    assert "vendor web app" in str(info.value)


def test_xfer_sends_report_id_and_pads_to_64() -> None:
    fake = FakePad()
    pad_with(fake).xfer([0x06, 0x05])
    assert fake.writes[-1] == bytes([0x06, 0x05]) + bytes(62)


def test_xfer_skips_stale_replies() -> None:
    fake = FakePad()
    fake.inbox.append([0xAA, 0x14, 0x01, 0x03] + [0] * 60)  # leftover from another command
    fake.inbox.append([0xAA, 0x0A] + [0] * 62)              # stale reply with the SAME opcode
    reply = pad_with(fake).read_lighting()
    assert reply[7] == 0x07  # the fresh reply, not the zeroed stale one


def test_no_reply_raises() -> None:
    fake = FakePad()
    fake.silent.add(0x10)
    with pytest.raises(NoReply, match="06 10"):
        pad_with(fake).write_key(1, bytes(4))


def test_rejected_write_raises() -> None:
    fake = FakePad()
    fake.reject.add(0x14)
    with pytest.raises(WriteRejected):
        pad_with(fake).write_color(1, (1, 2, 3))


def test_oserror_becomes_device_error() -> None:
    fake = FakePad()
    fake.unplug_after_writes = 0
    with pytest.raises(DeviceError, match="I/O error"):
        pad_with(fake).write_key(1, bytes.fromhex("20001400"))


def test_write_key_uses_4x_offset() -> None:
    fake = FakePad()
    pad = pad_with(fake)
    pad.write_key(2, bytes.fromhex("20001c00"))
    assert fake.writes[-1][:12] == bytes.fromhex("06 10 07 08 00 00 00 00 20 00 1c 00")
    assert pad.read_keymap()[8:12] == bytes.fromhex("20001c00")


def test_write_color_uses_3x_offset() -> None:
    pad = pad_with(FakePad())
    pad.write_color(4, (255, 0, 255))
    assert pad.read_region()[12:15] == bytes([255, 0, 255])


def test_read_region_contains_factory_keymap() -> None:
    region = pad_with(FakePad()).read_region()
    assert len(region) == 0xA8
    assert region[0x48:0xA8] == factory_keymap()


def test_write_lighting_uses_queried_profile_byte() -> None:
    fake = FakePad()
    fake.lighting[5] = 0x01
    pad_with(fake).write_lighting(0x04, 2, (0x02, 0x03), (0, 200, 255))
    sent = [w for w in fake.writes if w[1] in (0x16, 0x0B)]
    assert [w[5] for w in sent] == [0x01, 0x01]
    assert fake.lighting[7:16] == bytes([0x04, 2, 0x02, 0x03, 0, 0x07, 0, 200, 255])


def test_pad_closes_transport() -> None:
    fake = FakePad()
    with pad_with(fake):
        pass
    assert fake.closed


def test_read_macros_reads_4096_bytes_in_chunks() -> None:
    fake = FakePad()
    fake.macros[0:4] = b"\x40\x00\xff\xff"
    fake.macros[0xFFF] = 0x5A
    data = pad_with(fake).read_macros()
    assert len(data) == 4096
    assert data[:4] == b"\x40\x00\xff\xff" and data[0xFFF] == 0x5A
    reads = [w for w in fake.writes if w[1] == 0x0C]
    assert len(reads) == 74
    assert reads[-1][:5] == bytes.fromhex("06 0c 08 f8 0f")


def test_write_macro_chunk() -> None:
    fake = FakePad()
    pad = pad_with(fake)
    pad.write_macro_chunk(0x3B, bytes(range(1, 60)))
    assert fake.writes[-1][:6] == bytes.fromhex("06 0d 3b 3b 00 01")
    assert pad.read_macros()[0x3B:0x76] == bytes(range(1, 60))


def test_write_macro_chunk_rejects_bad_sizes() -> None:
    pad = pad_with(FakePad())
    with pytest.raises(ValueError):
        pad.write_macro_chunk(0, bytes(60))
    with pytest.raises(ValueError):
        pad.write_macro_chunk(0, b"")
