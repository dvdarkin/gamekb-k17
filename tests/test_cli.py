"""End-to-end CLI tests against FakePad."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gamekb.cli import EXIT_CONFIG, EXIT_DEVICE, EXIT_NO_PAD, EXIT_OK, main
from gamekb.device import DetectResult, DetectStatus, Pad, PadNotFound
from tests.fakepad import LIVE_KEYMAP, MUTATING, FakePad

CONFIG = {"keys": {"Q": {"shortcut": "Alt+T", "color": [160, 60, 255]}}}


@pytest.fixture
def cfg_path(tmp_path: Path) -> Path:
    path = tmp_path / "config.json"
    path.write_text(json.dumps(CONFIG), encoding="utf-8")
    return path


def run(argv: list[str], fake: FakePad, answer: str = "y") -> int:
    return main(
        argv,
        open_pad=lambda: Pad(fake, timeout_ms=30),
        detect_fn=lambda: DetectResult(DetectStatus.READY, b"x"),
        ask=lambda _prompt: answer,
    )


def mutations(fake: FakePad) -> list[bytes]:
    return [w for w in fake.writes if w[1] in MUTATING]


def test_write_confirms_applies_and_verifies(cfg_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["write", str(cfg_path)], FakePad()) == EXIT_OK
    out = capsys.readouterr().out
    assert "Q: factory -> shortcut Alt+T" in out
    assert "pad verified" in out


def test_write_declined_sends_nothing(cfg_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakePad()
    assert run(["write", str(cfg_path)], fake, answer="n") == EXIT_OK
    assert "nothing written" in capsys.readouterr().out
    assert mutations(fake) == []


def test_second_write_is_up_to_date(cfg_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakePad()
    run(["write", "-y", str(cfg_path)], fake)
    capsys.readouterr()
    assert run(["write", str(cfg_path)], fake, answer="n") == EXIT_OK
    assert "already matches" in capsys.readouterr().out


def test_invalid_config_never_opens_pad(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    path = tmp_path / "config.json"
    path.write_text('{"keys": {"Q": {"key": "Nope"}}}', encoding="utf-8")

    def boom() -> Pad:
        raise AssertionError("pad must not be opened")

    assert main(["write", str(path)], open_pad=boom) == EXIT_CONFIG
    assert "unknown key 'Nope'" in capsys.readouterr().err


def test_pad_not_found_exit_code(cfg_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def missing() -> Pad:
        raise PadNotFound(DetectResult(DetectStatus.NOT_SEEN))

    assert main(["write", str(cfg_path)], open_pad=missing) == EXIT_NO_PAD
    assert "does not see" in capsys.readouterr().err


def test_unplug_mid_write_exit_3_then_rerun_succeeds(
    cfg_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    fake = FakePad()
    fake.unplug_after_writes = 1
    assert run(["write", "-y", str(cfg_path)], fake) == EXIT_DEVICE
    assert "run the command again" in capsys.readouterr().err
    fake.unplug_after_writes = None
    assert run(["write", "-y", str(cfg_path)], fake) == EXIT_OK


def test_verify_mismatch_exit_3(cfg_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["verify", str(cfg_path)], FakePad()) == EXIT_DEVICE
    assert "FAIL Q" in capsys.readouterr().out


def test_diff_sends_no_writes(cfg_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakePad()
    assert run(["diff", str(cfg_path)], fake) == EXIT_OK
    assert "Q: factory -> shortcut Alt+T" in capsys.readouterr().out
    assert mutations(fake) == []


def test_read_output_verifies_against_pad(tmp_path: Path, cfg_path: Path) -> None:
    fake = FakePad()
    run(["write", "-y", str(cfg_path)], fake)
    out = tmp_path / "read.json"
    assert run(["read", "-o", str(out)], fake) == EXIT_OK
    assert run(["verify", str(out)], fake) == EXIT_OK


def test_read_warns_on_garbage(capsys: pytest.CaptureFixture[str]) -> None:
    garbage = bytes.fromhex("13000000 20012a00 28202020") + bytes(96 - 12)
    assert run(["read"], FakePad(live_keymap=garbage)) == EXIT_OK
    err = capsys.readouterr().err
    assert "warning: W:" in err and "gamekb reset" in err


def test_reset_restores_factory_keymap(cfg_path: Path) -> None:
    fake = FakePad()
    run(["write", "-y", str(cfg_path)], fake)
    assert run(["reset", "-y"], fake) == EXIT_OK
    assert fake.live_keymap()[4:8] == bytes.fromhex("20001400")


def test_detect(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["detect"], detect_fn=lambda: DetectResult(DetectStatus.NOT_SEEN)) == EXIT_NO_PAD
    assert "not_seen" in capsys.readouterr().out
    assert main(["detect"], detect_fn=lambda: DetectResult(DetectStatus.READY, b"x")) == EXIT_OK


def test_device_error_hints_at_vendor_web_app(cfg_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    # On Windows the vendor web app can share the HID handle, so its interference shows up as I/O errors.
    fake = FakePad()
    fake.silent.add(0x10)
    assert run(["write", "-y", str(cfg_path)], fake) == EXIT_DEVICE
    assert "vendor web app" in capsys.readouterr().err


from gamekb.macro import build_macro_area  # noqa: E402
from tests.captures import M0_YES  # noqa: E402


def test_read_normalizes_vendor_macro(capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakePad()
    fake.macros[:] = build_macro_area([M0_YES])
    fake.region[LIVE_KEYMAP + 88 : LIVE_KEYMAP + 92] = bytes.fromhex("60000100")  # Space -> M0
    assert run(["read"], fake) == EXIT_OK
    captured = capsys.readouterr()
    assert "Space: macro M0 timings normalized to 52 ms" in captured.err
    assert '"macro": "yes{Enter}"' in captured.out


def test_reset_leaves_macro_area_alone() -> None:
    fake = FakePad()
    fake.macros[:] = build_macro_area([M0_YES])
    assert run(["reset", "-y"], fake) == EXIT_OK
    assert bytes(fake.macros) == build_macro_area([M0_YES])


@pytest.fixture
def profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    directory = tmp_path / "profiles"
    directory.mkdir()
    (directory / "claude.json").write_text(json.dumps(CONFIG), encoding="utf-8")
    (directory / "default.json").write_text(json.dumps({"keys": {}}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    return directory


def test_write_by_profile_name(profiles: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert run(["write", "-y", "claude"], FakePad()) == EXIT_OK
    out = capsys.readouterr().out
    assert f"config: {profiles / 'claude.json'}" in out
    assert "pad verified" in out


def test_unknown_profile_never_opens_pad(profiles: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def boom() -> Pad:
        raise AssertionError("pad must not be opened")

    assert main(["write", "gaming"], open_pad=boom) == EXIT_CONFIG
    assert "profiles: claude, default" in capsys.readouterr().err


def test_profiles_marks_the_one_on_the_pad(profiles: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakePad()
    run(["write", "-y", "claude"], fake)
    capsys.readouterr()
    assert run(["profiles"], fake) == EXIT_OK
    out = capsys.readouterr().out
    assert "* claude   (on pad)" in out
    assert "  default" in out


def test_profiles_none_matching(profiles: Path, capsys: pytest.CaptureFixture[str]) -> None:
    fake = FakePad()
    run(["write", "-y", str(profiles / "claude.json")], fake)
    (profiles / "claude.json").write_text(json.dumps({"keys": {"Q": {"key": "Escape"}}}), encoding="utf-8")
    capsys.readouterr()
    assert run(["profiles"], fake) == EXIT_OK
    assert "the pad matches none of these" in capsys.readouterr().out


def test_profiles_without_pad_still_lists(profiles: Path, capsys: pytest.CaptureFixture[str]) -> None:
    def missing() -> Pad:
        raise PadNotFound(DetectResult(DetectStatus.NOT_SEEN))

    assert main(["profiles"], open_pad=missing) == EXIT_OK
    out = capsys.readouterr().out
    assert "  claude" in out and "  default" in out
    assert "pad not connected" in out


def test_profiles_reports_invalid_profile(profiles: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (profiles / "broken.json").write_text('{"keys": {"Q": {"key": "Nope"}}}', encoding="utf-8")
    assert run(["profiles"], FakePad()) == EXIT_OK
    assert "  broken   (invalid: run 'gamekb diff broken' for details)" in capsys.readouterr().out


def test_profiles_empty_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "profiles").mkdir()
    monkeypatch.chdir(tmp_path)

    def boom() -> Pad:
        raise AssertionError("pad must not be opened")

    assert main(["profiles"], open_pad=boom) == EXIT_OK
    assert "no profiles in" in capsys.readouterr().out
