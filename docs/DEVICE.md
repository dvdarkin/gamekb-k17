# GAMEKB-K17 (HCY-K017): the hardware

A one-handed gaming macropad: 14 keys, two thumb keys, a clickable volume dial and a side wheel,
with per-key RGB. It is sold unbranded under many shop names. The reliable way to recognise it is
by its USB identity, not by its listing.

## Identification

| | Value |
|---|---|
| Model label (back of the pad) | `HCY-K017` |
| Top plate | Embossed `KuaiJieJianPan` |
| USB ID | `0816:0220` |
| Manufacturer string | `SDCINNOVATION` |
| Product strings | `GAMEKB-K17` (configuration interface), `SD-Keyboard` (keyboard), `SDMulti` (media and other controls) |
| Configuration interface | HID, usage page `0xFF00`, usage `0x02`, interface 2 |
| Vendor configurator | Web app at sdcx-tech.com ("WEB HUB", Chrome or Edge only, uses WebHID). The device shows as "GAMEKB-K17" there. |
| Vendor Windows tool | "SDTech Options Setup" (SDCX keyboard driver) |
| Family | SDCX / SDINNOVATION macropads. They share the protocol and differ in layout. Note the spelling: this pad reports `SDCINNOVATION`; other family members (e.g. HCY-K006) report `SDINNOVATION`. |

USB interfaces as enumerated:

| Interface | Product string | Usage page / usage | Purpose |
|---|---|---|---|
| 0 | SD-Keyboard | `01` / `06` | Keyboard (what keys type) |
| 1 | SDMulti | `01` / `06`, `0C` / `01`, `01` / `80`, `01` / `0C`, `01` / `02` | Keyboard, consumer control, system control, mouse |
| 2 | GAMEKB-K17 | `FF00` / `02` | Vendor configuration (this protocol) |

### Is my pad one of these?

- **Windows:** Device Manager → Human Interface Devices → a device's Properties → Details → Hardware
  Ids shows `VID_0816&PID_0220`. Or install gamekb and run `gamekb detect`.
- **Linux:** `lsusb` lists `ID 0816:0220`. `0816` is not in the public USB ID database, so the
  vendor name may be blank.
- **Any OS:** if sdcx-tech.com recognises the pad as "GAMEKB-K17", it is this device.
- **Label:** the back of the pad reads `HCY-K017`. Shop listings use other names, so confirm with the
  USB ID.

Other SDCX / SDINNOVATION pads (different USB IDs, other key counts) probably use the same commands.
Their key positions differ. [sdcx-keypad](https://github.com/parsaj-dev/sdcx-keypad) lists the 196
USB IDs the vendor configurator accepts.

## Physical layout

```
   side wheel                                            dial
   (roll: Bright+ / Bright-)                             (press: Mute, turn: Vol+ / Vol-)
        \
         \       Q      W      E      R
           Tab   A      S      D      F                    ( O )
         LShift  Z      X      C      V
         [========= palm rest =========]   Space   FN
```

- The keycap at the end of the bottom letter row is **V**. Its factory output is `g`, and the vendor
  app labels it G.
- Space and FN are thumb keys to the right of the palm rest.

## Controls and what can be changed

| Control | Positions | Remappable | Notes |
|---|---|---|---|
| 14 letter-row keys | Q W E R, Tab A S D F, LShift Z X C V | yes | Factory: their letters, Tab, LShift alone |
| Thumb keys | Space, FN | yes | Remapping FN removes the factory Fn layer |
| Side wheel | Bright+, Bright- | yes | Factory: screen brightness up/down (consumer `6F` / `70`) |
| Dial | Mute (press), Vol+ / Vol- (turn) | **no** | Handled by firmware. Writes are stored but ignored. Color only. |

That is 18 remappable controls. Every control has a color entry, the dial included.

### Factory Fn layer

Held FN changes two keys (from the vendor app):

- FN + Q: switch profile
- FN + R: next lighting effect

The other keys pass through unchanged.

## Lighting

- Per-key RGB (the firmware's "UserLight" effect), or one of the built-in effects: LightOff, Static,
  Breath, ColorLoop, Stream, Meteor, TrigSpread Reverse.
- Brightness 0–4 and speed slow / medium / fast apply to the built-in effects. Per-key colors always
  show at full strength, whatever the brightness; to dim a key, give it a darker color.
- Effects use the firmware's own color cycle. No effect color can be set.

## Macros

16 slots (M0–M15) in a 4 KB flash area, assignable to any remappable key. Each macro is a sequence
of key press and release events with a delay after each one. Details:
[PROTOCOL.md §7](PROTOCOL.md#7-macros).

## Vendor app features gamekb does not cover

- Mouse actions and extra consumer actions (Homepage, Web-Refresh/Stop/Forward/Backward/Favorites/
  Search, Calculator, Local PC, Mail): their encodings are not decoded yet.
- Editing the Fn layer and switching profiles.
- The vendor app's "Download" section: not examined. gamekb sends nothing that could update firmware.
