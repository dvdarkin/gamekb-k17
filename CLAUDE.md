# gamekb-k17: agent and contributor notes

One-shot tool for the GAMEKB-K17 macropad (VID 0x0816, PID 0x0220): write the layout from
`config.json`, verify by read-back, exit. No resident process, no tray, no hotkeys, no live swaps.

## Run

```
gamekb write        # or: python -m gamekb write
gamekb write claude # profile: profiles/claude.json
gamekb profiles     # list; marks the one on the pad
gamekb gui
```

## Package (`gamekb/`)

| Module | Responsibility |
|---|---|
| `layout.py` | Key positions + factory entries, keycode/modifier/media/effect tables. Single source of truth. |
| `config.py` | `config.json` schema: parse (all errors at once), load, save |
| `encode.py` | Config <-> `DeviceImage` (keymap entries, colors, lighting), human descriptions |
| `macro.py` | Macro text <-> firmware macro area (pure) |
| `device.py` | HID transport: detect, reply-matched `xfer`, reads, writes |
| `sync.py` | `plan` / `apply` / `verify` / `run_write` |
| `profiles.py` | `profiles/<name>.json` lookup, listing, which profile the pad matches |
| `cli.py` | Subcommands and exit codes |
| `gui.py` | tkinter editor (device I/O only on button press) |

Tests use `tests/fakepad.py`, an in-memory pad with the verified byte-offset semantics.

## Protocol rules (see docs/PROTOCOL.md)

- One index space: color offset = 3 × pos, keymap offset = 4 × pos. Use `Key.color_offset` /
  `Key.keymap_offset`; never hand-compute offsets.
- Writes persist in flash and take effect immediately. Never send `06 FB`.
- Lighting commands use the profile byte from the `06 0A` reply byte[5].
- Always match replies to the command (`Pad.xfer`); never read "the next reply".

## Quality gates

```
mypy --strict gamekb/
pytest tests/
```

Both must pass before committing. Console output stays ASCII. Docs: `docs/CONFIG.md`,
`docs/DEVICE.md`, `docs/PROTOCOL.md`, `CONTRIBUTING.md`.

## Macros

`macro.py` converts macro text <-> strokes <-> 4-byte events <-> the 4 KB macro area (`06 0C` read,
`06 0D` write). Keys point at macros with `60 [slot] 01 00`. Writes go park -> macros -> keys -> lighting ->
colors; keys that point at macros are parked on their factory action first, so an interrupted write never leaves a key playing another key's macro. Format: docs/PROTOCOL.md "Macros".
