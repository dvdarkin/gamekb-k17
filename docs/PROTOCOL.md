# GAMEKB-K17 configuration protocol

The pad's vendor HID interface, reverse-engineered from the vendor's WebHID configurator
(sdcx-tech.com) and checked on real hardware. The pad belongs to the SDCX / SDINNOVATION family,
whose protocol is shared across many unbranded macropads. See [DEVICE.md](DEVICE.md) for how to
recognise one.

Every statement below is marked by how it is known:

- **Verified**: tested on a GAMEKB-K17 (2026-10). gamekb relies only on verified facts.
- **Observed**: seen in replies or captures, but its meaning is not confirmed.
- **sdcx-keypad**: documented by [sdcx-keypad](https://github.com/parsaj-dev/sdcx-keypad) for
  other devices in the family; not tested on the K17.

Bytes are hex unless written otherwise. "pos" is a key position (see [Key positions](#2-key-positions)).

---

## 1. Transport

**Verified.**

- USB `0816:0220`. Use the HID interface with usage page `FF00`, usage `02` (interface 2). The
  other interfaces are the keyboard and consumer-control endpoints the OS reads keys from.
- Every command is one 64-byte output report. With hidapi on Windows, prefix a `00` report ID
  (65 bytes written). Pad unused bytes with `00`.
- Every command byte 0 is `06` (the command group); byte 1 is the opcode.
- Replies are 64-byte input reports starting `AA [opcode]`. Exception: the live keymap read
  `06 08` replies `AA 07`.
- Write replies are `AA [opcode] 01 [offset]`: byte 2 `01` means accepted.
- The interface also produces unsolicited and stale input reports. Drain pending input before each
  command and wait for the reply whose opcode (and, for chunked reads, offset) matches the command.
  Reading "the next report" returns stale data.
- Timing: one write round trip is about 8 ms (23 key writes measured at 0.18 s). A full read of
  keymap, colors, lighting and the 4 KB macro area takes about 1.4 s; the 74-packet macro read
  dominates.

## 2. Key positions

**Verified.** One index space covers keys, colors and keymap entries.

| pos | key | pos | key | pos | key |
|---|---|---|---|---|---|
| 0 | (placeholder) | 8 | S | 16 | V |
| 1 | Q | 9 | D | 17 | Vol- (dial turn) |
| 2 | W | 10 | F | 18 | Bright+ (side wheel) |
| 3 | E | 11 | Vol+ (dial turn) | 19 | Bright- (side wheel) |
| 4 | R | 12 | LShift | 20 | (placeholder) |
| 5 | Mute (dial press) | 13 | Z | 21 | (placeholder) |
| 6 | Tab | 14 | X | 22 | Space |
| 7 | A | 15 | C | 23 | FN |

- **Color offset = 3 × pos. Keymap offset = 4 × pos.**
- Placeholders hold `13 00 00 00`; never write them.
- pos 16 is the keycap labelled V. Its factory output is `g`, and the vendor app labels it G.

## 3. Keymap entries

**Verified.** Each key has a 4-byte entry `[type, a, b, 00]`.

| type | meaning | examples |
|---|---|---|
| `20` | keyboard: a = modifier mask, b = HID keyboard usage | `20 00 14 00` Q · `20 04 17 00` Alt+T · `20 02 00 00` LShift alone |
| `30` | consumer control: a = consumer usage (low byte) | `30 e2` Mute · `30 e9` Vol+ · `30 ea` Vol- · `30 cd` Play/Pause · `30 6f`/`30 70` brightness up/down |
| `60` | macro: a = slot (M0–M15), b = `01` | `60 00 01 00` plays M0 |
| `1F` | pad function, run by the firmware: a = function | `1F 01 00 00` / `1F 02 00 00` LED brightness up/down · `1F 03 00 00` next lighting effect |
| `13` | placeholder (pos 0, 20, 21) | `13 00 00 00` |

- Modifier mask: LCtrl `01`, LShift `02`, LAlt `04`, LWin `08`, RCtrl `10`, RShift `20`, RAlt `40`,
  RWin `80`.
- FN's factory entry is `20 00 ff 00`. Usage `ff` is the firmware's Fn-layer key.
- Macro entry byte b is `01` in every capture. Observed: it probably means "play once". The vendor
  app has no other play mode for this pad.
- `1F` entries work on the main layer (side wheel, tested 2026-10-15). `1F 03` steps through the
  effects. `1F 01` / `1F 02` change the brightness while an effect runs, and the pad stores the new
  level; in userlight they do nothing (see [Lighting](#6-lighting)).
- From the vendor app's code, not tested: `1F 00` light on/off, `1F 04` color switch, `1F 05` /
  `1F 06` speed up/down, `1F 07` GUI lock, `1F 10` keyboard reset. Mouse buttons are type `10`,
  mouse movement `11`, system keys (power, sleep, wake) `40`. Consumer usages above `FF` put the
  high byte in b (Calculator `30 92 01 00`). gamekb writes none of these.

## 4. Config region (`06 13` addressing)

| Offset | Contents | Status |
|---|---|---|
| `000`–`047` | Per-key colors, RGB at 3 × pos | Verified |
| `048`–`0A7` | Factory main keymap, 4 bytes at 4 × pos | Verified |
| `0A8`–`107` | A second factory keymap (T Y U I / G H J K / V B N M on the letter keys, pos 22 reads `00 00 ff ff`) | Observed. Probably the second profile reached with FN+Q. |
| `108`– | Live main keymap (the same entries `06 08` reads and `06 10` writes) | Verified |

## 5. Commands

The "dec" column gives the opcode in decimal, the notation sdcx-keypad uses.

| Purpose | Command | dec | Status |
|---|---|---|---|
| Device info | `06 05` | 5 | Not used by gamekb. Payload fields: see sdcx-keypad |
| Read live keymap | `06 08 3A [off]`, off `00`, `38` → reply `AA 07 …`, `38` bytes from reply[8] | 8 | Verified |
| Lighting state | `06 0A` → reply [5] profile, [7] effect, [8] brightness, [9–10] speed, [12] color mode, [13–15] RGB | 10 | Verified |
| Lighting config | `06 0B 0B 00 00 [profile] 00 [effect] [bright] [spd hi] [spd lo] 00 07 [R G B]` | 11 | Verified |
| Read macro area | `06 0C [n] [off lo] [off hi]` | 12 | Verified, see §7 |
| Write macro area | `06 0D [n ≤ 3B] [off lo] [off hi] [n bytes]` | 13 | Verified, see §7 |
| Write one key | `06 10 07 [4×pos] 00 00 00 00 [4-byte entry]` | 16 | Verified |
| Read config region | `06 13 3A [off]`, off `00`, `38`, `70` (gamekb reads these), `A8`, `E0` → `38` bytes from reply[8] | 19 | Verified |
| Write one key color | `06 14 03 [3×pos] 00 00 00 00 [R G B]` | 20 | Verified |
| Select effect | `06 16 00 00 00 [profile] 00 [effect]` (reply `AA 16 0B …`, no status byte) | 22 | Verified |
| Select profile | `06 FB [profile]` | 251 | **Do not send.** See §8 |
| Factory reset | `06 0F FF` | 15 | sdcx-keypad, not tested. Discards keymap, macros and colors |

- `06 08` with the extra layer bytes sdcx-keypad documents (`06 08 3A 00 00 00 [layer]`) gets no
  reply on the K17. Observed.
- Writes take effect immediately and persist in flash across unplug and replug. There is no
  "apply" or "save" command. Verified.

## 6. Lighting

**Verified** unless marked.

- `[profile]` in `06 0B` and `06 16` must be the byte at reply[5] of `06 0A`. Any other value
  changes a profile that is not the one shown.
- Effects: off `00`, static `01`, breath `02`, colorloop `03`, stream `04`, meteor `05`,
  trigspread `06`, userlight `07`. The vendor app calls them LightOff, Static, Breath, ColorLoop,
  Stream, Meteor, TrigSpread Reverse and UserLight.
- **userlight** (`07`) shows the per-key color table. The other effects ignore it.
- Brightness 0–4. Speed wire bytes [9, 10]: slow `02 00`, medium `02 01`, fast `02 03`.
- Brightness only affects effects. In userlight the per-key colors always show at full strength:
  brightness 0 and 4 look the same. The pad still stores and reports the value. To dim a key, give
  it a darker color.
- Each effect seems to keep its own brightness and speed: stepping to breath with `1F 03` read
  back brightness 3, speed medium, after userlight at brightness 4. Observed once.
- The pad stores effect, brightness and speed from `06 0B`, but **never stores bytes [12–15]**
  (mode and color). They read back `07 ff ff ff` whatever is sent (`00`, `07`, `08` tried).
  Effects run the firmware's own color cycle; the vendor app has no effect color either.
- Per-key colors sent right after switching to userlight can be dropped. Wait about 300 ms.
- Per-key colors written while an effect runs are stored and appear when userlight is selected.

## 7. Macros

**Verified** (vendor app captures, then gamekb writes type-tested on the pad).

### Macro area

A separate 4096-byte area, not part of the config region.

- Read: `06 0C [n] [off lo] [off hi]` → reply `AA 0C n lo hi 00 00 00`, then n data bytes from
  reply[8]. Read with n = `38` at offsets `000`, `038`, …, `FC0`, then n = `08` at `FF8`.
- Write: `06 0D [n] [off lo] [off hi] [n data bytes]`, data from byte 5, n ≤ `3B` →
  reply `AA 0D 01 lo hi`. The vendor app rewrites the whole area at offsets `000`, `03B`, `076`, …,
  `FAC` (n = `3B`) and `FE7` (n = `19`): 70 writes, about 0.75 s. Writing only changed chunks on the
  same alignment works.

### Layout

- `000`–`03F`: directory. Slot n's pointer is a u16 little-endian at bytes 2n, 2n+1 (M0 at 0–1,
  …, M15 at 30–31). `FF FF` means empty. A factory-fresh pad reads `00 00`. Bytes `020`–`03F` read
  `FF`.
- Macro data is packed from `040`, in slot order. Bytes after the last macro are `00`.
- Maximum data: 4032 bytes (4096 − 64).

### Events

Each macro is a list of 4-byte events:

```
[delay lo] [delay hi] [flags] [usage]
```

- delay: milliseconds to wait **after** this event (u16 little-endian).
- flags: `42` press, `02` release; `| 80` on the macro's last event (`C2`, `82`).
- usage: HID keyboard usage. Modifiers are ordinary events with usages `E0`–`E7` (LCtrl `E0`,
  LShift `E1`, LAlt `E2`, LWin `E3`, RCtrl `E4`, RShift `E5`, RAlt `E6`, RWin `E7`), pressed before
  and released after the key they modify.
- The firmware replays recorded uneven delays as-is, and also plays uniform 20 ms delays reliably.

Captured from the vendor app, `yes⏎` recorded into M0:

```
56 00 42 1c | 16 01 02 1c | 58 00 42 08 | dd 00 02 08 | 43 00 42 16 | 5a 02 02 16 | 34 00 42 28 | 00 00 82 28
 y down 86ms  y up 278ms    e down 88ms  e up 221ms    s down 67ms  s up 602ms    Enter down    Enter up, last
```

Assigning it to Space: `06 10 07 58 00 00 00 00 60 00 01 00`. Pressing Space then types `yes⏎`.

### Safe rewrite order

A key that points at a slot plays whatever bytes are there. When the macro area changes, first set
every macro key to a non-macro entry, then write the area, then point the keys at their new slots.
An interrupted write then never leaves a key playing another key's macro.

## 8. Behaviour

**Verified** unless marked.

- Keymap, colors, lighting and macros persist across unplug and replug.
- **Dial:** press is pos 5, turns are pos 11 / 17. Writes to these positions are stored and read
  back, but the firmware ignores them: pressing the dial still mutes.
- **Side wheel (Bright+ / Bright-)** and **FN** can be remapped like any key. Test entries
  `20 00 05 00`, `20 00 11 00` and `20 00 09 00` typed b, n and f. Remapping FN removes the factory
  Fn layer.
- **Factory Fn layer** (from the vendor app): FN+Q = profile switch, FN+R = next lighting effect.
  The other keys pass through. Probably `1F 13` and `1F 03` (inferred from the vendor app's action
  table; the Fn layer has not been read).
- **Profiles:** `06 0A` reply[5] reads `01` on the tested pad. After `06 FB 00` it still reads `01`.
  What `06 FB` selects, and how the keymap in region `0A8`–`107` relates to it, is unresolved.
  gamekb never sends `06 FB` and writes only the active profile.

## 9. Hardware test log

GAMEKB-K17, firmware as shipped in 2026, Windows 11, hidapi.

- write + verify of a full layout: pass (13 changes, 0.9 s including reads); re-run sends nothing
- keys type-tested in Notepad: pass
- persistence after unplug/replug (keymap, colors, lighting): pass
- media remap on the dial press (Mute → Play/Pause): stored, **ignored by firmware**
- per-key colors written in effect mode, shown after switching to userlight: pass
- lighting persists: pass for effect, brightness, speed; base color is never stored
- read of vendor-recorded macros: pass (timings normalized to the most common gap, with a warning)
- macro `yes{Enter}` at a uniform 20 ms on Space: pass (2.7 s write including reads)
- macro persistence after unplug/replug: pass
- modifiers in macros (`Hello, World!{Enter}{Ctrl+A}`): pass: capitals, punctuation, Enter, select all
- clearing all macros, keys back to factory, then restoring: pass
- remapping Bright+, Bright-, FN: pass
- `1F 03` (next effect) on the side wheel: pass, each roll steps to the next effect
- `1F 01` / `1F 02` (LED brightness) on the side wheel: pass in breath (stored, read back as 0),
  **no effect in userlight**
- brightness 0 vs 4 in userlight via `06 0B`: no visible difference
- 18 keys remapped, 10 macros, per-key colors (a Claude Code layout): write + verify pass

## 10. Credits

The command group, the 4 KB macro area and the family device list were documented independently by
[parsaj-dev/sdcx-keypad](https://github.com/parsaj-dev/sdcx-keypad) (MIT), which targets the 6-key
HCY-K006 on Linux. The K17 positions, the macro event format, the lighting quirks and everything
marked Verified above come from this project's own captures and hardware tests.
