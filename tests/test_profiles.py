"""Tests for gamekb.profiles: named config files in the profiles folder."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from gamekb.config import ConfigError, load_config
from gamekb.encode import encode
from gamekb.profiles import list_profiles, matching_profiles, profile_path, resolve

ESC = {"keys": {"A": {"key": "Escape"}}}
YES = {"keys": {"Space": {"macro": "yes{Enter}"}}}


def write(directory: Path, name: str, data: object) -> Path:
    directory.mkdir(exist_ok=True)
    path = directory / f"{name}.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_list_profiles_sorted_json_only(tmp_path: Path) -> None:
    write(tmp_path, "zeta", ESC)
    write(tmp_path, "alpha", YES)
    (tmp_path / "notes.txt").write_text("x", encoding="utf-8")
    assert list_profiles(tmp_path) == ["alpha", "zeta"]


def test_list_profiles_missing_folder(tmp_path: Path) -> None:
    assert list_profiles(tmp_path / "nope") == []


def test_resolve_name_to_profile(tmp_path: Path) -> None:
    path = write(tmp_path / "profiles", "claude", YES)
    assert resolve("claude", tmp_path / "profiles") == path


def test_resolve_existing_file_wins_over_profile(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    write(tmp_path / "profiles", "claude", YES)
    (tmp_path / "claude").write_text("{}", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert resolve("claude", tmp_path / "profiles") == Path("claude")


def test_resolve_unknown_name_lists_available(tmp_path: Path) -> None:
    write(tmp_path / "profiles", "claude", YES)
    write(tmp_path / "profiles", "default", ESC)
    with pytest.raises(ConfigError) as info:
        resolve("gaming", tmp_path / "profiles")
    assert info.value.errors == [
        f"no config file or profile named 'gaming'; profiles: claude, default (in {tmp_path / 'profiles'})"
    ]


def test_resolve_unknown_name_without_profiles(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as info:
        resolve("gaming", tmp_path / "profiles")
    assert "profiles: none" in info.value.errors[0]


def test_resolve_missing_path_is_left_to_load_config(tmp_path: Path) -> None:
    missing = str(tmp_path / "missing.json")
    assert resolve(missing, tmp_path / "profiles") == Path(missing)


def test_matching_profiles(tmp_path: Path) -> None:
    write(tmp_path, "claude", YES)
    write(tmp_path, "copy", YES)
    write(tmp_path, "default", ESC)
    write(tmp_path, "broken", {"keys": {"Q": {"key": "Nope"}}})
    image = encode(load_config(tmp_path / "claude.json"))
    assert matching_profiles(image, tmp_path) == (["claude", "copy"], ["broken"])


@pytest.mark.parametrize("name", ["claude", "claude-2", "my_layout", "A1"])
def test_profile_path_accepts(tmp_path: Path, name: str) -> None:
    assert profile_path(name, tmp_path) == tmp_path / f"{name}.json"


@pytest.mark.parametrize("name", ["", " ", "../x", "a b", "x.json", "-x", "a/b"])
def test_profile_path_rejects(tmp_path: Path, name: str) -> None:
    with pytest.raises(ValueError, match="profile name"):
        profile_path(name, tmp_path)
