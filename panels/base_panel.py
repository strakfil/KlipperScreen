# -*- coding: utf-8 -*-
import logging
import os
import pathlib

import gi

gi.require_version("Gtk", "3.0")
from datetime import datetime
from math import log

from gi.repository import GdkPixbuf, Gio, GLib, Gtk, Pango
from jinja2 import Environment

from ks_includes.KlippyGtk import find_widget
from ks_includes.screen_panel import ScreenPanel
from ks_includes.svg_gradient import apply_filament_gradient, filament_colors_from_dict

try:
    import psutil

    psutil_available = True
except ImportError:
    psutil_available = False
    logging.debug("psutil is not installed. Unable to do battery check.")


class BasePanel(ScreenPanel):
    def __init__(self, screen, title=None):
        super().__init__(screen, title)
        self.current_panel = None
        self.time_min = -1
        self.time_format = self._config.get_main_config().getboolean("24htime", True)
        self.time_update = None
        self.battery_update = None
        self.spoolman_update = None
        self.titlebar_items = []
        self.titlebar_name_type = None
        self.titlebar_labels = {}
        self.titlebar_populated = False
        self.spoolman_low_limit = 20
        self.spoolman_current_colors = None
        self.current_extruder = None
        self.last_usage_report = datetime.now()
        self.usage_report = 0
        # Action bar buttons
        self.abscale = self.bts * 1.1
        self.control["back"] = self._gtk.Button("back", scale=self.abscale)
        self.control["back"].connect("clicked", self.back)
        self.control["home"] = self._gtk.Button("main", scale=self.abscale)
        self.control["home"].connect("clicked", self._screen._menu_go_back, True)
        for control in self.control:
            self.set_control_sensitive(False, control)
        self.control["estop"] = self._gtk.Button("emergency", scale=self.abscale)
        self.control["estop"].connect("clicked", self.emergency_stop)
        self.control["estop"].set_no_show_all(True)
        self.shutdown = {
            "panel": "shutdown",
        }
        self.control["shutdown"] = self._gtk.Button("shutdown", scale=self.abscale)
        self.control["shutdown"].connect("clicked", self.menu_item_clicked, self.shutdown)
        self.control["shutdown"].set_no_show_all(True)
        self.control["printer_select"] = self._gtk.Button("shuffle", scale=self.abscale)
        self.control["printer_select"].connect("clicked", self._screen.show_printer_select)
        self.control["printer_select"].set_no_show_all(True)

        self.shortcut = {}
        self.shortcut["panel"] = self._config.get_main_config().get(
            "side_shortcut_target", fallback="notifications"
        )
        self.shortcut["icon"] = self._get_shortcut_icon(self.shortcut["panel"])
        self.control["shortcut"] = self._gtk.Button(self.shortcut["icon"], scale=self.abscale)
        self.control["shortcut"].connect("clicked", self._shortcut_clicked, self.shortcut)

        # Any action bar button should close the keyboard
        for item in self.control:
            self.control[item].connect("clicked", self._screen.remove_keyboard)

        # Action bar
        self.action_bar = Gtk.Box(spacing=5)
        self.action_bar.get_style_context().add_class("action_bar")
        self.action_bar.add(self.control["back"])
        self.action_bar.add(self.control["home"])
        self.action_bar.add(self.control["printer_select"])
        self.action_bar.add(self.control["shortcut"])
        self.action_bar.add(self.control["estop"])
        self.action_bar.add(self.control["shutdown"])
        self.show_printer_select(len(self._config.get_printers()) > 1)

        # Titlebar

        # This box will be populated with titlebar_items
        self.control["item_box"] = Gtk.Box(spacing=10)

        self.titlelbl = Gtk.Label(
            hexpand=True, halign=Gtk.Align.CENTER, ellipsize=Pango.EllipsizeMode.END
        )

        self.control["time"] = Gtk.Label(label="00:00 AM")
        self.control["time_box"] = Gtk.Box(halign=Gtk.Align.END)
        self.control["time_box"].pack_end(self.control["time"], True, True, 10)

        self.battery_icons = self.load_battery_icons()
        self.labels["battery"] = Gtk.Label()
        self.labels["battery_icon"] = self._gtk.Image()
        self.labels["battery_icon"].set_from_pixbuf(self.battery_icons["unknown"])
        self.control["battery_box"] = Gtk.Box(halign=Gtk.Align.END)
        self.control["battery_box"].set_no_show_all(True)
        self.control["battery_box"].add(self.labels["battery"])
        self.control["battery_box"].add(self.labels["battery_icon"])
        for widget in self.control["battery_box"]:
            widget.show()

        self.labels["spoolman_icon"] = Gtk.Image()
        self.labels["spoolman_icon"].set_from_pixbuf(self.get_spoolman_icon_pixbuf())
        self.labels["spoolman_weight"] = Gtk.Label(label="?")
        spoolman_box = Gtk.Box()
        spoolman_box.pack_start(self.labels["spoolman_icon"], False, False, 7)
        spoolman_box.pack_start(self.labels["spoolman_weight"], False, False, 0)
        self.control["spoolman_box"] = Gtk.EventBox()
        self.control["spoolman_box"].add(spoolman_box)
        self.control["spoolman_box"].connect("button-press-event", self.open_spool_panel)

        self.nav_mode = (not self._screen.vertical_mode
                         and getattr(self._gtk, "action_bar_position", "left") == "top")
        self.titlebar = Gtk.Box(spacing=5, valign=Gtk.Align.CENTER)
        self.titlebar.get_style_context().add_class("title_bar")
        if self.nav_mode:
            self._build_nav_bar()
        else:
            self.titlebar.add(self.control["item_box"])
            self.titlebar.add(self.titlelbl)
            self.titlebar.add(self.control["time_box"])
            self.titlebar.add(self.control["battery_box"])
        self.set_title(title)

        # Main layout
        self.main_grid = Gtk.Grid()
        self.content_wrap = None  # scroll wrapper used when the action bar is at the bottom
        self._build_main_grid()

        self.update_time()

    def _reconfigure_main_grid(self):
        self.main_grid.remove(self.titlebar)
        if self.content_wrap is not None and self.content_wrap.get_parent() is self.main_grid:
            self.main_grid.remove(self.content_wrap)
            self.content_wrap.remove(self.content_wrap.get_child())  # viewport holding content
            self.content_wrap = None
        else:
            self.main_grid.remove(self.content)
        self.main_grid.remove(self.action_bar)
        self._build_main_grid()

    # ------------------------------------------------------------------ navigation bar (Apple)
    def _build_nav_bar(self):
        """‹ Back (parent title) | Title | temps · notifications · STOP. Back long-press = Home."""
        self.titlebar.get_style_context().add_class("nav-bar")
        self.titlebar.set_size_request(-1, self._gtk.titlebar_height)
        self.titlebar.set_valign(Gtk.Align.FILL)
        size = self._gtk.font_size * 1.45

        self.nav_back = Gtk.Button(label=_("Home"))
        back_icon = self._gtk.Image("back", size, size, role="accent")
        back_icon.show()  # the button uses no_show_all, so its image must be shown explicitly
        self.nav_back.set_image(back_icon)
        self.nav_back.set_always_show_image(True)
        self.nav_back.get_style_context().add_class("nav-back")
        self.nav_back.set_no_show_all(True)
        self.nav_back.connect("clicked", self.back)
        self.nav_back.connect("clicked", self._screen.remove_keyboard)
        self._nav_long_press = Gtk.GestureLongPress.new(self.nav_back)
        self._nav_long_press.connect("pressed", lambda *a: self._screen._menu_go_back(None, True))
        label = find_widget(self.nav_back, Gtk.Label)
        if label is not None:
            label.set_ellipsize(Pango.EllipsizeMode.END)
            label.set_max_width_chars(14)

        self.titlelbl.get_style_context().add_class("nav-title")
        self.control["item_box"].get_style_context().add_class("nav-temps")
        self.control["item_box"].set_no_show_all(True)

        self.nav_estop = Gtk.Button(label=_("STOP"))
        self.nav_estop.set_image(self._gtk.Image("emergency", size, size, role="red"))
        self.nav_estop.set_always_show_image(True)
        self.nav_estop.get_style_context().add_class("nav-estop")
        self.nav_estop.connect("clicked", self.emergency_stop)
        self.control["shortcut"].get_style_context().add_class("nav-icon")
        # The bell lives in the (unused) side bar by default: a widget can have one parent only
        if self.control["shortcut"].get_parent() is not None:
            self.control["shortcut"].get_parent().remove(self.control["shortcut"])
        # Side-bar buttons expand; in the navigation bar it must stay a compact icon button
        self.control["shortcut"].set_hexpand(False)
        self.control["shortcut"].set_vexpand(False)
        self.control["shortcut"].set_valign(Gtk.Align.CENTER)
        self.control["shortcut"].set_image(self._gtk.Image(self.shortcut["icon"], size, size))
        for widget in (self.nav_back, self.nav_estop):
            widget.set_valign(Gtk.Align.CENTER)

        self.titlebar.pack_start(self.nav_back, False, False, 0)
        self.titlebar.pack_start(self.titlelbl, True, True, 0)
        self.titlebar.pack_end(self.nav_estop, False, False, 0)
        self.titlebar.pack_end(self.control["shortcut"], False, False, 0)
        self.titlebar.pack_end(self.control["battery_box"], False, False, 0)
        self.titlebar.pack_end(self.control["item_box"], False, False, 0)

        # Edge swipe from the left = Back (like iOS). Captured on the window so it works
        # over any panel; it claims the touch only once it is clearly a horizontal swipe.
        self._edge = {"active": False, "done": False}
        self._edge_drag = Gtk.GestureDrag.new(self._screen)
        self._edge_drag.set_propagation_phase(Gtk.PropagationPhase.CAPTURE)
        self._edge_drag.connect("drag-begin", self._edge_begin)
        self._edge_drag.connect("drag-update", self._edge_update)
        self._edge_drag.connect("drag-end", self._edge_end)

    def _edge_begin(self, gesture, x, y):
        self._edge = {"active": x <= self._gtk.font_size * 1.4 and y > self._gtk.titlebar_height,
                      "done": False}

    def _edge_update(self, gesture, dx, dy):
        if not self._edge["active"] or self._edge["done"]:
            return
        if abs(dy) > self._gtk.font_size * 3:
            self._edge["active"] = False  # vertical scroll, not a back swipe
        elif dx > self._gtk.font_size * 1.2:
            gesture.set_state(Gtk.EventSequenceState.CLAIMED)  # cancels the tap underneath
            if dx > self._gtk.font_size * 4.5:
                self._edge["done"] = True
                if len(self._screen._cur_panels) > 1:
                    self.back()

    def _edge_end(self, gesture, dx, dy):
        self._edge = {"active": False, "done": False}

    def _render_title(self, title):
        if not title:
            return ""
        try:
            env = Environment(extensions=["jinja2.ext.i18n"], autoescape=True)
            env.install_gettext_translations(self._config.get_lang())
            return env.from_string(title).render()
        except Exception:
            return title

    def _update_nav_bar(self):
        printing, connected, printer_select = self.get_printer_state()
        stack = self._screen._cur_panels
        can_back = len(stack) > 1
        self.nav_back.set_visible(can_back)
        if can_back:
            parent = stack[-2]
            parent_panel = self._screen.panels.get(parent)
            if parent in ("main_menu", "splash_screen") or parent_panel is None:
                text = _("Home")
            else:
                text = self._render_title(getattr(parent_panel, "title", "")) or _("Back")
            if self.nav_back.get_label() != text:
                self.nav_back.set_label(text)
            image = self.nav_back.get_image()
            if image is not None:  # set_label() rebuilds the child; keep the chevron visible
                image.show()
        # Root screen (Home / print status) shows a large leading title, sub-pages a centred one
        ctx = self.titlelbl.get_style_context()
        centered = self.titlebar.get_center_widget() is self.titlelbl
        if can_back and not centered:  # true centre of the bar, independent of side widths
            self.titlebar.remove(self.titlelbl)
            self.titlebar.set_center_widget(self.titlelbl)
        elif not can_back and centered:
            self.titlebar.set_center_widget(None)
            self.titlebar.pack_start(self.titlelbl, True, True, 0)
            self.titlebar.reorder_child(self.titlelbl, 1)
        if can_back:
            ctx.remove_class("nav-large-title")
            self.titlelbl.set_halign(Gtk.Align.CENTER)
        else:
            ctx.add_class("nav-large-title")
            self.titlelbl.set_halign(Gtk.Align.START)
        self.titlelbl.show()
        self.control["item_box"].set_visible(can_back)
        if can_back:  # no_show_all box: show its (lazily added) temperature items explicitly
            for child in self.control["item_box"].get_children():
                child.show_all()
        # STOP is always visible; it can only act while Klipper is connected
        self.nav_estop.set_sensitive(bool(connected))
        # Notifications bell only when there is something to see
        if self.shortcut["panel"] == "notifications":
            self.control["shortcut"].set_visible(self.shortcut["icon"] != "notifications")
        else:
            self.control["shortcut"].set_visible(True)

    def _build_main_grid(self):
        self.action_bar.set_size_request(self._gtk.action_bar_width, self._gtk.action_bar_height)
        position = getattr(self._gtk, "action_bar_position", "left")
        ctx = self.action_bar.get_style_context()
        for pos in ("left", "right", "bottom"):
            ctx.remove_class(f"action_bar_{pos}")
        ctx.add_class(f"action_bar_{position}")
        if not self._screen.vertical_mode and position == "top":
            self.main_grid.attach(self.titlebar, 0, 0, 1, 1)
            self.main_grid.attach(self.content, 0, 1, 1, 1)  # side bar not attached
        elif not self._screen.vertical_mode and position == "right":
            self.main_grid.attach(self.titlebar, 0, 0, 1, 1)
            self.main_grid.attach(self.content, 0, 1, 1, 1)
            self.main_grid.attach(self.action_bar, 1, 0, 1, 2)
            self.action_bar.set_orientation(orientation=Gtk.Orientation.VERTICAL)
            self.action_bar.set_hexpand(False)
            self.action_bar.set_vexpand(True)
        elif not self._screen.vertical_mode and position == "bottom":
            # A panel taller than the remaining height must scroll instead of pushing the
            # action bar (incl. emergency stop) off the screen.
            self.content_wrap = Gtk.ScrolledWindow(hexpand=True, vexpand=True)
            self.content_wrap.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            self.content_wrap.add(self.content)
            self.main_grid.attach(self.titlebar, 0, 0, 1, 1)
            self.main_grid.attach(self.content_wrap, 0, 1, 1, 1)
            self.main_grid.attach(self.action_bar, 0, 2, 1, 1)
            self.action_bar.set_orientation(orientation=Gtk.Orientation.HORIZONTAL)
            self.action_bar.set_hexpand(True)
            self.action_bar.set_vexpand(False)
        elif self._screen.vertical_mode:
            self.main_grid.attach(self.titlebar, 0, 0, 1, 1)
            self.main_grid.attach(self.content, 0, 1, 1, 1)
            self.main_grid.attach(self.action_bar, 0, 2, 1, 1)
            self.action_bar.set_orientation(orientation=Gtk.Orientation.HORIZONTAL)
            self.action_bar.set_hexpand(True)
            self.action_bar.set_vexpand(False)
        else:
            self.main_grid.attach(self.titlebar, 1, 0, 1, 1)
            self.main_grid.attach(self.content, 1, 1, 1, 1)
            self.main_grid.attach(self.action_bar, 0, 0, 1, 2)
            self.action_bar.set_orientation(orientation=Gtk.Orientation.VERTICAL)
            self.action_bar.set_hexpand(False)
            self.action_bar.set_vexpand(True)

    def load_battery_icons(self):
        img_size = self._gtk.img_scale * self.bts
        return {
            "charging": self._gtk.PixbufFromIcon("battery-charging", img_size, img_size),
            "100": self._gtk.PixbufFromIcon("battery-100", img_size, img_size),
            "75": self._gtk.PixbufFromIcon("battery-75", img_size, img_size),
            "50": self._gtk.PixbufFromIcon("battery-50", img_size, img_size),
            "25": self._gtk.PixbufFromIcon("battery-25", img_size, img_size),
            "0": self._gtk.PixbufFromIcon("battery-0", img_size, img_size),
            "unknown": self._gtk.PixbufFromIcon("battery-unknown", img_size, img_size),
        }

    def reload_icons(self):
        button: Gtk.Button
        for button in self.action_bar.get_children():
            img = button.get_image()
            name = button.get_name()
            pixbuf = img.get_pixbuf()
            if pixbuf is not None:
                size = pixbuf.get_width()
            else:
                logging.error(f"Couldn't get pixbuf for {name},a custom theme may have caused this")
                size = self._gtk.img_scale * self.abscale * 1.4
            button.set_image(self._gtk.Image(name, size, size))

        self.battery_icons = self.load_battery_icons()
        self.battery_percentage()
        if getattr(self, "nav_mode", False):
            self._gtk.refresh_icons([self.titlebar])

    def get_spoolman_icon_pixbuf(self, colors=None):
        if not colors:
            colors = self.get_active_spoolman_colors()
        klipperscreendir = pathlib.Path(__file__).parent.resolve().parent
        icon_path = os.path.join(
            klipperscreendir, "styles", self._screen.theme, "images", "spool.svg"
        )
        icon_size = self._gtk.img_scale * self.bts * 0.9
        if not os.path.isfile(icon_path):
            icon_path = os.path.join(klipperscreendir, "styles", "spool.svg")
        try:
            svg = pathlib.Path(icon_path).read_text(encoding="utf-8")
            svg = apply_filament_gradient(svg, colors)
            stream = Gio.MemoryInputStream.new_from_data(svg.encode(), None)
            pixbuf = GdkPixbuf.Pixbuf.new_from_stream_at_scale(
                stream,
                -1,
                icon_size,
                True,
                None,
            )
            stream.close_async(2)
            return pixbuf
        except Exception as e:
            logging.error(f"Couldn't load spoolman icon: {e}")
            return self._gtk.PixbufFromIcon("spool", icon_size, icon_size)

    def get_active_spoolman_colors(self):
        if (
            self._printer is None
            or not self._printer.active_spool
            or "filament" not in self._printer.active_spool
            or not self._printer.active_spool["filament"]
        ):
            return filament_colors_from_dict(None)
        return filament_colors_from_dict(self._printer.active_spool["filament"])

    def show_titlebar_items(self):
        if self.titlebar_populated:
            return
        if self._printer is None:
            logging.warning("Cannot load Titlebar items, printer not initialized")
            return
        try:
            devices = self._printer.get_temp_devices()
            if not devices:
                return
            img_size = self._gtk.img_scale * self.bts
            for device in devices:
                self.titlebar_labels[device] = Gtk.Label(ellipsize=Pango.EllipsizeMode.START)
                self.titlebar_labels[f"{device}_box"] = Gtk.Box()
                icon = self.get_icon(device, img_size)
                if icon is not None:
                    self.titlebar_labels[f"{device}_box"].pack_start(icon, False, False, 3)
                self.titlebar_labels[f"{device}_box"].pack_start(
                    self.titlebar_labels[device], False, False, 0
                )
                self.titlebar_labels[f"{device}_eventbox"] = Gtk.EventBox()
                self.titlebar_labels[f"{device}_eventbox"].add(
                    self.titlebar_labels[f"{device}_box"]
                )
                self.titlebar_labels[f"{device}_eventbox"].connect(
                    "button-press-event", self.open_temp_panel, device
                )

            # Limit the number of items according to resolution
            nlimit = int(round(log(self._screen.width, 10) * 5 - 10.5))
            n = 0
            if len(self._printer.get_tools()) > (nlimit - 1):
                self.current_extruder = self._printer.get_stat("toolhead", "extruder")
                if self.current_extruder and f"{self.current_extruder}_box" in self.titlebar_labels:
                    self.control["item_box"].add(
                        self.titlebar_labels[f"{self.current_extruder}_eventbox"]
                    )
            else:
                self.current_extruder = False
            for device in devices:
                if n >= nlimit:
                    break
                if device.startswith("extruder") and self.current_extruder is False:
                    self.control["item_box"].add(self.titlebar_labels[f"{device}_eventbox"])
                    n += 1
                elif device.startswith("heater"):
                    self.control["item_box"].add(self.titlebar_labels[f"{device}_eventbox"])
                    n += 1
            for item in self.titlebar_items:
                # Users can fill the bar if they want
                if n >= nlimit + 1:
                    break
                if item == "spool" and self._printer.spoolman:
                    self.add_spoolman_box()
                    n += 1
                    continue
                for device in devices:
                    name = device.split()[1] if len(device.split()) > 1 else device
                    if (
                        name == item
                        and self.titlebar_labels[f"{device}_eventbox"].get_parent() is None
                    ):
                        self.control["item_box"].add(self.titlebar_labels[f"{device}_eventbox"])
                        n += 1
                        break
            if (
                n < nlimit
                and self._printer.spoolman
                and self.control["spoolman_box"].get_parent() != self.control["item_box"]
            ):
                self.add_spoolman_box()
                n += 1

            self.control["item_box"].show_all()
            self.titlebar_populated = True
        except Exception as e:
            logging.debug(f"Couldn't create heaters box: {e}")

    def clear_titlebar_items(self):
        if self.spoolman_update is not None:
            GLib.source_remove(self.spoolman_update)
            self.spoolman_update = None
        for child in list(self.control["item_box"].get_children()):
            self.control["item_box"].remove(child)
        self.titlebar_labels.clear()
        self.titlebar_populated = False

    def add_spoolman_box(self):
        self.control["item_box"].add(self.control["spoolman_box"])
        self.set_spoolman_refresh()

    def open_spool_panel(self, widget=None, event=None):
        self._screen.show_panel("spoolman")
        return True

    def open_temp_panel(self, widget=None, event=None, device=None):
        self._screen.show_panel("temperature", extra=device)
        return True

    def get_icon(self, device, img_size):
        if device.startswith("extruder"):
            if self._printer.extrudercount > 1:
                if device == "extruder":
                    device = "extruder0"
                return self._gtk.Image(f"extruder-{device[8:]}", img_size, img_size)
            return self._gtk.Image("extruder", img_size, img_size)
        elif device.startswith("heater_bed"):
            return self._gtk.Image("bed", img_size, img_size)
        # Extra items
        elif self.titlebar_name_type is not None:
            # The item has a name, do not use an icon
            return None
        elif device.startswith("temperature_fan"):
            return self._gtk.Image("fan", img_size, img_size)
        elif device.startswith("heater_generic"):
            return self._gtk.Image("heater", img_size, img_size)
        else:
            return self._gtk.Image("heat-up", img_size, img_size)

    def activate(self):
        if self.time_update is None:
            self.time_update = GLib.timeout_add_seconds(1, self.update_time)
        if self.battery_update is None:
            self.battery_update = GLib.timeout_add_seconds(60, self.battery_percentage)

    def set_spoolman_refresh(self):
        if self.spoolman_update is None:
            rate = self._config.get_config().getfloat("spoolman", "sync_rate", fallback=20)
            self.spoolman_update = GLib.timeout_add_seconds(int(rate), self.fetch_spoolman)

    def get_printer_state(self):
        printing = self._printer and self._printer.state in {"printing", "paused"}
        connected = self._printer and self._printer.state not in {
            "disconnected",
            "startup",
            "shutdown",
            "error",
        }
        printer_select = "printer_select" in self._screen._cur_panels
        return printing, connected, printer_select

    def update_action_bar(self):
        printing, connected, printer_select = self.get_printer_state()
        self.control["estop"].set_visible(printing)
        self.control["shutdown"].set_visible(not printing)
        self.show_shortcut(self.shortcut["panel"])
        self.show_printer_select(len(self._config.get_printers()) > 1)
        for control in ("back", "home"):
            self.set_control_sensitive(len(self._screen._cur_panels) > 1, control=control)
        if getattr(self, "nav_mode", False):
            self._update_nav_bar()

    def add_content(self, panel):
        self.update_action_bar()
        self.current_panel = panel
        self.set_title(panel.title)
        self.content.add(panel.content)

    def update_spoolman_alert_visuals(self, alert):
        if alert:
            self.labels["spoolman_weight"].get_style_context().add_class("spoolman_low")
        else:
            self.labels["spoolman_weight"].get_style_context().remove_class("spoolman_low")

    def update_spoolman_weight_label(self):
        if self._printer is None:
            return
        if (
            not self._printer.spoolman
            or not self._printer.active_spool
            or "remaining_weight" not in self._printer.active_spool
            or self._printer.active_spool["remaining_weight"] is None
        ):
            self.update_spoolman_alert_visuals(False)
            self.labels["spoolman_weight"].set_label("?")
            return
        remaining_weight = self._printer.active_spool["remaining_weight"]
        self.labels["spoolman_weight"].set_label(f"{round(remaining_weight):.0f} g")
        self.update_spoolman_alert_visuals(remaining_weight <= self.spoolman_low_limit)
        colors = self.get_active_spoolman_colors()
        if colors != self.spoolman_current_colors:
            self.labels["spoolman_icon"].set_from_pixbuf(self.get_spoolman_icon_pixbuf(colors))
            self.spoolman_current_colors = colors

    def fetch_spoolman(self):
        if (
            not self._printer
            or "printer_select" in self._screen._cur_panels
            or self.control["spoolman_box"].get_parent() != self.control["item_box"]
        ):
            logging.debug("Stopping Spoolman updates")
            if self.spoolman_update is not None:
                GLib.source_remove(self.spoolman_update)
                self.spoolman_update = None
            return False
        self._screen.get_active_spool()
        return True

    def back(self, widget=None):
        if self.current_panel is None:
            return
        self._screen.remove_keyboard()
        if (
            hasattr(self.current_panel, "back")
            and not self.current_panel.back()
            or not hasattr(self.current_panel, "back")
        ):
            self._screen._menu_go_back()

    def process_update(self, action, data):
        if action == "notify_active_spool_set":
            self.update_spoolman_weight_label()
            return
        if action == "notify_spoolman_status_changed":
            connected = data.get("spoolman_connected", False)
            self._printer.spoolman = connected
            self.update_spoolman_weight_label()
            if not connected:
                if self.spoolman_update is not None:
                    GLib.source_remove(self.spoolman_update)
                    self.spoolman_update = None
            elif self.spoolman_update is None and self._printer.active_spool:
                if self.control["spoolman_box"].get_parent() == self.control["item_box"]:
                    self.set_spoolman_refresh()
            return
        if action == "notify_proc_stat_update":
            cpu = data["system_cpu_usage"]["cpu"]
            memory = (data["system_memory"]["used"] / data["system_memory"]["total"]) * 100
            error = "message_popup_error"
            ctx = self.titlebar.get_style_context()
            msg = f"CPU: {cpu:2.0f}%    RAM: {memory:2.0f}%"
            if cpu > 80 or memory > 85:
                if self.usage_report < 3:
                    self.usage_report += 1
                    return
                self.last_usage_report = datetime.now()
                if not ctx.has_class(error):
                    ctx.add_class(error)
                self._screen.log_notification(f"{self._screen.state.printer_name}: {msg}", 2)
                self.titlelbl.set_label(msg)
            elif ctx.has_class(error):
                if (datetime.now() - self.last_usage_report).seconds < 5:
                    self.titlelbl.set_label(msg)
                    return
                self.usage_report = 0
                ctx.remove_class(error)
                self.titlelbl.set_label(f"{self._screen.state.printer_name}")
            return

        if action == "notify_update_response":
            if self.update_dialog is None:
                self.show_update_dialog()
            if "message" in data:
                self.labels["update_progress"].set_text(
                    f"{self.labels['update_progress'].get_text().strip()}\n{data['message']}\n"
                )
            if "complete" in data and data["complete"]:
                logging.info("Update complete")
                if self.update_dialog is not None:
                    try:
                        self.update_dialog.set_response_sensitive(Gtk.ResponseType.OK, True)
                        self.update_dialog.get_widget_for_response(Gtk.ResponseType.OK).show()
                    except AttributeError:
                        logging.error(
                            "error trying to show the updater button the dialog might be closed"
                        )
                        self._screen.state.updating = False
                        for dialog in self._screen.dialogs:
                            self._gtk.remove_dialog(dialog)
            return
        if action != "notify_status_update" or self._printer is None:
            return
        devices = self._printer.get_temp_devices()
        if not devices:
            return
        for device in devices:
            temp = self._printer.get_stat(device, "temperature")
            if temp and device in self.titlebar_labels:
                name = ""
                if not (device.startswith("extruder") or device.startswith("heater_bed")):
                    if self.titlebar_name_type == "full":
                        name = device.split()[1] if len(device.split()) > 1 else device
                        name = f"{self.prettify(name)}: "
                    elif self.titlebar_name_type == "short":
                        name = device.split()[1] if len(device.split()) > 1 else device
                        name = f"{name[:1].upper()}: "
                self.titlebar_labels[device].set_label(f"{name}{temp:.0f}°")

        if (
            self.current_extruder
            and "toolhead" in data
            and "extruder" in data["toolhead"]
            and data["toolhead"]["extruder"] != self.current_extruder
        ):
            current_key = f"{self.current_extruder}_eventbox"
            new_key = f"{data['toolhead']['extruder']}_eventbox"
            if current_key in self.titlebar_labels and new_key in self.titlebar_labels:
                self.control["item_box"].remove(
                    self.titlebar_labels[f"{self.current_extruder}_eventbox"]
                )
                self.current_extruder = data["toolhead"]["extruder"]
                self.control["item_box"].pack_start(
                    self.titlebar_labels[f"{self.current_extruder}_eventbox"], True, True, 3
                )
                self.control["item_box"].reorder_child(
                    self.titlebar_labels[f"{self.current_extruder}_eventbox"], 0
                )
                self.control["item_box"].show_all()

        return False

    def remove(self, widget):
        self.content.remove(widget)

    def set_control_sensitive(self, value=True, control="shortcut"):
        self.control[control].set_sensitive(value)

    def _get_shortcut_icon(self, panel):
        icons = {
            "notifications": "notifications",
            "lock_screen": "lock",
            "gcode_macros": "custom-script",
            "camera": "camera",
            "led": "light",
        }
        return icons.get(panel, panel)

    def _shortcut_clicked(self, widget, item):
        if self.shortcut["panel"] == "notifications":
            self.update_shortcut_icon(level=0)
        self.menu_item_clicked(widget, item)

    def update_shortcut(self, target):
        self.shortcut["panel"] = target
        self.shortcut["icon"] = self._get_shortcut_icon(target)
        image = self.control["shortcut"].get_image()
        pixbuf = image.get_pixbuf() if image is not None else None
        if pixbuf is None:
            return
        self.control["shortcut"].set_image(
            self._gtk.Image(self.shortcut["icon"], pixbuf.get_width(), pixbuf.get_height())
        )
        self.show_shortcut(target)

    def show_shortcut(self, target=None):
        current_panel = self._screen._cur_panels[-1] if self._screen._cur_panels else None
        onTarget = current_panel == target
        if onTarget:
            self.set_control_sensitive(False, control="shortcut")
            return
        if target in ("lock_screen", "notifications"):
            self.set_control_sensitive(True, control="shortcut")
            return
        if (
            self._printer is None
            or not self._screen.state.connected
            or self._screen._cur_panels[-1] == "printer_select"
        ):
            self.set_control_sensitive(False, control="shortcut")
            return
        status = self._printer.get_printer_status_data() or {}
        paths = {
            "gcode_macros": ("printer", "gcode_macros"),
            "camera": ("moonraker", "cameras"),
            "led": ("printer", "leds"),
        }
        available = True
        if target in paths:
            section, key = paths[target]
            count = status.get(section, {}).get(key, {}).get("count", 0)
            available = count > 0

        self.set_control_sensitive(available, control="shortcut")

    def update_shortcut_icon(self, level=None):
        if self.shortcut["panel"] != "notifications":
            return
        if level > 0:
            # Do not downgrade levels unless resetting
            if self.shortcut["icon"] == "notifications_important":
                level = 3
            if self.shortcut["icon"] == "notifications_active":
                level = 2
        if level == 3:
            icon = "notifications_important"
        elif level in (1, 2):
            icon = "notifications_active"
        else:
            icon = "notifications"
        self.shortcut["icon"] = icon
        if getattr(self, "nav_mode", False):
            self.control["shortcut"].set_visible(icon != "notifications")
        image = self.control["shortcut"].get_image()
        pixbuf = image.get_pixbuf() if image is not None else None
        if pixbuf is None:
            return
        role = "red" if icon == "notifications_important" else None
        self.control["shortcut"].set_image(
            self._gtk.Image(icon, pixbuf.get_width(), pixbuf.get_height(), role=role)
        )

    def show_printer_select(self, show=True):
        self.control["printer_select"].set_visible(show)
        self.control["printer_select"].set_sensitive(
            "printer_select" not in self._screen._cur_panels
        )

    def set_title(self, title):
        self.titlebar.get_style_context().remove_class("message_popup_error")
        if (
            self._screen.state.printer_name != "Printer"
            and "printer_select" not in self._screen._cur_panels
        ):
            printer = self._screen.state.printer_name
        else:
            printer = ""
        if getattr(self, "nav_mode", False):
            # Navigation bar: the page title only; the root shows the printer name
            rendered = self._render_title(title)
            stack = self._screen._cur_panels
            home = not stack or stack[-1] in ("main_menu", "splash_screen")
            self.titlelbl.set_label(printer if (home or not rendered) else rendered)
            return
        if not title:
            self.titlelbl.set_label(f"{printer}")
            return
        try:
            env = Environment(extensions=["jinja2.ext.i18n"], autoescape=True)
            env.install_gettext_translations(self._config.get_lang())
            j2_temp = env.from_string(title)
            title = j2_temp.render()
        except Exception as e:
            logging.debug(f"Error parsing jinja for title: {title}\n{e}")

        self.titlelbl.set_label(f"{printer} {title}")

    def update_time(self):
        now = datetime.now()
        confopt = self._config.get_main_config().getboolean("24htime", True)
        if now.minute != self.time_min or self.time_format != confopt:
            if confopt:
                self.control["time"].set_text(f"{now:%H:%M }")
            else:
                self.control["time"].set_text(f"{now:%I:%M %p}")
            self.time_min = now.minute
            self.time_format = confopt
        return True

    def get_battery_icon(self, charge: float, plugged: bool):
        if plugged:
            return self.battery_icons["charging"]
        elif charge > 75:
            return self.battery_icons["100"]
        elif charge > 50:
            return self.battery_icons["75"]
        elif charge > 25:
            return self.battery_icons["50"]
        elif charge > 10:
            return self.battery_icons["25"]
        elif charge >= 0:
            return self.battery_icons["0"]
        else:
            return self.battery_icons["unknown"]

    def battery_percentage(self):
        if not psutil_available:
            return False
        battery = psutil.sensors_battery()
        if battery and battery.percent:
            self.labels["battery_icon"].set_from_pixbuf(
                self.get_battery_icon(battery.percent, battery.power_plugged)
            )
            self.labels["battery"].set_text(f"{battery.percent:.0f}%")
            self.control["battery_box"].show()
            return True
        else:
            logging.debug("Battery information not available.")
            self.control["battery_box"].hide()
            return False

    def set_ks_printer_cfg(self, printer):
        ScreenPanel.ks_printer_cfg = self._config.get_printer_config(printer)
        if self.ks_printer_cfg is not None:
            self.titlebar_name_type = self.ks_printer_cfg.get("titlebar_name_type", None)
            titlebar_items = self.ks_printer_cfg.get("titlebar_items", None)
            if titlebar_items is not None:
                self.titlebar_items = [str(i.strip()) for i in titlebar_items.split(",")]
                logging.info(
                    f"Titlebar name type: {self.titlebar_name_type} items: {self.titlebar_items}"
                )
            else:
                self.titlebar_items = []
            hidden_sensors = self.ks_printer_cfg.get("hidden_sensors", None)
            if hidden_sensors is not None:
                ScreenPanel.hidden_sensors = [
                    str(i.strip()).lower() for i in hidden_sensors.split(",")
                ]
                logging.info(f"Hidden sensors: {self.hidden_sensors}")
            else:
                ScreenPanel.hidden_sensors = []
            low_limit = self.ks_printer_cfg.getfloat("spool_low_limit", fallback=None)
            if low_limit is None:
                low_limit = self._config.get_config().getfloat(
                    "spoolman", "spool_low_limit", fallback=20.0
                )
            self.spoolman_low_limit = low_limit
        else:
            self.titlebar_items = []
            ScreenPanel.hidden_sensors = []
            self.spoolman_low_limit = 20

    def show_update_dialog(self):
        if self.update_dialog is not None:
            return
        button = [{"name": _("Finish"), "response": Gtk.ResponseType.OK}]
        self.labels["update_progress"] = Gtk.Label(
            hexpand=True, vexpand=True, ellipsize=Pango.EllipsizeMode.END
        )
        self.labels["update_scroll"] = self._gtk.ScrolledWindow(steppers=False)
        self.labels["update_scroll"].set_property("overlay-scrolling", True)
        self.labels["update_scroll"].add(self.labels["update_progress"])
        self.labels["update_scroll"].connect("size-allocate", self._autoscroll)
        dialog = self._gtk.Dialog(
            _("Updating"), button, self.labels["update_scroll"], self.finish_updating
        )
        dialog.connect("delete-event", self.close_update_dialog)
        dialog.set_response_sensitive(Gtk.ResponseType.OK, False)
        dialog.get_widget_for_response(Gtk.ResponseType.OK).hide()
        dialog.close_blocked = True
        self.update_dialog = dialog
        self._screen.state.updating = True
        self._screen.base_panel.close_update_timer = GLib.timeout_add_seconds(
            300, self.unblock_update_dialog
        )

    def finish_updating(self, dialog, response_id):
        if response_id != Gtk.ResponseType.OK:
            return
        logging.info("Finishing update")
        if self.close_update_timer is not None:
            GLib.source_remove(self.close_update_timer)
            self.close_update_timer = None
        self._screen.state.updating = False
        self._gtk.remove_dialog(dialog)
        self._screen._menu_go_back(home=True)

    def unblock_update_dialog(self):
        if self.update_dialog is not None:
            self.update_dialog.close_blocked = False
        if self.close_update_timer is not None:
            GLib.source_remove(self.close_update_timer)
            self.close_update_timer = None
        return False

    def close_update_dialog(self, *args, force=False):
        if self.update_dialog is None:
            return
        if not force and self.update_dialog.close_blocked:
            logging.debug("Update dialog close is blocked by safety timer")
            return
        if self.close_update_timer is not None:
            GLib.source_remove(self.close_update_timer)
            self.close_update_timer = None
        logging.info("Closing update dialog")
        if self.update_dialog in self._screen.dialogs:
            self._screen.dialogs.remove(self.update_dialog)
        self._screen.state.updating = False
        self.update_dialog.destroy()
        self.update_dialog = None
        self._screen._menu_go_back(home=True)
