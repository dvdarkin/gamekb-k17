"""In-memory emulation of the pad's config interface.

Semantics verified on hardware 2026-10-14:
- 06 10 writes 4 bytes at keymap byte offset cmd[3] (live keymap at region 0x108)
- 06 0C n lo hi reads n bytes of the 4 KB macro area; 06 0D n lo hi data writes them (data from byte 5)
- 06 14 writes 3 bytes at color byte offset cmd[3] (region 0x00)
- 06 08 3A off reads the live keymap (reply echo 0x07); 06 13 3A off reads the region from 0
- 06 0A reply mirrors the 06 0B layout
"""

from __future__ import annotations

from collections import deque

from gamekb.layout import FACTORY_KEYMAP_OFFSET, KEYMAP_LEN, KEYS, MACRO_AREA_LEN, PLACEHOLDER_POSITIONS

LIVE_KEYMAP = 0x108
REGION_SIZE = 0x200
MUTATING = frozenset({0x10, 0x14, 0x16, 0x0B, 0x0D})


def factory_keymap() -> bytes:
    table = bytearray(KEYMAP_LEN)
    for pos in PLACEHOLDER_POSITIONS:
        table[4 * pos : 4 * pos + 4] = b"\x13\x00\x00\x00"
    for key in KEYS:
        table[key.keymap_offset : key.keymap_offset + 4] = key.factory
    return bytes(table)


class FakePad:
    def __init__(self, live_keymap: bytes | None = None) -> None:
        self.region = bytearray(REGION_SIZE)
        self.region[FACTORY_KEYMAP_OFFSET : FACTORY_KEYMAP_OFFSET + KEYMAP_LEN] = factory_keymap()
        self.region[LIVE_KEYMAP : LIVE_KEYMAP + KEYMAP_LEN] = live_keymap or factory_keymap()
        self.lighting = bytearray(64)
        self.lighting[0:16] = bytes(
            [0xAA, 0x0A, 0x0B, 0, 0, 0x01, 0, 0x07, 0x04, 0x02, 0x00, 0, 0x07, 0xFF, 0xFF, 0xFF]
        )
        self.macros = bytearray(MACRO_AREA_LEN)  # factory-fresh: all zeros
        self.inbox: deque[list[int]] = deque()
        self.writes: list[bytes] = []
        self.silent: set[int] = set()   # opcodes that get no reply
        self.reject: set[int] = set()   # opcodes answered with status 0x00
        self.unplug_after_writes: int | None = None  # OSError once this many mutations happened
        self.closed = False

    # --- Transport protocol -------------------------------------------------

    def write(self, data: bytes) -> int:
        assert len(data) == 65 and data[0] == 0x00, "expected report id + 64 bytes"
        cmd = bytes(data[1:])
        assert cmd[0] == 0x06
        if (
            cmd[1] in MUTATING
            and self.unplug_after_writes is not None
            and self.mutations() >= self.unplug_after_writes
        ):
            raise OSError("device disconnected")
        self.writes.append(cmd)
        reply = self._handle(cmd)
        if reply is not None and cmd[1] not in self.silent:
            self.inbox.append(list(reply + bytes(64 - len(reply))))
        return len(data)

    def read(self, max_length: int, timeout_ms: int = 0) -> list[int]:
        return self.inbox.popleft() if self.inbox else []

    def close(self) -> None:
        self.closed = True

    # --- helpers for tests --------------------------------------------------

    def live_keymap(self) -> bytes:
        return bytes(self.region[LIVE_KEYMAP : LIVE_KEYMAP + KEYMAP_LEN])

    def mutations(self) -> int:
        return sum(1 for w in self.writes if w[1] in MUTATING)

    def _handle(self, cmd: bytes) -> bytes | None:
        op = cmd[1]
        status = 0x00 if op in self.reject else 0x01
        if op == 0x05:
            return bytes([0xAA, 0x05, 0x0E, 0, 0, 0x01])
        if op == 0x0A:
            return bytes(self.lighting)
        if op in (0x08, 0x13):
            off = cmd[3]
            base = LIVE_KEYMAP if op == 0x08 else 0
            echo = 0x07 if op == 0x08 else 0x13
            return bytes([0xAA, echo, 0x3A, off, 0, 0, 0, 0]) + bytes(self.region[base + off : base + off + 0x38])
        if op == 0x10:
            off = cmd[3]
            if status:
                self.region[LIVE_KEYMAP + off : LIVE_KEYMAP + off + 4] = cmd[8:12]
            return bytes([0xAA, 0x10, status, off])
        if op == 0x14:
            off = cmd[3]
            if status:
                self.region[off : off + 3] = cmd[8:11]
            return bytes([0xAA, 0x14, status, off])
        if op == 0x16:
            self.lighting[7] = cmd[7]
            return bytes([0xAA, 0x16, 0x0B, 0, 0, self.lighting[5]])
        if op == 0x0B:
            if status:
                # Real pad (2026-10-14): in UserLight (0x07) the base color is not stored.
                end = 13 if cmd[7] == 0x07 else 16
                self.lighting[7:end] = cmd[7:end]
            return bytes([0xAA, 0x0B, status])
        if op == 0x0C:
            n, off = cmd[2], cmd[3] | cmd[4] << 8
            return bytes([0xAA, 0x0C, n, cmd[3], cmd[4], 0, 0, 0]) + bytes(self.macros[off : off + n])
        if op == 0x0D:
            n, off = cmd[2], cmd[3] | cmd[4] << 8
            if status:
                self.macros[off : off + n] = cmd[5 : 5 + n]
            return bytes([0xAA, 0x0D, status, cmd[3], cmd[4]])
        raise AssertionError(f"unexpected opcode {op:#04x}")
