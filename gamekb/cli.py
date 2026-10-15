"""gamekb command line: provision the GAMEKB-K17 pad from config.json, verify, exit."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from . import __version__
from .config import ConfigError, config_to_dict, load_config
from .device import DetectResult, DetectStatus, DeviceError, Pad, PadNotFound, detect
from .encode import DeviceImage, encode, image_to_config
from .profiles import PROJECT_ROOT, default_dir, list_profiles, matching_profiles, resolve
from .sync import Change, plan, read_factory_keys, read_image, run_write, verify

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_NO_PAD = 2
EXIT_DEVICE = 3


@dataclass(frozen=True)
class Context:
    open_pad: Callable[[], Pad]
    detect_fn: Callable[[], DetectResult]
    ask: Callable[[str], str]


def find_config(arg: str | None) -> Path:
    if arg:
        return resolve(arg, default_dir())
    for candidate in (Path.cwd() / "config.json", PROJECT_ROOT / "config.json"):
        if candidate.is_file():
            return candidate
    return Path.cwd() / "config.json"  # load_config reports it as missing


def _load_target(arg: str | None) -> DeviceImage:
    path = find_config(arg)
    target = encode(load_config(path))
    print(f"config: {path}")
    return target


def _print_plan(changes: list[Change]) -> None:
    if not changes:
        print("pad already matches config")
        return
    print(f"{len(changes)} change(s):")
    for change in changes:
        print(f"  {change.label}")


def _write_and_report(pad: Pad, target: DeviceImage, yes: bool, ctx: Context) -> int:
    def confirm(changes: list[Change]) -> bool:
        _print_plan(changes)
        if not changes or yes:
            return True
        answer = ctx.ask(f"Apply {len(changes)} change(s) to the pad? [y/N] ")
        return answer.strip().lower() in ("y", "yes")

    changes, report = run_write(
        pad, target, confirm=confirm, progress=lambda c: print(f"  wrote {c.label}")
    )
    if report is None:
        print("cancelled; nothing written")
        return EXIT_OK
    for line in report.lines():
        print(line)
    if not report.ok:
        print("verify FAILED: the pad does not match the config", file=sys.stderr)
        return EXIT_DEVICE
    print(f"done: {len(changes)} change(s) written, pad verified")
    return EXIT_OK


def cmd_detect(args: argparse.Namespace, ctx: Context) -> int:
    result = ctx.detect_fn()
    print(f"{result.status.value}: {result.hint}")
    return EXIT_OK if result.status is DetectStatus.READY else EXIT_NO_PAD


def cmd_read(args: argparse.Namespace, ctx: Context) -> int:
    with ctx.open_pad() as pad:
        image = read_image(pad)
    cfg, warnings = image_to_config(image)
    for warning in warnings:
        print(f"warning: {warning}", file=sys.stderr)
    text = json.dumps(config_to_dict(cfg), indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text, encoding="utf-8")
        print(f"wrote {args.output}")
    else:
        sys.stdout.write(text)
    return EXIT_OK


def cmd_diff(args: argparse.Namespace, ctx: Context) -> int:
    target = _load_target(args.config)
    with ctx.open_pad() as pad:
        changes = plan(target, read_image(pad))
    _print_plan(changes)
    return EXIT_OK


def cmd_write(args: argparse.Namespace, ctx: Context) -> int:
    target = _load_target(args.config)
    with ctx.open_pad() as pad:
        return _write_and_report(pad, target, args.yes, ctx)


def cmd_verify(args: argparse.Namespace, ctx: Context) -> int:
    target = _load_target(args.config)
    with ctx.open_pad() as pad:
        report = verify(pad, target)
    for line in report.lines():
        print(line)
    return EXIT_OK if report.ok else EXIT_DEVICE


def cmd_reset(args: argparse.Namespace, ctx: Context) -> int:
    with ctx.open_pad() as pad:
        current = read_image(pad)
        target = DeviceImage(read_factory_keys(pad), current.colors, current.lighting, current.macros)
        return _write_and_report(pad, target, args.yes, ctx)


def cmd_profiles(args: argparse.Namespace, ctx: Context) -> int:
    directory = default_dir()
    names = list_profiles(directory)
    if not names:
        print(f"no profiles in {directory}")
        return EXIT_OK
    image: DeviceImage | None
    try:
        with ctx.open_pad() as pad:
            image = read_image(pad)
    except PadNotFound:
        image = None
    matches, invalid = matching_profiles(image, directory)
    width = max(len(name) for name in names)
    print(f"profiles in {directory}:")
    for name in names:
        note = ""
        if name in matches:
            note = "(on pad)"
        elif name in invalid:
            note = f"(invalid: run 'gamekb diff {name}' for details)"
        mark = "*" if name in matches else " "
        print(f"{mark} {name:<{width}}  {note}".rstrip())
    if image is None:
        print("pad not connected; cannot tell which profile is on it")
    elif not matches:
        print("the pad matches none of these")
    return EXIT_OK


def cmd_gui(args: argparse.Namespace, ctx: Context) -> int:
    from .gui import run_gui

    run_gui(find_config(args.config), ctx.open_pad)
    return EXIT_OK


CONFIG_HELP = "config file or profile name from profiles/ (default: ./config.json)"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gamekb", description="Write config.json to the GAMEKB-K17 pad, verify, exit."
    )
    parser.add_argument("--version", action="version", version=f"gamekb {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("detect", help="check whether the pad is reachable").set_defaults(func=cmd_detect)

    read = sub.add_parser("read", help="dump the pad's current layout as a config")
    read.add_argument("-o", "--output", help="write to this file instead of stdout")
    read.set_defaults(func=cmd_read)

    for name, func, text in (
        ("diff", cmd_diff, "show what write would change; sends nothing"),
        ("verify", cmd_verify, "compare the pad with the config"),
        ("gui", cmd_gui, "open the layout editor"),
    ):
        p = sub.add_parser(name, help=text)
        p.add_argument("config", nargs="?", help=CONFIG_HELP)
        p.set_defaults(func=func)

    write = sub.add_parser("write", help="write the config to the pad and verify")
    write.add_argument("config", nargs="?", help=CONFIG_HELP)
    write.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    write.set_defaults(func=cmd_write)

    sub.add_parser(
        "profiles", help="list profiles and show which one is on the pad"
    ).set_defaults(func=cmd_profiles)

    reset = sub.add_parser("reset", help="restore the factory keymap (colors untouched)")
    reset.add_argument("-y", "--yes", action="store_true", help="do not ask for confirmation")
    reset.set_defaults(func=cmd_reset)
    return parser


def main(
    argv: list[str] | None = None,
    open_pad: Callable[[], Pad] = Pad.open,
    detect_fn: Callable[[], DetectResult] = detect,
    ask: Callable[[str], str] = input,
) -> int:
    args = _parser().parse_args(argv)
    ctx = Context(open_pad, detect_fn, ask)
    try:
        return int(args.func(args, ctx))
    except ConfigError as exc:
        print(exc, file=sys.stderr)
        return EXIT_CONFIG
    except PadNotFound as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_NO_PAD
    except DeviceError as exc:
        print(
            f"error: {exc}\nIf the vendor web app is open, close it. "
            "Nothing is lost: fix the cause and run the command again.",
            file=sys.stderr,
        )
        return EXIT_DEVICE


def run() -> None:
    sys.exit(main())
