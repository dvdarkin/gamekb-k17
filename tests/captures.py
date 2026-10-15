"""Macro bytes captured from the vendor web app on 2026-10-14 (see docs/PROTOCOL.md, Macros)."""

from __future__ import annotations

# M0: y, e, s, Enter recorded with real timings (86, 278, 88, 221, 67, 602, 52, 0 ms)
M0_YES = bytes.fromhex(
    "56 00 42 1c 16 01 02 1c 58 00 42 08 dd 00 02 08 43 00 42 16 5a 02 02 16 34 00 42 28 00 00 82 28"
)
# M1: n, o, Enter
M1_NO = bytes.fromhex("3c 00 42 11 1d 01 02 11 41 00 42 12 1b 04 02 12 4e 00 42 28 00 00 82 28")
