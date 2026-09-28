#!/usr/bin/env python3
import sys
import os
import re
import unittest

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from aura_backend import PacmanProgressParser, PackageManager, APP_DISPLAY_NAMES


class TestPacmanProgressParser(unittest.TestCase):

    def test_download_clamping_and_ratio_format(self):
        """
        Bug 1: downloaded_count was incremented every time ANY line matched 'downloading' or '.pkg.tar.',
        growing to 19 while total_packages was 6 (ratio 19/6).
        Requirement: NEVER produce a ratio (X/Y) where X > Y. dl_count must be clamped min(dl_count, total_packages)
        and use 'of' formatting: e.g. (1 of 6).
        """
        parser = PacmanProgressParser(action="update", is_system_upgrade=True)
        parser.process_line("Packages (6) bash linux nss papirus-icon-theme ttf-font-awesome firefox")
        self.assertEqual(parser.total_packages, 6)

        # Simulate 19 download lines for 6 packages
        lines = [
            ":: Retrieving packages...",
            "downloading bash-5.2.037-1-x86_64.pkg.tar.zst (10%)...",
            "downloading bash-5.2.037-1-x86_64.pkg.tar.zst (50%)...",
            "downloading bash-5.2.037-1-x86_64.pkg.tar.zst (100%)...",
            "downloading linux-6.11.arch1-1-x86_64.pkg.tar.zst...",
            "downloading nss-3.104-1-x86_64.pkg.tar.zst...",
            "downloading papirus-icon-theme-20240501-1-any.pkg.tar.zst...",
            "downloading ttf-font-awesome-6.6.0-1-any.pkg.tar.zst...",
            "downloading firefox-130.0.1-1-x86_64.pkg.tar.zst...",
            # Extra chunks and lines
            "bash-5.2.037-1-x86_64.pkg.tar.zst is up to date",
            "linux-6.11.arch1-1-x86_64.pkg.tar.zst is up to date",
            "downloading extra1-1.0-1-x86_64.pkg.tar.zst...",
            "downloading extra2-1.0-1-x86_64.pkg.tar.zst...",
            "downloading extra3-1.0-1-x86_64.pkg.tar.zst...",
            "downloading extra4-1.0-1-x86_64.pkg.tar.zst...",
            "downloading extra5-1.0-1-x86_64.pkg.tar.zst...",
            "downloading extra6-1.0-1-x86_64.pkg.tar.zst...",
            "downloading extra7-1.0-1-x86_64.pkg.tar.zst...",
            "downloading extra8-1.0-1-x86_64.pkg.tar.zst...",
        ]

        for line in lines:
            res = parser.process_line(line)
            if res:
                prog, msg, cur_p, cur_i, tot_p = res
                # Check formatting: NO slashes like (19/6) in download message
                self.assertNotIn("/", msg.split("...")[-1])  # Ensure no slash ratio
                match = re.search(r'\((\d+)\s+of\s+(\d+)\)', msg)
                if match:
                    cur = int(match.group(1))
                    tot = int(match.group(2))
                    self.assertLessEqual(cur, tot, f"X > Y detected in ratio: {msg}")
                    self.assertEqual(tot, 6)
                # Check download progress strictly between 0.08 and 0.28
                self.assertGreaterEqual(prog, 0.08)
                self.assertLessEqual(prog, 0.28)

    def test_no_premature_jump_to_96(self):
        """
        Bug 2: Matching 'icon', 'font', 'mime', 'desktop file' during the transaction caused
        premature jump to 0.96.
        Requirement: ONLY enter hooks/finalizing stage (0.90 to 0.98) if pacman has EXPLICITLY outputted
        ':: Running post-transaction hooks...' (in_post_hooks = True).
        """
        parser = PacmanProgressParser(action="update", is_system_upgrade=True)
        parser.process_line("Packages (6) linux papirus-icon-theme ttf-font-awesome shared-mime-info systemd firefox")

        # 1. Package with 'icon' in name
        r_icon = parser.process_line("(2/6) installing papirus-icon-theme")
        self.assertIsNotNone(r_icon)
        self.assertLess(r_icon[0], 0.55, f"Progress jumped prematurely to {r_icon[0]}!")
        self.assertFalse(parser.in_post_hooks)
        self.assertIn("Papirus Icon Theme", r_icon[1])

        # 2. Package with 'font' in name
        r_font = parser.process_line("(3/6) upgrading ttf-font-awesome")
        self.assertIsNotNone(r_font)
        self.assertLess(r_font[0], 0.65, f"Progress jumped prematurely to {r_font[0]}!")
        self.assertFalse(parser.in_post_hooks)

        # 3. Package with 'mime' in name
        r_mime = parser.process_line("(4/6) upgrading shared-mime-info")
        self.assertIsNotNone(r_mime)
        self.assertLess(r_mime[0], 0.75, f"Progress jumped prematurely to {r_mime[0]}!")
        self.assertFalse(parser.in_post_hooks)

        # 4. Package with 'systemd' in name
        r_sys = parser.process_line("(5/6) upgrading systemd")
        self.assertIsNotNone(r_sys)
        self.assertLess(r_sys[0], 0.85, f"Progress jumped prematurely to {r_sys[0]}!")
        self.assertFalse(parser.in_post_hooks)

        # 5. Last package firefox
        r_ff = parser.process_line("(6/6) upgrading firefox")
        self.assertIsNotNone(r_ff)
        self.assertEqual(r_ff[0], 0.88)
        self.assertFalse(parser.in_post_hooks)

        # NOW entering post-hooks
        r_hook_entry = parser.process_line(":: Running post-transaction hooks...")
        self.assertIsNotNone(r_hook_entry)
        self.assertEqual(r_hook_entry[0], 0.90)
        self.assertTrue(parser.in_post_hooks)

        # Hooks now advance 0.92 to 0.96
        r_h1 = parser.process_line("(1/3) Updating fontconfig cache...")
        self.assertIsNotNone(r_h1)
        self.assertGreaterEqual(r_h1[0], 0.92)
        self.assertLessEqual(r_h1[0], 0.96)
        self.assertEqual(r_h1[1], "Finalizing desktop & system environment...")

        r_h2 = parser.process_line("(2/3) Updating icon theme caches...")
        self.assertIsNotNone(r_h2)
        self.assertGreaterEqual(r_h2[0], 0.92)
        self.assertLessEqual(r_h2[0], 0.96)

        r_h3 = parser.process_line("(3/3) Arming ConditionNeedsUpdate...")
        self.assertIsNotNone(r_h3)
        self.assertEqual(r_h3[0], 0.96)

    def test_single_target_app_with_dependencies(self):
        """
        Requirement 3:
        When updating a single target app (target_pkg):
        If pkg_name == target_pkg:
          Updating {disp}... (or with ratio if tot_idx > 1)
        Else:
          Updating dependency {disp} ({cur_idx} of {tot_idx})...
        """
        parser = PacmanProgressParser(action="update", target_pkg="quickshell-git", is_system_upgrade=False)
        parser.process_line("Packages (2) qtengine quickshell-git")

        # Dependency
        r1 = parser.process_line("(1/2) upgrading qtengine")
        self.assertEqual(r1[1], "Updating dependency QtEngine (1 of 2)...")
        self.assertEqual(r1[2], "qtengine")
        self.assertEqual(r1[3], 1)
        self.assertEqual(r1[4], 2)
        self.assertEqual(r1[0], round(0.28 + (1/2)*0.60, 4))

        # Target app
        r2 = parser.process_line("(2/2) upgrading quickshell-git")
        self.assertEqual(r2[1], "Updating Quickshell (2 of 2)...")
        self.assertEqual(r2[2], "quickshell-git")
        self.assertEqual(r2[3], 2)
        self.assertEqual(r2[4], 2)
        self.assertEqual(r2[0], 0.88)

        # Completion
        self.assertEqual(parser.get_completion_message(), "✓ Updated Quickshell successfully!")

    def test_single_target_app_no_dependencies(self):
        """
        Single target app with tot_idx == 1:
        'Updating {disp}...'
        """
        parser = PacmanProgressParser(action="update", target_pkg="firefox", is_system_upgrade=False)
        parser.process_line("Packages (1) firefox")
        r = parser.process_line("(1/1) upgrading firefox")
        self.assertEqual(r[1], "Updating Firefox...")
        self.assertEqual(r[0], 0.88)
        self.assertEqual(parser.get_completion_message(), "✓ Updated Firefox successfully!")

    def test_multiple_apps_system_upgrade(self):
        """
        Requirement 3:
        Multiple apps (is_system_upgrade):
        'Updating {disp} ({cur_idx} of {tot_idx})...'
        """
        parser = PacmanProgressParser(action="update", is_system_upgrade=True)
        parser.process_line("Packages (3) bash linux firefox")

        r1 = parser.process_line("(1/3) upgrading bash")
        self.assertEqual(r1[1], "Updating Bash (1 of 3)...")

        r2 = parser.process_line("(2/3) upgrading linux")
        self.assertEqual(r2[1], "Updating Linux (2 of 3)...")

        r3 = parser.process_line("(3/3) upgrading firefox")
        self.assertEqual(r3[1], "Updating Firefox (3 of 3)...")

        self.assertEqual(parser.get_completion_message(), "✓ System update complete!")

    def test_install_and_remove_actions(self):
        """
        Install and remove verbs
        """
        # Install
        parser_inst = PacmanProgressParser(action="install", target_pkg="code")
        parser_inst.process_line("Packages (2) electron code")
        r_dep = parser_inst.process_line("(1/2) installing electron")
        self.assertEqual(r_dep[1], "Installing dependency Electron (1 of 2)...")
        r_tgt = parser_inst.process_line("(2/2) installing code")
        self.assertEqual(r_tgt[1], "Installing Visual Studio Code (2 of 2)...")
        self.assertEqual(parser_inst.get_completion_message(), "✓ Installed Visual Studio Code successfully!")

        # Remove
        parser_rem = PacmanProgressParser(action="remove", target_pkg="steam")
        parser_rem.process_line("Packages (1) steam")
        r_rem = parser_rem.process_line("(1/1) removing steam")
        self.assertEqual(r_rem[1], "Removing Steam...")
        self.assertEqual(parser_rem.get_completion_message(), "✓ Removed Steam successfully!")

    def test_monotonic_progress(self):
        """
        Requirement: Monotonic progression guarantees progress never moves backwards.
        """
        parser = PacmanProgressParser(action="update", is_system_upgrade=True)
        stream = [
            "resolving dependencies...",
            "looking for conflicting packages...",
            "Packages (3) a b c",
            ":: Retrieving packages...",
            "downloading a-1.0-1-x86_64.pkg.tar.zst...",
            "downloading b-1.0-1-x86_64.pkg.tar.zst...",
            "downloading c-1.0-1-x86_64.pkg.tar.zst...",
            ":: Checking keys in keyring...",
            ":: Checking available disk space...",
            ":: Processing package changes...",
            "(1/3) upgrading a",
            "(2/3) upgrading b",
            "(3/3) upgrading c",
            ":: Running post-transaction hooks...",
            "(1/2) Updating desktop file MIME type cache...",
            "(2/2) Updating icon theme caches...",
        ]

        curr = 0.0
        for s in stream:
            out = parser.process_line(s)
            if out:
                p = out[0]
                self.assertGreaterEqual(p, curr, f"Progress stepped backward! {p} < {curr} on line: {s}")
                curr = p

        self.assertLessEqual(curr, 0.98)
        self.assertEqual(parser.get_completion_message(), "✓ System update complete!")

    def test_error_formatting(self):
        """
        Requirement 6: Error handling
        """
        parser = PacmanProgressParser(action="update", target_pkg="firefox")
        parser.process_line("resolving dependencies...")
        parser.process_line("error: unresolvable package conflicts detected")
        parser.process_line("error: failed to prepare transaction (conflicting dependencies)")
        err_msg = parser.get_error_message(1)
        self.assertTrue(err_msg.startswith("Error: "))
        self.assertIn("conflicting dependencies", err_msg)


class TestPackageManagerIntegration(unittest.TestCase):
    def test_active_transaction_fields_and_progress_query(self):
        """
        Verify active_transaction tracking of current_pkg, current_idx, total_packages, progress, status_msg.
        """
        pm = PackageManager()
        
        # Simulate active transaction for quickshell-git with dependency qtengine
        with pm._action_lock:
            pm.active_transaction = {
                "action": "update",
                "pkg_name": "quickshell-git",
                "source": "aur",
                "progress": 0.06,
                "status": "Authenticating & preparing...",
                "status_msg": "Authenticating & preparing...",
                "start_time": 0,
                "current_pkg": "quickshell-git",
                "current_idx": 0,
                "total_packages": 2,
            }
            pm._last_progress = 0.06

        self.assertTrue(pm.is_pkg_installing("quickshell-git"))
        self.assertFalse(pm.is_pkg_installing("firefox"))

        prog, msg = pm.get_active_progress("quickshell-git")
        self.assertEqual(prog, 0.06)
        self.assertEqual(msg, "Authenticating & preparing...")

        # Now simulate dependency being updated
        with pm._action_lock:
            pm.active_transaction["current_pkg"] = "qtengine"
            pm.active_transaction["current_idx"] = 1
            pm.active_transaction["total_packages"] = 2
            pm.active_transaction["progress"] = 0.58
            pm.active_transaction["status"] = "Updating dependency QtEngine (1 of 2)..."
            pm.active_transaction["status_msg"] = "Updating dependency QtEngine (1 of 2)..."
            pm._last_progress = 0.58

        # Querying dependency qtengine directly returns True and progress
        self.assertTrue(pm.is_pkg_installing("qtengine"))
        prog_dep, msg_dep = pm.get_active_progress("qtengine")
        self.assertEqual(prog_dep, 0.58)
        self.assertEqual(msg_dep, "Updating dependency QtEngine (1 of 2)...")

        # Querying parent package quickshell-git still returns progress
        prog_tgt, msg_tgt = pm.get_active_progress("quickshell-git")
        self.assertEqual(prog_tgt, 0.58)
        self.assertEqual(msg_tgt, "Updating dependency QtEngine (1 of 2)...")

        # Verify get_active_transaction returns dict with all 5 required fields
        tx = pm.get_active_transaction()
        self.assertIsNotNone(tx)
        self.assertEqual(tx["current_pkg"], "qtengine")
        self.assertEqual(tx["current_idx"], 1)
        self.assertEqual(tx["total_packages"], 2)
        self.assertEqual(tx["progress"], 0.58)
        self.assertEqual(tx["status_msg"], "Updating dependency QtEngine (1 of 2)...")


if __name__ == "__main__":
    unittest.main()
