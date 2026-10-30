"""Automatic Light/Dark appearance for KlipperScreen (Apple-style "Automaticky").

Modes (KlipperScreen.conf, section [main], key `appearance`):
    light     - always light
    dark      - always dark
    auto      - light between sunrise and sunset (offline NOAA calculation)
    schedule  - light between `light_from` and `dark_from` (HH:MM, may wrap midnight)

Manual toggle while in auto/schedule behaves like Control Center on iOS/macOS:
the chosen variant holds only until the next scheduled transition.
"""
import datetime
import logging
import math
import re

try:
    from gi.repository import GLib
except ImportError:  # allows unit testing without GTK
    GLib = None

ZENITH = 90.833  # official sunrise/sunset incl. refraction and solar disc
CLOCK_SANE_YEAR = 2025  # CB1 has no RTC: before NTP sync the clock is bogus


def _sun_event_utc(date, lat, lon, rising):
    """Return the UTC datetime of sunrise/sunset on `date`, or a string for polar cases."""
    g = 2 * math.pi / 365 * (date.timetuple().tm_yday - 1)
    eqtime = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                       - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g)
            - 0.006758 * math.cos(2 * g) + 0.000907 * math.sin(2 * g)
            - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    phi = math.radians(lat)
    cos_ha = (math.cos(math.radians(ZENITH)) / (math.cos(phi) * math.cos(decl))
              - math.tan(phi) * math.tan(decl))
    if cos_ha > 1:
        return "polar_night"
    if cos_ha < -1:
        return "polar_day"
    ha = math.degrees(math.acos(cos_ha))
    minutes = 720 - 4 * (lon + (ha if rising else -ha)) - eqtime
    midnight = datetime.datetime(date.year, date.month, date.day, tzinfo=datetime.timezone.utc)
    return midnight + datetime.timedelta(minutes=minutes)


def sun_times(date, lat, lon):
    """(sunrise, sunset) as local aware datetimes, or a polar string for both."""
    rise = _sun_event_utc(date, lat, lon, True)
    if isinstance(rise, str):
        return rise, rise
    sset = _sun_event_utc(date, lat, lon, False)
    return rise.astimezone(), sset.astimezone()


# Apple system colours: (dark, light). Light values are the accessible variants
# where the standard one fails 4.5:1 as text on white.
ACCENTS = {
    "blue": ("#0A84FF", "#007AFF"),
    "purple": ("#BF5AF2", "#AF52DE"),
    "pink": ("#FF375F", "#D30F45"),
    "red": ("#FF453A", "#D70015"),
    "orange": ("#FF9F0A", "#C93400"),
    "yellow": ("#FFD60A", "#A05A00"),
    "green": ("#30D158", "#248A3D"),
    "graphite": ("#98989D", "#6C6C70"),
}
ON_ACCENT_DARK_TEXT = {"yellow"}  # white text would fail on these fills (dark mode)

PALETTES = {
    "dark": {
        "bg": "#000000", "bg2": "#1C1C1E", "bg3": "#2C2C2E", "key": "#3A3A3C",
        "fill": "rgba(118,118,128,0.24)", "fill_pressed": "rgba(118,118,128,0.42)",
        "sep": "#38383A", "label": "#FFFFFF", "label2": "#98989F", "label3": "#5A5A5F",
        "red": "#FF453A", "orange": "#FF9F0A", "green": "#30D158", "teal": "#64D2FF",
        "tint": "0.22", "shadow": "rgba(0,0,0,0.6)",
    },
    "light": {
        "bg": "#F2F2F7", "bg2": "#FFFFFF", "bg3": "#FFFFFF", "key": "#FFFFFF",
        "fill": "rgba(118,118,128,0.12)", "fill_pressed": "rgba(118,118,128,0.28)",
        "sep": "#C6C6C8", "label": "#000000", "label2": "#6C6C70", "label3": "#AEAEB2",
        "red": "#D70015", "orange": "#C93400", "green": "#248A3D", "teal": "#0071A4",
        "tint": "0.12", "shadow": "rgba(0,0,0,0.18)",
    },
}


def palette(variant, accent="blue"):
    """Resolved colour tokens for a variant + accent (used for CSS and icon recolouring)."""
    p = dict(PALETTES.get(variant, PALETTES["dark"]))
    acc = ACCENTS.get(accent, ACCENTS["blue"])
    p["accent"] = acc[0] if variant == "dark" else acc[1]
    p["on_accent"] = "#000000" if (variant == "dark" and accent in ON_ACCENT_DARK_TEXT) else "#FFFFFF"
    return p


def strip_motion(css):
    """Reduce Motion: drop every transition from a stylesheet."""
    return re.sub(r"transition\s*:[^;}]*;?", "", css)


def palette_css(variant, accent="blue"):
    """@define-color block that the apple theme's style.css builds on."""
    p = palette(variant, accent)
    tint = p.pop("tint")
    lines = [f"@define-color {k} {v};" for k, v in p.items()]
    for name in ("accent", "red", "orange", "green"):
        lines.append(f"@define-color {name}_tint alpha(@{name}, {tint});")
    # names the stock base.css / panels still reference
    lines += ["@define-color text @label;", "@define-color text-inv @bg;", "@define-color active @accent;",
              "@define-color echo @green;", "@define-color warning @orange;", "@define-color error @red;",
              "@define-color lines @sep;", "@define-color switch-scale-bg @accent;"]
    return "\n".join(lines) + "\n"


def _parse_hhmm(value, fallback):
    try:
        h, m = (int(x) for x in str(value).split(":"))
        return datetime.time(h, m)
    except (ValueError, TypeError):
        logging.warning(f"Appearance: invalid time '{value}', using {fallback}")
        return fallback


class AppearanceScheduler:
    def __init__(self, conf, apply_cb, now_fn=None):
        """conf: dict-like with mode/latitude/longitude/light_from/dark_from.
        apply_cb(variant) is called with 'light' or 'dark' whenever the variant changes."""
        self.apply_cb = apply_cb
        self.now_fn = now_fn or (lambda: datetime.datetime.now().astimezone())
        self.current = None
        self.override = None  # (variant, until)
        self.timer = None
        self.configure(conf)

    # ---------- configuration ----------
    def configure(self, conf):
        self.mode = conf.get("appearance", "auto")
        if self.mode not in ("light", "dark", "auto", "schedule"):
            logging.warning(f"Appearance: unknown mode '{self.mode}', using auto")
            self.mode = "auto"
        self.lat = float(conf.get("latitude", 49.195))
        self.lon = float(conf.get("longitude", 16.608))
        self.light_from = _parse_hhmm(conf.get("light_from", "07:00"), datetime.time(7, 0))
        self.dark_from = _parse_hhmm(conf.get("dark_from", "19:00"), datetime.time(19, 0))
        self.override = None

    # ---------- decision ----------
    def _transitions(self, day):
        """Sorted list of (local datetime, variant starting then) for one calendar day."""
        if self.mode == "schedule":
            tz = self.now_fn().tzinfo
            return sorted([
                (datetime.datetime.combine(day, self.light_from, tz), "light"),
                (datetime.datetime.combine(day, self.dark_from, tz), "dark"),
            ])
        rise, sset = sun_times(day, self.lat, self.lon)
        if isinstance(rise, str):
            return []
        return [(rise, "light"), (sset, "dark")]

    def _scheduled(self, now):
        if self.mode in ("light", "dark"):
            return self.mode, None
        if self.mode == "auto":
            rise, _ = sun_times(now.date(), self.lat, self.lon)
            if rise == "polar_day":
                return "light", None
            if rise == "polar_night":
                return "dark", None
        events = []
        for offset in (-1, 0, 1):
            events += self._transitions(now.date() + datetime.timedelta(days=offset))
        events.sort()
        past = [e for e in events if e[0] <= now]
        future = [e for e in events if e[0] > now]
        variant = past[-1][1] if past else "dark"
        return variant, (future[0][0] if future else None)

    def desired(self, now=None):
        now = now or self.now_fn()
        variant, next_change = self._scheduled(now)
        if self.override:
            o_variant, until = self.override
            if until is None or now < until:
                return o_variant, until
            self.override = None
        return variant, next_change

    # ---------- runtime ----------
    def start(self):
        self.tick()
        if GLib and self.timer is None:
            # Poll each minute instead of one long timeout: survives NTP clock jumps,
            # DST changes and suspend/resume of the screen.
            self.timer = GLib.timeout_add_seconds(60, self.tick)

    def stop(self):
        if GLib and self.timer:
            GLib.source_remove(self.timer)
        self.timer = None

    def tick(self):
        now = self.now_fn()
        if now.year < CLOCK_SANE_YEAR and self.current is not None:
            return True  # clock not synced yet: keep what we have
        variant, _ = self.desired(now)
        if variant != self.current:
            logging.info(f"Appearance: switching to {variant} (mode {self.mode})")
            self.current = variant
            self.apply_cb(variant)
        return True

    def toggle(self):
        """Manual flip. In auto/schedule it holds until the next transition (like iOS)."""
        now = self.now_fn()
        target = "light" if self.current == "dark" else "dark"
        _, next_change = self._scheduled(now)
        self.override = (target, next_change if self.mode in ("auto", "schedule") else None)
        self.tick()
        return target
