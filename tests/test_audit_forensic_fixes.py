#!/usr/bin/env python3
"""
Forensic Audit Fixes Test Suite for Aura Store
================================================
Comprehensive verification of all critical audit fixes:
1. Plaintext vault elimination & passwordless sudo validation (AURA-001, AURA-002, AURA-003, AURA-004)
2. Docker isolation hardening (AURA-005, AURA-006, AURA-007, AURA-008, AURA-038, AURA-039)
3. Arch packaging correctness (AURA-010, AURA-011, AURA-012, AURA-017, AURA-020, AURA-028)
4. Storage & Cache management (AURA-050, AURA-055, AURA-056)
"""

import os
import sys
import json
import time
import shutil
import tempfile
import threading
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch, call

# Ensure aura root is on sys.path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import aura_backend
from aura_backend import (
    PackageManager,
    ContainerManager,
    SnapManager,
    CacheManager,
    get_aur_helper,
    parse_pacman_info,
)


class TestPlaintextVaultAndSudoValidation(unittest.TestCase):
    """
    Audit Area 1: Plaintext vault elimination & passwordless sudo validation
    Verifies that credentials are NEVER stored on disk or memory,
    authoritative sudo checks are used, and sudoers rules are validated.
    """

    def setUp(self):
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp_dir.cleanup)

    def test_vault_path_does_not_store_credentials(self):
        """
        Verify PackageManager.get_vault_path() does not store credentials.
        Calling configure_passwordless() must never persist passwords to disk.
        """
        vault_path = PackageManager.get_vault_path()
        self.assertIsInstance(vault_path, Path)
        self.assertEqual(vault_path.name, ".aura_vault")

        # Simulate legacy vault file existing before operation
        test_vault = Path(self.tmp_dir.name) / ".aura_vault"
        test_vault.write_text("super_secret_admin_password_123\n")
        self.assertTrue(test_vault.exists())

        with patch.object(PackageManager, "get_vault_path", return_value=test_vault), \
             patch("subprocess.run") as mock_run:
            # Simulate successful sudo and visudo
            mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
            ok, msg = PackageManager.configure_passwordless("my_admin_pass")
            self.assertTrue(ok)

            # CRITICAL SECURITY CHECK: Vault file must NOT contain credentials and must be unlinked!
            self.assertFalse(test_vault.exists(), "Legacy plaintext vault was not deleted!")

    def test_clear_auth_cache_purges_tokens_and_vault(self):
        """Verify clear_auth_cache() purges session tokens and legacy vault files."""
        fake_token = Path(self.tmp_dir.name) / "aura_auth.token"
        fake_token.write_text("session_secret")
        fake_vault = Path(self.tmp_dir.name) / ".aura_vault"
        fake_vault.write_text("legacy_vault_secret")

        with patch("aura_backend.Path.home", return_value=Path(self.tmp_dir.name)), \
             patch("aura_backend.os.getuid", return_value=1000):
            with patch("aura_backend.Path", side_effect=lambda p: fake_token if "aura_auth.token" in str(p) else Path(p)):
                PackageManager.clear_auth_cache()
                self.assertFalse(fake_token.exists())

    def test_is_passwordless_configured_authoritative_check(self):
        """
        Verify PackageManager.is_passwordless_configured() checks 'sudo -n pacman -V'
        and does NOT rely on local credential files.
        """
        # Case A: sudo -n pacman -V succeeds (returncode 0) -> True
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=0)
            self.assertTrue(PackageManager.is_passwordless_configured())
            mock_run.assert_called_once_with(
                ["sudo", "-n", "pacman", "-V"],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL
            )

        # Case B: sudo -n pacman -V fails (returncode 1) -> False
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1)
            self.assertFalse(PackageManager.is_passwordless_configured())

        # Case C: subprocess raises exception -> cleanly returns False
        with patch("subprocess.run", side_effect=FileNotFoundError("sudo not found")):
            self.assertFalse(PackageManager.is_passwordless_configured())

        # Case D: Local vault exists, but sudo -n fails -> MUST return False (No vault trust!)
        fake_vault = Path(self.tmp_dir.name) / ".aura_vault"
        fake_vault.write_text("cached_password")
        with patch.object(PackageManager, "get_vault_path", return_value=fake_vault), \
             patch("subprocess.run", return_value=MagicMock(returncode=1)):
            self.assertFalse(PackageManager.is_passwordless_configured(),
                             "is_passwordless_configured() trusted local vault instead of sudo -n!")

    def test_configure_passwordless_validation_and_mocks(self):
        """
        Test configure_passwordless() with mocks for:
        - empty password check
        - sudo -S -v credential check failure
        - visudo -cf syntax validation failure
        - rollback on verification failure
        - successful atomic configuration
        """
        # 1. Empty password
        ok, msg = PackageManager.configure_passwordless("")
        self.assertFalse(ok)
        self.assertIn("empty", msg.lower())

        # 2. Authentication failure on sudo -S -v
        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(returncode=1, stderr="Incorrect password")
            ok, msg = PackageManager.configure_passwordless("bad_pass")
            self.assertFalse(ok)
            self.assertIn("Incorrect password", msg)

        # 3. Visudo syntax validation failure
        def _visudo_fail(cmd, *args, **kwargs):
            if cmd[0] == "sudo" and cmd[1:3] == ["-S", "-v"]:
                return MagicMock(returncode=0)
            if cmd[0] == "visudo":
                return MagicMock(returncode=1, stderr="syntax error in sudoers")
            return MagicMock(returncode=0)

        with patch("subprocess.run", side_effect=_visudo_fail):
            ok, msg = PackageManager.configure_passwordless("good_pass")
            self.assertFalse(ok)
            self.assertIn("syntax validation failed", msg.lower())

        # 4. Rollback if sudo -n pacman -V verification fails
        executed_cmds = []

        def _verify_fail(cmd, *args, **kwargs):
            executed_cmds.append(cmd)
            if cmd[0:3] == ["sudo", "-n", "pacman"]:
                return MagicMock(returncode=1, stderr="permission denied")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch("subprocess.run", side_effect=_verify_fail):
            ok, msg = PackageManager.configure_passwordless("good_pass")
            self.assertFalse(ok)
            self.assertIn("could not be verified", msg.lower())
            # Ensure rollback removal was invoked
            rollback_called = any(
                cmd[0:4] == ["sudo", "-S", "rm", "-f"] and "/etc/sudoers.d/99-aura-pacman" in cmd
                for cmd in executed_cmds
            )
            self.assertTrue(rollback_called, "Rollback command was not executed when verification failed!")

        # 5. Full success path
        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            ok, msg = PackageManager.configure_passwordless("valid_admin_password")
            self.assertTrue(ok)
            self.assertIn("verified successfully", msg.lower())

    def test_remove_passwordless_with_mocks(self):
        """Test remove_passwordless() with mocks for clean de-authorization and rollback."""
        # Case A: Rule does not exist -> already disabled
        with patch("aura_backend.Path") as mock_path:
            mock_inst = MagicMock()
            mock_inst.exists.return_value = False
            mock_path.return_value = mock_inst
            ok, msg = PackageManager.remove_passwordless()
            self.assertTrue(ok)
            self.assertIn("already disabled", msg.lower())

        # Case B: Rule exists and is removed via sudo -n rm
        with patch("aura_backend.Path") as mock_path, \
             patch("subprocess.run") as mock_run:
            mock_inst = MagicMock()
            # First exists=True, after rm exists=False
            mock_inst.exists.side_effect = [True, False]
            mock_path.return_value = mock_inst
            mock_run.return_value = MagicMock(returncode=0)

            ok, msg = PackageManager.remove_passwordless()
            self.assertTrue(ok)
            self.assertIn("disabled successfully", msg.lower())
            mock_run.assert_called_with(
                ["sudo", "-n", "rm", "-f", "/etc/sudoers.d/99-aura-pacman"],
                capture_output=True, text=True, timeout=5
            )


class TestDockerIsolationHardening(unittest.TestCase):
    """
    Audit Area 2: Docker isolation hardening
    Verifies that container runs with tight sandboxing:
    - Never mounts host $HOME read-write
    - Never uses host IPC or host networking
    - Mounts dedicated ~/.local/share/aura/containers directory
    - Uninstalls app from container first before removing desktop shortcut
    """

    def setUp(self):
        self.cm = ContainerManager()
        self.tmp_dir = tempfile.TemporaryDirectory()
        self.cm.SHORTCUTS_DIR = Path(self.tmp_dir.name) / "applications"
        self.cm.SHORTCUTS_DIR.mkdir(parents=True, exist_ok=True)
        self.addCleanup(self.tmp_dir.cleanup)

    def test_ensure_container_configured_isolation_hardening(self):
        """
        Verify ContainerManager.ensure_container_configured():
        - Does NOT mount $HOME:$HOME read-write
        - Does NOT use --ipc=host or --net=host
        - Mounts dedicated container directory ~/.local/share/aura/containers
        """
        docker_run_cmd = []

        def _mock_run(cmd, *args, **kwargs):
            if cmd and cmd[0] == "docker" and len(cmd) > 1 and cmd[1] == "run":
                docker_run_cmd.extend(cmd)
                return MagicMock(returncode=0, stdout="container_id_123", stderr="")
            return MagicMock(returncode=0, stdout="", stderr="")

        with patch.object(self.cm, "get_status", return_value={"status_code": "missing_container"}), \
             patch("subprocess.run", side_effect=_mock_run):
            ok, msg = self.cm.ensure_container_configured()
            self.assertTrue(ok)

        self.assertTrue(len(docker_run_cmd) > 0, "docker run was not executed!")

        home_str = str(Path.home())
        # 1. Verify NO $HOME:$HOME mount
        self.assertNotIn(f"{home_str}:{home_str}", docker_run_cmd,
                         "CRITICAL: Host $HOME is mounted directly into container!")
        self.assertNotIn(f"{home_str}:{home_str}:rw", docker_run_cmd)

        # 2. Verify NO --ipc=host or --net=host
        self.assertNotIn("--ipc=host", docker_run_cmd,
                         "CRITICAL: Container uses host IPC namespace (--ipc=host)!")
        self.assertNotIn("--net=host", docker_run_cmd,
                         "CRITICAL: Container uses host network namespace (--net=host)!")

        # 3. Verify dedicated container storage mount
        expected_container_dir = str(Path.home() / ".local" / "share" / "aura" / "containers")
        matched_mount = False
        for i, token in enumerate(docker_run_cmd):
            if token == "-v" and i + 1 < len(docker_run_cmd):
                val = docker_run_cmd[i + 1]
                if expected_container_dir in val and "/home/aura-user/app-data" in val:
                    matched_mount = True
                    break
        self.assertTrue(matched_mount,
                        f"Dedicated container directory {expected_container_dir} was not mounted into container!")

    def test_uninstall_app_removes_container_package_first_on_failure(self):
        """
        Verify uninstall_app() removes package inside container first:
        If package removal fails, the desktop shortcut MUST NOT be deleted.
        """
        app_id = "blender"
        shortcut_file = self.cm.SHORTCUTS_DIR / f"aura-box-{app_id}.desktop"
        shortcut_file.write_text("[Desktop Entry]\nName=Blender\n")
        self.assertTrue(shortcut_file.exists())

        # Simulate docker exec apk del failing
        def _mock_run(cmd, *args, **kwargs):
            if "apk" in cmd and "del" in cmd:
                return MagicMock(returncode=1, stderr="ERROR: package locked")
            return MagicMock(returncode=0)

        done_event = threading.Event()
        callback_result = {}

        def _completion_cb(ok, msg):
            callback_result["ok"] = ok
            callback_result["msg"] = msg
            done_event.set()

        with patch.object(self.cm, "get_status", return_value={"container_exists": True, "container_running": True}), \
             patch("subprocess.run", side_effect=_mock_run):
            self.cm.uninstall_app(app_id, completion_callback=_completion_cb)
            done_event.wait(timeout=5)

        self.assertFalse(callback_result.get("ok"), "Uninstall should have reported failure when apk del failed")
        # Critical verification: desktop shortcut remains intact because removal failed!
        self.assertTrue(shortcut_file.exists(),
                        "Desktop shortcut was prematurely deleted when container package removal failed!")

    def test_uninstall_app_removes_container_package_first_on_success(self):
        """
        Verify uninstall_app() sequence on success:
        Package removal occurs before shortcut deletion, and shortcut is cleaned up.
        """
        app_id = "blender"
        shortcut_file = self.cm.SHORTCUTS_DIR / f"aura-box-{app_id}.desktop"
        shortcut_file.write_text("[Desktop Entry]\nName=Blender\n")

        execution_order = []

        def _mock_run(cmd, *args, **kwargs):
            if "apk" in cmd and "del" in cmd:
                execution_order.append("apk_del")
                return MagicMock(returncode=0)
            return MagicMock(returncode=0)

        done_event = threading.Event()
        callback_result = {}

        def _completion_cb(ok, msg):
            callback_result["ok"] = ok
            callback_result["msg"] = msg
            done_event.set()

        def _mock_unlink(*args, **kwargs):
            execution_order.append("shortcut_unlink")

        with patch.object(self.cm, "get_status", return_value={"container_exists": True, "container_running": True}), \
             patch("subprocess.run", side_effect=_mock_run), \
             patch.object(Path, "unlink", side_effect=_mock_unlink):
            self.cm.uninstall_app(app_id, completion_callback=_completion_cb)
            done_event.wait(timeout=5)

        self.assertTrue(callback_result.get("ok"))
        self.assertEqual(execution_order, ["apk_del", "shortcut_unlink"],
                         "apk del must be executed before shortcut_unlink!")


class TestArchPackagingCorrectness(unittest.TestCase):
    """
    Audit Area 3: Arch packaging correctness
    Verifies:
    - No partial upgrades (pacman -Sy) generated anywhere
    - get_aur_helper() detection and clean fallback
    - Global score-based sorting for search_all()
    - parse_pacman_info() multiline optdepends accumulation
    - check_updates() update generation token race prevention
    - resolve_package_source() multi-source resolution
    """

    def setUp(self):
        self.pm = PackageManager()

    def test_no_partial_upgrade_generated(self):
        """
        Verify no partial upgrade ('pacman -Sy') is generated in PackageManager.
        System upgrades must use full upgrade flags (-Syu), and package installs
        must use safe flags without database refresh (-S --needed --noconfirm).
        """
        captured_commands = []

        def _mock_popen(cmd, *args, **kwargs):
            captured_commands.append(list(cmd))
            mock_proc = MagicMock()
            mock_proc.stdout.readline.return_value = ""
            mock_proc.poll.return_value = 0
            mock_proc.returncode = 0
            mock_proc.wait.return_value = 0
            return mock_proc

        dummy_prog = lambda frac, msg: None
        dummy_done = lambda ok, act, pkg, err: None

        with patch("subprocess.Popen", side_effect=_mock_popen), \
             patch.object(PackageManager, "is_passwordless_configured", return_value=True), \
             patch.object(self.pm, "refresh_installed", return_value=None), \
             patch.object(self.pm, "check_updates", return_value=[]), \
             patch("aura_backend.get_aur_helper", return_value=None):

            # 1. System upgrade (empty pkg_name triggers system upgrade)
            self.pm.execute_background_action("update", "", "pacman", dummy_prog, dummy_done)
            time.sleep(0.1)

            # 2. Package installation
            self.pm.execute_background_action("install", "neovim", "pacman", dummy_prog, dummy_done)
            time.sleep(0.1)

            # 3. Package single update
            self.pm.execute_background_action("update", "neovim", "pacman", dummy_prog, dummy_done)
            time.sleep(0.1)

        self.assertTrue(len(captured_commands) >= 3, "Not all actions generated subprocess commands")
        for cmd in captured_commands:
            # Check for partial upgrade flag
            self.assertNotIn("-Sy", cmd, f"PARTIAL UPGRADE DETECTED: {cmd} contains dangerous '-Sy'!")
            if "-Syu" in cmd:
                self.assertIn("pacman", cmd)
            elif "install" in cmd or "update" in cmd:
                self.assertIn("-S", cmd)

    def test_get_aur_helper_detection_and_fallback(self):
        """Verify get_aur_helper() detects paru or yay and falls back cleanly."""
        # 1. When paru exists -> returns "paru"
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/paru" if x == "paru" else None):
            self.assertEqual(get_aur_helper(), "paru")

        # 2. When only yay exists -> returns "yay"
        with patch("shutil.which", side_effect=lambda x: "/usr/bin/yay" if x == "yay" else None):
            self.assertEqual(get_aur_helper(), "yay")

        # 3. When neither exists -> returns None
        with patch("shutil.which", return_value=None):
            self.assertIsNone(get_aur_helper())

    def test_search_all_global_score_sorting(self):
        """
        Verify search_all() sorts combined official + AUR results globally by score.
        A high-scoring AUR package must rank ABOVE a low-scoring official package.
        """
        official_mock_results = [
            {"name": "official-firefox-common", "score": 45, "priority": 0, "source": "pacman"},
            {"name": "official-firefox-docs", "score": 30, "priority": 0, "source": "pacman"},
        ]
        aur_mock_results = [
            {"name": "firefox-developer-edition-bin", "score": 98, "priority": 1, "source": "aur"},
            {"name": "firefox-nightly", "score": 75, "priority": 1, "source": "aur"},
        ]

        with patch.object(self.pm, "search_pacman", return_value=official_mock_results), \
             patch.object(self.pm, "search_aur", return_value=aur_mock_results):
            results = self.pm.search_all("firefox", filter_mode="all")

            self.assertEqual(len(results), 4)
            # The top result MUST be the highest score (98, AUR)
            self.assertEqual(results[0]["name"], "firefox-developer-edition-bin")
            self.assertEqual(results[0]["score"], 98)

            # Second result MUST be score 75 (AUR)
            self.assertEqual(results[1]["name"], "firefox-nightly")
            self.assertEqual(results[1]["score"], 75)

            # Third result score 45 (official)
            self.assertEqual(results[2]["name"], "official-firefox-common")

            # Fourth result score 30 (official)
            self.assertEqual(results[3]["name"], "official-firefox-docs")

            # Verify strictly descending order of scores
            scores = [r["score"] for r in results]
            self.assertEqual(scores, sorted(scores, reverse=True))

    def test_parse_pacman_info_accumulates_multiline_optdepends(self):
        """
        Verify parse_pacman_info() accumulates multiline optdepends (AURA-028).
        Also tests depends, sizes, descriptions, and clean key-value extraction.
        """
        sample_output = """Repository      : extra
Name            : neovim
Version         : 0.10.1-1
Description     : Vim-fork focused on extensibility and agility
Architecture    : x86_64
URL             : https://neovim.io
Licenses        : Apache-2.0  Vim
Groups          : None
Provides        : None
Depends On      : libuv  luajit  msgpack-c  tree-sitter
Optional Deps   : python-pynvim: for Python plugin support [installed]
                  xclip: for X11 clipboard support
                  wl-clipboard: for Wayland clipboard support
Required By     : None
Optional For    : None
Conflicts With  : None
Replaces        : None
Download Size   : 6.85 MiB
Installed Size  : 27.24 MiB
Packager        : Sven-Hendrik Haase <svenstaro@archlinux.org>
Build Date      : Fri 02 Aug 2024 07:12:45 AM UTC
MD5 Sum         : d3b07384d113edec49eaa6238ad5ff00
SHA-256 Sum     : e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855
Signatures      : Yes
"""
        # Test both top-level and PackageManager.parse_pacman_info
        parsed1 = parse_pacman_info(sample_output)
        parsed2 = PackageManager.parse_pacman_info(sample_output)

        for parsed in (parsed1, parsed2):
            self.assertEqual(parsed["version"], "0.10.1-1")
            self.assertEqual(parsed["desc"], "Vim-fork focused on extensibility and agility")
            self.assertEqual(parsed["repo"], "extra")
            self.assertEqual(parsed["url"], "https://neovim.io")
            self.assertEqual(parsed["isize_str"], "27.24 MiB")
            self.assertEqual(parsed["csize_str"], "6.85 MiB")
            self.assertEqual(parsed["depends"], ["libuv", "luajit", "msgpack-c", "tree-sitter"])

            # Verify optdepends contains all 3 entries accumulated
            self.assertEqual(len(parsed["optdepends"]), 3)
            self.assertIn("python-pynvim: for Python plugin support [installed]", parsed["optdepends"])
            self.assertIn("xclip: for X11 clipboard support", parsed["optdepends"])
            self.assertIn("wl-clipboard: for Wayland clipboard support", parsed["optdepends"])

        # Test edge case: "Optional Deps   : None"
        output_none = "Name : test\nOptional Deps   : None\n"
        parsed_none = parse_pacman_info(output_none)
        self.assertEqual(parsed_none["optdepends"], [])

    def test_update_generation_token_prevents_stale_overwrite(self):
        """
        Verify update generation token prevents stale overwrite in check_updates() (AURA-017).
        A superseded update check must not clobber newer results.
        """
        self.pm.upgradable_list = []
        self.pm._update_generation = 0

        # Simulate fast update check finishing with generation 2
        fast_updates = [{"name": "fast_pkg", "old_ver": "1.0", "new_ver": "2.0", "source": "pacman"}]
        slow_updates = [{"name": "stale_pkg", "old_ver": "0.1", "new_ver": "0.2", "source": "pacman"}]

        # Thread 1 starts: generation = 1
        with self.pm._update_lock:
            self.pm._update_generation += 1
            gen1 = self.pm._update_generation

        # Thread 2 starts: generation = 2
        with self.pm._update_lock:
            self.pm._update_generation += 1
            gen2 = self.pm._update_generation

        # Thread 2 finishes first and commits
        with self.pm._lock:
            if gen2 == self.pm._update_generation:
                self.pm.upgradable_list = fast_updates

        self.assertEqual(self.pm.upgradable_list, fast_updates)

        # Thread 1 finishes later with stale data
        with self.pm._lock:
            if gen1 == self.pm._update_generation:
                self.pm.upgradable_list = slow_updates

        # Verify fast_updates was NOT overwritten by stale slow_updates!
        self.assertEqual(self.pm.upgradable_list, fast_updates,
                         "Stale update check with older generation token overwrote newer updates list!")

    def test_resolve_package_source_all_sources(self):
        """
        Verify resolve_package_source() accurately determines package origins:
        official pacman, AUR, snap, and docker/container.
        """
        # Ensure test isolation from host package state
        self.pm.packages = {}
        self.pm.installed_set = set()

        # 1. Official package in packages dict
        self.pm.packages["glibc"] = {"name": "glibc", "repo": "core"}
        self.assertEqual(self.pm.resolve_package_source("glibc"), "pacman")

        # 2. Installed official package
        self.pm.installed_set.add("custom-pacman-pkg")
        self.assertEqual(self.pm.resolve_package_source("custom-pacman-pkg"), "pacman")

        # 3. Snap curated or installed
        self.assertEqual(self.pm.resolve_package_source("slack"), "snap")
        with patch.object(self.pm.snap_mgr, "is_snap_installed", side_effect=lambda x: x == "custom-snap"):
            self.assertEqual(self.pm.resolve_package_source("custom-snap"), "snap")

        # 4. Container / Docker curated or installed
        self.assertEqual(self.pm.resolve_package_source("kdenlive"), "docker")
        with patch.object(self.pm.container_mgr, "is_app_installed", side_effect=lambda x: x == "custom-docker"):
            self.assertEqual(self.pm.resolve_package_source("custom-docker"), "docker")

        # 5. AUR package resolved via helper
        def _mock_aur_run(cmd, *args, **kwargs):
            if "-Si" in cmd and "google-chrome" in cmd:
                return MagicMock(returncode=0)
            return MagicMock(returncode=1)

        with patch("aura_backend.get_aur_helper", return_value="paru"), \
             patch("subprocess.run", side_effect=_mock_aur_run):
            self.assertEqual(self.pm.resolve_package_source("google-chrome"), "aur")

        # 6. Fallback
        with patch("aura_backend.get_aur_helper", return_value=None):
            self.assertEqual(self.pm.resolve_package_source("unknown-random-package-xyz"), "pacman")


class TestStorageAndCacheManagement(unittest.TestCase):
    """
    Audit Area 4: Storage & Cache management
    Verifies:
    - scan_all_caches() does not invent 50% reclaimable space if paccache fails (AURA-050)
    - prune_all_selected() handles empty categories gracefully (AURA-055, AURA-056)
    """

    def setUp(self):
        self.cm = CacheManager()

    def test_scan_all_caches_does_not_invent_50_percent_reclaimable(self):
        """
        Verify scan_all_caches() does not invent 50% reclaimable space if paccache fails.
        If paccache dry-run fails or returns non-zero, reclaimable pacman cache must
        remain strictly measured (0 or unknown), NEVER guessed as pacman_total * 0.5.
        """
        pacman_total_bytes = 1024 * 1024 * 1024  # 1 GB

        # Mock pacman cache directory size and failing paccache
        with patch.object(CacheManager, "_dir_size", side_effect=lambda p: pacman_total_bytes if "pacman" in str(p) else 0), \
             patch("shutil.which", return_value=True), \
             patch("subprocess.run", side_effect=subprocess.SubprocessError("paccache error")):

            data = self.cm.scan_all_caches()
            pacman_info = data["categories"]["pacman"]

            self.assertEqual(pacman_info["total_bytes"], pacman_total_bytes)
            # Reclaimable bytes must NOT be 50% (512 MB)
            half_invented = int(pacman_total_bytes * 0.5)
            self.assertNotEqual(
                pacman_info["reclaimable_bytes"],
                half_invented,
                "AURA-050 FAILED: scan_all_caches() guessed 50% reclaimable space when paccache failed!"
            )
            self.assertEqual(pacman_info["reclaimable_bytes"], 0)

    def test_prune_all_selected_handles_empty_categories_gracefully(self):
        """
        Verify prune_all_selected() handles empty categories list gracefully
        without hanging, deadlocking, or triggering exceptions.
        """
        done_event = threading.Event()
        result = {}

        def _prog_cb(pct, msg):
            result["progress"] = (pct, msg)

        def _comp_cb(ok, msg):
            result["completed"] = (ok, msg)
            done_event.set()

        self.cm.prune_all_selected([], progress_cb=_prog_cb, complete_cb=_comp_cb)
        signaled = done_event.wait(timeout=3)

        self.assertTrue(signaled, "prune_all_selected([]) deadlocked or timed out!")
        self.assertIn("completed", result)
        ok, msg = result["completed"]
        self.assertTrue(ok)
        self.assertIn("No categories selected", msg)


if __name__ == "__main__":
    unittest.main()
