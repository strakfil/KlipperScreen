import logging
import re

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango

from ks_includes.quick_macros import ICON_CHOICES, MAX_QUICK, QuickMacros, pretty_name
from ks_includes.screen_panel import ScreenPanel

PARAM_PATTERN = re.compile(
    r"params\.(?P<param>[a-zA-Z0-9_]+)"
    r"(?:\s*\|\s*default\(\s*(?P<default>[^\)]+)\s*\))?"
    r"(?:\s*\|\s*(?P<type_hint>[a-zA-Z]+))?"
)


class Panel(ScreenPanel):
    """Macros list with Control-Center-style editing:
    - normal mode: tap ▶ to run (always confirmed while printing)
    - edit mode: ⊕/⊖ pin to Home (max 4), arrows reorder, pencil renames / picks an icon
    """

    def __init__(self, screen, title):
        title = title or _("Macros")
        super().__init__(screen, title)
        self.qm = QuickMacros(screen)
        self.sort_reverse = False
        self.editing = False
        self.options = {}
        self.macros = {}
        self.menu = ["macros_menu"]
        self.macros_dirty = True
        self.icon_size = self._gtk.font_size * 1.6
        self.ctl_size = self._gtk.font_size * 1.4

        self.sort_btn = self._gtk.Button(
            "arrow-up", _("Name"), "color1", self.bts, Gtk.PositionType.RIGHT, 1
        )
        self.sort_btn.connect("clicked", self.change_sort)
        self.sort_btn.set_hexpand(True)
        self.sort_btn.get_style_context().add_class("buttons_slim")

        self.edit_btn = self._gtk.Button("edit", _("Edit"), "color4", self.bts, Gtk.PositionType.LEFT, 1)
        self.edit_btn.connect("clicked", self.toggle_edit)
        self.edit_btn.get_style_context().add_class("buttons_slim")
        self.edit_btn.get_style_context().add_class("tinted")

        adjust = self._gtk.Button(
            "settings", " " + _("Settings"), "color2", self.bts, Gtk.PositionType.LEFT, 1
        )
        adjust.get_style_context().add_class("buttons_slim")
        adjust.connect("clicked", self.load_menu, "options", _("Settings"))
        adjust.set_hexpand(False)

        sbox = Gtk.Box(vexpand=False)
        sbox.pack_start(self.sort_btn, True, True, 5)
        sbox.pack_start(self.edit_btn, True, True, 5)
        sbox.pack_start(adjust, True, True, 5)

        self.labels["macros_list"] = self._gtk.ScrolledWindow()
        self.labels["macros"] = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.labels["macros_list"].add(self.labels["macros"])

        self.labels["macros_menu"] = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.labels["macros_menu"].pack_start(sbox, False, False, 0)
        self.labels["macros_menu"].pack_start(self.labels["macros_list"], True, True, 0)

        self.content.add(self.labels["macros_menu"])
        self.labels["options_menu"] = self._gtk.ScrolledWindow()
        self.labels["options"] = Gtk.Grid()
        self.labels["options_menu"].add(self.labels["options"])

        # "Edit macro" view (rename + icon), filled per macro in open_editor()
        self.labels["edit_menu"] = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self.edit_target = None
        self.edit_icon = None
        self.icon_buttons = {}

    # ---------------------------------------------------------------- lifecycle
    def activate(self):
        self.macros_dirty = True
        self.reload_macros()

    def macro_visibility_changed(self, active):
        self.macros_dirty = True

    def back(self):
        if len(self.menu) > 1:
            self._screen.remove_keyboard()
            self.unload_menu()
            self.macros_dirty = True
            self.reload_macros()
            return True
        if self.editing:  # Back leaves edit mode first, like "Done"
            self.toggle_edit()
            return True
        return False

    # ---------------------------------------------------------------- building
    def _macro_section(self, macro):
        return self._printer.config.get(f"gcode_macro {macro}", None)

    def _description(self, macro):
        section = self._macro_section(macro) or {}
        desc = section.get("description", "") if hasattr(section, "get") else ""
        if desc and desc.strip().lower() != "g-code macro":
            return desc.strip()
        return ""

    def _section_header(self, text):
        label = Gtk.Label(label=text, halign=Gtk.Align.START, xalign=0)
        label.get_style_context().add_class("section-header")
        return label

    def _footnote(self, text):
        label = Gtk.Label(label=text, halign=Gtk.Align.START, xalign=0, wrap=True)
        label.get_style_context().add_class("secondary")
        label.get_style_context().add_class("section-footnote")
        return label

    def _icon_button(self, icon, role, callback, *args, sensitive=True, tooltip=None):
        btn = Gtk.Button()
        btn.set_image(self._gtk.Image(icon, self.ctl_size, self.ctl_size, role=role))
        btn.set_always_show_image(True)
        btn.get_style_context().add_class("plain")
        btn.get_style_context().add_class("icon-button")
        btn.set_sensitive(sensitive)
        if tooltip:
            btn.set_tooltip_text(tooltip)
        btn.connect("clicked", callback, *args)
        return btn

    def _params(self, macro):
        """Entries for `params.X | default(Y) | type` found in the macro body."""
        section = self._macro_section(macro)
        params = {}
        if not section or "gcode" not in section:
            return params
        for line in section["gcode"].split("\n"):
            if not (line.startswith("{") and "params." in line):
                continue
            result = re.search(PARAM_PATTERN, line)
            if not result:
                continue
            result = result.groupdict()
            entry = Gtk.Entry(placeholder_text=result.get("default") or "")
            type_hint = result.get("type_hint") or ""
            if type_hint == "int":
                entry.set_input_purpose(Gtk.InputPurpose.DIGITS)
            elif type_hint == "float":
                entry.set_input_purpose(Gtk.InputPurpose.NUMBER)
            else:
                entry.set_input_purpose(Gtk.InputPurpose.ALPHA)
            entry.connect("touch-event", self.show_keyboard)
            entry.connect("button-press-event", self.show_keyboard)
            entry.connect("focus-out-event", self._screen.remove_keyboard)
            params[result["param"]] = entry
        return params

    def build_row(self, macro, pinned=False, index=0, count=0, can_pin=True, hidden=False):
        section = self._macro_section(macro)
        if not section or "gcode" not in section or "rename_existing" in section:
            logging.debug(f"Skipping macro {macro}")
            return None

        icon = self._gtk.Image(self.qm.icon(macro), self.icon_size, self.icon_size, role="accent")
        icon.set_valign(Gtk.Align.CENTER)

        name = Gtk.Label(hexpand=True, halign=Gtk.Align.START, xalign=0, wrap=True,
                         wrap_mode=Pango.WrapMode.WORD_CHAR)
        name.set_markup(f"<b>{GLib.markup_escape_text(self.qm.display_name(macro))}</b>")
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, valign=Gtk.Align.CENTER, hexpand=True)
        labels.add(name)
        secondary = self._description(macro)
        if self.qm.custom_name(macro):
            secondary = f"{macro} · {secondary}" if secondary else macro
        if secondary:
            sub = Gtk.Label(label=secondary, halign=Gtk.Align.START, xalign=0, wrap=True,
                            wrap_mode=Pango.WrapMode.WORD_CHAR)
            sub.get_style_context().add_class("secondary")
            labels.add(sub)

        row = Gtk.Box(spacing=8)
        row.get_style_context().add_class("frame-item")
        row.get_style_context().add_class("macro-row")
        info = {"row": row, "params": {}}

        if self.editing and hidden:
            row.add(self._icon_button("visible", "accent", self.unhide, macro, tooltip=_("Show")))
            row.add(icon)
            row.add(labels)
            row.get_style_context().add_class("macro-row-hidden")
        elif self.editing:
            if pinned:
                row.add(self._icon_button("decrease", "red", self.unpin, macro,
                                          tooltip=_("Remove from Home")))
            else:
                row.add(self._icon_button("increase", "green", self.pin, macro, sensitive=can_pin,
                                          tooltip=_("Add to Home")))
            row.add(icon)
            row.add(labels)
            if pinned:
                row.add(self._icon_button("arrow-up", "label", self.move, macro, -1, sensitive=index > 0))
                row.add(self._icon_button("arrow-down", "label", self.move, macro, 1,
                                          sensitive=index < count - 1))
            row.add(self._icon_button("edit", "accent", self.open_editor, macro, tooltip=_("Rename / icon")))
            row.add(self._icon_button("hidden", "label2", self.hide, macro, tooltip=_("Hide")))
        else:
            row.add(icon)
            info["params"] = self._params(macro)
            for param, entry in info["params"].items():
                labels.add(Gtk.Label(label=param, halign=Gtk.Align.START))
                labels.add(entry)
            row.add(labels)
            run = self._icon_button("resume", "accent", self.run_gcode_macro, macro, tooltip=_("Run"))
            run.get_style_context().remove_class("plain")
            run.get_style_context().add_class("run-button")
            run.set_halign(Gtk.Align.END)
            run.set_valign(Gtk.Align.CENTER)
            row.add(run)

        self.macros[macro] = info
        return row

    def reload_macros(self):
        if not self.macros_dirty:
            return False
        self.macros_dirty = False
        box = self.labels["macros"]
        for child in box.get_children():
            box.remove(child)
        self.macros = {}
        self.options = {}
        self.labels["options"].remove_column(0)
        self.load_gcode_macros()
        return False

    def load_gcode_macros(self):
        visible = []
        self.hidden_macros = []
        for macro in self._printer.get_gcode_macros():
            self.options[macro] = {
                "name": macro,
                "section": f"displayed_macros {self._screen.state.printer_name}",
                "type": "binary",
                "callback": self.macro_visibility_changed,
            }
            if self._config.get_config().getboolean(
                self.options[macro]["section"], macro.lower(), fallback=True
            ):
                visible.append(macro)
            else:
                self.hidden_macros.append(macro)
        for macro in list(self.options):
            self.add_option("options", self.options, macro, self.options[macro])

        box = self.labels["macros"]
        pinned = self.qm.order(visible)
        others = sorted([m for m in visible if m not in pinned], reverse=self.sort_reverse, key=str.casefold)
        can_pin = len(pinned) < MAX_QUICK

        box.add(self._section_header(_("ON HOME") + f" · {len(pinned)}/{MAX_QUICK}"))
        if not pinned:
            box.add(self._footnote(_("No macros on Home yet. Tap Edit to add some.")))
        for i, macro in enumerate(pinned):
            row = self.build_row(macro, pinned=True, index=i, count=len(pinned))
            if row:
                box.add(row)
        if self.editing:
            box.add(self._footnote(
                _("Home holds at most 4 macros. Remove one before adding another.") if not can_pin
                else _("Use the arrows to change the order on Home.")))

        box.add(self._section_header(_("ADD MACRO") if self.editing else _("OTHER MACROS")))
        for macro in others:
            row = self.build_row(macro, can_pin=can_pin)
            if row:
                box.add(row)
        if self.editing and self.hidden_macros:
            box.add(self._section_header(_("HIDDEN MACROS")))
            for macro in sorted(self.hidden_macros, key=str.casefold):
                row = self.build_row(macro, hidden=True)
                if row:
                    box.add(row)
            box.add(self._footnote(_("Hidden macros are not listed and cannot be on Home.")))
        box.show_all()

    # ---------------------------------------------------------------- actions
    def toggle_edit(self, widget=None):
        self.editing = not self.editing
        self._screen.remove_keyboard()
        self.edit_btn.set_label(_("Done") if self.editing else _("Edit"))
        self.macros_dirty = True
        GLib.idle_add(self.reload_macros)

    def _changed(self):
        self.macros_dirty = True
        GLib.idle_add(self.reload_macros)

    def pin(self, widget, macro):
        if not self.qm.pin(macro):
            self._screen.show_popup_message(_("Home holds at most 4 macros"), 1)
        self._changed()

    def unpin(self, widget, macro):
        self.qm.unpin(macro)
        self._changed()

    def move(self, widget, macro, delta):
        self.qm.move(macro, delta)
        self._changed()

    def _set_visible(self, macro, visible):
        section = f"displayed_macros {self._screen.state.printer_name}"
        config = self._config.get_config()
        if section not in config.sections():
            config.add_section(section)
        config.set(section, macro.lower(), "True" if visible else "False")
        self._config.save_user_config_options()

    def hide(self, widget, macro):
        if self.qm.is_pinned(macro):
            self.qm.unpin(macro)
        self._set_visible(macro, False)
        self._changed()

    def unhide(self, widget, macro):
        self._set_visible(macro, True)
        self._changed()

    def run_gcode_macro(self, widget, macro):
        params = ""
        for param, entry in self.macros[macro]["params"].items():
            entry.set_sensitive(False)  # Move focus to prevent
            entry.set_sensitive(True)  # reopening the osk
            value = entry.get_text()
            if value:
                if re.findall(r"[G|M]\d{1,3}", macro):
                    params += f" {param}{value}"
                else:
                    params += f" {param}={value}"
        self.qm.run(macro, params, widget)

    def change_sort(self, widget):
        self.sort_reverse ^= True
        icon = "arrow-down" if self.sort_reverse else "arrow-up"
        self.sort_btn.set_image(self._gtk.Image(icon, self._gtk.img_scale * self.bts))
        self.sort_btn.show()
        self._changed()

    def show_keyboard(self, entry, event):
        self._screen.show_keyboard(entry, event)
        GLib.timeout_add(100, self.scroll_to_entry, entry)

    def scroll_to_entry(self, entry):
        self.labels["macros_list"].get_vadjustment().set_value(entry.get_allocation().y - 50)

    # ---------------------------------------------------------------- editor
    def open_editor(self, widget, macro):
        self.edit_target = macro
        self.edit_icon = self.qm.icon(macro)
        view = self.labels["edit_menu"]
        for child in view.get_children():
            view.remove(child)

        view.add(self._section_header(_("NAME")))
        self.name_entry = Gtk.Entry(hexpand=True)
        self.name_entry.set_text(self.qm.display_name(macro))
        self.name_entry.set_placeholder_text(pretty_name(macro))
        self.name_entry.connect("touch-event", self._screen.show_keyboard)
        self.name_entry.connect("button-press-event", self._screen.show_keyboard)
        self.name_entry.connect("activate", self.save_editor)
        view.add(self.name_entry)
        view.add(self._footnote(_("Macro in Klipper: %s") % macro))

        view.add(self._section_header(_("ICON")))
        grid = Gtk.Grid(row_homogeneous=True, column_homogeneous=True, row_spacing=4, column_spacing=4)
        self.icon_buttons = {}
        cols = 8
        size = self._gtk.font_size * 1.5
        for i, icon in enumerate(ICON_CHOICES):
            btn = Gtk.Button()
            btn.set_image(self._gtk.Image(icon, size, size, role="label"))
            btn.set_always_show_image(True)
            btn.get_style_context().add_class("icon-choice")
            if icon == self.edit_icon:
                btn.get_style_context().add_class("selected")
            btn.connect("clicked", self.pick_icon, icon)
            self.icon_buttons[icon] = btn
            grid.attach(btn, i % cols, i // cols, 1, 1)
        scroll = self._gtk.ScrolledWindow()
        scroll.add(grid)
        view.pack_start(scroll, True, True, 0)

        actions = Gtk.Box(spacing=8, homogeneous=True)
        reset = self._gtk.Button(None, _("Reset to default"), "destructive")
        reset.connect("clicked", self.reset_editor)
        save = self._gtk.Button(None, _("Save"), "primary")
        save.connect("clicked", self.save_editor)
        for btn in (reset, save):
            btn.set_vexpand(False)
            actions.add(btn)
        view.pack_start(actions, False, False, 0)
        self.load_menu(None, "edit", self.qm.display_name(macro))

    def pick_icon(self, widget, icon):
        self.icon_buttons[self.edit_icon].get_style_context().remove_class("selected")
        self.edit_icon = icon
        widget.get_style_context().add_class("selected")

    def save_editor(self, widget=None):
        self.qm.set_name(self.edit_target, self.name_entry.get_text())
        self.qm.set_icon(self.edit_target, self.edit_icon)
        self.back()

    def reset_editor(self, widget=None):
        self.qm.reset(self.edit_target)
        self.back()
