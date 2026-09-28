import os
import unittest
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import gi
gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Gtk, Adw, Gdk

from aura_backend import PackageManager, ContainerManager, SnapManager, CacheManager, get_app_display_name
from aura_ui import AuraWindow


class TestStorageAndLifecycle(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Adw.Application(application_id="io.github.aura.test.lifecycle")

    def setUp(self):
        self.pm = MagicMock(spec=PackageManager)
        self.pm.cache_mgr = CacheManager()
        self.pm.container_mgr = ContainerManager()
        self.pm.snap_mgr = SnapManager()
        self.pm.is_passwordless_configured.return_value = True
        self.pm.check_updates.return_value = []
        self.pm.upgradable_list = []
        self.pm.get_installed_desktop_apps.return_value = []
        self.pm.search.return_value = []
        self.pm.detect_desktop_entry.return_value = None
        self.win = AuraWindow(self.app, self.pm)

    def test_vlc_display_name_and_desktop_entry_differentiation(self):
        """Verify vlc and vlc-cli are cleanly differentiated and do not duplicate as identical GUI cards."""
        self.assertEqual(get_app_display_name("vlc"), "VLC Media Player")
        self.assertEqual(get_app_display_name("vlc-cli"), "VLC (CLI)")
        self.assertEqual(get_app_display_name("vlc-git"), "VLC (Git)")
        self.assertEqual(get_app_display_name("obs-studio-git"), "OBS Studio (Git)")

        real_pm = PackageManager()
        # vlc-cli should NOT detect vlc.desktop
        entry = real_pm.detect_desktop_entry("vlc-cli")
        self.assertIsNone(entry)

    def test_cache_manager_scan_and_format(self):
        """Verify CacheManager accurately scans system caches and returns structured data."""
        cm = CacheManager()
        data = cm.scan_all_caches()
        self.assertIn("categories", data)
        self.assertIn("pacman", data["categories"])
        self.assertIn("aur", data["categories"])
        self.assertIn("docker", data["categories"])
        self.assertIn("journal", data["categories"])
        self.assertIn("aura", data["categories"])
        self.assertIn("total_reclaimable_str", data)
        self.assertIn("disk_free_str", data)
        self.assertGreaterEqual(data["disk_total_bytes"], 0)

        # Formatting tests
        self.assertEqual(cm.format_size(500), "500 B")
        self.assertEqual(cm.format_size(2048), "2 KB")
        self.assertEqual(cm.format_size(10 * 1024 * 1024), "10.0 MB")
        self.assertEqual(cm.format_size(2 * 1024 * 1024 * 1024), "2.00 GB")

    def test_storage_page_structure(self):
        """Verify storage page is properly built with segmented bar and category rows."""
        self.assertIsNotNone(self.win.storage_page)
        self.assertIsInstance(self.win.storage_page, Gtk.ScrolledWindow)
        self.assertIsNotNone(self.win.seg_pacman)
        self.assertIsNotNone(self.win.seg_aur)
        self.assertIsNotNone(self.win.seg_docker)
        self.assertIsNotNone(self.win.seg_journal)
        self.assertIsNotNone(self.win.seg_aura)
        self.assertIsNotNone(self.win.seg_free)
        self.assertIsNotNone(self.win.btn_clean_all_caches)

        # Trigger UI update with test data
        test_data = {
            "categories": {
                "pacman": {"name": "Pacman", "reclaimable_str": "1.2 GB", "total_str": "2.0 GB", "reclaimable_bytes": 1200000000, "icon": "package-x-generic-symbolic"},
                "aur": {"name": "AUR", "reclaimable_str": "500 MB", "total_str": "500 MB", "reclaimable_bytes": 500000000, "icon": "folder-download-symbolic"},
            },
            "total_reclaimable_str": "1.7 GB",
            "total_reclaimable_bytes": 1700000000,
            "disk_total_bytes": 100000000000,
            "disk_used_bytes": 50000000000,
            "disk_free_bytes": 50000000000,
            "disk_free_str": "50 GB",
            "disk_total_str": "100 GB",
        }
        self.win._update_storage_ui(test_data)
        self.assertIn("1.7 GB", self.win.lbl_reclaimable_total.get_text())
        self.assertTrue(self.win.btn_clean_all_caches.get_sensitive())

    def test_docker_stopped_status_handling(self):
        """Verify Docker view displays 'PAUSED' pill and 'Start Sandbox' when container is stopped."""
        with patch.object(self.pm.container_mgr, "get_status", return_value={
            "has_docker": True,
            "daemon_running": True,
            "container_exists": True,
            "container_running": False,
            "status_code": "stopped",
            "status_text": "Container: aura-box (Stopped)"
        }):
            self.win._load_containers_view(update_status=True)
            self.assertEqual(self.win.container_status_pill.get_text(), "PAUSED")
            self.assertEqual(self.win.btn_configure_container.get_label(), "Start Sandbox")

    def test_snap_auto_configure_banner(self):
        """Verify Snap view displays 'AUTO-SETUP' pill and 'Auto-Configure' button when snapd is missing/stopped."""
        with patch.object(self.pm.snap_mgr, "get_status", return_value={
            "has_snap": False,
            "socket_active": False,
            "symlink_ok": False,
            "status_code": "missing",
            "status_text": "Snapd is not installed on this system"
        }):
            self.win._load_snap_view()
            self.assertEqual(self.win.snap_status_pill.get_text(), "AUTO-SETUP")
            self.assertEqual(self.win.btn_configure_snap.get_label(), "Auto-Configure")

    def test_type_to_search(self):
        """Verify typing printable characters transfers keystroke to search and switches to browse view."""
        self.win.main_stack.set_visible_child_name("discover")
        self.win.browse_search_entry.set_text("")

        # Simulate typing 'f' (keyval for 'f' is 102)
        handled = self.win._on_window_key_pressed(None, 102, 0, Gdk.ModifierType(0))
        self.assertTrue(handled)
        self.assertEqual(self.win.main_stack.get_visible_child_name(), "browse")
        self.assertEqual(self.win.browse_search_entry.get_text(), "f")


if __name__ == "__main__":
    unittest.main()
