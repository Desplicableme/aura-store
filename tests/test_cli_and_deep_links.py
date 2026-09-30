import unittest
from unittest.mock import MagicMock, patch
import sys
from pathlib import Path

# Ensure aura directory is in sys.path
AURA_DIR = Path(__file__).resolve().parent.parent
if str(AURA_DIR) not in sys.path:
    sys.path.insert(0, str(AURA_DIR))

from aura import AuraApplication


class TestCliAndDeepLinks(unittest.TestCase):
    def setUp(self):
        self.app = AuraApplication()
        self.app.pm = MagicMock()
        self.app.hold = MagicMock()
        self.app.release = MagicMock()
        self.app.window = MagicMock()

    def test_cli_install_resolves_source_and_holds_lifecycle(self):
        self.app.pm.resolve_package_source.return_value = "aur"
        cmd = MagicMock()
        cmd.get_arguments.return_value = ["aura", "-i", "spotify"]

        ret = self.app.do_command_line(cmd)
        self.assertEqual(ret, 0)
        self.app.pm.resolve_package_source.assert_called_with("spotify")
        self.app.hold.assert_called_once()
        self.assertTrue(self.app.pm.execute_background_action.called)

        action, pkg, src, prog_cb, done_cb = self.app.pm.execute_background_action.call_args[0]
        self.assertEqual(action, "install")
        self.assertEqual(pkg, "spotify")
        self.assertEqual(src, "aur")

        # Test callbacks
        prog_cb(0.75, "Building package")
        done_cb(True, "install", "spotify", "")
        self.app.release.assert_called_once()

    def test_cli_install_missing_arg(self):
        cmd = MagicMock()
        cmd.get_arguments.return_value = ["aura", "--install"]
        ret = self.app.do_command_line(cmd)
        self.assertEqual(ret, 1)

    def test_cli_detail_resolves_source(self):
        self.app.pm.resolve_package_source.return_value = "snap"
        cmd = MagicMock()
        cmd.get_arguments.return_value = ["aura", "--detail", "code"]

        ret = self.app.do_command_line(cmd)
        self.assertEqual(ret, 0)
        self.app.pm.resolve_package_source.assert_called_with("code")
        self.app.window._open_package_detail.assert_called_with("code", "snap")

    def test_cli_detail_missing_arg(self):
        cmd = MagicMock()
        cmd.get_arguments.return_value = ["aura", "-d"]
        ret = self.app.do_command_line(cmd)
        self.assertEqual(ret, 1)

    def test_appstream_deep_link(self):
        self.app.pm.resolve_package_source.return_value = "pacman"
        cmd = MagicMock()
        cmd.get_arguments.return_value = ["aura", "appstream://org.gnome.Calculator"]

        ret = self.app.do_command_line(cmd)
        self.assertEqual(ret, 0)
        self.app.pm.resolve_package_source.assert_called_with("org.gnome.Calculator")
        self.app.window._open_package_detail.assert_called_with("org.gnome.Calculator", "pacman")

    def test_desktop_placeholder_percent_u(self):
        cmd = MagicMock()
        cmd.get_arguments.return_value = ["aura", "%U"]

        ret = self.app.do_command_line(cmd)
        self.assertEqual(ret, 0)
        self.app.window._trigger_search.assert_not_called()

    def test_aura_deep_link_updates(self):
        self.app.window.sidebar_buttons = {"updates": MagicMock()}
        cmd = MagicMock()
        cmd.get_arguments.return_value = ["aura", "aura://updates"]

        ret = self.app.do_command_line(cmd)
        self.assertEqual(ret, 0)
        self.app.window._on_sidebar_channel_click.assert_called_with("updates")

    def test_unknown_option_flag_not_treated_as_search(self):
        cmd = MagicMock()
        cmd.get_arguments.return_value = ["aura", "--unknown-flag"]

        ret = self.app.do_command_line(cmd)
        self.assertEqual(ret, 1)
        self.app.window._trigger_search.assert_not_called()

    def test_valid_search_query(self):
        cmd = MagicMock()
        cmd.get_arguments.return_value = ["aura", "firefox"]

        ret = self.app.do_command_line(cmd)
        self.assertEqual(ret, 0)
        self.app.window._trigger_search.assert_called_with("firefox")


if __name__ == "__main__":
    unittest.main()
