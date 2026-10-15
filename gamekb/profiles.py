"""Profiles: named config files in a `profiles/` folder (e.g. profiles/claude.json).

A profile is an ordinary config. The pad holds one layout at a time; loading a
profile means writing it with the usual write -> verify flow.
"""

from __future__ import annotations

import re
from pathlib import Path

from .config import ConfigError, load_config
from .encode import DeviceImage, encode
from .sync import plan

PROJECT_ROOT = Path(__file__).resolve().parent.parent
_NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*")


def default_dir() -> Path:
    local = Path.cwd() / "profiles"
    return local if local.is_dir() else PROJECT_ROOT / "profiles"


def list_profiles(directory: Path) -> list[str]:
    if not directory.is_dir():
        return []
    return sorted(p.stem for p in directory.glob("*.json") if p.is_file())


def profile_path(name: str, directory: Path) -> Path:
    if not _NAME.fullmatch(name):
        raise ValueError(
            f"invalid profile name {name!r}: use letters, digits, '-' and '_' (no spaces or dots)"
        )
    return directory / f"{name}.json"


def resolve(arg: str, directory: Path) -> Path:
    """An existing file wins; otherwise a bare name means profiles/<name>.json."""
    path = Path(arg)
    if path.is_file() or not _NAME.fullmatch(arg):
        return path  # load_config reports a missing file
    candidate = directory / f"{arg}.json"
    if candidate.is_file():
        return candidate
    names = ", ".join(list_profiles(directory)) or "none"
    raise ConfigError([f"no config file or profile named '{arg}'; profiles: {names} (in {directory})"])


def matching_profiles(image: DeviceImage | None, directory: Path) -> tuple[list[str], list[str]]:
    """(profiles the pad image matches exactly, invalid profiles). No image: validity only."""
    matches: list[str] = []
    invalid: list[str] = []
    for name in list_profiles(directory):
        try:
            target = encode(load_config(directory / f"{name}.json"))
        except ConfigError:
            invalid.append(name)
            continue
        if image is not None and not plan(target, image):
            matches.append(name)
    return matches, invalid
