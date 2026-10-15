"""Smoke tests for the tkinter editor (skipped when no display is available)."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Iterator

import pytest

tk = pytest.importorskip("tkinter")

from gamekb.config import load_config  # noqa: E402
from gamekb.device import Pad  # noqa: E402
from gamekb.gui import App  # noqa: E402
from tests.fakepad import FakePad  # noqa: E402


@pytest.fixture(scope="module")
def root() -> Iterator[Any]:
    # One interpreter per module: creating many Tk() roots in one process
    # intermittently fails Tcl init on Windows ("invalid command name tcl_findLibrary").
    try:
        tk_root = tk.Tk()
    except tk.TclError as exc:
        pytest.skip(f"no display available: {exc}")
    tk_root.withdraw()
    yield tk_root
    tk_root.destroy()


@pytest.fixture
def app(root: Any, tmp_path: Path) -> Iterator[App]:
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"keys": {"Q": {"shortcut": "Alt+T"}}}), encoding="utf-8")
    fake = FakePad()
    application = App(root, path, lambda: Pad(fake, timeout_ms=30), profiles_dir=tmp_path / "profiles")
    yield application
    for child in root.winfo_children():
        child.destroy()


def test_loads_config_and_selects_key(app: App) -> None:
    app.select_key("Q")
    assert app.kind_var.get() == "shortcut"
    assert app.value_var.get() == "Alt+T"


def test_apply_valid_edit(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("key")
    app.value_var.set("Escape")
    app.label_var.set("esc")
    assert app.apply_key()
    assert app.data["keys"]["W"] == {"key": "Escape", "label": "esc"}
    assert app.dirty


def test_apply_invalid_edit_is_rejected(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("shortcut")
    app.value_var.set("Hyper+C")
    assert not app.apply_key()
    assert "W" not in app.data["keys"]
    assert "Hyper" in app.log_text()


def test_save_writes_loadable_config(app: App) -> None:
    app.select_key("E")
    app.kind_var.set("media")
    app.value_var.set("Mute")
    assert app.apply_key()
    assert app.save()
    assert load_config(app.path).keys["E"].action.value == "Mute"


def test_lighting_edit(app: App) -> None:
    app.mode_var.set("effect")
    app.effect_var.set("breath")
    app.bright_var.set("2")
    app.speed_var.set("0")
    assert app.apply_lighting()
    assert app.data["lighting"] == {
        "mode": "effect", "brightness": 2, "effect": "breath", "speed": 0,
    }


def test_write_button_runs_in_background(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gamekb.gui.messagebox.askyesno", lambda *a, **k: True)
    app.write_to_pad()
    deadline = time.monotonic() + 5
    while app.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.01)
    assert not app.busy
    assert "pad verified" in app.log_text()


def test_close_is_refused_while_writing(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    destroyed: list[bool] = []
    monkeypatch.setattr(app.root, "destroy", lambda: destroyed.append(True))
    app.busy = True
    app._on_close()
    assert destroyed == []
    assert "wait" in app.log_text()


def test_invalid_config_file_is_never_overwritten_or_written(
    root: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    path = tmp_path / "config.json"
    original = '{"keys": {"Q": {"colour": [1, 2, 3]}}}'
    path.write_text(original, encoding="utf-8")
    fake = FakePad()
    application = App(root, path, lambda: Pad(fake, timeout_ms=30))
    try:
        application.select_key("Q")
        application.kind_var.set("key")
        application.value_var.set("Escape")
        assert application.apply_key()
        assert not application.save()
        application.write_to_pad()
        assert not application.busy
        assert path.read_text(encoding="utf-8") == original
        assert fake.writes == []
    finally:
        for child in root.winfo_children():
            child.destroy()


def test_macro_edit(app: App) -> None:
    app.select_key("Space")
    app.kind_var.set("macro")
    app.value_var.set("yes{Enter}")
    app.delay_var.set("5")
    assert app.apply_key()
    assert app.data["keys"]["Space"] == {"macro": "yes{Enter}", "delay_ms": 5}
    app.select_key("Q")
    app.select_key("Space")
    assert app.kind_var.get() == "macro"
    assert app.value_var.get() == "yes{Enter}"
    assert app.delay_var.get() == "5"


def test_macro_edit_default_delay_not_written(app: App) -> None:
    app.select_key("Space")
    app.kind_var.set("macro")
    app.value_var.set(" hi ")
    app.delay_var.set("20")
    assert app.apply_key()
    assert app.data["keys"]["Space"] == {"macro": " hi "}


@pytest.fixture
def profiles_app(root: Any, tmp_path: Path) -> Iterator[tuple[App, Path, FakePad]]:
    directory = tmp_path / "profiles"
    directory.mkdir()
    (directory / "alpha.json").write_text(json.dumps({"keys": {"Q": {"key": "Escape"}}}), encoding="utf-8")
    (directory / "beta.json").write_text(json.dumps({"keys": {"Q": {"key": "Enter"}}}), encoding="utf-8")
    fake = FakePad()
    application = App(root, directory / "alpha.json", lambda: Pad(fake, timeout_ms=30), profiles_dir=directory)
    yield application, directory, fake
    for child in root.winfo_children():
        child.destroy()


def wait_idle(app: App) -> None:
    deadline = time.monotonic() + 5
    while app.busy and time.monotonic() < deadline:
        app.root.update()
        time.sleep(0.01)
    app.root.update()


def test_profile_picker_lists_and_switches(profiles_app: tuple[App, Path, FakePad]) -> None:
    app, directory, _ = profiles_app
    assert list(app.profile_box.cget("values")) == ["alpha", "beta"]
    assert app.profile_var.get() == "alpha"
    assert app.root.title() == "GAMEKB-K17 - alpha"
    app.load_profile("beta")
    assert app.path == directory / "beta.json"
    assert app.data["keys"]["Q"] == {"key": "Enter"}
    assert app.root.title() == "GAMEKB-K17 - beta"


def test_profile_switch_with_unsaved_edits_asks(
    profiles_app: tuple[App, Path, FakePad], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, directory, _ = profiles_app
    app.dirty = True
    monkeypatch.setattr("gamekb.gui.messagebox.askyesno", lambda *a, **k: False)
    app.load_profile("beta")
    assert app.path == directory / "alpha.json"
    assert app.profile_var.get() == "alpha"
    monkeypatch.setattr("gamekb.gui.messagebox.askyesno", lambda *a, **k: True)
    app.load_profile("beta")
    assert app.path == directory / "beta.json"


def test_save_as_profile(profiles_app: tuple[App, Path, FakePad]) -> None:
    app, directory, _ = profiles_app
    app.select_key("W")
    app.kind_var.set("key")
    app.value_var.set("Tab")
    assert app.apply_key()
    assert app.save_as_profile("gamma")
    assert load_config(directory / "gamma.json").keys["W"].action.value == "Tab"
    assert app.path == directory / "gamma.json"
    assert not app.dirty
    assert list(app.profile_box.cget("values")) == ["alpha", "beta", "gamma"]
    assert app.profile_var.get() == "gamma"
    assert app.root.title() == "GAMEKB-K17 - gamma"


def test_save_as_profile_rejects_bad_name(profiles_app: tuple[App, Path, FakePad]) -> None:
    app, directory, _ = profiles_app
    assert not app.save_as_profile("../evil")
    assert "invalid profile name" in app.log_text()
    assert sorted(p.name for p in directory.iterdir()) == ["alpha.json", "beta.json"]


def test_save_as_existing_profile_asks_before_overwrite(
    profiles_app: tuple[App, Path, FakePad], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, directory, _ = profiles_app
    before = (directory / "beta.json").read_text(encoding="utf-8")
    monkeypatch.setattr("gamekb.gui.messagebox.askyesno", lambda *a, **k: False)
    assert not app.save_as_profile("beta")
    assert (directory / "beta.json").read_text(encoding="utf-8") == before
    assert app.path == directory / "alpha.json"


def test_read_from_pad_names_matching_profile(
    profiles_app: tuple[App, Path, FakePad], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _, _ = profiles_app
    monkeypatch.setattr("gamekb.gui.messagebox.askyesno", lambda *a, **k: True)
    app.write_to_pad()
    wait_idle(app)
    app.read_from_pad()
    wait_idle(app)
    assert "pad matches profile: alpha" in app.log_text()


# --- edits apply without a separate Apply button (reported: picked color never reached the pad) ---


def test_pick_color_applies_to_key(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "_ask_color", lambda _initial: (0, 0, 255))
    app.select_key("R")
    app._pick_key_color()
    assert app.data["keys"]["R"] == {"color": [0, 0, 255]}
    assert app.dirty


def test_default_color_applies_to_key(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(app, "_ask_color", lambda _initial: (0, 0, 255))
    app.select_key("Q")
    app._pick_key_color()
    app._clear_key_color()
    assert app.data["keys"]["Q"] == {"shortcut": "Alt+T"}


def test_save_includes_unapplied_key_edit(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("key")
    app.value_var.set("Escape")
    assert app.save()
    assert load_config(app.path).keys["W"].action.value == "Escape"


def test_save_includes_unapplied_lighting_edit(app: App) -> None:
    app.bright_var.set("2")
    assert app.save()
    assert load_config(app.path).lighting.brightness == 2


def test_switching_key_keeps_unapplied_edit(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("key")
    app.value_var.set("Escape")
    app.select_key("Q")
    assert app.data["keys"]["W"] == {"key": "Escape"}


def test_write_includes_unapplied_edit(app: App, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("gamekb.gui.messagebox.askyesno", lambda *a, **k: True)
    app.select_key("W")
    app.kind_var.set("key")
    app.value_var.set("Escape")
    app.write_to_pad()
    wait_idle(app)
    assert "W: factory -> key Escape" in app.log_text()
    assert "pad verified" in app.log_text()


def test_invalid_unapplied_edit_blocks_write(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("shortcut")
    app.value_var.set("Hyper+C")
    app.write_to_pad()
    assert not app.busy
    assert "Hyper" in app.log_text()


def test_browsing_keys_does_not_mark_dirty(app: App) -> None:
    app.select_key("Q")
    app.select_key("W")
    app.select_key("Space")
    assert not app.dirty


def test_profile_switch_does_not_carry_panel_into_new_profile(
    profiles_app: tuple[App, Path, FakePad], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _, _ = profiles_app
    app.select_key("Q")  # alpha: Q = Escape; beta: Q = Enter
    app.load_profile("beta")
    assert app.data["keys"]["Q"] == {"key": "Enter"}
    assert app.value_var.get() == "Enter"
    assert not app.dirty


def test_profile_switch_asks_about_unapplied_edit(
    profiles_app: tuple[App, Path, FakePad], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, directory, _ = profiles_app
    app.select_key("W")
    app.value_var.set("Tab")
    app.kind_var.set("key")
    monkeypatch.setattr("gamekb.gui.messagebox.askyesno", lambda *a, **k: False)
    app.load_profile("beta")
    assert app.path == directory / "alpha.json"


# --- the key panel offers choices instead of free text ---


def test_key_choices_are_grouped_and_pick_only(app: App) -> None:
    from gamekb.gui import KEY_CHOICES

    assert KEY_CHOICES[:3] == ("A", "B", "C")
    assert KEY_CHOICES.index("Z") < KEY_CHOICES.index("1") < KEY_CHOICES.index("F1")
    assert KEY_CHOICES.index("F2") == KEY_CHOICES.index("F1") + 1
    assert KEY_CHOICES.index("F24") < KEY_CHOICES.index("Escape") < KEY_CHOICES.index("LCtrl")
    app.select_key("W")
    app.kind_var.set("key")
    app._refresh_value_choices()
    assert str(app.value_box.cget("state")) == "readonly"
    assert tuple(app.value_box.cget("values")) == KEY_CHOICES


def test_shortcut_checkboxes_compose_value(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("shortcut")
    app._refresh_value_choices()
    app.mod_vars["Ctrl"].set(True)
    app.mod_vars["Shift"].set(True)
    app.shortcut_key_var.set("Z")
    assert app.value_var.get() == "Ctrl+Shift+Z"
    app.mod_vars["Shift"].set(False)
    app.mod_vars["RAlt"].set(True)
    assert app.value_var.get() == "Ctrl+RAlt+Z"
    assert app.apply_key()
    assert app.data["keys"]["W"] == {"shortcut": "Ctrl+RAlt+Z"}


def test_selecting_shortcut_key_fills_checkboxes(app: App) -> None:
    app.select_key("Q")  # Alt+T
    assert {m for m, var in app.mod_vars.items() if var.get()} == {"Alt"}
    assert app.shortcut_key_var.get() == "T"
    app.select_key("W")
    assert not any(var.get() for var in app.mod_vars.values())


def test_media_choices_are_pick_only(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("media")
    app._refresh_value_choices()
    assert str(app.value_box.cget("state")) == "readonly"
    assert "PlayPause" in app.value_box.cget("values")


def test_macro_insert_adds_token_at_cursor(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("macro")
    app._refresh_value_choices()
    app.value_var.set("hi")
    app.macro_entry.icursor("end")
    app.insert_macro_token("{Enter}")
    assert app.value_var.get() == "hi{Enter}"
    app.macro_entry.icursor(0)
    app.insert_macro_token("{Ctrl+A}")
    assert app.value_var.get() == "{Ctrl+A}hi{Enter}"


def test_macro_usage_counts_shared_slots(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("macro")
    app.value_var.set("ab")
    assert app.apply_key()
    app.select_key("E")
    app.kind_var.set("macro")
    app.value_var.set("ab")
    assert app.apply_key()
    assert app.macro_usage_var.get().startswith("1/16 macros, 16/4032 bytes")


def test_factory_action_is_described(app: App) -> None:
    app.select_key("V")
    assert app.factory_var.get() == "factory: types g"
    app.select_key("Bright+")
    assert app.factory_var.get() == "factory: media BrightnessUp"
    app.select_key("LShift")
    assert app.factory_var.get() == "factory: sends LShift"
    app.select_key("FN")
    assert "Fn layer" in app.factory_var.get()


def test_dial_keys_cannot_get_an_action(app: App) -> None:
    app.select_key("Mute")
    assert str(app.kind_box.cget("state")) == "disabled"
    assert "fixed in firmware" in app.factory_var.get()
    app.select_key("W")
    assert str(app.kind_box.cget("state")) == "readonly"


def test_invalid_edit_shows_error_in_panel(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("macro")
    app.value_var.set("{Bogus}")
    assert "Bogus" in app.error_var.get()
    app.value_var.set("ok")
    assert app.error_var.get() == ""


def test_read_from_pad_keeps_labels(
    profiles_app: tuple[App, Path, FakePad], monkeypatch: pytest.MonkeyPatch
) -> None:
    app, _, _ = profiles_app  # alpha: Q = Escape
    app.select_key("Q")
    app.label_var.set("esc")
    app.select_key("W")
    app.kind_var.set("key")
    app.value_var.set("Tab")
    app.label_var.set("tab")
    monkeypatch.setattr("gamekb.gui.messagebox.askyesno", lambda *a, **k: True)
    app.write_to_pad()
    wait_idle(app)
    app.select_key("W")
    app.kind_var.set("key")
    app.value_var.set("Enter")  # differs from the pad now: its label is stale
    app.apply_key()
    app.read_from_pad()
    wait_idle(app)
    assert app.data["keys"]["Q"]["label"] == "esc"
    assert app.data["keys"]["W"]["key"] == "Tab"
    assert "label" not in app.data["keys"]["W"]
    assert "kept 1 label(s)" in app.log_text()


def test_macro_insert_appends_when_box_never_clicked(app: App) -> None:
    app.select_key("W")
    app.kind_var.set("macro")
    app.value_var.set("hi")
    assert app.apply_key()
    app.select_key("Q")
    app.select_key("W")
    app.insert_macro_token("{Enter}")
    assert app.value_var.get() == "hi{Enter}"
