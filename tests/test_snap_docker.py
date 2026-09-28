#!/usr/bin/env python3
"""
Unit and Integration Tests for Snap Store and Docker/Container features in Aura.
Tests cover:
- SnapManager: curated apps catalogue, search, CLI parsing, details parsing.
- ContainerManager: curated container apps, container status, update checks.
- PackageManager integration: snap_mgr and container_mgr instances, unified info fetching.
"""

import os
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from aura_backend import SnapManager, ContainerManager, PackageManager


class TestSnapManager(unittest.TestCase):
    """Tests for SnapManager functionalities."""

    def setUp(self):
        self.snap_mgr = SnapManager()

    def test_curated_snap_apps_count_and_required_fields(self):
        """
        Verify CURATED_SNAP_APPS has 15+ apps with required fields:
        name, title, summary, category.
        """
        apps = SnapManager.CURATED_SNAP_APPS
        self.assertGreaterEqual(
            len(apps), 15,
            f"Expected at least 15 curated snap apps, but found {len(apps)}"
        )

        required_fields = ("name", "title", "summary", "category")
        for app in apps:
            for field in required_fields:
                self.assertIn(
                    field, app,
                    f"App {app.get('name', 'UNKNOWN')} missing required field: {field}"
                )
                self.assertTrue(
                    isinstance(app[field], str) and len(app[field].strip()) > 0,
                    f"App {app.get('name')} field '{field}' must be a non-empty string"
                )

            # Check additional essential fields
            self.assertIn("publisher", app, f"App {app['name']} missing publisher")
            self.assertIn("version", app, f"App {app['name']} missing version")
            # Must support store-url
            self.assertTrue(
                "store-url" in app or "store_url" in app,
                f"App {app['name']} missing store-url or store_url"
            )

    def test_search_snaps_spotify(self):
        """
        Test search_snaps('spotify') returns valid results with title, summary, publisher.
        """
        results = self.snap_mgr.search_snaps("spotify")
        self.assertIsInstance(results, list)
        self.assertGreaterEqual(len(results), 1, "Expected at least 1 result for search_snaps('spotify')")

        spotify = next((r for r in results if r["name"] == "spotify"), None)
        self.assertIsNotNone(spotify, "Spotify should be found in search results")
        self.assertIn("title", spotify)
        self.assertIn("summary", spotify)
        self.assertIn("publisher", spotify)

        self.assertEqual(spotify["title"], "Spotify")
        self.assertTrue(len(spotify["summary"].strip()) > 0)
        self.assertTrue(len(spotify["publisher"].strip()) > 0)

    def test_search_snaps_case_insensitive_and_empty(self):
        """Test search_snaps is case-insensitive and empty query returns all curated apps."""
        results_lower = self.snap_mgr.search_snaps("spotify")
        results_upper = self.snap_mgr.search_snaps("SPOTIFY")
        self.assertEqual(len(results_lower), len(results_upper))

        all_results = self.snap_mgr.search_snaps("")
        self.assertGreaterEqual(len(all_results), len(SnapManager.CURATED_SNAP_APPS))

    @patch("aura_backend.shutil.which")
    @patch("aura_backend.subprocess.run")
    def test_search_snaps_cli_parsing(self, mock_run, mock_which):
        """
        Test search_snaps parses CLI output from 'snap find' when snap command is available.
        """
        mock_which.return_value = "/usr/bin/snap"
        cli_output = (
            "Name     Version               Publisher  Notes  Summary\n"
            "spotify  1.2.53.440.g7b2f582a  spotify✓   -      Music for everyone\n"
            "mockapp  2.0.0                 mockdev    -      Mock application for testing\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=cli_output, stderr="")

        results = self.snap_mgr.search_snaps("spotify")
        self.assertIsInstance(results, list)
        self.assertGreaterEqual(len(results), 1)

        spotify = next((r for r in results if r["name"] == "spotify"), None)
        self.assertIsNotNone(spotify)
        self.assertEqual(spotify["title"], "Spotify")
        self.assertEqual(spotify["summary"], "Music for everyone")
        self.assertEqual(spotify["publisher"], "spotify✓")

    def test_get_snap_details_spotify(self):
        """
        Test get_snap_details('spotify') parses version, publisher, store-url.
        """
        details = self.snap_mgr.get_snap_details("spotify")
        self.assertIsInstance(details, dict)
        self.assertEqual(details.get("name"), "spotify")
        self.assertEqual(details.get("title"), "Spotify")

        # Required parsed fields
        self.assertIn("version", details)
        self.assertTrue(
            isinstance(details["version"], str) and len(details["version"].strip()) > 0,
            "version must be a non-empty string"
        )

        self.assertIn("publisher", details)
        self.assertTrue(
            isinstance(details["publisher"], str) and len(details["publisher"].strip()) > 0,
            "publisher must be a non-empty string"
        )

        self.assertIn("store-url", details)
        self.assertTrue(
            isinstance(details["store-url"], str) and details["store-url"].startswith("http"),
            "store-url must be a valid web URL"
        )

    @patch("aura_backend.shutil.which")
    @patch("aura_backend.subprocess.run")
    def test_get_snap_details_cli_parsing(self, mock_run, mock_which):
        """
        Test get_snap_details parses version, publisher, and store-url from 'snap info' output.
        """
        mock_which.return_value = "/usr/bin/snap"
        snap_info_stdout = """name:      spotify
summary:   Music for everyone
publisher: Spotify✓
store-url: https://snapcraft.io/spotify
contact:   https://support.spotify.com/
license:   Proprietary
description: |
  Spotify is all the music you will ever need.
  Millions of tracks and episodes.
commands:
  - spotify
snap-id:      pOBIoZ2oM9umUDaL2StngTVqqUBHG4BE
tracking:     latest/stable
refresh-date: 2024-09-10
channels:
  latest/stable:    1.2.53.440.g7b2f582a 2024-09-10 (140) 203MB -
  latest/candidate: ↑
  latest/beta:      ↑
  latest/edge:      ↑
installed:          1.2.53.440.g7b2f582a       (140) 203MB -
"""
        mock_run.return_value = MagicMock(returncode=0, stdout=snap_info_stdout, stderr="")

        details = self.snap_mgr.get_snap_details("spotify")
        self.assertEqual(details["name"], "spotify")
        self.assertEqual(details["publisher"], "Spotify✓")
        self.assertEqual(details["store-url"], "https://snapcraft.io/spotify")
        self.assertEqual(details["version"], "1.2.53.440.g7b2f582a")
        self.assertIn("Spotify is all the music", details["description"])

    def test_parse_snap_info_helper_static(self):
        """Directly verify _parse_snap_info static parser helper."""
        sample_output = """name:      code
summary:   Code editing. Redefined.
publisher: Microsoft*
store-url: https://snapcraft.io/code
license:   Proprietary
description: |
  Visual Studio Code is a lightweight but powerful source code editor.
version:   1.93.1
"""
        parsed = SnapManager._parse_snap_info(sample_output)
        self.assertEqual(parsed.get("name"), "code")
        self.assertEqual(parsed.get("summary"), "Code editing. Redefined.")
        self.assertEqual(parsed.get("publisher"), "Microsoft*")
        self.assertEqual(parsed.get("store-url"), "https://snapcraft.io/code")
        self.assertEqual(parsed.get("version"), "1.93.1")
        self.assertIn("Visual Studio Code is a lightweight", parsed.get("description", ""))


class TestContainerManager(unittest.TestCase):
    """Tests for ContainerManager functionalities."""

    def setUp(self):
        self.container_mgr = ContainerManager()

    def test_curated_container_apps_has_12_apps(self):
        """
        Verify CURATED_CONTAINER_APPS has exactly 12 apps.
        """
        apps = ContainerManager.CURATED_CONTAINER_APPS
        self.assertEqual(
            len(apps), 12,
            f"Expected exactly 12 curated container apps, found {len(apps)}"
        )

        # Verify all apps have required fields
        required_fields = ("id", "name", "desc", "category", "binary", "pkg", "size")
        for app in apps:
            for field in required_fields:
                self.assertIn(
                    field, app,
                    f"Container app {app.get('id', 'UNKNOWN')} missing field '{field}'"
                )
                self.assertTrue(
                    isinstance(app[field], str) and len(app[field].strip()) > 0,
                    f"Container app {app.get('id')} field '{field}' must be a non-empty string"
                )

    def test_check_container_updates_returns_list(self):
        """
        Test check_container_updates() returns a list.
        """
        updates = self.container_mgr.check_container_updates()
        self.assertIsInstance(
            updates, list,
            f"check_container_updates() must return a list, got {type(updates)}"
        )

    @patch.object(ContainerManager, "get_status")
    @patch("aura_backend.subprocess.run")
    def test_check_container_updates_with_mock_updates(self, mock_run, mock_status):
        """
        Test check_container_updates() parses upgradable apk packages when container is active.
        """
        mock_status.return_value = {
            "daemon_running": True,
            "container_running": True,
            "status_code": "ready",
        }
        apk_stdout = (
            "openssl-3.1.4-r0 < openssl-3.1.5-r0\n"
            "curl-8.5.0-r0 < curl-8.6.0-r0\n"
        )
        mock_run.return_value = MagicMock(returncode=0, stdout=apk_stdout, stderr="")

        updates = self.container_mgr.check_container_updates()
        self.assertIsInstance(updates, list)
        self.assertEqual(len(updates), 2)

        self.assertEqual(updates[0]["name"], "openssl-3.1.4-r0")
        self.assertEqual(updates[0]["current_version"], "openssl-3.1.4-r0")
        self.assertEqual(updates[0]["new_version"], "openssl-3.1.5-r0")
        self.assertEqual(updates[0]["type"], "container")

        self.assertEqual(updates[1]["name"], "curl-8.5.0-r0")
        self.assertEqual(updates[1]["new_version"], "curl-8.6.0-r0")

    @patch.object(ContainerManager, "get_status")
    def test_check_container_updates_inactive_container(self, mock_status):
        """
        Test check_container_updates() returns empty list when container is stopped or daemon is inactive.
        """
        mock_status.return_value = {
            "daemon_running": False,
            "container_running": False,
            "status_code": "missing_engine",
        }
        updates = self.container_mgr.check_container_updates()
        self.assertIsInstance(updates, list)
        self.assertEqual(len(updates), 0)

    def test_container_get_status_structure(self):
        """Verify get_status returns expected keys and status code."""
        status = self.container_mgr.get_status()
        self.assertIsInstance(status, dict)
        expected_keys = (
            "has_docker", "daemon_running", "container_exists",
            "container_running", "container_name", "status_code", "status_text"
        )
        for key in expected_keys:
            self.assertIn(key, status)


class TestPackageManagerIntegration(unittest.TestCase):
    """Tests for PackageManager integration with Snap and Container managers."""

    def setUp(self):
        self.pm = PackageManager()

    def test_hasattr_snap_mgr(self):
        """
        Verify hasattr(pm, "snap_mgr") is True and it is an instance of SnapManager.
        """
        self.assertTrue(hasattr(self.pm, "snap_mgr"), "PackageManager must have 'snap_mgr' attribute")
        self.assertIsInstance(self.pm.snap_mgr, SnapManager)

    def test_hasattr_container_mgr(self):
        """
        Verify hasattr(pm, "container_mgr") is True and it is an instance of ContainerManager.
        """
        self.assertTrue(hasattr(self.pm, "container_mgr"), "PackageManager must have 'container_mgr' attribute")
        self.assertIsInstance(self.pm.container_mgr, ContainerManager)

    def test_get_package_info_spotify_snap(self):
        """
        Test pm.get_package_info("spotify", source="snap") returns valid snap info.
        """
        info = self.pm.get_package_info("spotify", source="snap")
        self.assertIsInstance(info, dict)
        self.assertEqual(info.get("name"), "spotify")
        self.assertEqual(info.get("title"), "Spotify")
        self.assertEqual(info.get("source"), "snap")

        # Verify essential fields
        self.assertIn("version", info)
        self.assertTrue(len(str(info["version"]).strip()) > 0)

        self.assertIn("publisher", info)
        self.assertTrue(len(str(info["publisher"]).strip()) > 0)

        self.assertIn("store-url", info)
        self.assertTrue(str(info["store-url"]).startswith("http"))

    def test_get_package_detail_spotify_snap(self):
        """
        Verify pm.get_package_detail("spotify", source="snap") also routes to snap info.
        """
        detail = self.pm.get_package_detail("spotify", source="snap")
        self.assertIsInstance(detail, dict)
        self.assertEqual(detail.get("name"), "spotify")
        self.assertEqual(detail.get("title"), "Spotify")
        self.assertIn("store-url", detail)

    def test_unified_search_snap_and_container(self):
        """
        Test unified search with source='snap' and source='container'.
        """
        snap_results = self.pm.search("spotify", source="snap")
        self.assertIsInstance(snap_results, list)
        self.assertGreaterEqual(len(snap_results), 1)
        self.assertEqual(snap_results[0]["name"], "spotify")

        container_results = self.pm.search("blender", source="container")
        self.assertIsInstance(container_results, list)
        self.assertGreaterEqual(len(container_results), 1)
        self.assertEqual(container_results[0]["id"], "blender")


class TestSnapAurSeparation(unittest.TestCase):
    """
    Tests ensuring complete separation between AUR/Pacman and Snap Store packages:
    - Pacman/AUR installed packages do not cause is_snap_installed to return True.
    - Snap cards and detail views never rely on pm.is_installed.
    - Detail views resolve to native pacman/aur if package is locally installed,
      unless explicitly opened from the Snap Store page.
    - Search deduplicates Snaps when native packages exist.
    """

    def setUp(self):
        self.pm = PackageManager()

    def test_pacman_paru_package_does_not_make_is_snap_installed_true(self):
        """
        Verify that a package installed via pacman/paru (present in pm.installed_set)
        does NOT cause snap_mgr.is_snap_installed to return True.
        """
        with self.pm._lock:
            self.pm.installed_set.add("vlc")
            self.pm.installed_set.add("mock-aur-package")

        self.assertTrue(self.pm.is_installed("vlc"))
        self.assertTrue(self.pm.is_installed("mock-aur-package"))

        # Verify is_snap_installed only checks actual snap system, returning False
        with patch.object(self.pm.snap_mgr, "get_installed_snaps", return_value=[]), \
             patch("pathlib.Path.exists", return_value=False):
            self.assertFalse(self.pm.snap_mgr.is_snap_installed("vlc"))
            self.assertFalse(self.pm.snap_mgr.is_snap_installed("mock-aur-package"))

    def test_snap_card_and_detail_never_use_pm_is_installed(self):
        """
        Verify that Snap card creation and snap detail parsing call
        pm.snap_mgr.is_snap_installed and never pm.is_installed.
        """
        import aura_ui

        mock_window = MagicMock()
        mock_pm = MagicMock()
        mock_pm.is_installed.return_value = True  # Native pacman/paru is installed
        mock_pm.snap_mgr.is_snap_installed.return_value = False  # Snap is NOT installed
        mock_pm.snap_mgr.get_installed_snaps.return_value = []
        mock_window.pm = mock_pm

        # 1. Check _get_snap_detail
        detail = aura_ui.AuraWindow._get_snap_detail(mock_window, "vlc")
        self.assertFalse(
            detail["is_installed"],
            "Snap detail is_installed must be False when only pacman/paru is installed"
        )
        self.assertEqual(detail["source"], "snap")
        mock_pm.snap_mgr.is_snap_installed.assert_called_with("vlc")
        self.assertFalse(
            mock_pm.is_installed.called,
            "pm.is_installed must NEVER be called in _get_snap_detail"
        )

        # 2. Check _create_snap_app_card
        mock_pm.reset_mock()
        card = aura_ui.AuraWindow._create_snap_app_card(
            mock_window,
            {"name": "vlc", "title": "VLC", "summary": "Media player"}
        )
        self.assertIsNotNone(card)
        mock_pm.snap_mgr.is_snap_installed.assert_called_with("vlc")
        self.assertFalse(
            mock_pm.is_installed.called,
            "pm.is_installed must NEVER be called in _create_snap_app_card"
        )

    def test_open_package_detail_prioritizes_native_unless_on_snap_page(self):
        """
        Verify that _open_package_detail resolves locally installed packages
        to native pacman or aur unless explicitly opened from the snap page.
        """
        import aura_ui

        mock_window = MagicMock()
        mock_pm = MagicMock()
        mock_pm.is_installed.return_value = True
        mock_pm.packages = {"vlc": {"name": "vlc", "desc": "VLC media player"}}
        mock_window.pm = mock_pm

        # Scenario A: Opened from Browse / All Search / Discover
        mock_window.main_stack.get_visible_child_name.return_value = "browse"
        mock_window._previous_page = "browse"

        with patch("threading.Thread"):
            aura_ui.AuraWindow._open_package_detail(mock_window, "vlc", "snap")

        # Source must be corrected to pacman because package is native-installed
        mock_window._clear_detail_page_loading.assert_called_with("vlc", "pacman")

        # Scenario B: Opened explicitly from Snap Store page
        mock_window.reset_mock()
        mock_window.main_stack.get_visible_child_name.return_value = "snap"
        mock_window._previous_page = "snap"

        with patch("threading.Thread"):
            aura_ui.AuraWindow._open_package_detail(mock_window, "vlc", "snap")

        # Source must remain snap when explicitly on snap page
        mock_window._clear_detail_page_loading.assert_called_with("vlc", "snap")

    def test_normal_search_strictly_excludes_snap_and_docker_cards(self):
        """
        Verify that normal search (filter_mode in ('native', 'all', 'pacman', 'aur'))
        strictly queries Arch Linux repositories (Pacman & AUR) and TOTALLY EXCLUDES
        Snap and Docker apps. Zero snaps and zero docker cards must be in normal search.
        """
        import aura_ui

        for filter_mode in ("native", "all", "pacman", "aur"):
            mock_window = MagicMock()
            mock_window.current_filter = filter_mode
            mock_window.active_request_id = 0
            mock_window.pm.search.return_value = [
                {"name": "vlc", "title": "VLC Media Player", "source": "pacman"},
                {"name": "vlc-git", "title": "VLC Git", "source": "aur"}
            ]
            mock_window.pm.container_mgr.list_apps.return_value = [
                {"id": "vlc-docker", "name": "VLC Container"}
            ]

            captured_results = []
            mock_window._display_search_results = MagicMock(
                side_effect=lambda q, res: captured_results.extend(res)
            )

            with patch("threading.Thread", side_effect=lambda target, daemon=True: MagicMock(start=lambda: target())):
                with patch("gi.repository.GLib.idle_add", side_effect=lambda fn: fn()):
                    aura_ui.AuraWindow._trigger_search(mock_window, "vlc")

            # Must contain native packages
            self.assertGreaterEqual(
                len(captured_results), 1,
                f"Normal search ({filter_mode}) should return native packages"
            )
            # All results must strictly be native packages
            for res in captured_results:
                self.assertEqual(
                    res.get("_card_type"), "pkg",
                    f"Normal search ({filter_mode}) contained non-pkg card: {res.get('_card_type')}"
                )

            # TOTALLY EXCLUDE Snap and Docker
            snap_cards = [r for r in captured_results if r.get("_card_type") == "snap"]
            docker_cards = [r for r in captured_results if r.get("_card_type") == "docker"]
            self.assertEqual(len(snap_cards), 0, f"Normal search ({filter_mode}) must have 0 snap cards")
            self.assertEqual(len(docker_cards), 0, f"Normal search ({filter_mode}) must have 0 docker cards")

    def test_snap_search_strictly_isolated(self):
        """
        Verify that searching in Snap Store mode strictly returns Snap apps
        and zero pacman/aur packages or docker containers.
        """
        import aura_ui

        mock_window = MagicMock()
        mock_window.current_filter = "snap"
        mock_window.active_request_id = 0
        mock_window.pm.search.return_value = [{"name": "spotify", "source": "pacman"}]
        mock_window.pm.container_mgr.list_apps.return_value = [{"id": "spotify-docker"}]

        captured_results = []
        mock_window._display_search_results = MagicMock(
            side_effect=lambda q, res: captured_results.extend(res)
        )

        with patch("threading.Thread", side_effect=lambda target, daemon=True: MagicMock(start=lambda: target())):
            with patch("gi.repository.GLib.idle_add", side_effect=lambda fn: fn()):
                with patch("urllib.request.urlopen") as mock_url:
                    mock_resp = MagicMock()
                    mock_resp.read.return_value = b'{"results": []}'
                    mock_resp.__enter__.return_value = mock_resp
                    mock_resp.__exit__.return_value = None
                    mock_url.return_value = mock_resp

                    aura_ui.AuraWindow._trigger_search(mock_window, "spotify")

        self.assertGreaterEqual(len(captured_results), 1)
        for res in captured_results:
            self.assertEqual(res.get("_card_type"), "snap")
        self.assertEqual(len([r for r in captured_results if r.get("_card_type") == "pkg"]), 0)
        self.assertEqual(len([r for r in captured_results if r.get("_card_type") == "docker"]), 0)

    def test_docker_search_strictly_isolated(self):
        """
        Verify that searching in Docker mode strictly returns Docker apps
        and zero pacman/aur packages or snaps.
        """
        import aura_ui

        mock_window = MagicMock()
        mock_window.current_filter = "docker"
        mock_window.active_request_id = 0
        mock_window.pm.search.return_value = [{"name": "blender", "source": "pacman"}]
        mock_window.pm.container_mgr.list_apps.return_value = [
            {"id": "blender", "name": "Blender Container", "desc": "3D suite in Docker"}
        ]

        captured_results = []
        mock_window._display_search_results = MagicMock(
            side_effect=lambda q, res: captured_results.extend(res)
        )

        with patch("threading.Thread", side_effect=lambda target, daemon=True: MagicMock(start=lambda: target())):
            with patch("gi.repository.GLib.idle_add", side_effect=lambda fn: fn()):
                aura_ui.AuraWindow._trigger_search(mock_window, "blender")

        self.assertEqual(len(captured_results), 1)
        self.assertEqual(captured_results[0].get("_card_type"), "docker")
        self.assertEqual(len([r for r in captured_results if r.get("_card_type") == "pkg"]), 0)
        self.assertEqual(len([r for r in captured_results if r.get("_card_type") == "snap"]), 0)

    def test_display_search_results_status_label_ecosystem_scope(self):
        """
        Verify _display_search_results updates count label with specific scope text:
        - Arch Linux Repositories (Pacman & AUR)
        - Canonical Snap Store
        - Docker App Sandbox
        """
        import aura_ui
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        mock_window = MagicMock()
        mock_window.browse_status_label = Gtk.Label()
        mock_window.browse_flow_box = MagicMock()

        # 1. Native / Arch
        mock_window.current_filter = "native"
        aura_ui.AuraWindow._display_search_results(
            mock_window, "vlc", [{"_card_type": "pkg", "data": {"name": "vlc"}}]
        )
        self.assertEqual(
            mock_window.browse_status_label.get_text(),
            "Found 1 results for 'vlc' in Arch Linux Repositories (Pacman & AUR)"
        )

        # 2. Canonical Snap Store
        mock_window.current_filter = "snap"
        aura_ui.AuraWindow._display_search_results(
            mock_window, "spotify", [{"_card_type": "snap", "data": {"name": "spotify"}}]
        )
        self.assertEqual(
            mock_window.browse_status_label.get_text(),
            "Found 1 results for 'spotify' in Canonical Snap Store"
        )

        # 3. Docker App Sandbox
        mock_window.current_filter = "docker"
        aura_ui.AuraWindow._display_search_results(
            mock_window, "redis", [{"_card_type": "docker", "data": {"id": "redis"}}]
        )
        self.assertEqual(
            mock_window.browse_status_label.get_text(),
            "Found 1 results for 'redis' in Docker App Sandbox"
        )

    def test_browse_page_ecosystem_capsule_structure(self):
        """
        Verify that _build_browse_page creates the 3 clean primary ecosystem options:
        'native' (Arch Linux (Pacman & AUR)), 'snap' (Snap Store), and 'docker' (Docker Apps),
        with 'native' selected by default.
        """
        import aura_ui
        import gi
        gi.require_version("Gtk", "4.0")
        from gi.repository import Gtk

        mock_window = MagicMock()
        mock_window.search_entry = Gtk.SearchEntry()
        mock_window._create_symmetric_grid = lambda **kwargs: Gtk.FlowBox()

        aura_ui.AuraWindow._build_browse_page(mock_window)

        self.assertIn("native", mock_window.filter_buttons)
        self.assertIn("snap", mock_window.filter_buttons)
        self.assertIn("docker", mock_window.filter_buttons)
        self.assertEqual(len(mock_window.filter_buttons), 3)

        # 'native' must be active by default
        self.assertTrue(mock_window.filter_buttons["native"].get_active())
        self.assertFalse(mock_window.filter_buttons["snap"].get_active())
        self.assertFalse(mock_window.filter_buttons["docker"].get_active())


if __name__ == "__main__":
    unittest.main()
