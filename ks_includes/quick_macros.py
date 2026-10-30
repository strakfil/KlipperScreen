"""User-chosen quick macros: which macros appear on the Home screen, in what order,
under what name and with which icon. Stored per printer in KlipperScreen.conf:

    [quick_macros Voron]
    order = G32, CLEAN_NOZZLE, HEATSOAK
    name_clean_nozzle = Očistit trysku
    icon_clean_nozzle = macro-broom

Running a macro while a print is active ALWAYS asks for confirmation.
"""
import logging

from gi.repository import GLib

MAX_QUICK = 4
DEFAULT_ICON = "macro-lightning"
# Icons offered in the picker (files in styles/<theme>/images, built by scripts/build_apple_icons.py)
ICON_CHOICES = (
    "macro-lightning", "macro-broom", "macro-sparkle", "macro-crosshair", "macro-house",
    "macro-arrows-vertical", "macro-thermometer", "macro-fire", "macro-snowflake", "macro-timer",
    "macro-fan", "macro-wind", "macro-drop", "macro-spool-in", "macro-spool-out", "macro-lightbulb",
    "macro-camera", "macro-wrench", "macro-ruler", "macro-magic-wand", "macro-play", "macro-stop",
    "macro-power", "macro-bell",
)


def pretty_name(macro):
    """CLEAN_NOZZLE -> 'CLEAN NOZZLE' (underscores as spaces, case kept)."""
    return " ".join(macro.replace("_", " ").split())


def _key(macro):
    return macro.strip().lower()


class QuickMacros:
    def __init__(self, screen):
        self.screen = screen

    # ---------- storage ----------
    @property
    def config(self):
        return self.screen._config.get_config()

    @property
    def section(self):
        return f"quick_macros {self.screen.state.printer_name}"

    def _get(self, option, fallback=None):
        if self.section not in self.config.sections():
            return fallback
        return self.config[self.section].get(option, fallback)

    def _set(self, option, value):
        if self.section not in self.config.sections():
            self.config.add_section(self.section)
        if value in (None, ""):
            self.config.remove_option(self.section, option)
        else:
            self.config.set(self.section, option, str(value))
        self.screen._config.save_user_config_options()

    # ---------- visibility ----------
    def available(self):
        """Macros Klipper reports, minus the ones hidden in Settings."""
        hidden_section = f"displayed_macros {self.screen.state.printer_name}"
        result = []
        for macro in self.screen.printer.get_gcode_macros():
            if self.config.getboolean(hidden_section, macro.lower(), fallback=True):
                result.append(macro)
        return result

    def _canonical(self, macro, available):
        lookup = {m.lower(): m for m in available}
        return lookup.get(macro.strip().lower())

    # ---------- order / pinning ----------
    def order(self, available=None):
        """Pinned macros that still exist and are visible (silently drops stale ones)."""
        raw = [m for m in (self._get("order", "") or "").split(",") if m.strip()]
        available = self.available() if available is None else available
        result = []
        for macro in raw:
            canonical = self._canonical(macro, available)
            if canonical and canonical not in result:
                result.append(canonical)
            elif not canonical:
                logging.info(f"Quick macro '{macro.strip()}' not available, skipping")
        return result[:MAX_QUICK]

    def _save_order(self, order):
        self._set("order", ", ".join(order))

    def is_pinned(self, macro):
        return macro.lower() in (m.lower() for m in self.order())

    def can_pin(self):
        return len(self.order()) < MAX_QUICK

    def pin(self, macro):
        order = self.order()
        if len(order) >= MAX_QUICK or self.is_pinned(macro):
            return False
        self._save_order(order + [macro])
        return True

    def unpin(self, macro):
        self._save_order([m for m in self.order() if m.lower() != macro.lower()])

    def move(self, macro, delta):
        order = self.order()
        idx = [m.lower() for m in order].index(macro.lower())
        new = idx + delta
        if 0 <= new < len(order):
            order[idx], order[new] = order[new], order[idx]
            self._save_order(order)

    # ---------- name / icon ----------
    def custom_name(self, macro):
        return self._get(f"name_{_key(macro)}")

    def display_name(self, macro):
        return self.custom_name(macro) or pretty_name(macro)

    def set_name(self, macro, name):
        name = " ".join((name or "").split())
        if name == pretty_name(macro):
            name = ""
        self._set(f"name_{_key(macro)}", name)

    def icon(self, macro):
        icon = self._get(f"icon_{_key(macro)}", DEFAULT_ICON)
        return icon if icon in ICON_CHOICES else DEFAULT_ICON

    def set_icon(self, macro, icon):
        self._set(f"icon_{_key(macro)}", None if icon == DEFAULT_ICON else icon)

    def reset(self, macro):
        self._set(f"name_{_key(macro)}", None)
        self._set(f"icon_{_key(macro)}", None)

    # ---------- running ----------
    def is_printing(self):
        return self.screen.printer.state in ("printing", "paused")

    def run(self, macro, params="", widget=None):
        script = f"{macro}{params}"
        if self.is_printing():
            # Always confirm while printing: a macro can move the toolhead or change temperatures.
            # Strip Jinja delimiters: the confirm dialog renders its text as a template.
            name = GLib.markup_escape_text(self.display_name(macro)).replace("{", "(").replace("}", ")")
            name = name.replace("%", "")
            text = (_("Run %s?") % f"<b>{name}</b>") + "\n\n" + _(
                "A print is in progress. The macro may move the toolhead or change temperatures."
            )
            self.screen._confirm_send_action(widget, text, "printer.gcode.script", {"script": script})
            return
        self.screen.show_popup_message(_("Running: %s") % self.display_name(macro), 1)
        self.screen._send_action(widget, "printer.gcode.script", {"script": script})
