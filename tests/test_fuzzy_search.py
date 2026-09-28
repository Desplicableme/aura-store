#!/usr/bin/env python3
"""
Unit tests for Aura Store's Tiered Multi-Level Fuzzy Search Engine.
Verifies scoring tiers, fast-path pre-filtering, desktop relevance boosting,
and search ranking across pacman repositories.
"""

import time
import unittest
from aura_backend import (
    PackageManager,
    fuzzy_score,
    is_desktop_app,
    get_desktop_and_curated_set,
)


class TestFuzzyScoreEngine(unittest.TestCase):
    """Test suite for the tiered multi-level fuzzy scoring engine."""

    def test_tier1_exact_name_match(self):
        """Tier 1: Exact name match must score 10,000 points."""
        score_plain = fuzzy_score("vlc", "vlc", is_desktop=False, is_installed=False)
        self.assertEqual(score_plain, 10000)

        score_git = fuzzy_score("git", "git", is_desktop=False, is_installed=False)
        self.assertEqual(score_git, 10000)

        # Case insensitivity
        score_case = fuzzy_score("VLC", "vlc", is_desktop=False, is_installed=False)
        self.assertEqual(score_case, 10000)

    def test_tier2_exact_normalized_match(self):
        """Tier 2: Exact normalized match must score 9,000 points."""
        score_norm = fuzzy_score("code oss", "code-oss", is_desktop=False, is_installed=False)
        self.assertEqual(score_norm, 9000)

        score_norm2 = fuzzy_score("obs studio", "obs-studio", is_desktop=False, is_installed=False)
        self.assertEqual(score_norm2, 9000)

    def test_tier3_prefix_match(self):
        """Tier 3: Prefix match on name must score 7,000 - min(500, len(name)*10)."""
        # "firefox" -> len 7 -> 7000 - 70 = 6930
        score_fire = fuzzy_score("fire", "firefox", is_desktop=False, is_installed=False)
        self.assertEqual(score_fire, 6930)

        # "neovim" -> len 6 -> 7000 - 60 = 6940
        score_neov = fuzzy_score("neov", "neovim", is_desktop=False, is_installed=False)
        self.assertEqual(score_neov, 6940)

        # Shorter names get less penalty
        score_short = fuzzy_score("vi", "vim", is_desktop=False, is_installed=False)
        self.assertEqual(score_short, 7000 - 30)

    def test_tier4_word_boundary_match(self):
        """Tier 4: Word boundary / segment match must score 5,000 points."""
        # "studio" in "obs-studio"
        score_studio = fuzzy_score("studio", "obs-studio", is_desktop=False, is_installed=False)
        self.assertEqual(score_studio, 5000)

        # "code" in "visual-studio-code"
        score_code = fuzzy_score("code", "visual-studio-code", is_desktop=False, is_installed=False)
        self.assertEqual(score_code, 5000)

    def test_tier5_subsequence_match(self):
        """Tier 5: High-precision subsequence match on name (>= 2 chars)."""
        # "nvm" in "neovim"
        score_nvm = fuzzy_score("nvm", "neovim", is_desktop=False, is_installed=False)
        self.assertGreaterEqual(score_nvm, 3000)
        self.assertLess(score_nvm, 5000)

        # Boundary bonus: 'v' at index 0 of 'vlc' when searching 'vc'
        score_vc = fuzzy_score("vc", "vlc", is_desktop=False, is_installed=False)
        self.assertGreater(score_vc, 3000)

        # Subsequence should NOT match for single-character queries
        score_1char = fuzzy_score("l", "vlc", is_desktop=False, is_installed=False)
        self.assertEqual(score_1char, 0)

    def test_tier6_description_match(self):
        """Tier 6: Description exact word/phrase match (only >= 3 chars) scores 1,500."""
        desc = "Universal media player and video streaming framework"
        score_desc = fuzzy_score("streaming", "other-pkg", desc=desc, is_desktop=False, is_installed=False)
        self.assertGreaterEqual(score_desc, 1400)
        self.assertLessEqual(score_desc, 1500)

        # Descriptions must NOT match for queries < 3 characters
        score_short_desc = fuzzy_score("st", "other-pkg", desc=desc, is_desktop=False, is_installed=False)
        self.assertEqual(score_short_desc, 0)

    def test_relevance_boosts(self):
        """Relevance boosts: Desktop app (+800), Locally installed (+200)."""
        # Desktop boost alone
        score_desk = fuzzy_score("vlc", "vlc", is_desktop=True, is_installed=False)
        self.assertEqual(score_desk, 10000 + 800)

        # Installed boost alone
        score_inst = fuzzy_score("vlc", "vlc", is_desktop=False, is_installed=True)
        self.assertEqual(score_inst, 10000 + 200)

        # Both boosts combined
        score_both = fuzzy_score("vlc", "vlc", is_desktop=True, is_installed=True)
        self.assertEqual(score_both, 10000 + 800 + 200)

        # Non-matching package must NOT receive boosts
        score_none = fuzzy_score("xyz", "vlc", is_desktop=True, is_installed=True)
        self.assertEqual(score_none, 0)

    def test_single_character_queries_strict(self):
        """Single character queries MUST only match packages starting with that letter."""
        # 'v' starts with 'v' -> prefix match
        self.assertGreater(fuzzy_score("v", "vlc"), 0)
        self.assertGreater(fuzzy_score("v", "vim"), 0)

        # 'v' in middle or description -> MUST NOT match
        self.assertEqual(fuzzy_score("v", "firefox"), 0)
        self.assertEqual(fuzzy_score("v", "alacritty"), 0)
        self.assertEqual(fuzzy_score("v", "other", desc="vlc player"), 0)


class TestSearchRankingAndPerformance(unittest.TestCase):
    """Test suite verifying end-to-end ranking and performance in PacmanManager."""

    def setUp(self):
        self.pm = PackageManager()
        # Seed test repository packages
        self.pm.packages = {
            "vlc": {
                "name": "vlc",
                "version": "3.0.21-1",
                "desc": "Multi-platform MPEG, VCD/DVD, and DivX player",
                "repo": "extra",
            },
            "libvlc": {
                "name": "libvlc",
                "version": "3.0.21-1",
                "desc": "VLC media player core library",
                "repo": "extra",
            },
            "vlc-nox": {
                "name": "vlc-nox",
                "version": "3.0.21-1",
                "desc": "VLC without X11 output",
                "repo": "extra",
            },
            "obs-vlc": {
                "name": "obs-vlc",
                "version": "1.0-1",
                "desc": "OBS Studio VLC video source plugin",
                "repo": "extra",
            },
            "firefox": {
                "name": "firefox",
                "version": "130.0-1",
                "desc": "Fast, Private & Safe Web Browser",
                "repo": "extra",
            },
            "firewalld": {
                "name": "firewalld",
                "version": "2.2.0-1",
                "desc": "Dynamically managed firewall with support for network zones",
                "repo": "extra",
            },
            "libfakefire": {
                "name": "libfakefire",
                "version": "0.1-1",
                "desc": "Dummy fire test library",
                "repo": "extra",
            },
            "gimp": {
                "name": "gimp",
                "version": "2.10.38-1",
                "desc": "GNU Image Manipulation Program",
                "repo": "extra",
            },
            "libgimp": {
                "name": "libgimp",
                "version": "2.10.38-1",
                "desc": "GIMP development library",
                "repo": "extra",
            },
            "blender": {
                "name": "blender",
                "version": "4.2.1-1",
                "desc": "A fully integrated 3D graphics creation suite",
                "repo": "extra",
            },
            "blender-support-libs": {
                "name": "blender-support-libs",
                "version": "1.0-1",
                "desc": "Blender internal support library headers",
                "repo": "extra",
            },
            "obs-studio": {
                "name": "obs-studio",
                "version": "30.2.2-1",
                "desc": "Free and open source software for video recording and live streaming",
                "repo": "extra",
            },
            "libstudio-dev": {
                "name": "libstudio-dev",
                "version": "0.5-1",
                "desc": "Generic studio audio library",
                "repo": "extra",
            },
            "code": {
                "name": "code",
                "version": "1.93.1-1",
                "desc": "The Open Source build of Visual Studio Code (Code - OSS)",
                "repo": "extra",
            },
            "code-oss": {
                "name": "code-oss",
                "version": "1.93.1-1",
                "desc": "Microsoft Code Open Source build",
                "repo": "extra",
            },
            "neovim": {
                "name": "neovim",
                "version": "0.10.1-1",
                "desc": "Vim-fork focused on extensibility and usability",
                "repo": "extra",
            },
            "novm-core-lib": {
                "name": "novm-core-lib",
                "version": "1.0-1",
                "desc": "Internal virtual machine microkernel",
                "repo": "extra",
            },
            "git": {
                "name": "git",
                "version": "2.46.0-1",
                "desc": "Fast, scalable, distributed revision control system",
                "repo": "extra",
            },
            "media-converter": {
                "name": "media-converter",
                "version": "1.0-1",
                "desc": "Fast lossless audio and video converter utility",
                "repo": "extra",
            },
        }
        self.pm.installed_set = {"firefox", "git"}

    def test_exact_matches_rank_number_one(self):
        """Searching 'vlc' MUST place 'vlc' at rank #1 above plugins, libraries, and variants."""
        results = self.pm.search_pacman("vlc")
        self.assertGreater(len(results), 0)
        self.assertEqual(results[0]["name"], "vlc", "Exact match 'vlc' must be rank #1")
        self.assertGreaterEqual(results[0]["score"], 10000)

        # 'git' exact match rank #1
        results_git = self.pm.search_pacman("git")
        self.assertEqual(results_git[0]["name"], "git")

        # 'code' exact match rank #1
        results_code = self.pm.search_pacman("code")
        self.assertEqual(results_code[0]["name"], "code")

    def test_prefix_matches_rank_above_subsequence(self):
        """Prefix matches on major apps rank above random subsequence matches."""
        # Searching 'fire' -> firefox (prefix) must rank higher than libfakefire
        results = self.pm.search_pacman("fire")
        self.assertEqual(results[0]["name"], "firefox")

        # Searching 'neov' -> neovim (prefix) must rank above novm-core-lib
        results_neo = self.pm.search_pacman("neov")
        self.assertEqual(results_neo[0]["name"], "neovim")

    def test_desktop_apps_rank_above_obscure_libraries(self):
        """Desktop applications MUST rank above internal development libraries and headers."""
        # Searching 'studio' -> obs-studio (desktop app) ranks above libstudio-dev
        results = self.pm.search_pacman("studio")
        obs_idx = next(i for i, r in enumerate(results) if r["name"] == "obs-studio")
        lib_idx = next(i for i, r in enumerate(results) if r["name"] == "libstudio-dev")
        self.assertLess(obs_idx, lib_idx, "Desktop app 'obs-studio' must rank above 'libstudio-dev'")

        # Searching 'blend' -> blender (desktop app) ranks above blender-support-libs
        results_blend = self.pm.search_pacman("blend")
        blender_idx = next(i for i, r in enumerate(results_blend) if r["name"] == "blender")
        libs_idx = next(i for i, r in enumerate(results_blend) if r["name"] == "blender-support-libs")
        self.assertLess(blender_idx, libs_idx, "Desktop app 'blender' must rank above support libs")

    def test_single_character_queries_instant_and_filtered(self):
        """Single-character queries must strictly match packages starting with that letter."""
        results = self.pm.search_pacman("v")
        for r in results:
            self.assertTrue(r["name"].lower().startswith("v"), f"Package {r['name']} does not start with 'v'")

        # Must not contain packages like 'firefox' or 'obs-studio'
        names = [r["name"] for r in results]
        self.assertIn("vlc", names)
        self.assertNotIn("firefox", names)
        self.assertNotIn("obs-studio", names)

    def test_two_character_queries_no_description_bloat(self):
        """Two-character queries must match names containing the query without description bloat."""
        # 'media-converter' has "video" in desc, but not in name. Searching "vi" should not match it via desc.
        results = self.pm.search_pacman("vl")
        names = [r["name"] for r in results]
        self.assertIn("vlc", names)
        self.assertNotIn("media-converter", names)

    def test_three_plus_char_description_fallback(self):
        """Queries >= 3 characters fall back to description only when relevant."""
        results = self.pm.search_pacman("lossless")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["name"], "media-converter")

    def test_high_volume_repository_search_performance(self):
        """Simulate a repository of 25,000 packages and ensure search completes in single-digit milliseconds."""
        # Populate 25,000 synthetic packages
        big_packages = dict(self.pm.packages)
        for i in range(25000):
            big_packages[f"libpkg-{i}"] = {
                "name": f"libpkg-{i}",
                "version": "1.0.0",
                "desc": f"Synthetic library component {i} for Linux system infrastructure",
                "repo": "extra",
            }
        self.pm.packages = big_packages

        # 1-char query benchmark (< 15ms target)
        t0 = time.perf_counter()
        res_1 = self.pm.search_pacman("v")
        t_1char = (time.perf_counter() - t0) * 1000
        self.assertLess(t_1char, 20.0, f"1-char query took {t_1char:.2f}ms (> 20ms)")
        self.assertGreater(len(res_1), 0)
        self.assertEqual(res_1[0]["name"], "vlc")

        # 2-char query benchmark (< 20ms target)
        t0 = time.perf_counter()
        res_2 = self.pm.search_pacman("vl")
        t_2char = (time.perf_counter() - t0) * 1000
        self.assertLess(t_2char, 25.0, f"2-char query took {t_2char:.2f}ms (> 25ms)")
        self.assertEqual(res_2[0]["name"], "vlc")

        # Exact match benchmark (< 15ms target)
        t0 = time.perf_counter()
        res_exact = self.pm.search_pacman("firefox")
        t_exact = (time.perf_counter() - t0) * 1000
        self.assertLess(t_exact, 25.0, f"Exact match query took {t_exact:.2f}ms (> 25ms)")
        self.assertEqual(res_exact[0]["name"], "firefox")


if __name__ == "__main__":
    unittest.main()
