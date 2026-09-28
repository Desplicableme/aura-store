#!/usr/bin/env python3
"""
Unit and Integration Tests for Search UI, Backspace Stability, and Fuzzy Search in Aura.
Tests cover:
- Sidebar search activation transfers text and focus to browse search entry.
- Backspacing down to empty in browse search entry strictly stays on "browse" page (never switches to "discover").
- Empty search state displays browse_hero_card, clears flow_box, and clears status shimmer.
- Stop-search (Escape / Clear button) cleanly clears text without page jumping.
- Enhanced fuzzy search: typo tolerance, word boundaries, prefix matches, single-letter isolation.
"""

import os
import sys
import time
import unittest
from unittest.mock import MagicMock

import gi
gi.require_version("Gtk", "4.0")
from gi.repository import Gtk, GLib

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
from aura_ui import AuraWindow
from aura_backend import fuzzy_score, is_damerau_levenshtein_one


def pump_main_loop(duration: float = 0.35):
    """Pump GLib default main context for specified duration."""
    deadline = time.time() + duration
    while time.time() < deadline:
        GLib.MainContext.default().iteration(False)
        time.sleep(0.01)


class TestSearchUIAndBackspaceStability(unittest.TestCase):
    """Test suite for GTK4 Search UI, backspacing stability, and page transitions."""

    @classmethod
    def setUpClass(cls):
        cls.app = Gtk.Application(application_id="org.archlinux.aura.search_ui_test")
        cls.app.register(None)

    def setUp(self):
        self.pm = MagicMock()
        self.pm.installed_set = {"bash", "linux"}
        self.pm.installed_versions = {"bash": "5.2.037-1", "linux": "6.11.arch1-1"}
        self.pm.packages = {}
        self.pm.upgradable_list = []
        self.pm.get_dynamic_featured_apps.return_value = []
        self.win = AuraWindow(self.app, self.pm)

    def test_sidebar_search_activates_browse_page(self):
        """Typing in sidebar search entry switches to browse page and transfers text."""
        self.assertEqual(self.win.main_stack.get_visible_child_name(), "discover")
        self.win.search_entry.set_text("firefox")
        pump_main_loop(0.35)

        self.assertEqual(self.win.main_stack.get_visible_child_name(), "browse")
        self.assertEqual(self.win.browse_search_entry.get_text(), "firefox")
        self.assertFalse(self.win.browse_hero_card.get_visible())

    def test_backspacing_to_empty_retains_browse_page(self):
        """
        Critical bug fix verification:
        Backspacing to empty query must NEVER jump back to 'discover' or flash carousel dots.
        Must stay on 'browse' with clean hero guidance.
        """
        # Start search
        self.win.search_entry.set_text("firefox")
        pump_main_loop(0.35)
        self.assertEqual(self.win.main_stack.get_visible_child_name(), "browse")

        # Simulate backspacing character by character down to empty
        for step in ["firefo", "firef", "fire", "fir", "fi", "f", ""]:
            self.win.browse_search_entry.set_text(step)
            pump_main_loop(0.06)
            self.assertEqual(
                self.win.main_stack.get_visible_child_name(), "browse",
                f"Page jumped away from 'browse' on query '{step}'!"
            )

        # Final state check on empty query
        pump_main_loop(0.30)
        self.assertEqual(self.win.main_stack.get_visible_child_name(), "browse")
        self.assertTrue(self.win.browse_hero_card.get_visible())
        self.assertEqual(self.win.browse_status_label.get_text(), "")
        self.assertFalse(self.win.browse_status_label.has_css_class("mac-loading-shimmer"))

    def test_stop_search_clears_entry_and_retains_browse(self):
        """Clear button / Escape on browse_search_entry resets text and stays on browse."""
        self.win.main_stack.set_visible_child_name("browse")
        self.win.browse_search_entry.set_text("blender")
        pump_main_loop(0.35)

        self.assertEqual(self.win.browse_search_entry.get_text(), "blender")
        self.win.browse_search_entry.emit("stop-search")
        pump_main_loop(0.35)

        self.assertEqual(self.win.browse_search_entry.get_text(), "")
        self.assertEqual(self.win.main_stack.get_visible_child_name(), "browse")
        self.assertTrue(self.win.browse_hero_card.get_visible())

    def test_no_forced_page_jump_on_empty_search_changed(self):
        """Calling _on_search_changed or _on_browse_search_changed with empty text never changes stack to discover."""
        self.win.main_stack.set_visible_child_name("browse")
        self.win.browse_search_entry.set_text("")
        self.win._on_browse_search_changed(self.win.browse_search_entry)
        self.assertEqual(self.win.main_stack.get_visible_child_name(), "browse")

        self.win.search_entry.set_text("")
        self.win._on_sidebar_search_activate(self.win.search_entry)
        self.assertEqual(self.win.main_stack.get_visible_child_name(), "browse")

    def test_docker_search_async_animation_and_status(self):
        """Docker search uses debounced async searching with an animated spinner and status update."""
        self.win.main_stack.set_visible_child_name("docker")
        self.assertTrue(hasattr(self.win, "container_spinner"))
        self.assertTrue(hasattr(self.win, "container_status_lbl"))

        # Trigger search
        self.win.container_search_entry.set_text("mysql")
        pump_main_loop(0.4)

        # After debounced search completes
        self.assertIn("mysql", self.win.container_status_lbl.get_text().lower())
        self.assertFalse(self.win.container_spinner.get_visible())

    def test_snap_search_async_animation_and_catalog(self):
        """Snap Store search uses debounced async searching with an animated spinner and status."""
        self.win.main_stack.set_visible_child_name("snap")
        self.assertTrue(hasattr(self.win, "snap_spinner"))
        self.assertTrue(hasattr(self.win, "snap_status_lbl"))

        self.pm.snap_mgr.search_snaps.return_value = [{"name": "code", "title": "Visual Studio Code"}]
        self.win._load_snap_view("code")
        self.assertTrue(self.win.snap_spinner.get_visible())
        pump_main_loop(0.5)

        # Status should report results
        self.assertIn("code", self.win.snap_status_lbl.get_text().lower())
        self.assertFalse(self.win.snap_spinner.get_visible())

    def test_browse_search_spinner_animation(self):
        """Browse search bar has dedicated spinner that activates during search and hides when done."""
        self.win.main_stack.set_visible_child_name("browse")
        self.assertTrue(hasattr(self.win, "browse_spinner"))
        self.pm.search.return_value = []
        self.win._trigger_search("firefox")
        self.assertTrue(self.win.browse_spinner.get_visible())
        pump_main_loop(0.6)
        self.assertFalse(self.win.browse_spinner.get_visible())

    def test_browse_page_scrolled_window_and_hero_symmetry(self):
        """Browse page is a ScrolledWindow with centered hero card and rigid sidebar width."""
        self.assertIsInstance(self.win.browse_page, Gtk.ScrolledWindow)
        self.assertEqual(self.win.browse_hero_card.get_halign(), Gtk.Align.CENTER)
        self.assertEqual(self.win.sidebar.get_size_request()[0], 220)

    def test_suggestion_chip_triggers_search(self):
        """Clicking a suggestion chip populates browse search entry and executes search."""
        self.win.main_stack.set_visible_child_name("browse")
        self.win._on_suggestion_chip_clicked("browser")
        self.assertEqual(self.win.browse_search_entry.get_text(), "browser")
        pump_main_loop(0.4)
        self.assertFalse(self.win.browse_hero_card.get_visible())
        self.assertTrue(self.win.browse_results_container.get_visible())


class TestFuzzySearchEngine(unittest.TestCase):
    """Test suite for reimagined fuzzy search algorithm in aura_backend."""

    def test_exact_and_prefix_scores(self):
        """Exact matches score highest, followed by prefix matches."""
        exact = fuzzy_score("firefox", "firefox")
        prefix = fuzzy_score("fire", "firefox")
        subseq = fuzzy_score("ffox", "firefox")

        self.assertGreater(exact, prefix)
        self.assertGreater(prefix, subseq)
        self.assertGreaterEqual(exact, 9000)
        self.assertGreaterEqual(prefix, 6000)

    def test_word_boundary_matching(self):
        """Query matching beginning of words in package name scores high."""
        calc = fuzzy_score("calc", "gnome-calculator")
        code = fuzzy_score("code", "visual-studio-code-bin")
        self.assertGreaterEqual(calc, 4000)
        self.assertGreaterEqual(code, 4000)

    def test_typo_tolerance_damerau_levenshtein(self):
        """Damerau-Levenshtein distance <= 1 matches typos like transposition and single edit."""
        self.assertTrue(is_damerau_levenshtein_one("firfox", "firefox"))
        self.assertTrue(is_damerau_levenshtein_one("chorme", "chrome"))
        self.assertTrue(is_damerau_levenshtein_one("spoify", "spotify"))
        self.assertTrue(is_damerau_levenshtein_one("dicord", "discord"))

        # Scoring for typo
        score_typo = fuzzy_score("firfox", "firefox")
        self.assertGreater(score_typo, 0, "Typo 'firfox' should match 'firefox'")

    def test_single_character_query_isolation(self):
        """Single-letter queries only match packages starting with that letter (no random subsequences)."""
        score_vlc = fuzzy_score("v", "vlc")
        score_gst = fuzzy_score("v", "gstreamer")

        self.assertGreater(score_vlc, 0)
        self.assertEqual(score_gst, 0, "Single char 'v' must not match 'gstreamer'")


if __name__ == "__main__":
    unittest.main()
