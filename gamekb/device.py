"""HID transport for the pad's vendor config interface. Bytes in, bytes out.

Every command waits for its own reply (AA [opcode]); stale input is drained first.
Write commands (06 10, 06 14, 06 0B) must answer status 0x01 in reply[2].
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from enum import Enum
from types import TracebackType
from typing import Any, Callable, Protocol, Sequence

from .layout import (
    KEYMAP_LEN,
    MACRO_AREA_LEN,
    MACRO_WRITE_CHUNK,
    PRODUCT_ID,
    RGB,
    USAGE,
    USAGE_PAGE,
    VENDOR_ID,
)

REPLY = 0xAA
CHUNK = 0x38
OK_STATUS = 0x01
POLL_MS = 50
DRAIN_LIMIT = 64


class DeviceError(Exception):
    """Talking to the pad failed (exit code 3). Re-running converges."""


class NoReply(DeviceError):
    pass


class WriteRejected(DeviceError):
    pass


class DetectStatus(Enum):
    NOT_SEEN = "not_seen"
    NO_CONFIG_INTERFACE = "no_config_interface"
    BUSY = "busy"
    READY = "ready"


_HINTS = {
    DetectStatus.NOT_SEEN: (
        "Windows does not see the pad (VID 0816 / PID 0220). "
        "Check the cable, try another port, avoid unpowered hubs."
    ),
    DetectStatus.NO_CONFIG_INTERFACE: (
        "The pad is connected but its config interface (usage page FF00, usage 02) "
        "is missing. Unplug and replug it."
    ),
    DetectStatus.BUSY: (
        "The pad's config interface could not be opened. "
        "Close the vendor web app or any other tool using the pad."
    ),
    DetectStatus.READY: "Pad ready.",
}


@dataclass(frozen=True)
class DetectResult:
    status: DetectStatus
    path: bytes | None = None

    @property
    def hint(self) -> str:
        return _HINTS[self.status]


class PadNotFound(Exception):
    """The pad cannot be reached at all (exit code 2)."""

    def __init__(self, result: DetectResult) -> None:
        self.result = result
        super().__init__(result.hint)


class Transport(Protocol):
    def write(self, data: bytes) -> int: ...
    def read(self, max_length: int, timeout_ms: int = 0) -> list[int]: ...
    def close(self) -> None: ...


def detect(enumerate_fn: Callable[[int, int], list[dict[str, Any]]] | None = None) -> DetectResult:
    if enumerate_fn is None:
        import hid

        enumerate_fn = hid.enumerate
    devices = enumerate_fn(VENDOR_ID, PRODUCT_ID)
    if not devices:
        return DetectResult(DetectStatus.NOT_SEEN)
    for dev in devices:
        if dev.get("usage_page") == USAGE_PAGE and dev.get("usage") == USAGE:
            return DetectResult(DetectStatus.READY, dev["path"])
    return DetectResult(DetectStatus.NO_CONFIG_INTERFACE)


def _hex(cmd: Sequence[int]) -> str:
    return bytes(cmd[:4]).hex(" ")


class Pad:
    def __init__(self, transport: Transport, timeout_ms: int = 500) -> None:
        self._t = transport
        self._timeout_ms = timeout_ms

    @classmethod
    def open(cls, detect_fn: Callable[[], DetectResult] = detect) -> Pad:
        result = detect_fn()
        if result.status is not DetectStatus.READY:
            raise PadNotFound(result)
        import hid

        dev = hid.device()
        try:
            dev.open_path(result.path)
        except OSError as exc:
            raise PadNotFound(DetectResult(DetectStatus.BUSY)) from exc
        dev.set_nonblocking(False)
        return cls(dev)

    def close(self) -> None:
        self._t.close()

    def __enter__(self) -> Pad:
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        self.close()

    # --- primitive ----------------------------------------------------------

    def xfer(self, cmd: Sequence[int], expect: int | None = None) -> bytes:
        if not 2 <= len(cmd) <= 64:
            raise ValueError(f"command must be 2-64 bytes, got {len(cmd)}")
        want = cmd[1] if expect is None else expect
        try:
            for _ in range(DRAIN_LIMIT):
                if not self._t.read(64, 1):
                    break
            if self._t.write(b"\x00" + bytes(cmd) + bytes(64 - len(cmd))) < 0:
                raise DeviceError(f"I/O error on {_hex(cmd)}: write failed")
            deadline = time.monotonic() + self._timeout_ms / 1000
            while time.monotonic() < deadline:
                reply = self._t.read(64, POLL_MS)
                if len(reply) >= 2 and reply[0] == REPLY and reply[1] == want:
                    return bytes(reply)
        except OSError as exc:
            raise DeviceError(f"I/O error on {_hex(cmd)}: {exc}") from exc
        raise NoReply(f"no reply to {_hex(cmd)} within {self._timeout_ms} ms")

    def _write(self, cmd: Sequence[int]) -> None:
        reply = self.xfer(cmd)
        if reply[2] != OK_STATUS:
            raise WriteRejected(f"pad rejected {_hex(cmd)} (status {reply[2]:#04x})")

    def _read_chunks(self, opcode: int, echo: int, offsets: Sequence[int]) -> bytes:
        out = bytearray()
        for off in offsets:
            reply = self.xfer([0x06, opcode, 0x3A, off], expect=echo)
            if reply[3] != off:
                raise DeviceError(f"out-of-order reply: asked offset {off:#04x}, got {reply[3]:#04x}")
            out += reply[8 : 8 + CHUNK]
        return bytes(out)

    # --- reads --------------------------------------------------------------

    def read_keymap(self) -> bytes:
        """Live main keymap, 4 bytes per position."""
        return self._read_chunks(0x08, 0x07, (0x00, 0x38))[:KEYMAP_LEN]

    def read_region(self) -> bytes:
        """Colors (0x00-0x47) + factory keymap (0x48-0xA7), as addressed by 06 13."""
        return self._read_chunks(0x13, 0x13, (0x00, 0x38, 0x70))

    def read_lighting(self) -> bytes:
        return self.xfer([0x06, 0x0A])

    def _read_macro_chunk(self, length: int, offset: int) -> bytes:
        lo, hi = offset & 0xFF, offset >> 8
        reply = self.xfer([0x06, 0x0C, length, lo, hi], expect=0x0C)
        if (reply[3], reply[4]) != (lo, hi):
            raise DeviceError(f"out-of-order reply: asked macro offset {offset:#06x}")
        return reply[8 : 8 + length]

    def read_macros(self) -> bytes:
        """The 4096-byte macro area: 0x38-byte chunks, then the last 8 bytes."""
        out = bytearray()
        for offset in range(0, MACRO_AREA_LEN - 8, CHUNK):
            out += self._read_macro_chunk(CHUNK, offset)
        out += self._read_macro_chunk(8, MACRO_AREA_LEN - 8)
        return bytes(out)

    # --- writes -------------------------------------------------------------

    def write_key(self, pos: int, entry: bytes) -> None:
        if len(entry) != 4:
            raise ValueError("keymap entry must be 4 bytes")
        self._write([0x06, 0x10, 0x07, 4 * pos, 0, 0, 0, 0, *entry])

    def write_color(self, pos: int, rgb: RGB) -> None:
        self._write([0x06, 0x14, 0x03, 3 * pos, 0, 0, 0, 0, *rgb])

    def write_macro_chunk(self, offset: int, data: bytes) -> None:
        if not 1 <= len(data) <= MACRO_WRITE_CHUNK:
            raise ValueError(f"macro chunk must be 1-{MACRO_WRITE_CHUNK} bytes")
        self._write([0x06, 0x0D, len(data), offset & 0xFF, offset >> 8, *data])

    def write_lighting(
        self, effect: int, brightness: int, speed_wire: tuple[int, int], color: RGB
    ) -> None:
        profile = self.read_lighting()[5]  # must match the displayed profile or nothing changes
        self.xfer([0x06, 0x16, 0, 0, 0, profile, 0, effect])  # replies AA 16 0B, no status byte
        self._write(
            [0x06, 0x0B, 0x0B, 0, 0, profile, 0, effect, brightness, *speed_wire, 0, 0x07, *color]
        )
