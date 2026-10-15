"""Offline layout editor. Edits config.json or a profile on a drawing of the pad.

The pad is only touched when a toolbar button is pressed; device work runs on a
worker thread and reports back through a queue polled by the Tk main loop.
Closing the window ends the program.
"""

from __future__ import annotations

import copy
import queue
import threading
import tkinter as tk
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, simpledialog, ttk
from typing import Any, Callable

from .config import (
    ACTION_FIELDS,
    COLOR_ONLY_KEYS,
    ConfigError,
    config_to_dict,
    load_config,
    parse_config,
    save_config,
)
from .device import DeviceError, Pad, PadNotFound
from .encode import DeviceImage, encode, image_to_config
from .macro import DEFAULT_DELAY_MS, MACRO_SLOTS, MAX_MACRO_BYTES, MacroError, macro_bytes
from .layout import (
    EFFECTS,
    ENTRY_MEDIA,
    KEY_BY_NAME,
    KEYCODE_NAMES,
    KEYCODES,
    KEYS,
    MEDIA,
    MEDIA_NAMES,
    MODIFIER_KEY_BY_BIT,
    MODIFIER_KEYS,
    MODIFIERS,
    RGB,
    Key,
)
from .profiles import default_dir, list_profiles, matching_profiles, profile_path
from .sync import Change, plan, read_image, run_write

KEY_PX = 58
GAP = 6
MARGIN = 14
BACKGROUND = "#1e1e2e"
ACTION_KINDS = ("factory", *ACTION_FIELDS)
EFFECT_CHOICES = tuple(e for e in EFFECTS if e != "userlight")


def _grouped_keys() -> tuple[str, ...]:
    """KEYCODES as a person scans them: letters, digits, F1-F24, then the rest in table order."""
    names = list(KEYCODES)
    letters = sorted(n for n in names if len(n) == 1 and n.isalpha())
    digits = [n for n in names if n.isdigit()]
    fkeys = sorted((n for n in names if n[0] == "F" and n[1:].isdigit()), key=lambda n: int(n[1:]))
    grouped = {*letters, *digits, *fkeys}
    return (*letters, *digits, *fkeys, *(n for n in names if n not in grouped))


SHORTCUT_KEY_CHOICES = _grouped_keys()
KEY_CHOICES = (*SHORTCUT_KEY_CHOICES, *MODIFIER_KEYS)
MACRO_TOKENS = (
    "{Enter}", "{Tab}", "{Escape}", "{Backspace}", "{Up}", "{Down}", "{Left}", "{Right}",
    "{Ctrl+C}", "{Ctrl+V}", "{Ctrl+A}", "{Ctrl+Z}", "{Alt+Tab}", "{wait 100}", "{{", "}}",
)
MACRO_HINT = "Typed as-is. {Key} or {Ctrl+C} presses keys, {wait 100} pauses, {{ }} types braces."
EMPTY_CONFIG: dict[str, Any] = {
    "lighting": {"mode": "per-key", "brightness": 4},
    "default_color": [0, 0, 0],
    "keys": {},
}

Log = Callable[[str], None]


def _rgb(value: Any) -> RGB:
    r, g, b = value
    return (int(r), int(g), int(b))


def _hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _text_color(rgb: RGB) -> str:
    r, g, b = rgb
    return "#111111" if 0.299 * r + 0.587 * g + 0.114 * b > 140 else "#ffffff"


def _action_text(spec: dict[str, Any]) -> str:
    for kind in ("key", "shortcut", "media", "macro"):
        if kind in spec:
            return str(spec[kind])
    return ""


def _action_fields(spec: dict[str, Any]) -> dict[str, Any]:
    return {k: spec[k] for k in (*ACTION_FIELDS, "delay_ms") if k in spec}


def factory_text(key: Key) -> str:
    """What a key does with no action configured, from its factory keymap entry."""
    kind, a, b = key.factory[0], key.factory[1], key.factory[2]
    if key.name in COLOR_ONLY_KEYS:
        return f"dial: {MEDIA_NAMES[a]}, fixed in firmware (color and label only)"
    if kind == ENTRY_MEDIA:
        return f"factory: media {MEDIA_NAMES[a]}"
    if b == 0:
        return f"factory: sends {MODIFIER_KEY_BY_BIT[a]}"
    name = KEYCODE_NAMES.get(b)
    if name is None:
        return "factory: Fn layer (FN+Q switches profile, FN+R next effect)"
    return f"factory: types {name.lower()}" if len(name) == 1 else f"factory: sends {name}"


def macro_usage(keys: dict[str, Any]) -> tuple[int, int]:
    """(slots, bytes) the keys' macros take; equal text and delay share a slot, invalid ones are skipped."""
    seen: set[tuple[str, int]] = set()
    total = 0
    for spec in keys.values():
        ident = (spec.get("macro"), spec.get("delay_ms", DEFAULT_DELAY_MS))
        if not isinstance(ident[0], str) or not isinstance(ident[1], int) or ident in seen:
            continue
        try:
            total += len(macro_bytes(ident[0], ident[1]))
        except MacroError:
            continue
        seen.add((ident[0], ident[1]))
    return len(seen), total


def _as_int(text: str) -> int | str:
    try:
        return int(text)
    except ValueError:
        return text  # parse_config reports it


class App:
    def __init__(
        self, root: tk.Tk, path: Path, open_pad: Callable[[], Pad], profiles_dir: Path | None = None
    ) -> None:
        self.root = root
        self.path = path
        self.open_pad = open_pad
        self.profiles_dir = profiles_dir if profiles_dir is not None else default_dir()
        self.data: dict[str, Any] = copy.deepcopy(EMPTY_CONFIG)
        self.selected: str | None = None
        self.color: RGB | None = None
        self.default_color: RGB = (0, 0, 0)
        self.dirty = False
        self.busy = False
        self.protected = False  # path failed to load: never overwrite it or write its stand-in
        self._events: queue.Queue[tuple[str, Any]] = queue.Queue()
        self._buttons: list[ttk.Button] = []

        self.kind_var = tk.StringVar(master=root, value="factory")
        self.value_var = tk.StringVar(master=root)
        self.label_var = tk.StringVar(master=root)
        self.delay_var = tk.StringVar(master=root, value=str(DEFAULT_DELAY_MS))
        self.mode_var = tk.StringVar(master=root, value="per-key")
        self.bright_var = tk.StringVar(master=root, value="4")
        self.effect_var = tk.StringVar(master=root, value="static")
        self.speed_var = tk.StringVar(master=root, value="1")
        self.profile_var = tk.StringVar(master=root)
        self.mod_vars = {name: tk.BooleanVar(master=root) for name in MODIFIERS}
        self.shortcut_key_var = tk.StringVar(master=root)
        self.insert_var = tk.StringVar(master=root)
        self.factory_var = tk.StringVar(master=root)
        self.macro_usage_var = tk.StringVar(master=root)
        self.error_var = tk.StringVar(master=root)
        self._syncing = False  # True while panel fields are set from code, not by the user

        root.title("GAMEKB-K17")
        self._build()
        self.kind_var.trace_add("write", lambda *_: self._refresh_value_choices())
        self.value_var.trace_add("write", lambda *_: self._on_value_changed())
        for var in (*self.mod_vars.values(), self.shortcut_key_var):
            var.trace_add("write", lambda *_: self._compose_shortcut())
        for var in (self.label_var, self.delay_var):
            var.trace_add("write", lambda *_: self._refresh_status())
        self._load(path)
        root.protocol("WM_DELETE_WINDOW", self._on_close)
        root.after(50, self._pump)

    # --- layout ---------------------------------------------------------------

    def _build(self) -> None:
        bar = ttk.Frame(self.root, padding=6)
        bar.pack(fill="x")
        ttk.Label(bar, text="Profile").pack(side="left", padx=(2, 4))
        self.profile_box = ttk.Combobox(bar, textvariable=self.profile_var, state="readonly", width=16)
        self.profile_box.pack(side="left", padx=2)
        self.profile_box.bind("<<ComboboxSelected>>", lambda _e: self.load_profile(self.profile_var.get()))
        for text, command in (
            ("Open...", self.open_file),
            ("Save", self.save),
            ("Save as profile...", self.save_as_profile),
            ("Read from pad", self.read_from_pad),
            ("Diff", self.diff),
            ("Write to pad", self.write_to_pad),
        ):
            button = ttk.Button(bar, text=text, command=command)
            button.pack(side="left", padx=2)
            self._buttons.append(button)
        self.path_label = ttk.Label(bar, text="")
        self.path_label.pack(side="left", padx=8)

        body = ttk.Frame(self.root, padding=6)
        body.pack(fill="both", expand=True)
        cols = max(k.col + k.width for k in KEYS)
        rows = max(k.row for k in KEYS) + 1
        self.canvas = tk.Canvas(
            body,
            width=int(2 * MARGIN + cols * (KEY_PX + GAP)),
            height=int(2 * MARGIN + rows * (KEY_PX + GAP)),
            bg=BACKGROUND,
            highlightthickness=0,
        )
        self.canvas.pack(side="left")
        self.canvas.bind("<Button-1>", self._on_click)

        side = ttk.Frame(body, padding=(10, 0))
        side.pack(side="left", fill="y")
        self._build_key_panel(side)
        self._build_lighting_panel(side)

        self.log = tk.Text(self.root, height=12, width=100, state="disabled", font=("Consolas", 9))
        self.log.pack(fill="both", expand=True, padx=6, pady=6)

    def _build_key_panel(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Key", padding=6)
        box.pack(fill="x")
        box.columnconfigure(1, weight=1)
        self.key_label = ttk.Label(box, text="click a key")
        self.key_label.grid(row=0, column=0, columnspan=2, sticky="w")
        ttk.Label(box, text="Action").grid(row=1, column=0, sticky="w")
        self.kind_box = ttk.Combobox(
            box, textvariable=self.kind_var, values=ACTION_KINDS, state="readonly", width=22
        )
        self.kind_box.grid(row=1, column=1, sticky="w")

        # One frame per action kind, stacked in the same cell; only the current kind's is shown.
        self.factory_frame = ttk.Frame(box)
        ttk.Label(
            self.factory_frame, textvariable=self.factory_var, wraplength=260, foreground="#666666"
        ).pack(anchor="w")

        self.choice_frame = ttk.Frame(box)
        ttk.Label(self.choice_frame, text="Value").grid(row=0, column=0, sticky="w", padx=(0, 6))
        self.value_box = ttk.Combobox(
            self.choice_frame, textvariable=self.value_var, state="readonly", width=22, height=20
        )
        self.value_box.grid(row=0, column=1, sticky="w")

        self.shortcut_frame = ttk.Frame(box)
        for i, name in enumerate(MODIFIERS):
            ttk.Checkbutton(self.shortcut_frame, text=name, variable=self.mod_vars[name]).grid(
                row=i // 4, column=i % 4, sticky="w"
            )
        ttk.Label(self.shortcut_frame, text="+ key").grid(row=2, column=0, sticky="w")
        ttk.Combobox(
            self.shortcut_frame, textvariable=self.shortcut_key_var, values=SHORTCUT_KEY_CHOICES,
            state="readonly", width=14, height=20,
        ).grid(row=2, column=1, columnspan=3, sticky="w")
        ttk.Label(self.shortcut_frame, textvariable=self.value_var, font=("Consolas", 9)).grid(
            row=3, column=0, columnspan=4, sticky="w"
        )

        self.macro_frame = ttk.Frame(box)
        self.macro_entry = ttk.Entry(self.macro_frame, textvariable=self.value_var, width=34)
        self.macro_entry.grid(row=0, column=0, columnspan=2, sticky="we")
        insert = ttk.Combobox(
            self.macro_frame, textvariable=self.insert_var, values=MACRO_TOKENS,
            state="readonly", width=12, height=20,
        )
        insert.grid(row=1, column=0, sticky="w", pady=2)
        insert.bind("<<ComboboxSelected>>", lambda _e: self.insert_macro_token(self.insert_var.get()))
        self.insert_var.set("Insert...")
        delay = ttk.Frame(self.macro_frame)
        delay.grid(row=1, column=1, sticky="e")
        ttk.Label(delay, text="delay ms").pack(side="left")
        ttk.Spinbox(delay, textvariable=self.delay_var, from_=0, to=65535, width=6).pack(side="left", padx=2)
        ttk.Label(self.macro_frame, text=MACRO_HINT, wraplength=260, foreground="#666666").grid(
            row=2, column=0, columnspan=2, sticky="w"
        )
        ttk.Label(self.macro_frame, textvariable=self.macro_usage_var).grid(
            row=3, column=0, columnspan=2, sticky="w"
        )

        self._kind_frames = {
            "factory": self.factory_frame, "key": self.choice_frame, "shortcut": self.shortcut_frame,
            "media": self.choice_frame, "macro": self.macro_frame,
        }
        for frame in self._all_kind_frames():
            frame.grid(row=2, column=0, columnspan=2, sticky="we", pady=4)
            frame.grid_remove()

        ttk.Label(box, text="Label").grid(row=3, column=0, sticky="w")
        ttk.Entry(box, textvariable=self.label_var, width=24).grid(row=3, column=1, sticky="w")
        ttk.Label(box, text="Color").grid(row=4, column=0, sticky="w")
        colors = ttk.Frame(box)
        colors.grid(row=4, column=1, sticky="w")
        self.swatch = tk.Label(colors, text="      ", relief="groove")
        self.swatch.pack(side="left")
        ttk.Button(colors, text="Pick", command=self._pick_key_color).pack(side="left", padx=2)
        ttk.Button(colors, text="Default", command=self._clear_key_color).pack(side="left")
        ttk.Label(box, textvariable=self.error_var, wraplength=260, foreground="#c00000").grid(
            row=5, column=0, columnspan=2, sticky="w"
        )
        ttk.Button(box, text="Apply to key", command=self.apply_key).grid(
            row=6, column=0, columnspan=2, pady=(6, 0)
        )

    def _all_kind_frames(self) -> list[ttk.Frame]:
        return list(dict.fromkeys(self._kind_frames.values()))

    def _build_lighting_panel(self, parent: ttk.Frame) -> None:
        box = ttk.LabelFrame(parent, text="Lighting", padding=6)
        box.pack(fill="x", pady=(8, 0))
        widgets: tuple[tuple[str, tk.Widget], ...] = (
            ("Mode", ttk.Combobox(box, textvariable=self.mode_var, values=("per-key", "effect"), state="readonly", width=12)),
            ("Brightness", ttk.Spinbox(box, textvariable=self.bright_var, from_=0, to=4, width=5)),
            ("Effect", ttk.Combobox(box, textvariable=self.effect_var, values=EFFECT_CHOICES, state="readonly", width=12)),
            ("Speed", ttk.Spinbox(box, textvariable=self.speed_var, from_=0, to=2, width=5)),
        )
        for row, (text, widget) in enumerate(widgets):
            ttk.Label(box, text=text).grid(row=row, column=0, sticky="w")
            widget.grid(row=row, column=1, sticky="w")
        ttk.Button(box, text="Default key color", command=self._pick_default_color).grid(
            row=4, column=0, columnspan=2, sticky="w"
        )
        ttk.Button(box, text="Apply lighting", command=self.apply_lighting).grid(
            row=5, column=0, columnspan=2, pady=(6, 0)
        )

    # --- state ----------------------------------------------------------------

    def _load(self, path: Path) -> None:
        self.path = path
        self.path_label.configure(text=str(path))
        self._refresh_profiles()
        self.protected = False
        if path.exists():
            try:
                self._set_data(config_to_dict(load_config(path)), dirty=False)
                self._log(f"loaded {path}")
                return
            except ConfigError as exc:
                self.protected = True
                self._log(str(exc))
                self._log(
                    f"{path.name} will not be overwritten and nothing can be written to the pad "
                    "until a valid config is opened; fix the file and use Open..."
                )
        self._set_data(copy.deepcopy(EMPTY_CONFIG), dirty=False)

    def _set_data(self, data: dict[str, Any], dirty: bool) -> None:
        self.data = data
        self.dirty = dirty
        lighting: dict[str, Any] = data.get("lighting", {})
        self.mode_var.set(str(lighting.get("mode", "per-key")))
        self.bright_var.set(str(lighting.get("brightness", 4)))
        self.effect_var.set(str(lighting.get("effect", "static")))
        self.speed_var.set(str(lighting.get("speed", 1)))
        self.default_color = _rgb(data.get("default_color", (0, 0, 0)))
        if self.selected is not None:
            self.select_key(self.selected)
        self._draw()

    def _accept(self, candidate: dict[str, Any]) -> bool:
        try:
            parse_config(candidate)
        except ConfigError as exc:
            self._log(str(exc))
            return False
        self.data = candidate
        self.dirty = True
        self._draw()
        return True

    def select_key(self, name: str) -> None:
        if self.selected is not None and self.selected != name and self._key_pending():
            if not self.apply_key():
                self._log(f"{self.selected}: edit not applied (see above); it was discarded")
        self.selected = name
        spec: dict[str, Any] = self.data.get("keys", {}).get(name, {})
        kind = next((k for k in ACTION_FIELDS if k in spec), "factory")
        key = KEY_BY_NAME[name]
        self.kind_box.configure(state="disabled" if name in COLOR_ONLY_KEYS else "readonly")
        self.factory_var.set(factory_text(key))
        self.kind_var.set(kind)
        self.value_var.set("" if kind == "factory" else str(spec[kind]))
        self.macro_entry.icursor("end")  # Insert... appends until the text is clicked
        self.label_var.set(str(spec.get("label", "")))
        self.delay_var.set(str(spec.get("delay_ms", DEFAULT_DELAY_MS)))
        self.color = _rgb(spec["color"]) if "color" in spec else None
        self.key_label.configure(text=f"{name}   factory entry {key.factory.hex(' ')}")
        self._refresh_value_choices()
        self._refresh_swatch()
        self._draw()

    def apply_key(self) -> bool:
        if self.selected is None:
            self._log("select a key first")
            return False
        return self._accept(self._key_candidate(self.selected))

    def _key_candidate(self, name: str) -> dict[str, Any]:
        """self.data with the key panel's fields stored on key `name`."""
        entry: dict[str, Any] = {}
        kind = self.kind_var.get()
        if kind != "factory":
            value = self.value_var.get()
            entry[kind] = value if kind == "macro" else value.strip()  # macro whitespace is typed
        if kind == "macro":
            delay = _as_int(self.delay_var.get())
            if delay != DEFAULT_DELAY_MS:
                entry["delay_ms"] = delay
        label = self.label_var.get().strip()
        if label:
            entry["label"] = label
        if self.color is not None:
            entry["color"] = list(self.color)
        candidate = copy.deepcopy(self.data)
        keys: dict[str, Any] = candidate.setdefault("keys", {})
        if entry:
            keys[name] = entry
        else:
            keys.pop(name, None)
        return candidate

    def apply_lighting(self) -> bool:
        return self._accept(self._lighting_candidate())

    def _lighting_candidate(self) -> dict[str, Any]:
        lighting: dict[str, Any] = {"mode": self.mode_var.get(), "brightness": _as_int(self.bright_var.get())}
        if lighting["mode"] == "effect":
            lighting.update(effect=self.effect_var.get(), speed=_as_int(self.speed_var.get()))
        candidate = copy.deepcopy(self.data)
        candidate["lighting"] = lighting
        candidate["default_color"] = list(self.default_color)
        return candidate

    # --- pending edits: panel fields not yet stored in self.data ---------------------

    def _differs(self, candidate: dict[str, Any]) -> bool:
        try:
            return parse_config(candidate) != parse_config(self.data)
        except ConfigError:
            return True  # invalid edit: applying it reports the error

    def _key_pending(self) -> bool:
        return self.selected is not None and self._differs(self._key_candidate(self.selected))

    def _has_unsaved(self) -> bool:
        return self.dirty or self._key_pending() or self._differs(self._lighting_candidate())

    def _commit_pending(self) -> bool:
        """Store unapplied panel edits before saving, diffing or writing. False if one is invalid."""
        if self._key_pending() and not self.apply_key():
            return False
        if self._differs(self._lighting_candidate()) and not self.apply_lighting():
            return False
        return True

    # --- colors -----------------------------------------------------------------

    def _ask_color(self, initial: RGB) -> RGB | None:
        rgb, _ = colorchooser.askcolor(color=_hex(initial), parent=self.root)
        return None if rgb is None else _rgb(rgb)

    def _pick_key_color(self) -> None:
        picked = self._ask_color(self.color or self.default_color)
        if picked is not None:
            self.color = picked
            self._refresh_swatch()
            self.apply_key()

    def _clear_key_color(self) -> None:
        self.color = None
        self._refresh_swatch()
        if self.selected is not None:
            self.apply_key()

    def _pick_default_color(self) -> None:
        picked = self._ask_color(self.default_color)
        if picked is not None:
            self.default_color = picked
            self.apply_lighting()

    def _refresh_swatch(self) -> None:
        shown = self.color or self.default_color
        self.swatch.configure(bg=_hex(shown), fg=_text_color(shown), text="      " if self.color else "default")

    def _refresh_value_choices(self) -> None:
        """Show the controls for the chosen action kind."""
        kind = self.kind_var.get()
        for frame in self._all_kind_frames():
            frame.grid_remove()
        self._kind_frames[kind].grid()
        if kind in ("key", "media"):
            choices = KEY_CHOICES if kind == "key" else tuple(MEDIA)
            self.value_box.configure(values=choices)
            if self.value_var.get().strip() not in choices:
                self.value_var.set("")
        elif kind == "factory":
            self.value_var.set("")
        self._sync_shortcut_fields()
        self._refresh_status()

    def _on_value_changed(self) -> None:
        if not self._syncing:
            self._sync_shortcut_fields()
        self._refresh_status()

    def _sync_shortcut_fields(self) -> None:
        """Set the modifier boxes and shortcut key from value_var ('Ctrl+Shift+Z')."""
        parts = [p.strip() for p in self.value_var.get().split("+")] if self.kind_var.get() == "shortcut" else []
        self._syncing = True
        try:
            for name, var in self.mod_vars.items():
                var.set(name in parts[:-1])
            last = parts[-1] if parts else ""
            self.shortcut_key_var.set(last if last in SHORTCUT_KEY_CHOICES else "")
        finally:
            self._syncing = False

    def _compose_shortcut(self) -> None:
        if self._syncing or self.kind_var.get() != "shortcut":
            return
        parts = [name for name, var in self.mod_vars.items() if var.get()]
        if self.shortcut_key_var.get():
            parts.append(self.shortcut_key_var.get())
        self._syncing = True
        try:
            self.value_var.set("+".join(parts))
        finally:
            self._syncing = False

    def insert_macro_token(self, token: str) -> None:
        self.macro_entry.insert("insert", token)
        self.insert_var.set("Insert...")
        self.macro_entry.focus_set()

    def _refresh_status(self) -> None:
        """Panel error line and macro usage, for the panel as it would be applied."""
        keys: dict[str, Any] = self.data.get("keys", {})
        self.error_var.set("")
        if self.selected is not None:
            candidate = self._key_candidate(self.selected)
            keys = candidate.get("keys", {})
            try:
                parse_config(candidate)
            except ConfigError as exc:
                self.error_var.set("\n".join(exc.errors))
        slots, used = macro_usage(keys)
        self.macro_usage_var.set(f"{slots}/{MACRO_SLOTS} macros, {used}/{MAX_MACRO_BYTES} bytes on the pad")

    # --- drawing ------------------------------------------------------------------

    def _key_box(self, key: Key) -> tuple[float, float, float, float]:
        x = MARGIN + key.col * (KEY_PX + GAP)
        y = MARGIN + key.row * (KEY_PX + GAP)
        width = key.width * KEY_PX + (key.width - 1) * GAP
        return x, y, x + width, y + KEY_PX

    def _draw(self) -> None:
        canvas = self.canvas
        canvas.delete("all")
        keys: dict[str, Any] = self.data.get("keys", {})
        for key in KEYS:
            spec: dict[str, Any] = keys.get(key.name, {})
            rgb = _rgb(spec["color"]) if "color" in spec else self.default_color
            x0, y0, x1, y1 = self._key_box(key)
            selected = key.name == self.selected
            canvas.create_rectangle(
                x0, y0, x1, y1, fill=_hex(rgb),
                outline="#ffffff" if selected else "#45475a", width=3 if selected else 1,
            )
            detail = str(spec.get("label") or _action_text(spec))
            canvas.create_text(
                (x0 + x1) / 2, (y0 + y1) / 2,
                text=f"{key.name}\n{detail}" if detail else key.name,
                fill=_text_color(rgb), font=("Segoe UI", 8), width=x1 - x0 - 4, justify="center",
            )

    def _on_click(self, event: tk.Event[tk.Canvas]) -> None:
        for key in KEYS:
            x0, y0, x1, y1 = self._key_box(key)
            if x0 <= event.x <= x1 and y0 <= event.y <= y1:
                self.select_key(key.name)
                return

    # --- toolbar --------------------------------------------------------------------

    def open_file(self) -> None:
        name = filedialog.askopenfilename(
            parent=self.root, filetypes=[("JSON", "*.json")], initialdir=str(self.path.parent)
        )
        if name:
            self._load(Path(name))

    # --- profiles ----------------------------------------------------------------------

    def _current_profile(self) -> str:
        return self.path.stem if self.path.parent.resolve() == self.profiles_dir.resolve() else ""

    def _refresh_profiles(self) -> None:
        self.profile_box.configure(values=list_profiles(self.profiles_dir))
        name = self._current_profile()
        self.profile_var.set(name)
        self.root.title(f"GAMEKB-K17 - {name or self.path.name}")

    def load_profile(self, name: str) -> None:
        path = self.profiles_dir / f"{name}.json"
        if self.busy or path == self.path:
            self.profile_var.set(self._current_profile())
            return
        if self._has_unsaved() and not messagebox.askyesno(
            "Unsaved changes", "Discard unsaved changes?", parent=self.root
        ):
            self.profile_var.set(self._current_profile())
            return
        self._load(path)

    def save_as_profile(self, name: str | None = None) -> bool:
        if name is None:
            name = simpledialog.askstring("Save as profile", "Profile name:", parent=self.root)
            if not name:
                return False
        if not self._commit_pending():
            return False
        try:
            path = profile_path(name, self.profiles_dir)
            cfg = parse_config(self.data)
        except (ValueError, ConfigError) as exc:
            self._log(str(exc))
            return False
        if path.exists() and path != self.path and not messagebox.askyesno(
            "Overwrite profile", f"Profile '{name}' exists. Overwrite it?", parent=self.root
        ):
            return False
        self.profiles_dir.mkdir(parents=True, exist_ok=True)
        save_config(cfg, path)
        self.path = path
        self.path_label.configure(text=str(path))
        self.protected = False
        self.dirty = False
        self._refresh_profiles()
        self._log(f"saved profile {name} ({path})")
        return True

    def save(self) -> bool:
        if self.protected:
            self._log(f"not saving: {self.path.name} failed to load and is left untouched")
            return False
        if not self._commit_pending():
            return False
        try:
            cfg = parse_config(self.data)
        except ConfigError as exc:
            self._log(str(exc))
            return False
        save_config(cfg, self.path)
        self.dirty = False
        self._log(f"saved {self.path}")
        return True

    def _target(self) -> DeviceImage | None:
        try:
            return encode(parse_config(self.data))
        except ConfigError as exc:
            self._log(str(exc))
            return None

    def read_from_pad(self) -> None:
        def job(log: Log) -> None:
            with self.open_pad() as pad:
                image = read_image(pad)
            cfg, warnings = image_to_config(image)
            for warning in warnings:
                log(f"warning: {warning}")
            matches, _ = matching_profiles(image, self.profiles_dir)
            log(f"pad matches profile: {', '.join(matches)}" if matches else "pad matches no profile")
            self._events.put(("loaded", config_to_dict(cfg)))

        self._background("read from pad", job)

    def diff(self) -> None:
        if not self._commit_pending():
            return
        target = self._target()
        if target is None:
            return

        def job(log: Log) -> None:
            with self.open_pad() as pad:
                changes = plan(target, read_image(pad))
            if not changes:
                log("pad already matches config")
            for change in changes:
                log(f"  {change.label}")

        self._background("diff", job)

    def write_to_pad(self) -> None:
        if self.protected:
            self._log("not writing: the loaded config was invalid; open a valid config first")
            return
        if not self._commit_pending():
            return
        if self.dirty and not self.save():
            return
        target = self._target()
        if target is None:
            return

        def job(log: Log) -> None:
            with self.open_pad() as pad:
                changes, report = run_write(
                    pad, target, confirm=self._confirm_from_worker,
                    progress=lambda c: log(f"  wrote {c.label}"),
                )
            if report is None:
                log("cancelled; nothing written")
                return
            for line in report.lines():
                log(line)
            log(
                f"done: {len(changes)} change(s) written, pad verified"
                if report.ok
                else "verify FAILED: the pad does not match the config"
            )

        self._background("write to pad", job)

    # --- worker plumbing --------------------------------------------------------------

    def _background(self, title: str, job: Callable[[Log], None]) -> None:
        if self.busy:
            return
        self._set_busy(True)
        self._log(f"--- {title}")

        def worker() -> None:
            try:
                job(lambda text: self._events.put(("log", text)))
            except (PadNotFound, DeviceError) as exc:
                self._events.put(("log", f"error: {exc}"))
            finally:
                self._events.put(("done", None))

        threading.Thread(target=worker, daemon=True).start()

    def _confirm_from_worker(self, changes: list[Change]) -> bool:
        if not changes:
            self._events.put(("log", "pad already matches config"))
            return True
        for change in changes:
            self._events.put(("log", f"  {change.label}"))
        answer: list[bool] = []
        done = threading.Event()
        self._events.put(("confirm", (len(changes), answer, done)))
        done.wait()
        return bool(answer and answer[0])

    def _pump(self) -> None:
        while True:
            try:
                kind, payload = self._events.get_nowait()
            except queue.Empty:
                break
            if kind == "log":
                self._log(str(payload))
            elif kind == "loaded":
                kept = self._keep_labels(payload)
                self._set_data(payload, dirty=True)
                self._log("loaded layout from pad (not saved yet)")
                if kept:
                    self._log(f"kept {kept} label(s) from {self.path.name}; the pad does not store labels")
            elif kind == "confirm":
                count, answer, done = payload
                answer.append(messagebox.askyesno(
                    "Write to pad", f"Apply {count} change(s) to the pad?", parent=self.root
                ))
                done.set()
            elif kind == "done":
                self._set_busy(False)
        self.root.after(50, self._pump)

    def _keep_labels(self, loaded: dict[str, Any]) -> int:
        """Copy labels onto a layout read from the pad where the key's action is unchanged."""
        kept = 0
        keys: dict[str, Any] = loaded.setdefault("keys", {})
        for name, spec in self.data.get("keys", {}).items():
            label = spec.get("label")
            if label and _action_fields(spec) == _action_fields(keys.get(name, {})):
                keys.setdefault(name, {})["label"] = label
                kept += 1
        return kept

    def _set_busy(self, busy: bool) -> None:
        self.busy = busy
        for button in self._buttons:
            button.configure(state="disabled" if busy else "normal")
        self.profile_box.configure(state="disabled" if busy else "readonly")

    # --- log / close ------------------------------------------------------------------

    def _log(self, text: str) -> None:
        self.log.configure(state="normal")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def log_text(self) -> str:
        return self.log.get("1.0", "end")

    def _on_close(self) -> None:
        if self.busy:
            self._log("please wait: the pad is being written")
            return
        if self._has_unsaved() and not messagebox.askyesno(
            "Unsaved changes", "Discard unsaved changes?", parent=self.root
        ):
            return
        self.root.destroy()


def run_gui(path: Path, open_pad: Callable[[], Pad]) -> None:
    root = tk.Tk()
    App(root, path, open_pad)
    root.mainloop()
