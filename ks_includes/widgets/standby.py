"""StandBy screen saver (Apple-style): big clock when idle, print progress while printing.

Shown inside the screen saver's full-screen button, so any tap still wakes the screen.
Always on black; with the dark appearance (night) everything turns dim red like iOS StandBy.
Content shifts by a few pixels every minute.
"""
import logging
from datetime import datetime, timedelta
from math import pi

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk

CS_DAYS = ("pondělí", "úterý", "středa", "čtvrtek", "pátek", "sobota", "neděle")
CS_MONTHS = ("ledna", "února", "března", "dubna", "května", "června", "července", "srpna",
             "září", "října", "listopadu", "prosince")
SHIFTS = ((0, 0), (6, 4), (-6, 6), (4, -6), (-4, -4))


def format_remaining(seconds):
    seconds = max(0, int(seconds))
    hours, minutes = seconds // 3600, (seconds % 3600) // 60
    if hours:
        return f"{hours} h {minutes:02d} min"
    return f"{max(minutes, 1)} min"


class StandBy:
    def __init__(self, screen):
        self.screen = screen
        self.printer = screen.printer
        self.timer = None
        self.tick_count = 0
        self.progress = 0.0
        self.night = False
        self.done = False

        self.root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True,
                            halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
        self.root.get_style_context().add_class("standby")

        # idle: clock + date
        self.clock = Gtk.Label()
        self.clock.get_style_context().add_class("standby-clock")
        self.date = Gtk.Label()
        self.date.get_style_context().add_class("standby-secondary")
        self.idle_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.idle_box.add(self.clock)
        self.idle_box.add(self.date)

        # printing: ring + remaining time
        size = int(min(screen.width, screen.height) * 0.5)
        self.ring = Gtk.DrawingArea(width_request=size, height_request=size)
        self.ring.connect("draw", self.draw_ring)
        self.percent = Gtk.Label()
        self.percent.get_style_context().add_class("standby-percent")
        ring_overlay = Gtk.Overlay()
        ring_overlay.add(self.ring)
        ring_overlay.add_overlay(self.percent)

        self.state_label = Gtk.Label(halign=Gtk.Align.START)
        self.state_label.get_style_context().add_class("standby-secondary")
        self.remaining = Gtk.Label(halign=Gtk.Align.START)
        self.remaining.get_style_context().add_class("standby-remaining")
        self.filename = Gtk.Label(halign=Gtk.Align.START, ellipsize=3, max_width_chars=22)
        self.filename.get_style_context().add_class("standby-file")
        self.eta = Gtk.Label(halign=Gtk.Align.START)
        self.eta.get_style_context().add_class("standby-secondary")
        info = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2, valign=Gtk.Align.CENTER)
        for widget in (self.state_label, self.remaining, self.filename, self.eta):
            info.add(widget)
        self.print_box = Gtk.Box(spacing=int(screen.width * 0.05))
        self.print_box.add(ring_overlay)
        self.print_box.add(info)

        self.root.add(self.idle_box)
        self.root.add(self.print_box)

    # ------------------------------------------------------------------ lifecycle
    def start(self):
        self.update()
        self.root.show_all()
        self._apply_mode()
        if self.timer is None:
            self.timer = GLib.timeout_add_seconds(5, self.update)

    def stop(self):
        if self.timer is not None:
            GLib.source_remove(self.timer)
            self.timer = None

    # ------------------------------------------------------------------ data
    def _printing(self):
        return self.printer is not None and self.printer.state in ("printing", "paused")

    def _finished(self):
        """Last print completed (Klipper keeps 'complete' until the next print / file reset)."""
        return (self.printer is not None and not self._printing()
                and self.printer.get_stat("print_stats", "state") == "complete")

    def _progress_and_remaining(self):
        stat = self.printer.get_stat
        # Prefer the print status screen's estimate (slicer / filament / file, per settings)
        job = self.screen.panels.get("job_status") if hasattr(self.screen, "panels") else None
        estimate = getattr(job, "last_estimate", None)
        if estimate and estimate.get("total", 0) > 1:
            elapsed = float(stat("print_stats", "print_duration") or 0)
            total = estimate["total"]
            return min(max(elapsed / total, 0), 1), max(total - elapsed, 0)
        progress = stat("display_status", "progress") or stat("virtual_sdcard", "progress") or 0
        duration = stat("print_stats", "print_duration") or 0
        remaining = None
        if progress and progress > 0.005 and duration:
            remaining = duration / progress - duration
        return float(progress), remaining

    def _date_text(self, now):
        if self.screen._config.get_main_config().get("language", "").startswith("cs"):
            return f"{CS_DAYS[now.weekday()]} {now.day}. {CS_MONTHS[now.month - 1]}"
        return now.strftime("%A %d %B")

    def update(self):
        try:
            now = datetime.now()
            use_24h = self.screen._config.get_main_config().getboolean("24htime", True)
            clock = now.strftime("%H:%M") if use_24h else now.strftime("%I:%M").lstrip("0")
            self.clock.set_label(clock)
            self.date.set_label(self._date_text(now))
            self.done = False
            if self._printing():
                self.progress, remaining = self._progress_and_remaining()
                paused = self.printer.state == "paused"
                self.state_label.set_label(_("Paused") if paused else _("Remaining"))
                self.remaining.set_label(format_remaining(remaining) if remaining is not None else "—")
                name = self.printer.get_stat("print_stats", "filename") or ""
                self.filename.set_label(name.rsplit("/", 1)[-1].rsplit(".", 1)[0])
                if remaining is not None:
                    done = now + timedelta(seconds=remaining)
                    self.eta.set_label(_("Done at %s") % done.strftime("%H:%M") + f"  ·  {clock}")
                else:
                    self.eta.set_label(clock)
                self.percent.set_label(f"{self.progress * 100:.0f} %")
                self.ring.queue_draw()
            elif self._finished():
                self.done = True
                self.progress = 1.0
                self.state_label.set_label(_("Print finished"))
                self.remaining.set_label(_("Done"))
                name = self.printer.get_stat("print_stats", "filename") or ""
                self.filename.set_label(name.rsplit("/", 1)[-1].rsplit(".", 1)[0])
                self.eta.set_label(clock)
                self.percent.set_label("✓")
                self.ring.queue_draw()
            self._apply_mode()
            self.tick_count += 1
            if self.tick_count % 12 == 0:  # every minute: tiny shift against image retention
                dx, dy = SHIFTS[(self.tick_count // 12) % len(SHIFTS)]
                self.root.set_margin_start(max(0, dx) * 2)
                self.root.set_margin_end(max(0, -dx) * 2)
                self.root.set_margin_top(max(0, dy) * 2)
                self.root.set_margin_bottom(max(0, -dy) * 2)
        except Exception as e:
            logging.exception(f"StandBy update failed: {e}")
        return True

    def _apply_mode(self):
        printing = self._printing() or self._finished()
        self.idle_box.set_visible(not printing)
        self.print_box.set_visible(printing)
        appearance = getattr(self.screen, "appearance", None)
        night_red = self.screen._config.get_main_config().getboolean("standby_night_red", True)
        self.night = bool(night_red and appearance is not None and appearance.current == "dark")
        ctx = self.root.get_style_context()
        (ctx.add_class if self.night else ctx.remove_class)("standby-night")

    # ------------------------------------------------------------------ drawing
    def draw_ring(self, area, ctx):
        w, h = area.get_allocated_width(), area.get_allocated_height()
        line = min(w, h) * 0.09
        r = min(w, h) / 2 - line
        if self.night:
            track, fill = (1.0, 0.27, 0.23, 0.22), (1.0, 0.27, 0.23, 0.85)
        else:
            palette = self.screen.gtk.icon_palette or {}
            acc = "#30D158" if self.done else palette.get("accent", "#0A84FF")
            fill = tuple(int(acc[i:i + 2], 16) / 255 for i in (1, 3, 5)) + (1,)
            track = (0.46, 0.46, 0.5, 0.3)
        ctx.set_line_width(line)
        ctx.set_line_cap(1)  # round
        ctx.translate(w / 2, h / 2)
        ctx.set_source_rgba(*track)
        ctx.arc(0, 0, r, 0, 2 * pi)
        ctx.stroke()
        if self.progress > 0:
            ctx.set_source_rgba(*fill)
            ctx.arc(0, 0, r, -pi / 2, -pi / 2 + self.progress * 2 * pi)
            ctx.stroke()
