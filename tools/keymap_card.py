"""Render a profile as a keymap card (SVG, optional PNG) for the README and posts.

    python tools/keymap_card.py claude                      # docs/img/keymap-claude.svg
    python tools/keymap_card.py claude --png                # + .png via headless Chrome/Edge
    python tools/keymap_card.py claude --bare               # pad only, no title or notes

The drawing follows the pad's physical layout (not the editor's grid): Tab and LShift sit
left of the letter block, the dial on the right edge, Space and FN are thumb keys right of
the palm rest, and the side wheel (Bright+/Bright-) is drawn on the top edge at the top-left,
between Tab and Q.
"""

from __future__ import annotations

import argparse
import html
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from gamekb.config import ACTION_FIELDS, config_to_dict, load_config  # noqa: E402
from gamekb.layout import RGB  # noqa: E402
from gamekb.profiles import default_dir, resolve  # noqa: E402

U = 112          # one key pitch in px
CAP = 98         # keycap size in px
SANS = "'Segoe UI', Inter, 'Helvetica Neue', Arial, sans-serif"
MONO = "'Cascadia Mono', 'JetBrains Mono', Consolas, Menlo, monospace"
BG = "#0b0d12"
INK = "#e8eaf0"
MUTED = "#8a90a0"


@dataclass(frozen=True)
class Cap:
    name: str
    x: float  # pitch units, top-left of the cap
    y: float
    w: float = 1.0


# Physical positions (pitch units, origin = top-left of the Q key minus one column).
CAPS: tuple[Cap, ...] = (
    Cap("Q", 1, 0), Cap("W", 2, 0), Cap("E", 3, 0), Cap("R", 4, 0),
    Cap("Tab", -0.3, 1, 1.2), Cap("A", 1, 1), Cap("S", 2, 1), Cap("D", 3, 1), Cap("F", 4, 1),
    Cap("LShift", -0.3, 2, 1.2), Cap("Z", 1, 2), Cap("X", 2, 2), Cap("C", 3, 2), Cap("V", 4, 2),
    Cap("Space", 4.25, 3.35, 1.1), Cap("FN", 5.5, 3.35, 1.1),
)
DIAL = (5.95, 1.45, 0.62)              # centre x, centre y, radius (units)
WHEEL = (-0.2, -0.62, 0.85, 0.3)       # x, y, w, h (units): roller on the top-left edge
PALM = (-0.35, 3.45, 4.35, 0.62)       # palm-rest groove


def _hex(rgb: RGB) -> str:
    return "#{:02x}{:02x}{:02x}".format(*rgb)


def _mix(rgb: RGB, other: RGB, t: float) -> RGB:
    r, g, b = (round(a + (c - a) * t) for a, c in zip(rgb, other))
    return (r, g, b)


def action_text(spec: dict[str, Any]) -> str:
    """Short wire-level description shown at the bottom of a cap."""
    kind = next((k for k in ACTION_FIELDS if k in spec), None)
    if kind is None:
        return "factory"
    value = str(spec[kind])
    if kind == "macro":
        value = value.replace("{Enter}", " \u21b5").replace("\n", " \u21b5").strip()
        return value if value.endswith("\u21b5") else f"{value} \u2026"  # waits for Enter
    return value


def _text(x: float, y: float, s: str, size: float, fill: str, *, weight: int = 400,
          family: str = SANS, anchor: str = "middle", opacity: float = 1.0) -> str:
    return (
        f'<text x="{x:.1f}" y="{y:.1f}" font-family="{family}" font-size="{size}" '
        f'font-weight="{weight}" fill="{fill}" fill-opacity="{opacity}" text-anchor="{anchor}">'
        f"{html.escape(s)}</text>"
    )


def _fit(s: str, size: float, width: float, mono: bool) -> float:
    per_char = 0.62 if mono else 0.56
    need = len(s) * size * per_char
    return size if need <= width else max(8.0, size * width / need)


def render_cap(cap: Cap, spec: dict[str, Any], default: RGB, ox: float, oy: float, idx: int) -> str:
    rgb: RGB = tuple(spec["color"]) if "color" in spec else default
    x, y = ox + cap.x * U, oy + cap.y * U
    w, h = CAP + (cap.w - 1) * U, CAP
    col = _hex(rgb)
    light = _hex(_mix(rgb, (255, 255, 255), 0.55))
    label = str(spec.get("label") or "")
    detail = action_text(spec)
    if not label and detail != "factory":  # unlabeled: the action is the headline
        label, detail = detail, ""
    out = [
        f'<rect x="{x - 4:.1f}" y="{y - 4:.1f}" width="{w + 8:.1f}" height="{h + 8:.1f}" rx="20" '
        f'fill="{col}" opacity="0.55" filter="url(#glow)"/>',
        f'<linearGradient id="cap{idx}" x1="0" y1="0" x2="0" y2="1">'
        f'<stop offset="0" stop-color="{col}" stop-opacity="0.42"/>'
        f'<stop offset="1" stop-color="{col}" stop-opacity="0.16"/></linearGradient>',
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="16" fill="#0e1016"/>',
        f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="16" fill="url(#cap{idx})" '
        f'stroke="{light}" stroke-opacity="0.85" stroke-width="1.6"/>',
        f'<ellipse cx="{x + w / 2:.1f}" cy="{y + 14:.1f}" rx="{w * 0.22:.1f}" ry="7" fill="{light}" '
        f'opacity="0.55" filter="url(#soft)"/>',
        _text(x + 11, y + 22, cap.name, 12.5, light, weight=600, anchor="start", opacity=0.9),
    ]
    if label:
        size = _fit(label, 21, w - 14, mono=False)
        out.append(_text(x + w / 2, y + 60, label, size, INK, weight=650))
    size = _fit(detail, 11.5, w - 12, mono=True)
    out.append(_text(x + w / 2, y + h - 12, detail, size, INK, family=MONO, opacity=0.72))
    return "\n".join(out)


def render_pad(data: dict[str, Any], ox: float, oy: float) -> str:
    keys: dict[str, Any] = data.get("keys", {})
    default: RGB = tuple(data.get("default_color", [0, 0, 0]))
    parts: list[str] = []
    # body
    bx, by, bw, bh = ox - 0.75 * U, oy - 0.5 * U, 7.55 * U, 5.15 * U
    parts.append(
        f'<rect x="{bx:.1f}" y="{by:.1f}" width="{bw:.1f}" height="{bh:.1f}" rx="{0.62 * U:.1f}" '
        f'fill="url(#body)" stroke="#2c3039" stroke-width="2"/>'
    )
    px, py, pw, ph = (ox + PALM[0] * U, oy + PALM[1] * U, PALM[2] * U, PALM[3] * U)
    parts.append(
        f'<rect x="{px:.1f}" y="{py:.1f}" width="{pw:.1f}" height="{ph:.1f}" rx="{ph / 2:.1f}" '
        f'fill="#101218" stroke="#262a33" stroke-width="1.5"/>'
    )
    parts.append(_text(px + pw / 2, py + ph / 2 + 5, "palm rest", 13, MUTED, opacity=0.6))
    # side wheel
    wx, wy, ww, wh = (ox + WHEEL[0] * U, oy + WHEEL[1] * U, WHEEL[2] * U, WHEEL[3] * U)
    parts.append(
        f'<rect x="{wx:.1f}" y="{wy:.1f}" width="{ww:.1f}" height="{wh:.1f}" rx="{wh / 2:.1f}" '
        f'fill="url(#knurl)" stroke="#3a3f4a" stroke-width="1.5"/>'
    )
    wheel = [str(keys.get(k, {}).get("label") or action_text(keys.get(k, {}))) for k in ("Bright+", "Bright-")]
    wheel_text = wheel[0] if wheel[0] == wheel[1] else f"{wheel[0]} / {wheel[1]}"
    parts.append(_text(wx + ww / 2, wy + wh + 22, f"wheel: {wheel_text}", 12.5, MUTED, family=MONO))
    # dial
    cx, cy, r =ox + DIAL[0] * U, oy + DIAL[1] * U, DIAL[2] * U
    dial_rgb: RGB = tuple(keys.get("Mute", {}).get("color", default))
    parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r + 6:.1f}" fill="{_hex(dial_rgb)}" '
                 f'opacity="0.5" filter="url(#glow)"/>')
    parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r:.1f}" fill="url(#knurl)" '
                 f'stroke="#3a3f4a" stroke-width="2"/>')
    parts.append(f'<circle cx="{cx:.1f}" cy="{cy:.1f}" r="{r * 0.72:.1f}" fill="url(#dialtop)"/>')
    parts.append(_text(cx, cy - 2, "dial", 14, INK, weight=600, opacity=0.85))
    parts.append(_text(cx, cy + 16, "mute \u00b7 vol", 11.5, MUTED, family=MONO))
    for i, cap in enumerate(CAPS):
        parts.append(render_cap(cap, keys.get(cap.name, {}), default, ox, oy, i))
    return "\n".join(parts)


def _callout(x: float, y: float, title: str, lines: list[tuple[RGB | None, str, str]]) -> str:
    out = [_text(x, y, title, 13, MUTED, weight=600, anchor="start")]
    for i, (rgb, label, detail) in enumerate(lines):
        ly = y + 28 + i * 26
        if rgb is not None:
            out.append(f'<circle cx="{x + 6:.1f}" cy="{ly - 5:.1f}" r="6" fill="{_hex(rgb)}" '
                       f'filter="url(#soft)"/>')
            out.append(f'<circle cx="{x + 6:.1f}" cy="{ly - 5:.1f}" r="4.5" fill="{_hex(rgb)}"/>')
        out.append(_text(x + 22, ly, label, 16, INK, weight=600, anchor="start"))
        out.append(_text(x + 132, ly, detail, 13.5, MUTED, family=MONO, anchor="start"))
    return "\n".join(out)


DEFS = """<defs>
<filter id="glow" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="14"/></filter>
<filter id="soft" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="3"/></filter>
<radialGradient id="bg" cx="0.35" cy="0.45" r="0.85">
  <stop offset="0" stop-color="#151925"/><stop offset="1" stop-color="#07080b"/></radialGradient>
<linearGradient id="body" x1="0" y1="0" x2="0" y2="1">
  <stop offset="0" stop-color="#1b1e25"/><stop offset="1" stop-color="#121419"/></linearGradient>
<radialGradient id="dialtop" cx="0.4" cy="0.35" r="0.8">
  <stop offset="0" stop-color="#2a2e37"/><stop offset="1" stop-color="#14161b"/></radialGradient>
<pattern id="knurl" width="5" height="5" patternUnits="userSpaceOnUse">
  <rect width="5" height="5" fill="#1a1d23"/><rect width="2" height="5" fill="#252931"/></pattern>
</defs>"""


def render(name: str, data: dict[str, Any], bare: bool, title: str | None = None, subtitle: str | None = None) -> str:
    keys: dict[str, Any] = data.get("keys", {})
    default: RGB = tuple(data.get("default_color", [0, 0, 0]))

    def entry(key: str) -> tuple[RGB, str, str]:
        spec = keys.get(key, {})
        rgb: RGB = tuple(spec.get("color", default))
        return rgb, str(spec.get("label") or key), action_text(spec)

    if bare:
        width, height = 920, 660
        ox, oy = 0.95 * U, 0.85 * U
        body = render_pad(data, ox, oy)
        return (f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
                f'viewBox="0 0 {width} {height}">{DEFS}'
                f'<rect width="{width}" height="{height}" rx="18" fill="url(#bg)"/>{body}</svg>')

    width, height = 1600, 900
    ox, oy = 150 + 0.75 * U, 210 + 0.5 * U
    parts = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}">',
        DEFS,
        f'<rect width="{width}" height="{height}" fill="url(#bg)"/>',
        _text(80, 92, "GAMEKB-K17", 15, MUTED, weight=600, anchor="start"),
        _text(80, 140, title or f"{name} profile", 44, INK, weight=700, anchor="start"),
        _text(80, 174, subtitle or f"gamekb write {name}", 17, MUTED, family=MONO, anchor="start"),
        render_pad(data, ox, oy),
    ]
    rx = 1095
    up, down = entry("Bright+"), entry("Bright-")
    wheel_lines: list[tuple[RGB | None, str, str]]
    if up[1:] == down[1:]:  # same action both ways: one line
        wheel_lines = [(up[0], up[1], f"either way \u00b7 {up[2]}")]
    else:
        wheel_lines = [(up[0], up[1], f"Bright+ \u00b7 {up[2]}"), (down[0], down[1], f"Bright- \u00b7 {down[2]}")]
    parts.append(_callout(rx, 250, "SIDE WHEEL", wheel_lines))
    thumbs = [entry("Space"), entry("FN")]
    parts.append(_callout(rx, 380, "THUMB KEYS", [
        (thumbs[0][0], thumbs[0][1], f"Space \u00b7 {thumbs[0][2]}"),
        (thumbs[1][0], thumbs[1][1], f"FN \u00b7 {thumbs[1][2]}"),
    ]))
    notes = [
        "Macros run in the pad's own firmware:",
        "no app or driver running, works on any PC.",
        "\u21b5 presses Enter.  \u2026 types the command and",
        "waits for you to confirm.",
    ]
    for i, line in enumerate(notes):
        parts.append(_text(rx, 540 + i * 26, line, 15, MUTED, anchor="start"))
    parts.append(_text(rx, 800, "github.com/dvdarkin/gamekb-k17", 15, MUTED, family=MONO, anchor="start",
                       opacity=0.8))
    parts.append("</svg>")
    return "\n".join(parts)


def _browser() -> str | None:
    for candidate in (
        shutil.which("chrome"), shutil.which("msedge"),
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    ):
        if candidate and Path(candidate).exists():
            return candidate
    return None


def to_png(svg: Path, png: Path, scale: int) -> None:
    browser = _browser()
    if browser is None:
        raise SystemExit("no Chrome/Edge found for PNG export")
    text = svg.read_text(encoding="utf-8")
    width = int(text.split('width="', 1)[1].split('"', 1)[0])
    height = int(text.split('height="', 1)[1].split('"', 1)[0])
    subprocess.run(
        [browser, "--headless=new", "--disable-gpu", "--hide-scrollbars", f"--force-device-scale-factor={scale}",
         f"--window-size={width},{height}", f"--screenshot={png.resolve()}", "--default-background-color=00000000",
         svg.resolve().as_uri()],
        check=True, capture_output=True, timeout=60,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("profile", help="profile name or config path")
    parser.add_argument("-o", "--out", type=Path, help="output .svg (default docs/img/keymap-NAME[-bare].svg)")
    parser.add_argument("--bare", action="store_true", help="pad only, no title or notes")
    parser.add_argument("--png", action="store_true", help="also write a PNG next to the SVG")
    parser.add_argument("--title", help="card title (default: NAME profile)")
    parser.add_argument("--subtitle", help="line under the title (default: gamekb write NAME)")
    parser.add_argument("--scale", type=int, default=2, help="PNG pixel scale (default 2)")
    args = parser.parse_args()

    path = resolve(args.profile, default_dir())
    data = config_to_dict(load_config(path))
    name = path.stem
    out: Path = args.out or ROOT / "docs" / "img" / f"keymap-{name}{'-bare' if args.bare else ''}.svg"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render(name, data, args.bare, args.title, args.subtitle), encoding="utf-8")
    print(f"wrote {out}")
    if args.png:
        png = out.with_suffix(".png")
        to_png(out, png, args.scale)
        print(f"wrote {png}")


if __name__ == "__main__":
    main()
