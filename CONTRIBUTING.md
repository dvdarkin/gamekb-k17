# Contributing

## Setup

```
pip install -e ".[dev]"
python -m mypy --strict gamekb/
python -m pytest tests/
```

Both must pass before a pull request. Tests run without a pad: `tests/fakepad.py` is an in-memory
pad that implements the verified byte-level behaviour from [docs/PROTOCOL.md](docs/PROTOCOL.md).
The GUI tests are skipped when no display is available.

## Code map

| Module | Responsibility |
|---|---|
| `gamekb/layout.py` | Key positions, factory entries, keycode/modifier/media/effect tables. Single source of truth. |
| `gamekb/config.py` | Config schema: parse (all errors at once), load, save |
| `gamekb/encode.py` | Config <-> `DeviceImage` (keymap entries, colors, lighting, macro area) |
| `gamekb/macro.py` | Macro text <-> events <-> the 4 KB macro area (pure) |
| `gamekb/device.py` | HID transport: detect, reply-matched `xfer`, reads, writes |
| `gamekb/sync.py` | `plan` / `apply` / `verify` / `run_write` |
| `gamekb/profiles.py` | `profiles/<name>.json` lookup, listing, matching the pad |
| `gamekb/cli.py`, `gamekb/gui.py` | Command line and tkinter editor |

## Rules

- Console output stays ASCII (Windows consoles).
- Use `Key.color_offset` / `Key.keymap_offset`; never compute offsets by hand.
- Always match replies to their command (`Pad.xfer`).
- Never send a command that is not verified on hardware. In particular never send `06 FB`
  (profile select) or `06 0F` (reset), whose effects on the K17 are unknown.

## Hardware findings

New facts about the pad are welcome, as long as they say how they are known. Add them to
[docs/PROTOCOL.md](docs/PROTOCOL.md) marked **Verified** (tested on a K17, with the bytes sent and
what happened) or **Observed** (seen, meaning unconfirmed), and add a line to its hardware test log.
Captures from the vendor web app are the best source: they show exact bytes.

## Other devices in the family

Other SDCX / SDINNOVATION pads probably share the protocol with different key positions. A port
needs that device's position table and factory entries (read them with `06 13`, see PROTOCOL.md §4)
and hardware tests of every write type. Open an issue with the USB ID, the product strings and the
key layout first.
