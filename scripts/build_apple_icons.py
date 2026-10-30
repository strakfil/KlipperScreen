#!/usr/bin/env python3
"""Generate styles/apple/images/*.svg from Phosphor Icons (MIT) + a few custom glyphs.

Usage: build_apple_icons.py <phosphor-core>/assets <KlipperScreen>/styles/apple/images

Every icon uses `currentColor`; KlippyGtk recolours it at render time, so one set
serves light/dark mode and every accent colour.
"""
import pathlib
import re
import sys

# KlipperScreen icon name -> (phosphor name, weight)
MAP = {
    "archived": ("archive", "regular"),
    "arrow-down": ("arrow-down", "regular"), "arrow-up": ("arrow-up", "regular"),
    "arrow-left": ("arrow-left", "regular"), "arrow-right": ("arrow-right", "regular"),
    "back": ("caret-left", "bold"), "backspace": ("backspace", "regular"),
    "battery-0": ("battery-empty", "regular"), "battery-25": ("battery-low", "regular"),
    "battery-50": ("battery-medium", "regular"), "battery-75": ("battery-high", "regular"),
    "battery-100": ("battery-full", "regular"), "battery-charging": ("battery-charging", "regular"),
    "battery-unknown": ("battery-warning", "regular"),
    "bed-level": ("crosshair", "regular"), "bed-mesh": ("grid-nine", "regular"),
    "camera": ("camera", "regular"), "cancel": ("x", "bold"),
    "ccw": ("arrow-counter-clockwise", "regular"), "cw": ("arrow-clockwise", "regular"),
    "clock": ("clock", "regular"), "complete": ("check-circle", "regular"),
    "console": ("terminal-window", "regular"), "cool-down": ("snowflake", "regular"),
    "custom-script": ("code", "regular"), "decrease": ("minus", "bold"), "increase": ("plus", "bold"),
    "delete": ("trash", "regular"), "edit": ("pencil-simple", "regular"),
    "emergency": ("warning-octagon", "bold"), "fan": ("fan", "regular"), "fan-on": ("fan", "fill"),
    "file": ("file", "regular"), "files": ("files", "regular"), "folder": ("folder", "regular"),
    "fine-tune": ("sliders-horizontal", "regular"), "hashtag": ("hash", "regular"),
    "heat-up": ("fire", "regular"), "heater": ("thermometer", "regular"), "home": ("house", "regular"),
    "hourglass": ("hourglass", "regular"), "info": ("info", "regular"), "klipper": ("cube", "regular"),
    "light": ("lightbulb", "regular"), "load": ("download-simple", "regular"), "lock": ("lock", "regular"),
    "main": ("squares-four", "regular"), "motor-off": ("engine", "regular"),
    "move": ("arrows-out-cardinal", "regular"), "network": ("network", "regular"),
    "notifications": ("bell", "regular"), "notifications_active": ("bell-ringing", "regular"),
    "notifications_important": ("bell-ringing", "fill"),
    "pause": ("pause", "fill"), "resume": ("play", "fill"), "stop": ("stop", "fill"),
    "printer": ("cube", "regular"), "refresh": ("arrows-clockwise", "regular"), "sd": ("sim-card", "regular"),
    "settings": ("gear-six", "regular"), "shuffle": ("shuffle", "regular"), "shutdown": ("power", "regular"),
    "toolchanger": ("swap", "regular"), "warning": ("warning", "regular"),
    "wifi_excellent": ("wifi-high", "regular"), "wifi_good": ("wifi-medium", "regular"),
    "wifi_fair": ("wifi-low", "regular"), "wifi_weak": ("wifi-none", "regular"),
    "z-closer": ("arrows-in-line-vertical", "regular"), "z-farther": ("arrows-out-line-vertical", "regular"),
    "z-tilt": ("scales", "regular"),
}

# Badged composites: base phosphor icon + small text/sign in the corner
BADGED = {
    "home-x": (("house", "regular"), "X"), "home-y": (("house", "regular"), "Y"),
    "home-z": (("house", "regular"), "Z"),
    "flow+": (("drop", "regular"), "+"), "flow-": (("drop", "regular"), "−"),
    "speed+": (("gauge", "regular"), "+"), "speed-": (("gauge", "regular"), "−"),
}

S = 'fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"'
NOZZLE = "M7 3h10v5l-3 4v3h-4v-3L7 8z M12 17.5v2"
SPOOL = "M12 3a9 9 0 1 0 0 18a9 9 0 1 0 0-18z M12 9a3 3 0 1 0 0 6a3 3 0 1 0 0-6z M12 3v6 M12 15v6"


def svg24(body):
    return f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">{body}</svg>\n'


def custom():
    out = {
        "bed": svg24(f'<path {S} d="M3 16h18v3.5H3z M7 12.5c0-2 1.5-2 1.5-4 M12 12.5c0-2 1.5-2 1.5-4 M17 12.5c0-2 1.5-2 1.5-4"/>'),
        "extruder": svg24(f'<path {S} d="{NOZZLE}"/>'),
        "extrude": svg24(f'<path {S} d="M5 3h8v4l-2.5 3v2.5h-3V10L5 7z M9 14.5v1.5 M18 4v12 M14.5 12.5L18 16l3.5-3.5"/>'),
        "retract": svg24(f'<path {S} d="M5 3h8v4l-2.5 3v2.5h-3V10L5 7z M9 14.5v1.5 M18 16V4 M14.5 7.5L18 4l3.5 3.5"/>'),
        "filament": svg24(f'<path {S} d="{SPOOL}"/>'),
        "spool": svg24(f'<path {S} d="{SPOOL}"/>'),
        "spoolman": svg24(f'<path {S} d="M10 3a7 7 0 1 0 0 14a7 7 0 1 0 0-14z M10 8a2 2 0 1 0 0 4a2 2 0 1 0 0-4z M15 20h6 M15 17h6"/>'),
    }
    for i in range(12):
        out[f"extruder-{i}"] = svg24(
            f'<path {S} d="M4 3h10v5l-3 4v3H7v-3L4 8z M9 17.5v2"/>'
            f'<text x="19.5" y="21" font-family="Inter, sans-serif" font-size="9" font-weight="700" '
            f'text-anchor="middle" fill="currentColor">{i}</text>')
    # bed-level probe points: plate outline + dot at a 3x3 position
    pos = {"t-l": (7, 7), "t-m": (12, 7), "t-r": (17, 7), "l-m": (7, 12), "center": (12, 12),
           "r-m": (17, 12), "b-l": (7, 17), "b-m": (12, 17), "b-r": (17, 17)}
    for key, (x, y) in pos.items():
        out[f"bed-level-{key}"] = svg24(
            f'<rect x="3.5" y="3.5" width="17" height="17" rx="3.5" {S}/>'
            f'<circle cx="{x}" cy="{y}" r="2.4" fill="currentColor"/>')
    return out


def inner(assets, name, weight):
    suffix = "" if weight == "regular" else f"-{weight}"
    text = (assets / weight / f"{name}{suffix}.svg").read_text()
    return re.search(r"<svg[^>]*>(.*)</svg>", text, re.S).group(1)


def main(assets, dest):
    assets, dest = pathlib.Path(assets), pathlib.Path(dest)
    dest.mkdir(parents=True, exist_ok=True)
    count = 0
    for ks, (name, weight) in MAP.items():
        body = inner(assets, name, weight)
        (dest / f"{ks}.svg").write_text(
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="currentColor">{body}</svg>\n')
        count += 1
    for ks, ((name, weight), badge) in BADGED.items():
        body = inner(assets, name, weight)
        (dest / f"{ks}.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 256 256" fill="currentColor">'
            f'<g transform="scale(0.78)">{body}</g>'
            f'<text x="206" y="246" font-family="Inter, sans-serif" font-size="120" font-weight="700" '
            f'text-anchor="middle">{badge}</text></svg>\n')
        count += 1
    for ks, svg in custom().items():
        (dest / f"{ks}.svg").write_text(svg)
        count += 1
    print(f"{count} icons written to {dest}")


if __name__ == "__main__":
    main(*sys.argv[1:3])
