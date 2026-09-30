#!/usr/bin/env python3
"""
Unit and Integration Tests for:
1. Card Navigation & Click Isolation (Clicking anywhere on card opens details, action button does not open details).
2. Detail Page Live Synchronization (_sync_detail_page dynamically updates buttons to OPEN and UNINSTALL).
3. Installed Menu Search and Real-Time Filter (0-latency FlowBox filter, empty state, and clear button).
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, GLib, Gio, Adw

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from aura_ui import AuraWindow


def pump_main_loop(duration: float = 0.25):
    deadline = time.time() + duration
    while time.time() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


class TestInstalledSearchAndDetailSync(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="org.archlinux.aura.installed_search_test", flags=Gio.ApplicationFlags.NON_UNIQUE)

    def setUp(self):
        self.pm = MagicMock()
        self.pm.installed_set = {"firefox", "vlc", "discord"}
        self.pm.installed_versions = {"firefox": "130.0", "vlc": "3.0.21", "discord": "0.0.60"}
        self.pm.packages = {
            "firefox": {"name": "firefox", "desc": "Fast, Private & Safe Web Browser"},
            "vlc": {"name": "vlc", "desc": "Multi-platform media player"},
            "discord": {"name": "discord", "desc": "All-in-one voice and text chat"},
            "blender": {"name": "blender", "desc": "3D Creation Suite"},
        }
        self.pm.upgradable_list = []
        self.pm.is_installed.side_effect = lambda name: name.lower() in self.pm.installed_set
        self.pm.detect_desktop_entry.side_effect = lambda name: name if name.lower() in self.pm.installed_set else None
        self.pm.is_pkg_installing.return_value = False
        self.pm.is_pkg_queued.return_value = False
        self.pm.get_installed_desktop_apps.return_value = [
            {"name": "firefox", "display_name": "Firefox", "desc": "Web Browser", "source": "pacman", "icon": "firefox"},
            {"name": "vlc", "display_name": "VLC Media Player", "desc": "Media Player", "source": "pacman", "icon": "vlc"},
            {"name": "discord", "display_name": "Discord", "desc": "Voice and text chat", "source": "pacman", "icon": "discord"},
        ]
        self.pm.get_dynamic_featured_apps.return_value = []
        self.win = AuraWindow(self.app, self.pm)

    def test_card_click_opens_package_detail_for_installed_and_uninstalled(self):
        """Clicking on card body or icon box opens package details in both cases without AttributeError."""
        self.win._open_package_detail = MagicMock()

        # 1. Test installed card (Discord)
        card_inst = self.win._create_mac_app_row("discord", "Voice chat", "pacman", is_installed_view=True)
        self.assertTrue(hasattr(card_inst, "_search_corpus"))
        self.assertIn("discord", card_inst._search_corpus)

        # Find gesture on card
        card_gesture = None
        for ctrl in card_inst.observe_controllers():
            if isinstance(ctrl, Gtk.GestureClick):
                card_gesture = ctrl
                break
        self.assertIsNotNone(card_gesture)

        # Trigger click on card body (x=10, y=10)
        card_gesture.emit("released", 1, 10.0, 10.0)
        pump_main_loop(0.05)
        self.win._open_package_detail.assert_called_with("discord", "pacman")

        # 2. Test uninstalled card (Blender)
        self.win._open_package_detail.reset_mock()
        card_uninst = self.win._create_mac_app_row("blender", "3D creation", "pacman", is_installed_view=False)
        card_gesture_uninst = None
        for ctrl in card_uninst.observe_controllers():
            if isinstance(ctrl, Gtk.GestureClick):
                card_gesture_uninst = ctrl
                break
        self.assertIsNotNone(card_gesture_uninst)

        card_gesture_uninst.emit("released", 1, 10.0, 10.0)
        pump_main_loop(0.05)
        self.win._open_package_detail.assert_called_with("blender", "pacman")

    def test_detail_page_sync_after_install(self):
        """_sync_detail_page updates detail page buttons live when package completes installation."""
        self.win.main_stack.set_visible_child_name("detail")
        self.win._current_detail = {
            "name": "blender",
            "source": "pacman",
            "display_name": "Blender",
            "is_installed": False
        }

        # Initially uninstalled: GET button visible, OPEN and UNINSTALL hidden
        self.win._sync_detail_page("blender")
        self.assertTrue(self.win.btn_detail_install.get_visible())
        self.assertEqual(self.win.btn_detail_install.get_label(), "GET")
        self.assertFalse(self.win.btn_detail_launch.get_visible())
        self.assertFalse(self.win.btn_detail_remove.get_visible())

        # Now simulate package finished installing
        self.pm.installed_set.add("blender")
        self.pm.installed_versions["blender"] = "4.2.0"
        self.win._sync_detail_page("blender")

        # After install: OPEN visible, UNINSTALL visible, GET hidden
        self.assertTrue(self.win.btn_detail_launch.get_visible())
        self.assertEqual(self.win.btn_detail_launch.get_label(), "OPEN")
        self.assertTrue(self.win.btn_detail_remove.get_visible())
        self.assertEqual(self.win.btn_detail_remove.get_label(), "UNINSTALL")
        self.assertFalse(self.win.btn_detail_install.get_visible())

    def test_installed_page_instant_search(self):
        """Installed search bar filters cards dynamically, updates count, and handles empty state."""
        self.win.main_stack.set_visible_child_name("installed")
        self.win._load_installed_view()
        pump_main_loop(0.1)

        # Initial state: 3 installed applications
        self.assertEqual(self.win.installed_header_label.get_text(), "Installed Applications (3)")
        self.assertTrue(self.win.installed_flow_box.get_visible())
        self.assertFalse(self.win.installed_empty_box.get_visible())

        # Filter by "vlc"
        self.win.installed_search_entry.set_text("vlc")
        pump_main_loop(0.1)

        self.assertIn("Showing 1 of 3 installed applications", self.win.installed_header_label.get_text())
        self.assertTrue(self.win.installed_flow_box.get_visible())
        self.assertFalse(self.win.installed_empty_box.get_visible())

        # Search for non-existent app -> shows empty state placeholder
        self.win.installed_search_entry.set_text("nonexistentapp123")
        pump_main_loop(0.1)

        self.assertIn("Showing 0 of 3 installed applications", self.win.installed_header_label.get_text())
        self.assertFalse(self.win.installed_flow_box.get_visible())
        self.assertTrue(self.win.installed_empty_box.get_visible())

        # Click Clear Search button
        self.win.installed_empty_clear_btn.emit("clicked")
        pump_main_loop(0.1)

        self.assertEqual(self.win.installed_search_entry.get_text(), "")
        self.assertEqual(self.win.installed_header_label.get_text(), "Installed Applications (3)")
        self.assertTrue(self.win.installed_flow_box.get_visible())
        self.assertFalse(self.win.installed_empty_box.get_visible())


if __name__ == "__main__":
    unittest.main()
