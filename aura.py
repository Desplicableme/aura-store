#!/usr/bin/env python3
"""
Aura Package Hub - Main Application Entrypoint
Ultra-fast, beautiful Arch Linux & AUR package manager with fuzzy search.
Prioritizes Pacman official repositories first, then Paru (AUR).
"""

import sys
import os
from pathlib import Path

# Add current directory to path
AURA_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(AURA_DIR))

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
from gi.repository import Adw, Gio, GLib

from aura_backend import PackageManager
from aura_ui import AuraWindow


class AuraApplication(Adw.Application):
    def __init__(self, initial_query: str = ""):
        super().__init__(
            application_id="io.github.aura",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )
        self.initial_query = initial_query
        self.pm = PackageManager()
        self.window = None
        self.connect("command-line", self.on_command_line)

    def do_activate(self):
        if not self.window:
            self.window = AuraWindow(self, self.pm)
            self.window.connect("close-request", lambda w: self.quit())
            if self.initial_query:
                self.window.search_entry.set_text(self.initial_query)
        self.window.present()

    def do_shutdown(self):
        PackageManager.clear_auth_cache()
        Adw.Application.do_shutdown(self)

    def on_command_line(self, app, command_line):
        args = command_line.get_arguments()
        query = ""
        action = None
        target_pkg = None

        if len(args) > 1:
            if args[1] in ["-h", "--help"]:
                print("Usage: aura [search-query] [--discover] [--updates|-u] [--installed] [--docker|-D] [--detail <pkg>] [--install <pkg>]")
                print("Aura App Store - Minimal, Modern App Store tailored for Hyprland")
                return 0
            elif args[1] in ["--discover"]:
                action = "discover"
            elif args[1] in ["-u", "--updates"]:
                action = "updates"
            elif args[1] in ["--installed"]:
                action = "installed"
            elif args[1] in ["--docker", "-D", "--containers", "-C"]:
                action = "docker"
            elif args[1] in ["--snap", "-S"]:
                action = "snap"
            elif args[1] in ["--storage", "--maintenance"]:
                action = "storage"
            elif args[1] in ["-m", "--maximize"]:
                action = "maximize"
            elif args[1] in ["-f", "--fullscreen"]:
                action = "fullscreen"
            elif args[1] in ["-c", "--category"] and len(args) > 2:
                action = "category"
                target_pkg = args[2]
            elif args[1] in ["--auth-dialog"]:
                action = "auth_dialog"
            elif args[1] in ["--scroll-bottom"]:
                action = "scroll_bottom"
            elif args[1] in ["-d", "--detail"] and len(args) > 2:
                action = "detail"
                target_pkg = args[2]
            elif args[1] in ["-i", "--install"] and len(args) > 2:
                pkg_name = args[2]
                print(f"[Aura] Direct install requested for: {pkg_name}")
                self.pm.execute_background_action("install", pkg_name, "pacman", lambda f, s: None, lambda ok, a, n, e: None)
                return 0
            else:
                query = " ".join(args[1:])

        if not self.window:
            self.window = AuraWindow(self, self.pm)
            self.window.connect("close-request", lambda w: self.quit())

        if action == "discover":
            self.window.back_btn.set_visible(False)
            target = self.window.sidebar_buttons.get("discover")
            if target:
                target.set_active(True)
            self.window._on_sidebar_channel_click("discover")
        elif action == "updates":
            self.window.back_btn.set_visible(False)
            target = self.window.sidebar_buttons.get("updates")
            if target:
                target.set_active(True)
            self.window._on_sidebar_channel_click("updates")
        elif action == "installed":
            self.window.back_btn.set_visible(False)
            target = self.window.sidebar_buttons.get("installed")
            if target:
                target.set_active(True)
            self.window._on_sidebar_channel_click("installed")
        elif action in ("docker", "containers"):
            self.window.back_btn.set_visible(False)
            target = self.window.sidebar_buttons.get("docker") or self.window.sidebar_buttons.get("containers")
            if target:
                target.set_active(True)
            self.window._on_sidebar_channel_click("docker")
        elif action == "snap":
            self.window.back_btn.set_visible(False)
            target = self.window.sidebar_buttons.get("snap")
            if target:
                target.set_active(True)
            self.window._on_sidebar_channel_click("snap")
        elif action == "storage":
            self.window.back_btn.set_visible(False)
            target = self.window.sidebar_buttons.get("storage")
            if target:
                target.set_active(True)
            self.window._on_sidebar_channel_click("storage")
        elif action == "maximize":
            self.window.maximize()
        elif action == "fullscreen":
            self.window.fullscreen()
        elif action == "category" and target_pkg:
            self.window._open_category(target_pkg)
        elif action == "detail" and target_pkg:
            self.window._open_package_detail(target_pkg, "pacman")
        elif action == "auth_dialog":
            self.window._show_password_config_dialog()
        elif action == "scroll_bottom":
            def _scroll():
                adj = self.window.detail_page.get_vadjustment()
                if adj:
                    adj.set_value(adj.get_upper())
                return False
            GLib.timeout_add(300, _scroll)
        elif query:
            self.window.back_btn.set_visible(False)
            self.window.search_entry.set_text(query)
            self.window._trigger_search(query)

        self.window.present()
        return 0


def main():
    initial_q = ""
    if len(sys.argv) > 1 and not sys.argv[1].startswith("-"):
        initial_q = " ".join(sys.argv[1:])

    app = AuraApplication(initial_query=initial_q)
    return app.run(sys.argv)


if __name__ == "__main__":
    sys.exit(main())
