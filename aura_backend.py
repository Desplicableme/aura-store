#!/usr/bin/env python3
"""
Aura Package Hub - Backend Module
Handles:
- Pacman local repository database indexing & caching
- Installed package querying & status tracking
- Real-time system updates checking (official + AUR)
- AUR RPC query and paru fallback
- High-performance fuzzy matching with strict priority (Pacman > Paru)
- Authentic Linux application icon mapping
- In-process background installation & updates with real-time progress callbacks
"""

import os
import re
import sys
import glob
import json
import time
import random
import pickle
import shutil
import tarfile
import threading
import subprocess
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any, Callable

CACHE_DIR = Path.home() / ".cache" / "aura"
CACHE_FILE = CACHE_DIR / "sync_cache.pkl"
UPDATES_CACHE_FILE = CACHE_DIR / "updates_cache.json"
FEATURED_STATE_FILE = CACHE_DIR / "featured_state.json"
SYNC_DIR = Path("/var/lib/pacman/sync")
ASKPASS_SCRIPT = Path.home() / ".local" / "share" / "aura" / "aura-askpass"

def _load_featured_state() -> Dict[str, Any]:
    """Load persistent featured rotation state from disk."""
    if FEATURED_STATE_FILE.exists():
        try:
            with open(FEATURED_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
                if isinstance(data, dict):
                    return {
                        "offset": int(data.get("offset", 0)),
                        "last_shown": [str(x) for x in data.get("last_shown", []) if isinstance(x, str)],
                    }
        except Exception:
            pass
    return {"offset": 0, "last_shown": []}

def _save_featured_state(offset: int, last_shown: List[str]):
    """Save persistent featured rotation state to disk immediately."""
    try:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmp_file = FEATURED_STATE_FILE.with_suffix(".tmp")
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump({"offset": int(offset), "last_shown": list(last_shown)}, f, indent=2)
        tmp_file.replace(FEATURED_STATE_FILE)
    except Exception:
        pass


# Apple-style Curated Categories (Symmetric, No Emojis, Authentic Functional Channels)
CURATED_CATEGORIES = [
    {
        "id": "essential",
        "category": "Essential Applications",
        "subtitle": "Must-have applications for everyday desktop workflow",
        "icon": "starred-symbolic",
        "apps": [
            {"name": "firefox", "title": "Firefox", "desc": "Fast, privacy-respecting browser", "source": "pacman"},
            {"name": "code", "title": "Visual Studio Code", "desc": "Code editor and developer platform", "source": "pacman"},
            {"name": "discord", "title": "Discord", "desc": "Voice and text chat for communities", "source": "pacman"},
            {"name": "steam", "title": "Steam", "desc": "Gaming platform and game launcher", "source": "pacman"},
            {"name": "spotify", "title": "Spotify", "desc": "Music streaming service desktop app", "source": "aur"},
            {"name": "proton-vpn-gtk-app", "title": "Proton VPN", "desc": "Secure VPN client with WireGuard", "source": "pacman"},
            {"name": "telegram-desktop", "title": "Telegram Desktop", "desc": "Fast and secure cloud-based messaging", "source": "pacman"},
            {"name": "vlc", "title": "VLC Media Player", "desc": "Universal multimedia player and framework", "source": "pacman"},
            {"name": "obs-studio", "title": "OBS Studio", "desc": "High-performance screen recorder and livestreamer", "source": "pacman"},
            {"name": "gimp", "title": "GIMP", "desc": "Advanced image manipulation and artwork editor", "source": "pacman"},
            {"name": "blender", "title": "Blender", "desc": "Full 3D creation suite and animation editor", "source": "pacman"},
            {"name": "bitwarden", "title": "Bitwarden", "desc": "Secure open-source password vault", "source": "pacman"},
        ]
    },
    {
        "id": "dev",
        "category": "Development",
        "subtitle": "High-performance compilers, code editors, and terminals",
        "icon": "utilities-terminal-symbolic",
        "apps": [
            {"name": "code", "title": "Visual Studio Code", "desc": "Code editor and developer platform", "source": "pacman"},
            {"name": "neovim", "title": "Neovim", "desc": "Extensible high-speed text editor", "source": "pacman"},
            {"name": "git", "title": "Git", "desc": "Fast distributed version control", "source": "pacman"},
            {"name": "lazygit", "title": "LazyGit", "desc": "Simple terminal UI for git commands", "source": "pacman"},
            {"name": "alacritty", "title": "Alacritty", "desc": "Fast GPU-accelerated terminal", "source": "pacman"},
            {"name": "kitty", "title": "Kitty", "desc": "Feature-rich modern terminal", "source": "pacman"},
            {"name": "zed", "title": "Zed", "desc": "High-performance multiplayer code editor", "source": "aur"},
            {"name": "postman-bin", "title": "Postman", "desc": "API development and testing platform", "source": "aur"},
            {"name": "docker", "title": "Docker", "desc": "Enterprise container platform and tooling", "source": "pacman"},
            {"name": "rust", "title": "Rust & Cargo", "desc": "Modern memory-safe systems programming language", "source": "pacman"},
            {"name": "go", "title": "Go", "desc": "Fast, compiled concurrency-focused language", "source": "pacman"},
            {"name": "dbeaver", "title": "DBeaver", "desc": "Universal SQL database client and GUI", "source": "pacman"},
        ]
    },
    {
        "id": "productivity",
        "category": "Productivity",
        "subtitle": "Office suites, knowledge bases, and focused writing",
        "icon": "x-office-document-symbolic",
        "apps": [
            {"name": "libreoffice-fresh", "title": "LibreOffice", "desc": "Comprehensive office suite for docs and sheets", "source": "pacman"},
            {"name": "obsidian", "title": "Obsidian", "desc": "Knowledge base and connected markdown notes", "source": "aur"},
            {"name": "thunar", "title": "Thunar", "desc": "Fast lightweight desktop file manager", "source": "pacman"},
            {"name": "micro", "title": "Micro Editor", "desc": "Intuitive terminal text editor with mouse support", "source": "pacman"},
            {"name": "fastfetch", "title": "Fastfetch", "desc": "High-speed system information tool", "source": "pacman"},
            {"name": "pavucontrol", "title": "Volume Control", "desc": "Advanced PulseAudio and PipeWire audio control", "source": "pacman"},
            {"name": "joplin-desktop", "title": "Joplin", "desc": "Secure markdown note taking with cloud sync", "source": "aur"},
            {"name": "onlyoffice-bin", "title": "OnlyOffice", "desc": "Collaborative document and spreadsheet editor", "source": "aur"},
            {"name": "anytype-bin", "title": "Anytype", "desc": "Privacy-first decentralized knowledge base", "source": "aur"},
            {"name": "evince", "title": "Evince", "desc": "Document viewer for PDF, PostScript, and DjVu", "source": "pacman"},
            {"name": "krita", "title": "Krita", "desc": "Professional free and open source painting program", "source": "pacman"},
            {"name": "foliate", "title": "Foliate", "desc": "Modern ebook reader with distraction-free layout", "source": "pacman"},
        ]
    },
    {
        "id": "privacy",
        "category": "Internet & Privacy",
        "subtitle": "Secure browsers, encrypted messaging, and privacy tools",
        "icon": "security-high-symbolic",
        "apps": [
            {"name": "brave-bin", "title": "Brave Browser", "desc": "Privacy-focused browser with ad and tracker blocking", "source": "aur"},
            {"name": "torbrowser-launcher", "title": "Tor Browser", "desc": "Secure anonymous browsing over the Tor network", "source": "pacman"},
            {"name": "keepassxc", "title": "KeepassXC", "desc": "Offline cross-platform password database", "source": "pacman"},
            {"name": "proton-vpn-gtk-app", "title": "Proton VPN", "desc": "Encrypted VPN client with Kill Switch", "source": "pacman"},
            {"name": "wireguard-tools", "title": "WireGuard", "desc": "Extremely fast modern encrypted VPN tunnel", "source": "pacman"},
            {"name": "mullvad-vpn-bin", "title": "Mullvad VPN", "desc": "Privacy-focused WireGuard VPN desktop app", "source": "aur"},
            {"name": "chromium", "title": "Chromium", "desc": "Open-source web browser foundation", "source": "pacman"},
            {"name": "signal-desktop", "title": "Signal Desktop", "desc": "Private end-to-end encrypted messaging", "source": "pacman"},
            {"name": "wireshark-qt", "title": "Wireshark", "desc": "Network packet analyzer and traffic inspector", "source": "pacman"},
            {"name": "bleachbit", "title": "BleachBit", "desc": "Clean caches, free disk space, and guard privacy", "source": "pacman"},
            {"name": "tailscale", "title": "Tailscale", "desc": "Zero config mesh VPN for secure device networks", "source": "pacman"},
            {"name": "thunderbird", "title": "Thunderbird", "desc": "Feature-packed email, calendar, and contacts client", "source": "pacman"},
        ]
    },
    {
        "id": "media",
        "category": "Media & Creative",
        "subtitle": "Audio, video streaming, recording, and digital artistry",
        "icon": "applications-graphics-symbolic",
        "apps": [
            {"name": "vlc", "title": "VLC Media Player", "desc": "Plays every audio and video codec natively", "source": "pacman"},
            {"name": "spotify", "title": "Spotify", "desc": "Stream millions of songs and podcasts", "source": "aur"},
            {"name": "gimp", "title": "GIMP", "desc": "GNU Image Manipulation and photo editing tool", "source": "pacman"},
            {"name": "inkscape", "title": "Inkscape", "desc": "Professional vector graphics illustrator", "source": "pacman"},
            {"name": "audacity", "title": "Audacity", "desc": "Multi-track audio editor and recorder", "source": "pacman"},
            {"name": "kdenlive", "title": "Kdenlive", "desc": "Powerful non-linear multi-track video editor", "source": "pacman"},
            {"name": "blender", "title": "Blender", "desc": "World-class 3D modeling, rendering, and VFX suite", "source": "pacman"},
            {"name": "obs-studio", "title": "OBS Studio", "desc": "Live video streaming and desktop screen capture", "source": "pacman"},
            {"name": "handbrake", "title": "HandBrake", "desc": "Fast universal video transcoder and converter", "source": "pacman"},
            {"name": "mpv", "title": "mpv", "desc": "Minimalist, powerful GPU-accelerated video player", "source": "pacman"},
            {"name": "darktable", "title": "Darktable", "desc": "Virtual lighttable and raw photo developer", "source": "pacman"},
            {"name": "ardour", "title": "Ardour", "desc": "Professional digital audio workstation (DAW)", "source": "pacman"},
        ]
    },
    {
        "id": "system",
        "category": "System & Tools",
        "subtitle": "Hardware monitors, fast terminal utilities, and tools",
        "icon": "system-run-symbolic",
        "apps": [
            {"name": "btop", "title": "Btop", "desc": "Aesthetic terminal system monitor and resource viewer", "source": "pacman"},
            {"name": "htop", "title": "Htop", "desc": "Interactive process viewer and processor monitor", "source": "pacman"},
            {"name": "gparted", "title": "GParted", "desc": "Partition editor for graphically managing disks", "source": "pacman"},
            {"name": "timeshift", "title": "Timeshift", "desc": "System restore utility creating incremental snapshots", "source": "pacman"},
            {"name": "baobab", "title": "Baobab", "desc": "Visual disk usage analyzer with tree rings", "source": "pacman"},
            {"name": "fastfetch", "title": "Fastfetch", "desc": "Rapid system info display for modern Linux", "source": "pacman"},
            {"name": "stacer-bin", "title": "Stacer", "desc": "Linux system optimizer and hardware monitoring dashboard", "source": "aur"},
            {"name": "pavucontrol", "title": "Volume Control", "desc": "Audio routing and mixer for PipeWire", "source": "pacman"},
            {"name": "fish", "title": "Fish Shell", "desc": "Smart user-friendly command line shell", "source": "pacman"},
            {"name": "kitty", "title": "Kitty", "desc": "GPU-accelerated terminal emulator with tabs", "source": "pacman"},
            {"name": "alacritty", "title": "Alacritty", "desc": "Blazing fast OpenGL terminal emulator", "source": "pacman"},
            {"name": "hardinfo-git", "title": "Hardinfo", "desc": "System benchmark and hardware information tool", "source": "aur"},
        ]
    },
    {
        "id": "games",
        "category": "Gaming",
        "subtitle": "Game platforms, launchers, emulators, and gaming utilities",
        "icon": "applications-games-symbolic",
        "apps": [
            {"name": "steam", "title": "Steam", "desc": "Ultimate gaming platform with Proton compatibility", "source": "pacman"},
            {"name": "lutris", "title": "Lutris", "desc": "Open gaming platform for Windows, Linux, and emulators", "source": "pacman"},
            {"name": "heroic-games-launcher-bin", "title": "Heroic Games Launcher", "desc": "Epic Games and GOG game launcher", "source": "aur"},
            {"name": "discord", "title": "Discord", "desc": "Voice, video, and text communication for gamers", "source": "pacman"},
            {"name": "retroarch", "title": "RetroArch", "desc": "Multi-system emulator frontend for classic games", "source": "pacman"},
            {"name": "prismlauncher", "title": "Prism Launcher", "desc": "Custom Minecraft launcher with modpack support", "source": "pacman"},
            {"name": "mangohud", "title": "MangoHud", "desc": "Vulkan and OpenGL overlay for monitoring FPS and temps", "source": "pacman"},
            {"name": "bottles", "title": "Bottles", "desc": "Run Windows software and games using Wine environments", "source": "aur"},
            {"name": "wine", "title": "Wine", "desc": "Compatibility layer capable of running Windows apps", "source": "pacman"},
            {"name": "obs-studio", "title": "OBS Studio", "desc": "Capture, stream, and record your gameplay", "source": "pacman"},
            {"name": "gamemode", "title": "GameMode", "desc": "Optimizes Linux system performance on demand", "source": "pacman"},
            {"name": "ryujinx-bin", "title": "Ryujinx", "desc": "Experimental Nintendo Switch emulator written in C#", "source": "aur"},
        ]
    }
]
FEATURED_APPS = CURATED_CATEGORIES

# Dynamic Featured Applications Metadata & Editorial Catalog
FEATURED_APP_PRESETS: Dict[str, Dict[str, Any]] = {
    # Media & Creative
    "blender": {
        "title": "Blender 3D Studio",
        "tag": "FEATURED 3D SUITE",
        "sub": "Unleash next-gen 3D modeling, animation, physics simulation, and real-time photorealistic rendering",
        "accent_color": "#eb7700",
        "bg": "radial-gradient(circle at 82% 50%, rgba(235, 119, 0, 0.32) 0%, rgba(235, 119, 0, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(235, 119, 0, 0.24) 0%, rgba(22, 24, 32, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(235, 119, 0, 0.40)",
        "icon": "blender",
        "source": "pacman",
    },
    "obs-studio": {
        "title": "OBS Studio",
        "tag": "BROADCAST ESSENTIAL",
        "sub": "Stream high-definition gameplay and capture pristine multi-source desktop broadcasts",
        "accent_color": "#a371f7",
        "bg": "radial-gradient(circle at 82% 50%, rgba(163, 113, 247, 0.32) 0%, rgba(163, 113, 247, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(48, 54, 61, 0.4) 0%, rgba(163, 113, 247, 0.24) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(163, 113, 247, 0.40)",
        "icon": "obs-studio",
        "source": "pacman",
    },
    "kdenlive": {
        "title": "Kdenlive Video Editor",
        "tag": "NEXT-GEN CREATIVE",
        "sub": "Powerful non-linear multi-track video editing with color grading, transitions, and audio mastering",
        "accent_color": "#2980b9",
        "bg": "radial-gradient(circle at 82% 50%, rgba(41, 128, 185, 0.32) 0%, rgba(41, 128, 185, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(41, 128, 185, 0.24) 0%, rgba(18, 25, 35, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(41, 128, 185, 0.40)",
        "icon": "kdenlive",
        "source": "pacman",
    },
    "gimp": {
        "title": "GIMP Studio",
        "tag": "CREATIVE IMAGE SUITE",
        "sub": "Advanced open-source image manipulation, high-bit-depth retouching, and digital artwork creation",
        "accent_color": "#e67e22",
        "bg": "radial-gradient(circle at 82% 50%, rgba(230, 126, 34, 0.32) 0%, rgba(230, 126, 34, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(230, 126, 34, 0.24) 0%, rgba(25, 23, 20, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(230, 126, 34, 0.40)",
        "icon": "gimp",
        "source": "pacman",
    },
    "inkscape": {
        "title": "Inkscape Vector Studio",
        "tag": "VECTOR DESIGN STUDIO",
        "sub": "Professional open-source vector graphics editor for diagrams, typography, logos, and illustration",
        "accent_color": "#00d2d3",
        "bg": "radial-gradient(circle at 82% 50%, rgba(0, 210, 211, 0.32) 0%, rgba(0, 210, 211, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(0, 210, 211, 0.24) 0%, rgba(18, 30, 32, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(0, 210, 211, 0.40)",
        "icon": "inkscape",
        "source": "pacman",
    },
    "audacity": {
        "title": "Audacity Audio Studio",
        "tag": "AUDIO MASTERING SUITE",
        "sub": "Multi-track audio editor, recorder, and mastering suite with real-time effects and spectrum analysis",
        "accent_color": "#0984e3",
        "bg": "radial-gradient(circle at 82% 50%, rgba(9, 132, 227, 0.32) 0%, rgba(9, 132, 227, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(9, 132, 227, 0.24) 0%, rgba(18, 26, 38, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(9, 132, 227, 0.40)",
        "icon": "audacity",
        "source": "pacman",
    },
    "darktable": {
        "title": "Darktable Photography",
        "tag": "PRO PHOTO WORKFLOW",
        "sub": "Virtual lighttable and non-destructive RAW photo developer for professional photographers",
        "accent_color": "#d35400",
        "bg": "radial-gradient(circle at 82% 50%, rgba(211, 84, 0, 0.32) 0%, rgba(211, 84, 0, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(211, 84, 0, 0.24) 0%, rgba(28, 20, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(211, 84, 0, 0.40)",
        "icon": "darktable",
        "source": "pacman",
    },
    "ardour": {
        "title": "Ardour Digital Audio",
        "tag": "DIGITAL AUDIO WORKSTATION",
        "sub": "Professional digital audio workstation (DAW) for recording, editing, mixing, and mastering",
        "accent_color": "#c0392b",
        "bg": "radial-gradient(circle at 82% 50%, rgba(192, 57, 43, 0.32) 0%, rgba(192, 57, 43, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(192, 57, 43, 0.24) 0%, rgba(28, 18, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(192, 57, 43, 0.40)",
        "icon": "ardour",
        "source": "pacman",
    },
    "handbrake": {
        "title": "HandBrake Transcoder",
        "tag": "VIDEO TRANSCODING PRO",
        "sub": "Universal open-source video transcoder converting videos to modern AV1, HEVC, and H.264 formats",
        "accent_color": "#e74c3c",
        "bg": "radial-gradient(circle at 82% 50%, rgba(231, 76, 60, 0.32) 0%, rgba(231, 76, 60, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(231, 76, 60, 0.24) 0%, rgba(28, 19, 20, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(231, 76, 60, 0.40)",
        "icon": "fr.handbrake.ghb",
        "source": "pacman",
    },
    "vlc": {
        "title": "VLC Media Player",
        "tag": "UNIVERSAL MEDIA PLAYER",
        "sub": "Universal media player playing every format, codec, stream, and subtitle out of the box with zero fuss",
        "accent_color": "#ff793f",
        "bg": "radial-gradient(circle at 82% 50%, rgba(255, 121, 63, 0.32) 0%, rgba(255, 121, 63, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(255, 121, 63, 0.24) 0%, rgba(28, 22, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(255, 121, 63, 0.40)",
        "icon": "vlc",
        "source": "pacman",
    },
    "spotify": {
        "title": "Spotify",
        "tag": "STREAMING SPOTLIGHT",
        "sub": "Stream millions of high-fidelity tracks, personalized playlists, and podcasts directly on desktop",
        "accent_color": "#1db954",
        "bg": "radial-gradient(circle at 82% 50%, rgba(29, 185, 84, 0.32) 0%, rgba(29, 185, 84, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(29, 185, 84, 0.24) 0%, rgba(16, 26, 20, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(29, 185, 84, 0.40)",
        "icon": "spotify",
        "source": "aur",
    },
    "mpv": {
        "title": "mpv Video Player",
        "tag": "MEDIA SPOTLIGHT",
        "sub": "Minimalist, powerhouse GPU-accelerated video player with high-quality video scaling and shaders",
        "accent_color": "#6c5ce7",
        "bg": "radial-gradient(circle at 82% 50%, rgba(108, 92, 231, 0.32) 0%, rgba(108, 92, 231, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(108, 92, 231, 0.24) 0%, rgba(20, 19, 32, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(108, 92, 231, 0.40)",
        "icon": "mpv",
        "source": "pacman",
    },

    # Development
    "code": {
        "title": "Visual Studio Code",
        "tag": "DEVELOPER SPOTLIGHT",
        "sub": "The world's most versatile code editor with intelligent AI autocompletion, debugging, and cloud workflows",
        "accent_color": "#007acc",
        "bg": "radial-gradient(circle at 82% 50%, rgba(0, 122, 204, 0.32) 0%, rgba(0, 122, 204, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(0, 122, 204, 0.25) 0%, rgba(18, 24, 34, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(0, 122, 204, 0.40)",
        "icon": "code",
        "source": "pacman",
    },
    "zed": {
        "title": "Zed Code Editor",
        "tag": "NEXT-GEN CODE EDITOR",
        "sub": "Lightning-fast, GPU-accelerated code editor engineered in Rust for instantaneous multiplayer collaboration",
        "accent_color": "#47c8ff",
        "bg": "radial-gradient(circle at 82% 50%, rgba(71, 200, 255, 0.32) 0%, rgba(71, 200, 255, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(71, 200, 255, 0.24) 0%, rgba(18, 28, 38, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(71, 200, 255, 0.40)",
        "icon": "zed",
        "source": "aur",
    },
    "neovim": {
        "title": "Neovim",
        "tag": "HYPER-EXTENSIBLE VIM",
        "sub": "Hyperextensible Vim-based text editor built for high-speed terminal coding, Lua plugins, and native LSP",
        "accent_color": "#57a143",
        "bg": "radial-gradient(circle at 82% 50%, rgba(87, 161, 67, 0.32) 0%, rgba(87, 161, 67, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(87, 161, 67, 0.24) 0%, rgba(18, 26, 20, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(87, 161, 67, 0.40)",
        "icon": "nvim",
        "source": "pacman",
    },
    "alacritty": {
        "title": "Alacritty Terminal",
        "tag": "BLAZING FAST TERMINAL",
        "sub": "Blazing-fast GPU-accelerated terminal emulator optimized for raw throughput, low latency, and simplicity",
        "accent_color": "#f39c12",
        "bg": "radial-gradient(circle at 82% 50%, rgba(243, 156, 18, 0.32) 0%, rgba(243, 156, 18, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(243, 156, 18, 0.24) 0%, rgba(28, 24, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(243, 156, 18, 0.40)",
        "icon": "Alacritty",
        "source": "pacman",
    },
    "kitty": {
        "title": "Kitty Terminal",
        "tag": "GPU POWER TERMINAL",
        "sub": "Feature-rich GPU-accelerated terminal with tabs, splits, graphics protocol support, and scriptability",
        "accent_color": "#2ecc71",
        "bg": "radial-gradient(circle at 82% 50%, rgba(46, 204, 113, 0.32) 0%, rgba(46, 204, 113, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(46, 204, 113, 0.24) 0%, rgba(18, 28, 22, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(46, 204, 113, 0.40)",
        "icon": "kitty",
        "source": "pacman",
    },
    "lazygit": {
        "title": "LazyGit",
        "tag": "TERMINAL GIT SPOTLIGHT",
        "sub": "Intuitive terminal graphical user interface for effortlessly navigating Git commits, branches, and diffs",
        "accent_color": "#ff6b6b",
        "bg": "radial-gradient(circle at 82% 50%, rgba(255, 107, 107, 0.32) 0%, rgba(255, 107, 107, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(255, 107, 107, 0.24) 0%, rgba(30, 20, 22, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(255, 107, 107, 0.40)",
        "icon": "lazygit",
        "source": "pacman",
    },
    "postman-bin": {
        "title": "Postman API Suite",
        "tag": "API DEVELOPMENT SUITE",
        "sub": "Complete API development platform for designing, testing, automating, and mocking HTTP & GraphQL endpoints",
        "accent_color": "#ff6c37",
        "bg": "radial-gradient(circle at 82% 50%, rgba(255, 108, 55, 0.32) 0%, rgba(255, 108, 55, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(255, 108, 55, 0.24) 0%, rgba(30, 21, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(255, 108, 55, 0.40)",
        "icon": "postman",
        "source": "aur",
    },
    "docker": {
        "title": "Docker Platform",
        "tag": "CONTAINER PLATFORM",
        "sub": "Industry-standard container platform to build, package, and deploy isolated microservices effortlessly",
        "accent_color": "#2496ed",
        "bg": "radial-gradient(circle at 82% 50%, rgba(36, 150, 237, 0.32) 0%, rgba(36, 150, 237, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(36, 150, 237, 0.24) 0%, rgba(18, 25, 36, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(36, 150, 237, 0.40)",
        "icon": "docker",
        "source": "pacman",
    },
    "dbeaver": {
        "title": "DBeaver Studio",
        "tag": "UNIVERSAL DATABASE GUI",
        "sub": "Universal database management tool supporting PostgreSQL, MySQL, SQLite, and cloud databases",
        "accent_color": "#377ba8",
        "bg": "radial-gradient(circle at 82% 50%, rgba(55, 123, 168, 0.32) 0%, rgba(55, 123, 168, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(55, 123, 168, 0.24) 0%, rgba(18, 25, 32, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(55, 123, 168, 0.40)",
        "icon": "dbeaver",
        "source": "pacman",
    },
    "rust": {
        "title": "Rust & Cargo",
        "tag": "SYSTEMS LANGUAGE",
        "sub": "Empowering everyone to build reliable, memory-safe, and blazingly fast modern systems",
        "accent_color": "#ce412b",
        "bg": "radial-gradient(circle at 82% 50%, rgba(206, 65, 43, 0.32) 0%, rgba(206, 65, 43, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(206, 65, 43, 0.24) 0%, rgba(30, 19, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(206, 65, 43, 0.40)",
        "icon": "rust",
        "source": "pacman",
    },
    "go": {
        "title": "Go Language",
        "tag": "CLOUD CONCURRENCY",
        "sub": "Fast, compiled, concurrency-focused programming language engineered by Google for cloud scale",
        "accent_color": "#00add8",
        "bg": "radial-gradient(circle at 82% 50%, rgba(0, 173, 216, 0.32) 0%, rgba(0, 173, 216, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(0, 173, 216, 0.24) 0%, rgba(16, 26, 34, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(0, 173, 216, 0.40)",
        "icon": "go",
        "source": "pacman",
    },
    "git": {
        "title": "Git Version Control",
        "tag": "DISTRIBUTED VERSION CONTROL",
        "sub": "Fast, scalable distributed revision control system designed for projects of any scale",
        "accent_color": "#f05032",
        "bg": "radial-gradient(circle at 82% 50%, rgba(240, 80, 50, 0.32) 0%, rgba(240, 80, 50, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(240, 80, 50, 0.24) 0%, rgba(30, 20, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(240, 80, 50, 0.40)",
        "icon": "git",
        "source": "pacman",
    },

    # Gaming
    "steam": {
        "title": "Steam on Linux",
        "tag": "PRO GAMING PLATFORM",
        "sub": "Play thousands of native and Windows titles with seamless Proton performance and Steam Deck synergy",
        "accent_color": "#66c0f4",
        "bg": "radial-gradient(circle at 82% 50%, rgba(102, 192, 244, 0.32) 0%, rgba(102, 192, 244, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(23, 29, 37, 0.6) 0%, rgba(102, 192, 244, 0.24) 45%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(102, 192, 244, 0.40)",
        "icon": "steam",
        "source": "pacman",
    },
    "heroic-games-launcher-bin": {
        "title": "Heroic Games Launcher",
        "tag": "EPIC & GOG LAUNCHER",
        "sub": "Modern native open-source launcher for Epic Games, GOG, and Amazon Prime Gaming on Linux",
        "accent_color": "#d9534f",
        "bg": "radial-gradient(circle at 82% 50%, rgba(217, 83, 79, 0.32) 0%, rgba(217, 83, 79, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(217, 83, 79, 0.24) 0%, rgba(30, 20, 22, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(217, 83, 79, 0.40)",
        "icon": "heroic",
        "source": "aur",
    },
    "lutris": {
        "title": "Lutris Gaming Platform",
        "tag": "OPEN GAMING HUB",
        "sub": "Open gaming management platform organizing your GOG, Epic, Steam, Battle.net, and emulator libraries",
        "accent_color": "#ff6f00",
        "bg": "radial-gradient(circle at 82% 50%, rgba(255, 111, 0, 0.32) 0%, rgba(255, 111, 0, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(255, 111, 0, 0.24) 0%, rgba(30, 23, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(255, 111, 0, 0.40)",
        "icon": "lutris",
        "source": "pacman",
    },
    "retroarch": {
        "title": "RetroArch",
        "tag": "RETRO EMULATION MATRIX",
        "sub": "The premier multi-system emulator frontend for classic consoles, handhelds, and arcade machines",
        "accent_color": "#3498db",
        "bg": "radial-gradient(circle at 82% 50%, rgba(52, 152, 219, 0.32) 0%, rgba(52, 152, 219, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(52, 152, 219, 0.24) 0%, rgba(18, 26, 35, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(52, 152, 219, 0.40)",
        "icon": "retroarch",
        "source": "pacman",
    },
    "prismlauncher": {
        "title": "Prism Launcher",
        "tag": "MINECRAFT POWER LAUNCHER",
        "sub": "High-performance custom Minecraft launcher with seamless modpack installation and instance isolation",
        "accent_color": "#30d158",
        "bg": "radial-gradient(circle at 82% 50%, rgba(48, 209, 88, 0.32) 0%, rgba(48, 209, 88, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(48, 209, 88, 0.24) 0%, rgba(18, 28, 20, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(48, 209, 88, 0.40)",
        "icon": "org.prismlauncher.PrismLauncher",
        "source": "pacman",
    },
    "bottles": {
        "title": "Bottles for Linux",
        "tag": "WINE ENVIRONMENT MANAGER",
        "sub": "Easily manage Wine and Proton prefixes to run Windows software and games with custom environments",
        "accent_color": "#54a0ff",
        "bg": "radial-gradient(circle at 82% 50%, rgba(84, 160, 255, 0.32) 0%, rgba(84, 160, 255, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(84, 160, 255, 0.24) 0%, rgba(20, 26, 36, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(84, 160, 255, 0.40)",
        "icon": "com.usebottles.bottles",
        "source": "aur",
    },
    "ryujinx-bin": {
        "title": "Ryujinx Emulator",
        "tag": "PRECISION EMULATION",
        "sub": "Experimental Nintendo Switch emulator offering exceptional accuracy, performance, and Vulkan backend",
        "accent_color": "#e056fd",
        "bg": "radial-gradient(circle at 82% 50%, rgba(224, 86, 253, 0.32) 0%, rgba(224, 86, 253, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(224, 86, 253, 0.24) 0%, rgba(30, 20, 32, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(224, 86, 253, 0.40)",
        "icon": "ryujinx",
        "source": "aur",
    },
    "gamemode": {
        "title": "Feral GameMode",
        "tag": "SYSTEM GAME OPTIMIZER",
        "sub": "System optimization daemon that tunes CPU governors and scheduler priorities for smooth frame rates",
        "accent_color": "#e84118",
        "bg": "radial-gradient(circle at 82% 50%, rgba(232, 65, 24, 0.32) 0%, rgba(232, 65, 24, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(232, 65, 24, 0.24) 0%, rgba(30, 19, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(232, 65, 24, 0.40)",
        "icon": "applications-games",
        "source": "pacman",
    },
    "mangohud": {
        "title": "MangoHud Overlay",
        "tag": "VULKAN GAMING OVERLAY",
        "sub": "Vulkan and OpenGL overlay for monitoring FPS, frametimes, GPU temperatures, and memory consumption",
        "accent_color": "#e84393",
        "bg": "radial-gradient(circle at 82% 50%, rgba(232, 67, 147, 0.32) 0%, rgba(232, 67, 147, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(232, 67, 147, 0.24) 0%, rgba(30, 19, 26, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(232, 67, 147, 0.40)",
        "icon": "applications-games",
        "source": "pacman",
    },
    "wine": {
        "title": "Wine Compatibility Layer",
        "tag": "WINDOWS COMPATIBILITY",
        "sub": "Run Windows applications, productivity tools, and legacy software natively on Linux desktops",
        "accent_color": "#8e44ad",
        "bg": "radial-gradient(circle at 82% 50%, rgba(142, 68, 173, 0.32) 0%, rgba(142, 68, 173, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(142, 68, 173, 0.24) 0%, rgba(26, 19, 30, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(142, 68, 173, 0.40)",
        "icon": "wine",
        "source": "pacman",
    },

    # Privacy & Security
    "proton-vpn-gtk-app": {
        "title": "Proton VPN",
        "tag": "ENCRYPTED VPN SHIELD",
        "sub": "High-speed encrypted WireGuard VPN tunnel with strict zero-logging policy and Swiss privacy",
        "accent_color": "#6d4aff",
        "bg": "radial-gradient(circle at 82% 50%, rgba(109, 74, 255, 0.32) 0%, rgba(109, 74, 255, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(109, 74, 255, 0.25) 0%, rgba(20, 20, 35, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(109, 74, 255, 0.40)",
        "icon": "proton-vpn-gtk-app",
        "source": "pacman",
    },
    "brave-bin": {
        "title": "Brave Browser",
        "tag": "SHIELDED WEB BROWSING",
        "sub": "High-speed browser with native ad-blocking, tracker shielding, and Web3 capabilities",
        "accent_color": "#fb542b",
        "bg": "radial-gradient(circle at 82% 50%, rgba(251, 84, 43, 0.32) 0%, rgba(251, 84, 43, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(251, 84, 43, 0.24) 0%, rgba(30, 21, 19, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(251, 84, 43, 0.40)",
        "icon": "brave-browser",
        "source": "aur",
    },
    "signal-desktop": {
        "title": "Signal Desktop",
        "tag": "ENCRYPTED MESSAGING",
        "sub": "State-of-the-art end-to-end encrypted messaging with voice calls, video chats, and vanishing messages",
        "accent_color": "#3a76f0",
        "bg": "radial-gradient(circle at 82% 50%, rgba(58, 118, 240, 0.32) 0%, rgba(58, 118, 240, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(58, 118, 240, 0.24) 0%, rgba(19, 25, 36, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(58, 118, 240, 0.40)",
        "icon": "signal-desktop",
        "source": "pacman",
    },
    "keepassxc": {
        "title": "KeePassXC Vault",
        "tag": "OFFLINE PASSWORD VAULT",
        "sub": "Secure offline password manager with AES-256 encryption, auto-type, and TOTP authentication",
        "accent_color": "#52982d",
        "bg": "radial-gradient(circle at 82% 50%, rgba(82, 152, 45, 0.32) 0%, rgba(82, 152, 45, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(82, 152, 45, 0.24) 0%, rgba(19, 27, 20, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(82, 152, 45, 0.40)",
        "icon": "keepassxc",
        "source": "pacman",
    },
    "bitwarden": {
        "title": "Bitwarden Vault",
        "tag": "SECURITY ESSENTIAL",
        "sub": "Open-source zero-knowledge password vault protecting credentials across all your devices",
        "accent_color": "#175ddc",
        "bg": "radial-gradient(circle at 82% 50%, rgba(23, 93, 220, 0.32) 0%, rgba(23, 93, 220, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(23, 93, 220, 0.24) 0%, rgba(18, 24, 36, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(23, 93, 220, 0.40)",
        "icon": "bitwarden",
        "source": "pacman",
    },
    "mullvad-vpn-bin": {
        "title": "Mullvad VPN",
        "tag": "ZERO-LOG WIREGUARD",
        "sub": "Privacy-focused VPN with no personal data collection, WireGuard tunnels, and quantum-resistant encryption",
        "accent_color": "#e5ad23",
        "bg": "radial-gradient(circle at 82% 50%, rgba(229, 173, 35, 0.32) 0%, rgba(229, 173, 35, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(229, 173, 35, 0.24) 0%, rgba(28, 25, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(229, 173, 35, 0.40)",
        "icon": "mullvad-vpn",
        "source": "aur",
    },
    "torbrowser-launcher": {
        "title": "Tor Browser",
        "tag": "ANONYMOUS ONION ROUTING",
        "sub": "Defend against tracking, surveillance, and censorship with multi-layered onion encryption routing",
        "accent_color": "#7d4698",
        "bg": "radial-gradient(circle at 82% 50%, rgba(125, 70, 152, 0.32) 0%, rgba(125, 70, 152, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(125, 70, 152, 0.24) 0%, rgba(25, 19, 28, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(125, 70, 152, 0.40)",
        "icon": "tor-browser",
        "source": "pacman",
    },
    "wireshark-qt": {
        "title": "Wireshark Packet Analyzer",
        "tag": "NETWORK PACKET ANALYZER",
        "sub": "Network packet analyzer and traffic inspector for deep protocol inspection and network diagnostics",
        "accent_color": "#16a085",
        "bg": "radial-gradient(circle at 82% 50%, rgba(22, 160, 133, 0.32) 0%, rgba(22, 160, 133, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(22, 160, 133, 0.24) 0%, rgba(18, 27, 25, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(22, 160, 133, 0.40)",
        "icon": "wireshark",
        "source": "pacman",
    },
    "tailscale": {
        "title": "Tailscale Mesh VPN",
        "tag": "ZERO-CONFIG MESH VPN",
        "sub": "Zero config mesh VPN for secure peer-to-peer device networks built upon modern WireGuard protocols",
        "accent_color": "#4a69bd",
        "bg": "radial-gradient(circle at 82% 50%, rgba(74, 105, 189, 0.32) 0%, rgba(74, 105, 189, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(74, 105, 189, 0.24) 0%, rgba(19, 23, 34, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(74, 105, 189, 0.40)",
        "icon": "network-vpn",
        "source": "pacman",
    },
    "wireguard-tools": {
        "title": "WireGuard",
        "tag": "CRYPTOGRAPHIC VPN TUNNEL",
        "sub": "Extremely fast, modern cryptographic network tunnel with peer-to-peer simplicity and minimal overhead",
        "accent_color": "#8854d0",
        "bg": "radial-gradient(circle at 82% 50%, rgba(136, 84, 208, 0.32) 0%, rgba(136, 84, 208, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(136, 84, 208, 0.24) 0%, rgba(24, 19, 32, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(136, 84, 208, 0.40)",
        "icon": "network-vpn",
        "source": "pacman",
    },
    "thunderbird": {
        "title": "Thunderbird Mail",
        "tag": "ENTERPRISE EMAIL & CALENDAR",
        "sub": "Feature-packed email, calendar, and contacts client with advanced privacy and OpenPGP encryption",
        "accent_color": "#0984e3",
        "bg": "radial-gradient(circle at 82% 50%, rgba(9, 132, 227, 0.32) 0%, rgba(9, 132, 227, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(9, 132, 227, 0.24) 0%, rgba(18, 26, 38, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(9, 132, 227, 0.40)",
        "icon": "thunderbird",
        "source": "pacman",
    },
    "bleachbit": {
        "title": "BleachBit Cleaner",
        "tag": "PRIVACY CLEANER & SHREDDER",
        "sub": "Clean caches, free disk space, and guard privacy with deep system scrubbing and file shredding",
        "accent_color": "#e74c3c",
        "bg": "radial-gradient(circle at 82% 50%, rgba(231, 76, 60, 0.32) 0%, rgba(231, 76, 60, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(231, 76, 60, 0.24) 0%, rgba(28, 19, 20, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(231, 76, 60, 0.40)",
        "icon": "bleachbit",
        "source": "pacman",
    },

    # Productivity
    "libreoffice-fresh": {
        "title": "LibreOffice Fresh",
        "tag": "OFFICE PRODUCTIVITY SUITE",
        "sub": "Comprehensive enterprise-grade office productivity suite compatible with Microsoft Office formats",
        "accent_color": "#18a058",
        "bg": "radial-gradient(circle at 82% 50%, rgba(24, 160, 88, 0.32) 0%, rgba(24, 160, 88, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(24, 160, 88, 0.24) 0%, rgba(18, 27, 21, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(24, 160, 88, 0.40)",
        "icon": "libreoffice-main",
        "source": "pacman",
    },
    "obsidian": {
        "title": "Obsidian",
        "tag": "KNOWLEDGE REVOLUTION",
        "sub": "Second brain and knowledge graph application storing linked markdown notes locally on your filesystem",
        "accent_color": "#7c3aed",
        "bg": "radial-gradient(circle at 82% 50%, rgba(124, 58, 237, 0.32) 0%, rgba(124, 58, 237, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(124, 58, 237, 0.24) 0%, rgba(24, 19, 34, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(124, 58, 237, 0.40)",
        "icon": "obsidian",
        "source": "aur",
    },
    "krita": {
        "title": "Krita Digital Painting",
        "tag": "DIGITAL ART STUDIO",
        "sub": "Professional digital painting and illustration studio with world-class brush engines and stabilizers",
        "accent_color": "#f368e0",
        "bg": "radial-gradient(circle at 82% 50%, rgba(243, 104, 224, 0.32) 0%, rgba(243, 104, 224, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(243, 104, 224, 0.24) 0%, rgba(30, 20, 28, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(243, 104, 224, 0.40)",
        "icon": "krita",
        "source": "pacman",
    },
    "joplin-desktop": {
        "title": "Joplin Notes",
        "tag": "ENCRYPTED CLOUD NOTES",
        "sub": "Secure, open-source note-taking and to-do application with end-to-end encrypted synchronization",
        "accent_color": "#1b6ac9",
        "bg": "radial-gradient(circle at 82% 50%, rgba(27, 106, 201, 0.32) 0%, rgba(27, 106, 201, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(27, 106, 201, 0.24) 0%, rgba(18, 24, 34, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(27, 106, 201, 0.40)",
        "icon": "joplin",
        "source": "aur",
    },
    "onlyoffice-bin": {
        "title": "OnlyOffice Docs",
        "tag": "COLLABORATIVE DOCS",
        "sub": "High-compatibility office suite featuring collaborative document, spreadsheet, and slide editing",
        "accent_color": "#ff6f59",
        "bg": "radial-gradient(circle at 82% 50%, rgba(255, 111, 89, 0.32) 0%, rgba(255, 111, 89, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(255, 111, 89, 0.24) 0%, rgba(30, 21, 20, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(255, 111, 89, 0.40)",
        "icon": "onlyoffice-desktopeditors",
        "source": "aur",
    },
    "anytype-bin": {
        "title": "Anytype Workspace",
        "tag": "DECENTRALIZED WORKSPACE",
        "sub": "Next-generation private knowledge base and decentralized operating space for personal ideas and wikis",
        "accent_color": "#f59e0b",
        "bg": "radial-gradient(circle at 82% 50%, rgba(245, 158, 11, 0.32) 0%, rgba(245, 158, 11, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(245, 158, 11, 0.24) 0%, rgba(28, 24, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(245, 158, 11, 0.40)",
        "icon": "anytype",
        "source": "aur",
    },
    "foliate": {
        "title": "Foliate Reader",
        "tag": "ELEGANT EBOOK READER",
        "sub": "Modern, distraction-free ebook reader with custom typography, annotations, and dictionary lookup",
        "accent_color": "#10b981",
        "bg": "radial-gradient(circle at 82% 50%, rgba(16, 185, 129, 0.32) 0%, rgba(16, 185, 129, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(16, 185, 129, 0.24) 0%, rgba(18, 28, 24, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(16, 185, 129, 0.40)",
        "icon": "com.github.johnfactotum.Foliate",
        "source": "pacman",
    },
    "thunar": {
        "title": "Thunar File Manager",
        "tag": "LIGHTWEIGHT FILE MANAGER",
        "sub": "Fast lightweight desktop file manager with clean modern interface and custom action plugins",
        "accent_color": "#3498db",
        "bg": "radial-gradient(circle at 82% 50%, rgba(52, 152, 219, 0.32) 0%, rgba(52, 152, 219, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(52, 152, 219, 0.24) 0%, rgba(18, 26, 35, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(52, 152, 219, 0.40)",
        "icon": "org.xfce.thunar",
        "source": "pacman",
    },
    "micro": {
        "title": "Micro Editor",
        "tag": "INTUITIVE TERMINAL EDITOR",
        "sub": "Intuitive terminal text editor with full mouse support, multi-cursors, and syntax highlighting",
        "accent_color": "#fdcb6e",
        "bg": "radial-gradient(circle at 82% 50%, rgba(253, 203, 110, 0.32) 0%, rgba(253, 203, 110, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(253, 203, 110, 0.24) 0%, rgba(30, 26, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(253, 203, 110, 0.40)",
        "icon": "micro",
        "source": "pacman",
    },
    "evince": {
        "title": "Evince Document Viewer",
        "tag": "UNIVERSAL DOCUMENT VIEWER",
        "sub": "Clean, high-performance document viewer for PDF, PostScript, and DjVu with text search",
        "accent_color": "#d63031",
        "bg": "radial-gradient(circle at 82% 50%, rgba(214, 48, 49, 0.32) 0%, rgba(214, 48, 49, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(214, 48, 49, 0.24) 0%, rgba(28, 18, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(214, 48, 49, 0.40)",
        "icon": "org.gnome.Evince",
        "source": "pacman",
    },

    # System
    "btop": {
        "title": "Btop Resource Monitor",
        "tag": "SYSTEM SPOTLIGHT",
        "sub": "Stunning aesthetic terminal monitor tracking CPU, GPU, memory, disks, and network with live graphs",
        "accent_color": "#ff5370",
        "bg": "radial-gradient(circle at 82% 50%, rgba(255, 83, 112, 0.32) 0%, rgba(255, 83, 112, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(255, 83, 112, 0.24) 0%, rgba(30, 20, 23, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(255, 83, 112, 0.40)",
        "icon": "btop",
        "source": "pacman",
    },
    "timeshift": {
        "title": "Timeshift System Restore",
        "tag": "SYSTEM RESTORE ENGINE",
        "sub": "Rock-solid system snapshot utility protecting your OS files using incremental BTRFS and RSYNC backups",
        "accent_color": "#e056fd",
        "bg": "radial-gradient(circle at 82% 50%, rgba(224, 86, 253, 0.32) 0%, rgba(224, 86, 253, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(224, 86, 253, 0.24) 0%, rgba(28, 20, 30, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(224, 86, 253, 0.40)",
        "icon": "timeshift",
        "source": "pacman",
    },
    "fastfetch": {
        "title": "Fastfetch",
        "tag": "BLAZING HARDWARE INFO",
        "sub": "Lightning-fast, highly customizable modern system information display written in performant C",
        "accent_color": "#00b894",
        "bg": "radial-gradient(circle at 82% 50%, rgba(0, 184, 148, 0.32) 0%, rgba(0, 184, 148, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(0, 184, 148, 0.24) 0%, rgba(18, 28, 25, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(0, 184, 148, 0.40)",
        "icon": "fastfetch",
        "source": "pacman",
    },
    "gparted": {
        "title": "GParted Partition Editor",
        "tag": "PARTITION ARCHITECT",
        "sub": "Graphical partition editor to resize, format, check, and reorganize hard drives and SSDs safely",
        "accent_color": "#f39c12",
        "bg": "radial-gradient(circle at 82% 50%, rgba(243, 156, 18, 0.32) 0%, rgba(243, 156, 18, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(243, 156, 18, 0.24) 0%, rgba(28, 24, 18, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(243, 156, 18, 0.40)",
        "icon": "gparted",
        "source": "pacman",
    },
    "stacer-bin": {
        "title": "Stacer Optimizer",
        "tag": "SYSTEM CLEANER & DASHBOARD",
        "sub": "Comprehensive Linux system optimizer, startup manager, and hardware monitoring dashboard",
        "accent_color": "#3498db",
        "bg": "radial-gradient(circle at 82% 50%, rgba(52, 152, 219, 0.32) 0%, rgba(52, 152, 219, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(52, 152, 219, 0.24) 0%, rgba(18, 26, 35, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(52, 152, 219, 0.40)",
        "icon": "stacer",
        "source": "aur",
    },
    "fish": {
        "title": "Fish Shell",
        "tag": "SMART INTERACTIVE SHELL",
        "sub": "Smart user-friendly command line shell with syntax highlighting, autosuggestions, and tab completions",
        "accent_color": "#9b59b6",
        "bg": "radial-gradient(circle at 82% 50%, rgba(155, 89, 182, 0.32) 0%, rgba(155, 89, 182, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(155, 89, 182, 0.24) 0%, rgba(26, 20, 30, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(155, 89, 182, 0.40)",
        "icon": "fish",
        "source": "pacman",
    },
    "htop": {
        "title": "Htop Process Viewer",
        "tag": "INTERACTIVE PROCESS MONITOR",
        "sub": "Cross-platform interactive process viewer, CPU thread inspector, and process kill manager",
        "accent_color": "#2ecc71",
        "bg": "radial-gradient(circle at 82% 50%, rgba(46, 204, 113, 0.32) 0%, rgba(46, 204, 113, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(46, 204, 113, 0.24) 0%, rgba(18, 28, 22, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(46, 204, 113, 0.40)",
        "icon": "htop",
        "source": "pacman",
    },
    "baobab": {
        "title": "Baobab Disk Analyzer",
        "tag": "VISUAL DISK USAGE",
        "sub": "Visual disk usage analyzer graphically displaying folder structures via dynamic rings and treemaps",
        "accent_color": "#e67e22",
        "bg": "radial-gradient(circle at 82% 50%, rgba(230, 126, 34, 0.32) 0%, rgba(230, 126, 34, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(230, 126, 34, 0.24) 0%, rgba(25, 23, 20, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(230, 126, 34, 0.40)",
        "icon": "org.gnome.baobab",
        "source": "pacman",
    },
    "hardinfo-git": {
        "title": "Hardinfo Hardware Profiler",
        "tag": "SYSTEM BENCHMARK SUITE",
        "sub": "System benchmark and hardware information tool reporting CPU modules, PCI devices, and sensors",
        "accent_color": "#00cec9",
        "bg": "radial-gradient(circle at 82% 50%, rgba(0, 206, 201, 0.32) 0%, rgba(0, 206, 201, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(0, 206, 201, 0.24) 0%, rgba(18, 28, 28, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(0, 206, 201, 0.40)",
        "icon": "hardinfo",
        "source": "aur",
    },
    "pavucontrol": {
        "title": "Volume Control",
        "tag": "ADVANCED AUDIO ROUTER",
        "sub": "Audio routing, volume levels, and multi-channel mixer for PipeWire and PulseAudio streams",
        "accent_color": "#6c5ce7",
        "bg": "radial-gradient(circle at 82% 50%, rgba(108, 92, 231, 0.32) 0%, rgba(108, 92, 231, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(108, 92, 231, 0.24) 0%, rgba(20, 19, 32, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(108, 92, 231, 0.40)",
        "icon": "org.pulseaudio.pavucontrol",
        "source": "pacman",
    },

    # Essential
    "firefox": {
        "title": "Firefox Browser",
        "tag": "FAST & PRIVATE WEB",
        "sub": "Fast, independent browser with built-in total cookie protection, container tabs, and fingerprint resistance",
        "accent_color": "#ff7139",
        "bg": "radial-gradient(circle at 82% 50%, rgba(255, 113, 57, 0.32) 0%, rgba(255, 113, 57, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(255, 113, 57, 0.24) 0%, rgba(26, 18, 28, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(255, 113, 57, 0.40)",
        "icon": "firefox",
        "source": "pacman",
    },
    "discord": {
        "title": "Discord",
        "tag": "COMMUNITY VOICE & CHAT",
        "sub": "All-in-one low-latency voice, video, and text communication for communities and gaming squads",
        "accent_color": "#5865f2",
        "bg": "radial-gradient(circle at 82% 50%, rgba(88, 101, 242, 0.32) 0%, rgba(88, 101, 242, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(88, 101, 242, 0.24) 0%, rgba(20, 22, 36, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(88, 101, 242, 0.40)",
        "icon": "discord",
        "source": "pacman",
    },
    "telegram-desktop": {
        "title": "Telegram Desktop",
        "tag": "INSTANT CLOUD MESSAGING",
        "sub": "Blazing fast cloud messaging client with instant synchronization, giant channels, and voice chats",
        "accent_color": "#0088cc",
        "bg": "radial-gradient(circle at 82% 50%, rgba(0, 136, 204, 0.32) 0%, rgba(0, 136, 204, 0.05) 50%, transparent 75%), linear-gradient(135deg, rgba(0, 136, 204, 0.24) 0%, rgba(18, 26, 34, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)",
        "border": "rgba(0, 136, 204, 0.40)",
        "icon": "telegram",
        "source": "pacman",
    },
}
FEATURED_APP_METADATA = FEATURED_APP_PRESETS

FEATURED_CATEGORY_PRIORITIES: Dict[str, List[str]] = {
    "media": [
        "blender", "kdenlive", "obs-studio", "gimp", "inkscape",
        "audacity", "handbrake", "mpv", "darktable", "ardour", "vlc", "spotify"
    ],
    "dev": [
        "code", "zed", "neovim", "alacritty", "kitty",
        "postman-bin", "docker", "rust", "go", "dbeaver", "lazygit", "git"
    ],
    "games": [
        "steam", "heroic-games-launcher-bin", "lutris", "retroarch",
        "prismlauncher", "bottles", "gamemode", "ryujinx-bin", "mangohud", "wine", "discord", "obs-studio"
    ],
    "privacy": [
        "proton-vpn-gtk-app", "brave-bin", "signal-desktop", "keepassxc",
        "bitwarden", "wireguard-tools", "mullvad-vpn-bin", "torbrowser-launcher", "tailscale", "thunderbird", "wireshark-qt", "bleachbit"
    ],
    "productivity": [
        "libreoffice-fresh", "obsidian", "krita", "joplin-desktop",
        "onlyoffice-bin", "anytype-bin", "foliate", "micro", "evince", "thunar", "fastfetch", "pavucontrol"
    ],
    "system": [
        "btop", "timeshift", "fastfetch", "gparted",
        "stacer-bin", "fish", "htop", "baobab", "hardinfo-git", "pavucontrol", "kitty", "alacritty"
    ],
    "essential": [
        "firefox", "discord", "telegram-desktop", "spotify",
        "vlc", "bitwarden", "code", "steam", "blender", "obs-studio", "gimp", "proton-vpn-gtk-app"
    ],
}

CATEGORY_TAG_DEFAULTS: Dict[str, str] = {
    "media": "FEATURED CREATIVE SUITE",
    "dev": "DEVELOPER SPOTLIGHT",
    "games": "NEXT-GEN GAMING",
    "privacy": "PRIVACY ESSENTIAL",
    "productivity": "EDITORS' CHOICE",
    "system": "SYSTEM SPOTLIGHT",
    "essential": "COMMUNITY FAVORITE",
}

CATEGORY_ACCENT_DEFAULTS: Dict[str, str] = {
    "media": "#eb7700",
    "dev": "#007acc",
    "games": "#66c0f4",
    "privacy": "#6d4aff",
    "productivity": "#18a058",
    "system": "#ff5370",
    "essential": "#30d158",
}

def _color_to_gradient_and_border(hex_color: str, alpha_bg: float = 0.24, alpha_border: float = 0.42) -> Tuple[str, str]:
    """Generate modern atmospheric gradient background with radial glow and subtle accent border from hex color."""
    h = hex_color.lstrip("#")
    if len(h) == 6:
        try:
            r = int(h[0:2], 16)
            g = int(h[2:4], 16)
            b = int(h[4:6], 16)
        except ValueError:
            r, g, b = (10, 132, 255)
    else:
        r, g, b = (10, 132, 255)
    bg = (
        f"radial-gradient(circle at 82% 50%, rgba({r}, {g}, {b}, 0.32) 0%, rgba({r}, {g}, {b}, 0.05) 50%, transparent 75%), "
        f"linear-gradient(135deg, rgba({r}, {g}, {b}, {alpha_bg}) 0%, rgba(20, 24, 33, 0.95) 55%, rgba(13, 17, 23, 0.98) 100%)"
    )
    border = f"rgba({r}, {g}, {b}, {alpha_border})"
    return bg, border

# Human-friendly Apple App Store display names
APP_DISPLAY_NAMES: Dict[str, str] = {
    "firefox": "Firefox",
    "firefox-pure": "Firefox",
    "firefox-developer-edition": "Firefox Developer Edition",
    "code": "Visual Studio Code",
    "visual-studio-code-bin": "Visual Studio Code",
    "discord": "Discord",
    "vesktop": "Vesktop Discord",
    "steam": "Steam",
    "spotify": "Spotify",
    "proton-vpn-gtk-app": "Proton VPN",
    "proton-vpn-cli": "Proton VPN CLI",
    "proton-vpn": "Proton VPN",
    "neovim": "Neovim",
    "git": "Git",
    "docker": "Docker",
    "alacritty": "Alacritty",
    "kitty": "Kitty",
    "brave-bin": "Brave Browser",
    "brave": "Brave Browser",
    "brave-origin-bin": "Brave Origin",
    "zen-browser-bin": "Zen Browser",
    "chromium": "Chromium",
    "torbrowser-launcher": "Tor Browser",
    "telegram-desktop": "Telegram Desktop",
    "telegram": "Telegram",
    "signal-desktop": "Signal Desktop",
    "signal": "Signal",
    "obs-studio": "OBS Studio",
    "vlc": "VLC Media Player",
    "lutris": "Lutris",
    "btop": "Btop",
    "fastfetch": "Fastfetch",
    "foot": "Foot Terminal",
    "micro": "Micro Editor",
    "gimp": "GIMP",
    "inkscape": "Inkscape",
    "blender": "Blender",
    "kdenlive": "Kdenlive",
    "audacity": "Audacity",
    "mpv": "mpv",
    "qbittorrent": "qBittorrent",
    "wireshark-qt": "Wireshark",
    "htop": "Htop",
    "nvtop": "nvtop",
    "libreoffice-fresh": "LibreOffice",
    "libreoffice-still": "LibreOffice",
    "libreoffice": "LibreOffice",
    "helix": "Helix",
    "obsidian": "Obsidian",
    "heroic": "Heroic Games Launcher",
    "heroic-games-launcher-bin": "Heroic Games Launcher",
    "retroarch": "RetroArch",
    "bottles": "Bottles",
    "mangohud": "MangoHud",
    "bitwarden": "Bitwarden",
    "lazygit": "LazyGit",
    "zed": "Zed",
    "postman-bin": "Postman",
    "postman": "Postman",
    "rust": "Rust & Cargo",
    "go": "Go",
    "dbeaver": "DBeaver",
    "pavucontrol": "Volume Control",
    "joplin-desktop": "Joplin",
    "joplin": "Joplin",
    "onlyoffice-bin": "OnlyOffice",
    "onlyoffice": "OnlyOffice",
    "anytype-bin": "Anytype",
    "anytype": "Anytype",
    "evince": "Evince",
    "krita": "Krita",
    "foliate": "Foliate",
    "keepassxc": "KeepassXC",
    "wireguard-tools": "WireGuard",
    "wireguard": "WireGuard",
    "mullvad-vpn-bin": "Mullvad VPN",
    "mullvad-vpn": "Mullvad VPN",
    "bleachbit": "BleachBit",
    "tailscale": "Tailscale",
    "thunderbird": "Thunderbird",
    "handbrake": "HandBrake",
    "darktable": "Darktable",
    "ardour": "Ardour",
    "gparted": "GParted",
    "timeshift": "Timeshift",
    "baobab": "Baobab",
    "stacer-bin": "Stacer",
    "stacer": "Stacer",
    "fish": "Fish Shell",
    "hardinfo-git": "Hardinfo",
    "hardinfo": "Hardinfo",
    "hardinfo2": "Hardinfo",
    "prismlauncher": "Prism Launcher",
    "wine": "Wine",
    "gamemode": "GameMode",
    "ryujinx-bin": "Ryujinx",
    "ryujinx": "Ryujinx",
}

APP_EXTENDED_DESCRIPTIONS: Dict[str, str] = {
    "proton-vpn-gtk-app": (
        "Proton VPN is a high-security virtual private network developed by the CERN scientists behind Proton Mail. "
        "It features strict zero-logging architecture, 10 Gbps encrypted server backbones, Secure Core multi-hop routing, "
        "built-in NetShield DNS ad-blocking, IPv6 leak protection, and hardware-accelerated WireGuard protocol tunnels."
    ),
    "code": (
        "Visual Studio Code is a powerful modern source code editor. Features native debugging support, Git version control, "
        "IntelliSense syntax autocompletion, integrated terminal profiles, and a vast ecosystem of extensions "
        "for Python, Rust, C++, TypeScript, Go, Docker, and cloud workflows."
    ),
    "firefox": (
        "Mozilla Firefox is a modern, privacy-respecting desktop browser. Includes total cookie protection, "
        "fingerprinting resistance, high-speed multi-process WebRender graphics pipeline, and customizable tabbed browsing."
    ),
    "steam": (
        "Steam is the world's premier digital gaming distribution and community platform. "
        "Includes Proton compatibility layer for native Linux execution of Windows games, cloud save synchronization, and community workshop."
    ),
    "discord": (
        "Discord provides voice, video, and text communication for developer and gaming communities. "
        "Includes low-latency voice channels, stream sharing, server permissions, and rich presence integration."
    ),
    "spotify": (
        "Spotify provides instant desktop music and podcast streaming. "
        "Features high-bitrate audio playback, personalized recommendations, synchronized lyrics, and offline playback libraries."
    ),
    "obs-studio": (
        "OBS Studio is an open-source real-time video recording and live broadcasting suite. "
        "Features hardware-accelerated NVENC and VAAPI encoding, multi-source compositing, modular dock layouts, and VST audio filters."
    ),
    "libreoffice-fresh": (
        "LibreOffice is a full-featured office productivity suite compatible with Microsoft Office formats. "
        "Includes Writer (documents), Calc (spreadsheets), Impress (presentations), Draw (vector diagrams), Math, and Base databases."
    ),
    "obsidian": (
        "Obsidian is a powerful knowledge base and personal note-taking tool that works on a local folder of Markdown files. "
        "Features bidirectional note linking, interactive knowledge graph visualization, and an extensive plugin ecosystem."
    ),
    "vlc": (
        "VLC is a versatile media player and streaming framework that natively decodes MPEG, DivX, H.264, H.265/HEVC, AV1, MKV, "
        "and lossless audio formats with zero external codec requirements."
    ),
    "blender": (
        "Blender is a 3D creation suite supporting modeling, rigging, simulation, Cycles ray-traced rendering, compositing, "
        "motion tracking, and 2D animation pipelines."
    ),
    "gimp": (
        "GIMP is an image manipulation program featuring layers, channels, high-bit-depth color management, "
        "custom brushes, and extensible Python scripting for digital artists and photographers."
    ),
    "neovim": (
        "Neovim is an extensible Vim-fork text editor featuring built-in Language Server Protocol (LSP) client, "
        "tree-sitter syntax highlighting, asynchronous I/O plugin support, and Lua configuration scripting."
    ),
    "alacritty": (
        "Alacritty is an OpenGL-accelerated terminal emulator focused on simplicity and maximum execution performance. "
        "Features low latency input processing and modern true-color rendering."
    ),
    "btop": (
        "btop is an interactive system resource monitor showing real-time CPU core utilization, memory breakdown, "
        "disk throughput, network bandwidth graphs, and process tree management."
    ),
    "fastfetch": (
        "Fastfetch is a system information tool written in C for high performance and minimal runtime overhead, "
        "displaying system hardware, OS kernel, desktop environment, and GPU status."
    ),
    "git": (
        "Git is the distributed version control system designed to handle everything from small to very large projects "
        "with speed, cryptographic integrity, and flexible non-linear branching workflows."
    ),
    "docker": (
        "Docker is an open container platform for developing, shipping, and running containerized applications. "
        "Enables seamless isolation, rapid deployment pipelines, and reproducible desktop environments."
    ),
    "kitty": (
        "Kitty is a fast, GPU-accelerated terminal emulator written in C and Python. "
        "Supports modern ligatures, true color, graphics protocols, tabs, splits, and comprehensive scriptability."
    ),
    "brave-bin": (
        "Brave is a privacy-first browser that automatically blocks trackers, fingerprinting, and intrusive ads out of the box. "
        "Includes Shields protection, private Tor browsing windows, and built-in IPFS protocol support."
    ),
    "zen-browser-bin": (
        "Zen Browser is an open-source Firefox-based browser emphasizing modern design, split workspaces, "
        "vertical tabs, compact UI layouts, and deep user privacy without telemetry."
    ),
    "chromium": (
        "Chromium is the open-source browser project that powers the modern web. "
        "Provides fast page rendering, sandbox process isolation, and broad compatibility with web standards."
    ),
    "torbrowser-launcher": (
        "Tor Browser protects your online privacy and anonymity by routing encrypted traffic through the decentralized Tor network, "
        "defending against tracking, surveillance, and censorship."
    ),
    "telegram-desktop": (
        "Telegram Desktop is a fast, cloud-synchronized messaging application featuring instant multi-device syncing, "
        "high-capacity group channels, encrypted voice/video calls, and file transfers up to 2 GB."
    ),
    "signal-desktop": (
        "Signal Desktop is a private messaging app providing end-to-end encrypted messaging, voice calls, "
        "and video conferences with zero metadata retention or surveillance."
    ),
    "lutris": (
        "Lutris is an open gaming platform for Linux that unifies games from Steam, GOG, Epic Games, Wine, and emulators "
        "into a single library with tailored per-game execution scripts and runner optimization."
    ),
    "heroic": (
        "Heroic is an open-source GUI launcher for Epic Games, GOG, and Amazon Games. "
        "Leverages Legendary and GOGdl backends with seamless Wine and Proton compatibility management."
    ),
    "retroarch": (
        "RetroArch is a frontend for emulators, game engines, and media players. "
        "Features real-time rewinding, shaders, netplay, and precise audio latency configuration."
    ),
    "bottles": (
        "Bottles easily manages Windows prefixes and gaming software on Linux. "
        "Features automated DLL installation, DXVK/VKD3D runners, and sandboxed bottle environments."
    ),
    "mangohud": (
        "MangoHud is a Vulkan and OpenGL overlay for monitoring FPS, frametimes, CPU/GPU temperatures, "
        "VRAM consumption, and system power draw directly in-game."
    ),
    "kdenlive": (
        "Kdenlive is a powerful non-linear video editor built on the MLT framework. "
        "Supports multi-track timeline editing, color grading, keyframe audio/video effects, and hardware-accelerated rendering."
    ),
    "audacity": (
        "Audacity is a multi-track audio editor and recorder. "
        "Supports 32-bit float audio, noise reduction, spectrum analysis, and an extensive library of audio effect plugins."
    ),
    "thunar": (
        "Thunar is a modern, lightweight, and fast file manager for the desktop. "
        "Features tabbed navigation, emblem badges, custom user actions, and smooth responsive directory browsing."
    ),
    "micro": (
        "Micro is a modern terminal-based text editor featuring full mouse support, syntax highlighting for over 130 languages, "
        "multiple cursors, and an intuitive keybinding system."
    ),
    "htop": (
        "htop is an interactive process viewer for Unix systems, providing an intuitive, color-coded terminal interface "
        "for monitoring CPU, memory, swap, and task threads in real time."
    ),
    "nvtop": (
        "nvtop is a task monitor for AMD, Intel, and NVIDIA GPUs, offering real-time telemetry on compute engine utilization, "
        "memory allocation, temperature, and graphics process threads."
    ),
    "pavucontrol": (
        "PulseAudio Volume Control (pavucontrol) provides a detailed mixer interface to configure audio volume levels, "
        "device routing, and per-application playback and recording streams."
    ),
}

_DESKTOP_OVERRIDES: Dict[str, str] = {
    "code": "code-oss",
    "visual-studio-code-bin": "code-oss",
    "vesktop": "vesktop",
    "discord": "discord",
    "proton-vpn-gtk-app": "protonvpn-app",
    "proton-vpn": "protonvpn-app",
    "brave-bin": "brave-origin",
    "brave": "brave-origin",
    "brave-origin-bin": "brave-origin",
    "zen-browser-bin": "zen",
    "spotify": "spotify",
    "spotify-launcher": "spotify-launcher",
    "steam": "steam",
    "alacritty": "Alacritty",
    "thunar": "thunar",
    "pavucontrol": "pavucontrol",
    "libreoffice-fresh": "libreoffice-startcenter",
    "libreoffice": "libreoffice-startcenter",
    "helix": "helix",
    "obsidian": "obsidian",
    "heroic": "heroic",
    "heroic-games-launcher-bin": "heroic",
    "retroarch": "retroarch",
    "bottles": "com.usebottles.bottles",
}


def format_bytes(size: Any) -> str:
    """Format byte size into human readable string."""
    try:
        size = float(size)
    except (ValueError, TypeError):
        return ""
    if size <= 0:
        return ""
    for unit in ['B', 'KiB', 'MiB', 'GiB', 'TiB']:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PiB"


def sanitize_str(val: Any, default: str = "") -> str:
    """Ensure a value is a clean single string without lists or None."""
    if val is None:
        return default
    if isinstance(val, (list, tuple, set)):
        return ", ".join(str(x) for x in val if x)
    return str(val).strip()


_DESKTOP_ICONS_CACHE: Dict[str, str] = {}
_DESKTOP_NAMES_CACHE: Dict[str, str] = {}
_DESKTOP_ENTRIES_CACHE: Dict[str, str] = {}


def get_desktop_icons_map() -> Dict[str, str]:
    """Index system .desktop files to resolve authentic application icons and launchers."""
    global _DESKTOP_ICONS_CACHE, _DESKTOP_NAMES_CACHE, _DESKTOP_ENTRIES_CACHE
    if _DESKTOP_ICONS_CACHE:
        return _DESKTOP_ICONS_CACHE

    mapping = {}
    names_mapping = {}
    entries_mapping = {}
    search_dirs = [
        Path("/usr/share/applications"),
        Path(os.path.expanduser("~/.local/share/applications")),
    ]
    for d in search_dirs:
        if not d.exists():
            continue
        for p in d.glob("*.desktop"):
            try:
                base = p.stem.lower()
                c_icon = None
                c_name = None
                c_exec = None
                with open(p, "r", errors="ignore") as f:
                    for line in f:
                        if line.startswith("Icon=") and not c_icon:
                            c_icon = line.strip().split("=", 1)[1]
                        elif line.startswith("Name=") and not c_name:
                            c_name = line.strip().split("=", 1)[1]
                        elif line.startswith("Exec=") and not c_exec:
                            c_exec = line.strip().split("=", 1)[1]
                        if c_icon and c_name and c_exec:
                            break
                if c_icon:
                    mapping[base] = c_icon
                    if "." in base:
                        mapping[base.split(".")[-1]] = c_icon
                if c_name:
                    names_mapping[base] = c_name
                    if "." in base:
                        names_mapping[base.split(".")[-1]] = c_name
                # Launcher entries mapping
                entries_mapping[base] = p.stem
                if "." in base:
                    entries_mapping[base.split(".")[-1]] = p.stem
                bin_name = ""
                if c_exec:
                    clean_exec = c_exec.split()[0].strip('"').strip("'")
                    bin_name = os.path.basename(clean_exec).lower()
                    if bin_name and bin_name not in entries_mapping:
                        entries_mapping[bin_name] = p.stem
                for key in [base, bin_name]:
                    if not key:
                        continue
                    clean = re.sub(r'-(bin|launcher|desktop|oss|browser|git|app|gtk-app|qt-app)$', '', key)
                    if clean and clean not in entries_mapping:
                        entries_mapping[clean] = p.stem
                    clean_nodash = clean.replace("-", "")
                    if clean_nodash and clean_nodash not in entries_mapping:
                        entries_mapping[clean_nodash] = p.stem
            except Exception:
                pass

    _DESKTOP_ICONS_CACHE = mapping
    _DESKTOP_NAMES_CACHE = names_mapping
    _DESKTOP_ENTRIES_CACHE = entries_mapping
    return _DESKTOP_ICONS_CACHE


def get_desktop_entries_map() -> Dict[str, str]:
    """Return fast in-memory map of application package and binary names to desktop launcher stems."""
    get_desktop_icons_map()
    return _DESKTOP_ENTRIES_CACHE


def get_desktop_names_map() -> Dict[str, str]:
    """Index system .desktop files to resolve human-readable app names."""
    get_desktop_icons_map()
    return _DESKTOP_NAMES_CACHE


def get_app_display_name(pkg_name: str, fallback_title: str = "") -> str:
    """Return a polished human-readable Apple App Store title."""
    if fallback_title:
        return fallback_title
    clean_pkg = pkg_name.lower().strip()
    if clean_pkg in APP_DISPLAY_NAMES:
        return APP_DISPLAY_NAMES[clean_pkg]
    dt_names = get_desktop_names_map()
    if clean_pkg in dt_names:
        return dt_names[clean_pkg]
    # Clean up prefixes/suffixes
    clean = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|cli|daemon|desktop|launcher)$', '', clean_pkg)
    if clean in APP_DISPLAY_NAMES:
        return APP_DISPLAY_NAMES[clean]
    if clean in dt_names:
        return dt_names[clean]
    return clean.replace("-", " ").title()


def resolve_icon_name(pkg_name: str, desc: str = "") -> str:
    """
    Intelligently map a package name to an authentic Linux desktop icon (Papirus / System).
    Guarantees 100% modern vector icons, never emojis or dated glyphs.
    """
    name = pkg_name.lower().strip()
    overrides = {
        "proton-vpn-gtk-app": "proton-vpn-logo",
        "proton-vpn-cli": "proton-vpn-logo",
        "proton-vpn": "proton-vpn-logo",
        "protonvpn": "proton-vpn-logo",
        "proton-pass": "proton-pass",
        "protonplus": "protonplus",
        "protonup-qt": "protonup-qt",
        "protontricks": "wine",
        "mullvad-vpn-bin": "mullvad-vpn",
        "mullvad-vpn": "mullvad-vpn",
        "zen-browser-bin": "zen-browser",
        "zen-browser": "zen-browser",
        "zen": "zen-browser",
        "visual-studio-code-bin": "com.visualstudio.code.oss",
        "code": "com.visualstudio.code.oss",
        "code-oss": "com.visualstudio.code.oss",
        "brave-bin": "brave-browser",
        "brave": "brave-browser",
        "brave-origin-bin": "brave-browser",
        "brave-origin": "brave-browser",
        "torbrowser-launcher": "tor-browser",
        "firefox-pure": "firefox",
        "firefox": "firefox",
        "vesktop": "discord",
        "discord": "discord",
        "steam": "steam",
        "spotify": "spotify-client",
        "spotify-launcher": "spotify-client",
        "vlc": "vlc",
        "mpv": "mpv",
        "obs-studio": "obs",
        "obs": "obs",
        "foot": "foot",
        "kitty": "kitty",
        "btop": "utilities-system-monitor",
        "btop++": "utilities-system-monitor",
        "fastfetch": "utilities-terminal",
        "neovim": "nvim",
        "nvim": "nvim",
        "git": "git",
        "docker": "docker-desktop",
        "alacritty": "Alacritty",
        "telegram-desktop": "telegram",
        "telegram": "telegram",
        "signal-desktop": "signal-desktop",
        "lutris": "net.lutris.Lutris",
        "thunar": "org.xfce.thunar",
        "nautilus": "org.gnome.Nautilus",
        "dolphin": "org.kde.dolphin",
        "pavucontrol": "org.pulseaudio.pavucontrol",
        "micro": "micro",
        "gimp": "gimp",
        "blender": "blender",
        "chromium": "chromium",
        "btrfs-assistant": "btrfs-assistant",
        "cachyos-hello": "org.cachyos.hello",
        "org.cachyos.kernelmanager": "org.cachyos.KernelManager",
        "cachyos-pi": "cachyos-pi",
        "chatgpt": "chatgpt",
        "com.anthropic.claude": "claude-desktop",
        "cmake-gui": "CMakeSetup",
        "freedownloadmanager": "freedownloadmanager",
        "goverlay": "io.github.benjamimgois.goverlay",
        "com.heroicgameslauncher.hgl": "com.heroicgameslauncher.hgl",
        "hyprmod": "io.github.bluemancz.hyprmod",
        "kdeconnect": "kdeconnect",
        "localsend": "localsend",
        "meld": "org.gnome.Meld",
        "scx-manager": "org.cachyos.scx-manager",
        "stremio": "smartcode-stremio",
        "bash": "utilities-terminal",
        "zsh": "utilities-terminal",
        "fish": "utilities-terminal",
        "libreoffice-fresh": "libreoffice-main",
        "libreoffice": "libreoffice-main",
        "libreoffice-still": "libreoffice-main",
        "obsidian": "obsidian",
        "heroic": "com.heroicgameslauncher.hgl",
        "heroic-games-launcher-bin": "com.heroicgameslauncher.hgl",
        "kdenlive": "kdenlive",
        "audacity": "audacity",
        "retroarch": "retroarch",
        "bottles": "com.usebottles.bottles",
        "mangohud": "utilities-system-monitor",
        "bitwarden": "bitwarden",
        "lazygit": "lazygit",
        "zed": "dev.zed.Zed",
        "postman-bin": "postman",
        "postman": "postman",
        "dbeaver": "dbeaver",
        "joplin-desktop": "joplin",
        "joplin": "joplin",
        "onlyoffice-bin": "onlyoffice-desktopeditors",
        "anytype-bin": "anytype",
        "evince": "org.gnome.Evince",
        "krita": "krita",
        "foliate": "com.github.johnfactotum.Foliate",
        "keepassxc": "org.keepassxc.KeePassXC",
        "wireguard-tools": "network-vpn",
        "bleachbit": "bleachbit",
        "tailscale": "tailscale",
        "thunderbird": "org.mozilla.Thunderbird",
        "handbrake": "fr.handbrake.ghb",
        "darktable": "darktable",
        "ardour": "ardour",
        "gparted": "gparted",
        "timeshift": "timeshift",
        "baobab": "org.gnome.baobab",
        "stacer-bin": "stacer",
        "stacer": "stacer",
        "hardinfo-git": "hardinfo",
        "hardinfo": "hardinfo",
        "hardinfo2": "hardinfo",
        "prismlauncher": "org.prismlauncher.PrismLauncher",
        "wine": "wine",
        "gamemode": "preferences-system",
        "ryujinx-bin": "ryujinx",
        "ryujinx": "ryujinx",
    }
    if name in overrides:
        return overrides[name]

    # Check desktop icons cache
    dt_map = get_desktop_icons_map()
    if name in dt_map:
        return dt_map[name]

    clean = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|cli|daemon|desktop|launcher)$', '', name)
    clean2 = re.sub(r'-(gtk|qt|gtk[0-9]|qt[0-9]|electron|wayland|x11)$', '', clean)

    if clean in overrides:
        return overrides[clean]
    if clean in dt_map:
        return dt_map[clean]
    if clean2 in overrides:
        return overrides[clean2]
    if clean2 in dt_map:
        return dt_map[clean2]

    combined = f"{name} {desc}".lower()
    if "vpn" in combined or "wireguard" in combined:
        return "network-vpn"
    if any(k in combined for k in ["browser", "web-browser", "firefox", "chromium", "chrome"]):
        return "web-browser"
    if any(k in combined for k in ["terminal", "shell", "console", "emulator", "prompt"]):
        return "utilities-terminal"
    if any(k in combined for k in ["audio", "music", "sound", "player", "alsa", "pipewire", "pulse"]):
        return "multimedia-audio-player"
    if any(k in combined for k in ["video", "movie", "recording", "stream", "codec", "ffmpeg"]):
        return "video-player"
    if any(k in combined for k in ["editor", "code", "ide", "develop", "compiler", "sdk"]):
        return "text-editor"
    if any(k in combined for k in ["game", "gaming", "steam", "emulator", "wine", "proton"]):
        return "applications-games"
    if any(k in combined for k in ["image", "photo", "graphics", "paint", "draw", "render"]):
        return "applications-graphics"
    if any(k in combined for k in ["font", "ttf-", "otf-", "noto-"]):
        return "preferences-desktop-font"
    if any(k in combined for k in ["theme", "icon-theme", "cursor"]):
        return "preferences-desktop-theme"
    if any(k in combined for k in ["system", "kernel", "linux", "cachyos", "bpf", "udev", "firmware", "driver"]):
        return "preferences-system"
    if any(k in combined for k in ["security", "crypto", "cert", "auth", "keyring", "password", "vpn"]):
        return "security-high"
    if any(k in combined for k in ["network", "wifi", "bluetooth", "wireless", "ethernet", "ip"]):
        return "network-wired"
    if name.startswith("lib") or "library" in combined:
        return "system-software-install"

    return "system-software-install"



def fuzzy_score(query: str, target: str, desc: str = "") -> int:
    """Fuzzy matching score with multi-word, prefix, and boundary bonuses."""
    q = query.lower().strip()
    t = target.lower()
    d = desc.lower() if desc else ""

    if not q or not t:
        return 0

    if q == t:
        return 1000

    q_norm = q.replace("-", " ").replace("_", " ")
    t_norm = t.replace("-", " ").replace("_", " ")

    if q_norm == t_norm:
        return 980

    if t_norm.startswith(q_norm) or t.startswith(q):
        return 850 - min(100, len(t))

    idx = t_norm.find(q_norm)
    if idx != -1:
        is_boundary = (idx == 0 or t_norm[idx - 1] == " ")
        score = 720 - (idx * 5) - min(100, len(t))
        if is_boundary:
            score += 80
        return score

    words = q_norm.split()
    if len(words) > 1:
        all_in_name = all(w in t_norm for w in words)
        if all_in_name:
            score = 660 - min(100, len(t))
            last_pos = -1
            ordered = True
            for w in words:
                pos = t_norm.find(w)
                if pos <= last_pos:
                    ordered = False
                last_pos = pos
            if ordered:
                score += 70
            return score

        all_in_record = all((w in t_norm or w in d) for w in words)
        if all_in_record:
            return 320 - min(100, len(t))

    qi = 0
    score = 400
    last_idx = -2
    consecutive = 0
    for i, ch in enumerate(t):
        if qi < len(q) and ch == q[qi]:
            if last_idx == i - 1:
                consecutive += 1
                score += 25 * consecutive
            else:
                consecutive = 0
            if i == 0 or t[i - 1] in "-_.":
                score += 35
            last_idx = i
            qi += 1
    if qi == len(q):
        return score - min(100, len(t))

    if d and q in d:
        return 180 - min(80, d.find(q))

    return 0


class PackageManager:
    def __init__(self):
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        self.packages: Dict[str, Dict[str, Any]] = {}
        self.installed_set: Set[str] = set()
        self.installed_versions: Dict[str, str] = {}
        self.upgradable_list: List[Dict[str, str]] = []
        if UPDATES_CACHE_FILE.exists():
            try:
                with open(UPDATES_CACHE_FILE, "r") as f:
                    self.upgradable_list = json.load(f)
            except Exception:
                pass
        self._lock = threading.Lock()
        self._aur_cache: Dict[str, List[Dict[str, Any]]] = {}
        self.is_loaded = False
        self.active_transaction: Optional[Dict[str, Any]] = None
        self._last_progress: float = 0.0
        self._action_lock = threading.Lock()
        self.is_checking_updates = False
        self.updates_checked = False
        self.container_mgr = ContainerManager()
        # Load installed packages synchronously so cards immediately reflect installed status
        self.refresh_installed()
        # Persistent rotating featured apps state
        state = _load_featured_state()
        self._featured_rotation_offset: int = (state.get("offset", 0) + 1) % 50
        self._last_featured_shown: List[str] = list(state.get("last_shown", []))
        _save_featured_state(self._featured_rotation_offset, self._last_featured_shown)
        self._current_featured_apps: Optional[List[Dict[str, Any]]] = None

    def refresh_installed(self):
        """Quickly reload the list of installed packages and their versions."""
        try:
            res = subprocess.run(["pacman", "-Q"], capture_output=True, text=True, check=True)
            installed = {}
            for line in res.stdout.splitlines():
                parts = line.strip().split()
                if len(parts) >= 2:
                    installed[parts[0]] = parts[1]
            with self._lock:
                self.installed_versions = installed
                self.installed_set = set(installed.keys())
        except Exception as e:
            print(f"[Aura] Error loading installed packages: {e}", file=sys.stderr)

    def is_installed(self, pkg_name: str) -> bool:
        """Check if a package or its equivalent launcher/alias is installed on the system."""
        if not pkg_name:
            return False
        name_lower = pkg_name.lower().strip()
        if name_lower in self.installed_set:
            return True
        clean = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|cli|daemon|desktop|launcher)$', '', name_lower)
        if clean in self.installed_set:
            return True
        clean_nodash = clean.replace("-", "")
        if clean_nodash in self.installed_set:
            return True
        # Explicit variants
        if name_lower in ["code", "visual-studio-code-bin", "visual-studio-code"] and ("code" in self.installed_set or "code-oss" in self.installed_set):
            return True
        if name_lower in ["brave", "brave-bin", "brave-browser"] and ("brave-origin-bin" in self.installed_set or "brave-bin" in self.installed_set or "brave" in self.installed_set):
            return True
        if name_lower in _DESKTOP_OVERRIDES:
            ov = _DESKTOP_OVERRIDES[name_lower]
            if ov in self.installed_set:
                return True
        return False

    def get_active_transaction(self) -> Optional[Dict[str, Any]]:
        """Returns a copy of self.active_transaction safely under lock."""
        with self._action_lock:
            if self.active_transaction is not None:
                return dict(self.active_transaction)
            return None

    def is_pkg_installing(self, pkg_name: str) -> bool:
        """
        Returns True if self.active_transaction exists and matches pkg_name
        (or if action is 'update'/'upgrade' and pkg_name is in transaction).
        """
        if not pkg_name:
            return False
        target = pkg_name.strip().lower()
        with self._action_lock:
            if not self.active_transaction:
                return False
            tx_pkg = (self.active_transaction.get("pkg_name") or "").strip().lower()
            if tx_pkg == target:
                return True
            pkgs = self.active_transaction.get("packages")
            if isinstance(pkgs, (list, set, tuple)):
                if any(isinstance(p, str) and p.strip().lower() == target for p in pkgs):
                    return True
            action = (self.active_transaction.get("action") or "").lower()
            if action in ["update", "upgrade"]:
                if tx_pkg in ["system", "--all", "all", "", None]:
                    if target in ["system", "--all", "all", ""]:
                        return True
                    with self._lock:
                        if any(u.get("name", "").strip().lower() == target for u in self.upgradable_list):
                            return True
            return False

    def get_active_progress(self, pkg_name: Optional[str] = None) -> Tuple[float, str]:
        """
        Returns (progress_fraction, status_msg) for the active transaction,
        or (0.0, "") if none.
        """
        with self._action_lock:
            if not self.active_transaction:
                return (0.0, "")
            if pkg_name is not None:
                target = pkg_name.strip().lower()
                tx_pkg = (self.active_transaction.get("pkg_name") or "").strip().lower()
                matches = (tx_pkg == target)
                if not matches:
                    pkgs = self.active_transaction.get("packages")
                    if isinstance(pkgs, (list, set, tuple)):
                        matches = any(isinstance(p, str) and p.strip().lower() == target for p in pkgs)
                    if not matches:
                        action = (self.active_transaction.get("action") or "").lower()
                        if action in ["update", "upgrade"]:
                            if tx_pkg in ["system", "--all", "all", "", None]:
                                if target in ["system", "--all", "all", ""]:
                                    matches = True
                                else:
                                    with self._lock:
                                        matches = any(u.get("name", "").strip().lower() == target for u in self.upgradable_list)
                if not matches:
                    return (0.0, "")
            prog = float(self.active_transaction.get("progress", self._last_progress))
            msg = str(self.active_transaction.get("status_msg") or self.active_transaction.get("status") or "")
            return (prog, msg)

    def get_installed_desktop_apps(self) -> List[Dict[str, Any]]:
        """Return list of real installed desktop applications with .desktop launchers."""
        apps = []
        seen = set()
        search_dirs = [
            Path("/usr/share/applications"),
            Path(os.path.expanduser("~/.local/share/applications")),
        ]
        ignored_stems = {
            "bssh", "bvnc", "avahi-discover", "xfce4-about", "thunar-bulk-rename",
            "thunar-settings", "io.github.eugeniosegala.mako.uninstaller", "qv4l2", "qvidcap",
            "lstopo", "footclient", "foot-server", "org.kde.kdeconnect.nonplasma",
            "org.kde.kdeconnect.sms", "cups", "limine-snapper-restore", "uuctl"
        }
        for d in search_dirs:
            if not d.exists():
                continue
            for p in sorted(d.glob("*.desktop")):
                stem = p.stem.lower()
                if stem in ignored_stems:
                    continue
                name, icon, comment, exec_cmd = "", "", "", ""
                no_display = False
                try:
                    with open(p, "r", errors="ignore") as f:
                        in_entry = False
                        for line in f:
                            sline = line.strip()
                            if sline == "[Desktop Entry]":
                                in_entry = True
                                continue
                            if in_entry and sline.startswith("["):
                                break
                            if in_entry:
                                if sline.startswith("Name=") and not name:
                                    name = sline.split("=", 1)[1]
                                elif sline.startswith("Icon=") and not icon:
                                    icon = sline.split("=", 1)[1]
                                elif sline.startswith("Comment=") and not comment:
                                    comment = sline.split("=", 1)[1]
                                elif sline.startswith("Exec=") and not exec_cmd:
                                    exec_cmd = sline.split("=", 1)[1]
                                elif sline.startswith("NoDisplay=true"):
                                    no_display = True
                except Exception:
                    continue

                if no_display or not name or not icon:
                    continue

                clean_key = name.lower()
                if clean_key in seen:
                    continue
                seen.add(clean_key)

                pkg_name = stem
                if stem.startswith("org.gnome."):
                    clean_id = stem.replace("org.gnome.", "")
                elif "." in stem:
                    clean_id = stem.split(".")[-1]
                else:
                    clean_id = stem

                if clean_id in self.installed_set:
                    pkg_name = clean_id
                elif stem in self.installed_set:
                    pkg_name = stem

                apps.append({
                    "name": pkg_name,
                    "display_name": name,
                    "desc": comment or f"{name} desktop application",
                    "icon": icon,
                    "desktop_entry": p.stem,
                    "source": "pacman",
                    "is_installed": True,
                })

        apps.sort(key=lambda x: x["display_name"].lower())
        return apps

    def _format_featured_app(self, app_id: str, cat_id: str = "", app_info: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Produce rich editorial metadata for a featured application."""
        curated = FEATURED_APP_PRESETS.get(app_id) or FEATURED_APP_METADATA.get(app_id)
        if curated:
            accent_color = curated.get("accent_color", CATEGORY_ACCENT_DEFAULTS.get(cat_id, "#0a84ff"))
            def_bg, def_border = _color_to_gradient_and_border(accent_color)
            bg = curated.get("bg", def_bg)
            border = curated.get("border", def_border)
            sub = curated.get("sub") or (app_info.get("desc") if app_info else "")
            source = curated.get("source") or (app_info.get("source") if app_info else "pacman")
            icon = curated.get("icon") or resolve_icon_name(app_id, sub)
            tag = curated.get("tag", CATEGORY_TAG_DEFAULTS.get(cat_id, "EDITORS' CHOICE"))
            title = curated.get("title", APP_DISPLAY_NAMES.get(app_id, app_id.title()))
            return {
                "id": app_id,
                "source": source,
                "tag": tag,
                "title": title,
                "sub": sub,
                "bg": bg,
                "border": border,
                "accent_color": accent_color,
                "color": accent_color,
                "icon": icon,
                "is_installed": self.is_installed(app_id),
            }

        # Dynamic fallback for any catalog app
        accent_color = CATEGORY_ACCENT_DEFAULTS.get(cat_id, "#0a84ff")
        bg, border = _color_to_gradient_and_border(accent_color)
        source = app_info.get("source", "pacman") if app_info else "pacman"
        title = APP_DISPLAY_NAMES.get(
            app_id,
            app_info.get("title", app_id.replace("-", " ").title()) if app_info else app_id.replace("-", " ").title()
        )
        sub = APP_EXTENDED_DESCRIPTIONS.get(
            app_id,
            app_info.get("desc", f"High-performance {cat_id} application for modern Linux") if app_info else ""
        )
        icon = resolve_icon_name(app_id, sub)
        tag = CATEGORY_TAG_DEFAULTS.get(cat_id, "EDITORS' CHOICE")
        return {
            "id": app_id,
            "source": source,
            "tag": tag,
            "title": title,
            "sub": sub,
            "bg": bg,
            "border": border,
            "accent_color": accent_color,
            "color": accent_color,
            "icon": icon,
            "is_installed": self.is_installed(app_id),
        }

    def get_dynamic_featured_apps(
        self,
        count: int = 5,
        refresh: bool = False,
        rotate: bool = False,
        seed: Optional[int] = None
    ) -> List[Dict[str, Any]]:
        """
        Dynamically select 5-6 exciting, varied featured applications across diverse
        categories from the 84+ application catalog with rich editorial styling.
        Guarantees distinct categories, non-overlapping consecutive launches,
        and dynamically updated installed state.
        """
        if count <= 0:
            count = 5

        # Handle rotation or refresh requests
        if refresh or rotate:
            self._featured_rotation_offset = (self._featured_rotation_offset + 1) % 50
            self._current_featured_apps = None

        if self._current_featured_apps is not None and not refresh and not rotate and seed is None:
            if len(self._current_featured_apps) == count:
                # Refresh installed status dynamically
                for item in self._current_featured_apps:
                    item["is_installed"] = self.is_installed(item["id"])
                return self._current_featured_apps

        offset = seed if seed is not None else self._featured_rotation_offset

        # Build category map from CURATED_CATEGORIES
        cat_map = {cat["id"]: cat for cat in CURATED_CATEGORIES}

        # 7 distinct core categories rotating each launch
        core_categories = ["media", "dev", "games", "privacy", "productivity", "system", "essential"]
        cat_start = offset % len(core_categories)
        chosen_cats = [core_categories[(cat_start + i) % len(core_categories)] for i in range(min(count, len(core_categories)))]

        seen_ids: Set[str] = set()
        last_shown_set = set(self._last_featured_shown)
        featured_apps: List[Dict[str, Any]] = []

        for cat_id in chosen_cats:
            cands = FEATURED_CATEGORY_PRIORITIES.get(cat_id)
            if not cands and cat_id in cat_map:
                cands = [a["name"] for a in cat_map[cat_id]["apps"]]
            if not cands:
                continue

            app_dict = {}
            if cat_id in cat_map:
                app_dict = {a["name"]: a for a in cat_map[cat_id]["apps"]}

            cat_pos = core_categories.index(cat_id) if cat_id in core_categories else 0
            base_idx = (offset * 3 + cat_pos * 2) % len(cands)

            chosen_pkg = None
            # Pass 1: candidate not in seen_ids and not in last_shown
            for step in range(len(cands)):
                cand_pkg = cands[(base_idx + step) % len(cands)]
                if cand_pkg not in seen_ids and cand_pkg not in last_shown_set:
                    chosen_pkg = cand_pkg
                    break

            # Pass 2: fallback to any candidate not in seen_ids
            if not chosen_pkg:
                for step in range(len(cands)):
                    cand_pkg = cands[(base_idx + step) % len(cands)]
                    if cand_pkg not in seen_ids:
                        chosen_pkg = cand_pkg
                        break

            if chosen_pkg:
                seen_ids.add(chosen_pkg)
                app_info = app_dict.get(chosen_pkg)
                slide_meta = self._format_featured_app(chosen_pkg, cat_id=cat_id, app_info=app_info)
                featured_apps.append(slide_meta)

        # Fallback if fewer than count were picked (e.g. if count > 7)
        if len(featured_apps) < count:
            for cat in CURATED_CATEGORIES:
                cat_id = cat["id"]
                for app in cat["apps"]:
                    pkg = app["name"]
                    if pkg not in seen_ids:
                        seen_ids.add(pkg)
                        featured_apps.append(self._format_featured_app(pkg, cat_id=cat_id, app_info=app))
                        if len(featured_apps) >= count:
                            break
                if len(featured_apps) >= count:
                    break

        if seed is None:
            self._current_featured_apps = featured_apps
            new_ids = [a["id"] for a in featured_apps]
            self._last_featured_shown = (self._last_featured_shown + new_ids)[-10:]
            _save_featured_state(self._featured_rotation_offset, self._last_featured_shown)

        return featured_apps

    def rotate_featured_apps(self, count: int = 5) -> List[Dict[str, Any]]:
        """Advance rotation and return the next exciting batch of featured applications."""
        return self.get_dynamic_featured_apps(count=count, refresh=True)

    def check_updates(self) -> List[Dict[str, str]]:
        """Check for upgradable packages and return detailed list."""
        self.is_checking_updates = True
        updates: List[Dict[str, str]] = []
        try:
            res = subprocess.run(["checkupdates"], capture_output=True, text=True, timeout=14)
            if res.returncode == 0:
                for line in res.stdout.splitlines():
                    parts = line.strip().split()
                    if len(parts) >= 4 and parts[2] == "->":
                        pkg_name = parts[0]
                        updates.append({
                            "name": pkg_name,
                            "old_ver": parts[1],
                            "new_ver": parts[3],
                            "source": "pacman",
                            "icon": resolve_icon_name(pkg_name)
                        })
        except Exception as e:
            print(f"[Aura] Update check: {e}", file=sys.stderr)
        finally:
            self.is_checking_updates = False
            self.updates_checked = True

        with self._lock:
            self.upgradable_list = updates
            try:
                with open(UPDATES_CACHE_FILE, "w") as f:
                    json.dump(updates, f)
            except Exception:
                pass
        return updates

    def _get_sync_mtime(self) -> float:
        """Get latest modification timestamp of pacman sync databases."""
        latest = 0.0
        if SYNC_DIR.exists():
            for f in SYNC_DIR.glob("*.db"):
                try:
                    m = f.stat().st_mtime
                    if m > latest:
                        latest = m
                except OSError:
                    pass
        return latest

    def load_pacman_db(self, force_reload: bool = False):
        """Load or parse pacman sync databases into memory."""
        self.refresh_installed()
        sync_mtime = self._get_sync_mtime()

        if not force_reload and CACHE_FILE.exists():
            try:
                cache_stat = CACHE_FILE.stat()
                if cache_stat.st_mtime >= sync_mtime:
                    with open(CACHE_FILE, "rb") as f:
                        cached_pkgs = pickle.load(f)
                    with self._lock:
                        self.packages = cached_pkgs
                        self.is_loaded = True
                    return
            except Exception as e:
                print(f"[Aura] Cache read error, rebuilding: {e}", file=sys.stderr)

        pkgs: Dict[str, Dict[str, Any]] = {}
        for db_file in sorted(glob.glob("/var/lib/pacman/sync/*.db")):
            repo = os.path.basename(db_file).replace(".db", "")
            try:
                with tarfile.open(db_file, "r:*") as tar:
                    for m in tar.getmembers():
                        if m.name.endswith("/desc"):
                            f = tar.extractfile(m)
                            if not f:
                                continue
                            content = f.read().decode("utf-8", errors="replace").split("\n\n")
                            item: Dict[str, Any] = {
                                "repo": repo,
                                "source": "pacman",
                                "depends": [],
                                "optdepends": [],
                                "provides": [],
                                "conflicts": []
                            }
                            for section in content:
                                lines = section.strip().split("\n")
                                if not lines or not lines[0].startswith("%") or not lines[0].endswith("%"):
                                    continue
                                key = lines[0][1:-1].lower()
                                if key in ["depends", "optdepends", "provides", "conflicts"]:
                                    item[key] = lines[1:]
                                else:
                                    item[key] = lines[1] if len(lines) > 1 else ""

                            if "name" in item and isinstance(item["name"], str):
                                pkgs[item["name"]] = item
            except Exception as e:
                print(f"[Aura] Error parsing {db_file}: {e}", file=sys.stderr)

        with self._lock:
            self.packages = pkgs
            self.is_loaded = True

        try:
            with open(CACHE_FILE, "wb") as f:
                pickle.dump(pkgs, f)
        except Exception as e:
            print(f"[Aura] Error saving cache: {e}", file=sys.stderr)

    def search_pacman(self, query: str, limit: int = 150) -> List[Dict[str, Any]]:
        """Fuzzy search pacman repository packages."""
        if not query.strip():
            return []

        results = []
        with self._lock:
            items = list(self.packages.values())
            installed_set = self.installed_set
            installed_vers = self.installed_versions

        for pkg in items:
            name = pkg.get("name", "")
            desc = pkg.get("desc", "")
            score = fuzzy_score(query, name, desc)
            if score > 0:
                is_installed = name in installed_set
                results.append({
                    "name": name,
                    "version": pkg.get("version", ""),
                    "desc": desc,
                    "repo": pkg.get("repo", "extra"),
                    "source": "pacman",
                    "priority": 0,  # 1st Priority
                    "score": score,
                    "is_installed": is_installed,
                    "installed_version": installed_vers.get(name, ""),
                    "icon": resolve_icon_name(name, desc),
                    "csize": pkg.get("csize", ""),
                    "isize": pkg.get("isize", ""),
                    "url": pkg.get("url", ""),
                    "license": sanitize_str(pkg.get("license", "")),
                    "packager": sanitize_str(pkg.get("packager", "")),
                    "depends": pkg.get("depends", []),
                    "optdepends": pkg.get("optdepends", []),
                })

        results.sort(key=lambda x: x["score"], reverse=True)
        return results[:limit]

    def search_aur(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search AUR packages using AUR RPC API with paru fallback."""
        if not query.strip() or len(query.strip()) < 2:
            return []

        clean_q = query.strip()
        if clean_q in self._aur_cache:
            return self._aur_cache[clean_q]

        results = []
        with self._lock:
            installed_set = self.installed_set
            installed_vers = self.installed_versions

        try:
            url = f"https://aur.archlinux.org/rpc/v5/search/{urllib.parse.quote(clean_q)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Aura-PackageHub/3.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("results", []):
                    name = item.get("Name", "")
                    desc = item.get("Description", "") or ""
                    score = fuzzy_score(clean_q, name, desc)
                    if score > 0:
                        is_installed = name in installed_set
                        results.append({
                            "name": name,
                            "version": item.get("Version", ""),
                            "desc": desc,
                            "repo": "aur",
                            "source": "aur",
                            "priority": 1,  # 2nd Priority
                            "score": score,
                            "is_installed": is_installed,
                            "installed_version": installed_vers.get(name, ""),
                            "icon": resolve_icon_name(name, desc),
                            "url": item.get("URL", "") or f"https://aur.archlinux.org/packages/{name}",
                            "votes": item.get("NumVotes", 0),
                            "popularity": item.get("Popularity", 0.0),
                            "maintainer": sanitize_str(item.get("Maintainer", "Unknown")),
                            "license": sanitize_str(item.get("License", "")),
                            "depends": item.get("Depends", []),
                            "optdepends": item.get("OptDepends", []),
                        })
        except Exception:
            try:
                p = subprocess.run(["paru", "-Ssa", clean_q], capture_output=True, text=True, timeout=5)
                lines = p.stdout.splitlines()
                i = 0
                while i < len(lines):
                    line = lines[i].strip()
                    if line.startswith("aur/"):
                        parts = line[4:].split()
                        pkg_name = parts[0] if parts else ""
                        ver = parts[1] if len(parts) > 1 else ""
                        desc = ""
                        if i + 1 < len(lines) and lines[i+1].startswith("    "):
                            desc = lines[i+1].strip()
                            i += 1
                        if pkg_name:
                            score = fuzzy_score(clean_q, pkg_name, desc)
                            if score > 0:
                                results.append({
                                    "name": pkg_name,
                                    "version": ver,
                                    "desc": desc,
                                    "repo": "aur",
                                    "source": "aur",
                                    "priority": 1,
                                    "score": score,
                                    "is_installed": pkg_name in installed_set,
                                    "installed_version": installed_vers.get(pkg_name, ""),
                                    "icon": resolve_icon_name(pkg_name, desc),
                                    "url": f"https://aur.archlinux.org/packages/{pkg_name}",
                                })
                    i += 1
            except Exception as e:
                print(f"[Aura] Paru AUR fallback error: {e}", file=sys.stderr)

        results.sort(key=lambda x: x["score"], reverse=True)
        final_results = results[:limit]
        self._aur_cache[clean_q] = final_results
        return final_results

    def search_all(self, query: str, filter_mode: str = "all", limit: int = 150) -> List[Dict[str, Any]]:
        """Unified search prioritizing Pacman official repositories first."""
        q = query.strip()
        pacman_res = []
        aur_res = []

        if filter_mode in ["all", "pacman", "installed"]:
            pacman_res = self.search_pacman(q, limit=limit)

        if filter_mode in ["all", "aur"]:
            aur_res = self.search_aur(q, limit=limit)

        if filter_mode == "all":
            combined = pacman_res + aur_res
        elif filter_mode == "pacman":
            combined = pacman_res
        elif filter_mode == "aur":
            combined = aur_res
        elif filter_mode == "installed":
            combined = [p for p in pacman_res if p.get("is_installed")]
            with self._lock:
                for inst_name, inst_ver in self.installed_versions.items():
                    if inst_name not in self.packages:
                        score = fuzzy_score(q, inst_name)
                        if score > 0:
                            combined.append({
                                "name": inst_name,
                                "version": inst_ver,
                                "desc": "Locally installed package",
                                "repo": "local",
                                "source": "pacman",
                                "priority": 0,
                                "score": score,
                                "is_installed": True,
                                "installed_version": inst_ver,
                                "icon": resolve_icon_name(inst_name, ""),
                            })
            combined.sort(key=lambda x: x["score"], reverse=True)
        else:
            combined = []

        return combined[:limit]

    def search(self, query: str, source: str = "all", limit: int = 100) -> List[Dict[str, Any]]:
        """Unified search across pacman and/or AUR with strict priority."""
        if source == "pacman":
            return self.search_pacman(query, limit=limit)
        elif source == "aur":
            return self.search_aur(query, limit=limit)
        return self.search_all(query, filter_mode=source, limit=limit)

    def get_package_detail(self, name: str, source: str = "pacman") -> Dict[str, Any]:
        """Fetch full details of a package with strict sanitization."""
        with self._lock:
            is_installed = self.is_installed(name)
            installed_ver = self.installed_versions.get(name, "")
            if not installed_ver and is_installed:
                clean = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|cli|daemon|desktop|launcher)$', '', name.lower())
                installed_ver = self.installed_versions.get(clean, "")
                if not installed_ver and name.lower() in ["code", "visual-studio-code-bin"]:
                    installed_ver = self.installed_versions.get("code", "")

        info: Dict[str, Any] = {
            "name": name,
            "is_installed": is_installed,
            "installed_version": installed_ver,
            "source": source,
            "repo": "aur" if source == "aur" else "extra",
            "depends": [],
            "optdepends": [],
            "icon": resolve_icon_name(name, ""),
        }

        if source == "pacman" and name in self.packages:
            raw = self.packages[name]
            info.update(raw)
            info["license"] = sanitize_str(raw.get("license", ""))
            info["packager"] = sanitize_str(raw.get("packager", ""))

        if source == "pacman" and not info.get("desc"):
            try:
                res = subprocess.run(["pacman", "-Si", name], capture_output=True, text=True, timeout=3)
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            k = k.strip().lower().replace(" ", "_")
                            v = v.strip()
                            if k == "description":
                                info["desc"] = v
                            elif k == "version":
                                info["version"] = v
                            elif k == "repository":
                                info["repo"] = v
                            elif k == "url":
                                info["url"] = v
                            elif k == "licenses":
                                info["license"] = v
                            elif k == "installed_size":
                                info["isize_str"] = v
                            elif k == "download_size":
                                info["csize_str"] = v
                            elif k == "packager":
                                info["packager"] = sanitize_str(v)
                            elif k == "depends_on":
                                info["depends"] = [d for d in v.split() if d != "None"]
            except Exception:
                pass

        if is_installed:
            try:
                target_qi = name if name in self.installed_set else ("code" if name.lower() in ["code", "visual-studio-code-bin"] else name)
                res = subprocess.run(["pacman", "-Qi", target_qi], capture_output=True, text=True, timeout=3)
                if res.returncode == 0:
                    for line in res.stdout.splitlines():
                        if ":" in line:
                            k, v = line.split(":", 1)
                            k = k.strip().lower().replace(" ", "_")
                            v = v.strip()
                            if k == "depends_on":
                                info["depends"] = [d for d in v.split() if d != "None"]
                            elif k == "optional_deps":
                                info["optdepends"] = [v] if v != "None" else []
                            elif k == "installed_size":
                                info["isize_str"] = v
                            elif k == "url" and not info.get("url"):
                                info["url"] = v
                            elif k == "licenses" and not info.get("license"):
                                info["license"] = v
                            elif k == "packager" and not info.get("packager"):
                                info["packager"] = sanitize_str(v)
            except Exception:
                pass

        if source == "aur" or not info.get("desc"):
            try:
                url = f"https://aur.archlinux.org/rpc/v5/info/{urllib.parse.quote(name)}"
                req = urllib.request.Request(url, headers={"User-Agent": "Aura-PackageHub/3.0"})
                with urllib.request.urlopen(req, timeout=4) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    results = data.get("results", [])
                    if results:
                        res0 = results[0]
                        info["desc"] = res0.get("Description", info.get("desc", ""))
                        info["version"] = res0.get("Version", info.get("version", ""))
                        info["url"] = res0.get("URL", info.get("url", ""))
                        info["votes"] = res0.get("NumVotes", 0)
                        info["popularity"] = res0.get("Popularity", 0.0)
                        info["maintainer"] = sanitize_str(res0.get("Maintainer", "Unknown"))
                        info["license"] = sanitize_str(res0.get("License", ""))
                        if not info.get("depends"):
                            info["depends"] = res0.get("Depends", [])
            except Exception:
                pass

        raw_lic = sanitize_str(info.get("license", "Custom License"))
        if raw_lic.lower().startswith("licenseref-"):
            raw_lic = raw_lic[11:]
        if raw_lic.lower() in ("custom", "custom:none", "unknown", "unspecified", "none", "custom / unspecified"):
            raw_lic = "Custom License"
        info["license"] = raw_lic

        raw_pack = sanitize_str(info.get("packager", info.get("maintainer", "Community")))
        clean_pack = re.sub(r'<[^>]*>', '', raw_pack).strip()
        info["packager"] = clean_pack if clean_pack else "Community"

        info["repo"] = sanitize_str(info.get("repo", "extra"))
        info["version"] = sanitize_str(info.get("version", installed_ver))
        info["desc"] = sanitize_str(info.get("desc", "No description available."))
        info["url"] = sanitize_str(info.get("url", ""))

        if "isize" in info and isinstance(info["isize"], (int, str)) and not info.get("isize_str"):
            info["isize_str"] = format_bytes(info["isize"])
        if "csize" in info and isinstance(info["csize"], (int, str)):
            info["csize_str"] = format_bytes(info["csize"])

        info["display_name"] = get_app_display_name(name)
        info["desktop_entry"] = self.detect_desktop_entry(name)
        info["icon"] = resolve_icon_name(name, info["desc"])

        # Attach rich extended description if available
        name_clean = name.lower().strip()
        if name_clean in APP_EXTENDED_DESCRIPTIONS:
            info["extended_desc"] = APP_EXTENDED_DESCRIPTIONS[name_clean]
        else:
            base = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|cli|daemon|desktop|launcher)$', '', name_clean)
            if base in APP_EXTENDED_DESCRIPTIONS:
                info["extended_desc"] = APP_EXTENDED_DESCRIPTIONS[base]

        return info

    def detect_desktop_entry(self, pkg_name: str) -> Optional[str]:
        """Detect if an installed package provides a desktop application launcher (fast in-memory)."""
        if not self.is_installed(pkg_name):
            return None
        entries = get_desktop_entries_map()
        name_lower = pkg_name.lower()
        if name_lower in _DESKTOP_OVERRIDES:
            ov = _DESKTOP_OVERRIDES[name_lower]
            if ov in entries or os.path.exists(f"/usr/share/applications/{ov}.desktop"):
                return ov
        if name_lower in entries:
            return entries[name_lower]
        clean = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|cli|daemon|desktop|launcher)$', '', name_lower)
        if clean in entries:
            return entries[clean]
        clean2 = re.sub(r'-(gtk|qt|gtk[0-9]|qt[0-9]|electron|wayland|x11)$', '', clean)
        if clean2 in entries:
            return entries[clean2]
        clean_nodash = clean.replace("-", "")
        if clean_nodash in entries:
            return entries[clean_nodash]
        return None

    def launch_desktop_app(self, desktop_name: str) -> bool:
        """Launch a desktop application using gtk-launch or gio."""
        try:
            subprocess.Popen(["gtk-launch", desktop_name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except Exception:
            try:
                subprocess.Popen(["gio", "launch", f"/usr/share/applications/{desktop_name}.desktop"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                return True
            except Exception:
                return False


    def execute_background_action(
        self,
        action: str,
        pkg_name: str,
        source: str,
        progress_cb: Callable[[float, str], None],
        complete_cb: Callable[[bool, str, str, str], None]
    ):
        """
        Run installation, removal, or upgrade in background without terminal popups.
        """
        is_system_upgrade = pkg_name in ["system", "--all", "all", ""] or not pkg_name

        with self._action_lock:
            self.active_transaction = {
                "action": action,
                "pkg_name": pkg_name,
                "source": source,
                "progress": 0.06,
                "status": "Authenticating & preparing...",
                "status_msg": "Authenticating & preparing...",
                "start_time": time.time(),
            }
            if action in ["upgrade", "update"] and is_system_upgrade:
                with self._lock:
                    self.active_transaction["packages"] = [u.get("name") for u in self.upgradable_list if u.get("name")]
            elif pkg_name:
                self.active_transaction["packages"] = [pkg_name]
            self._last_progress = 0.06

        def _safe_progress(target_frac: float, msg: str):
            with self._action_lock:
                frac = max(self._last_progress, min(0.98, target_frac))
                self._last_progress = frac
                if isinstance(self.active_transaction, dict):
                    self.active_transaction["progress"] = frac
                    self.active_transaction["status"] = msg
                    self.active_transaction["status_msg"] = msg
            progress_cb(frac, msg)

        _report_prog = _safe_progress

        def _worker():
            env = os.environ.copy()
            if ASKPASS_SCRIPT.exists():
                env["SUDO_ASKPASS"] = str(ASKPASS_SCRIPT)
            env["LC_ALL"] = "C"

            if action in ["upgrade", "update"]:
                if is_system_upgrade:
                    if shutil.which("paru"):
                        cmd = ["paru", "-Syu", "--noconfirm", "--sudoflags", "-A"]
                    else:
                        cmd = ["sudo", "-A", "pacman", "-Syu", "--noconfirm"]
                else:
                    if source == "aur":
                        cmd = ["paru", "-S", "--noconfirm", "--sudoflags", "-A", pkg_name]
                    else:
                        cmd = ["sudo", "-A", "pacman", "-S", "--noconfirm", pkg_name]
            elif action == "install":
                if source == "pacman":
                    cmd = ["sudo", "-A", "pacman", "-S", "--noconfirm", "--needed", pkg_name]
                else:
                    cmd = ["paru", "-S", "--noconfirm", "--needed", "--sudoflags", "-A", pkg_name]
            elif action == "remove":
                cmd = ["sudo", "-A", "pacman", "-Rns", "--noconfirm", pkg_name]
            else:
                cmd = ["sudo", "-A", "pacman", "-S", "--noconfirm", pkg_name]

            _safe_progress(0.06, "Authenticating & preparing...")

            error_lines = []
            total_packages = 1
            downloaded_count = 0
            installed_count = 0

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )

                for line in proc.stdout:
                    clean = line.strip()
                    if not clean:
                        continue
                    l_lower = clean.lower()

                    if "error:" in l_lower or "failed" in l_lower:
                        error_lines.append(clean)

                    pkg_match = re.search(r'packages\s*\(\s*(\d+)\s*\)', l_lower)
                    if pkg_match:
                        total_packages = max(total_packages, int(pkg_match.group(1)))

                    idx_match = re.search(r'\((\d+)/(\d+)\)', clean)
                    cur_idx = 0
                    if idx_match:
                        cur_idx = int(idx_match.group(1))
                        tot_idx = int(idx_match.group(2))
                        total_packages = max(total_packages, tot_idx)

                    # Multi-stage monotonic progression (Guarantees progress ONLY moves forward)
                    if "resolving dependencies" in l_lower or "calculating dependencies" in l_lower:
                        _safe_progress(0.16, "Resolving package dependencies...")
                    elif (
                        "checking keyring" in l_lower
                        or "checking keys in keyring" in l_lower
                        or "package integrity" in l_lower
                        or "verifying package integrity" in l_lower
                        or "loading package files" in l_lower
                    ):
                        _safe_progress(0.26, "Checking package integrity & keyring...")
                    elif (
                        "looking for conflicting" in l_lower
                        or "checking for conflicting" in l_lower
                        or "file conflicts" in l_lower
                        or "available disk space" in l_lower
                        or "verifying disk space" in l_lower
                    ):
                        _safe_progress(0.38, "Verifying disk space & conflicts...")
                    elif "retrieving packages" in l_lower or "downloading" in l_lower or ".pkg.tar." in l_lower:
                        downloaded_count = max(downloaded_count + 1, cur_idx if cur_idx else downloaded_count + 1)
                        dl_stage = min(0.68, 0.40 + (downloaded_count / max(1, total_packages)) * 0.28)
                        dl_msg = (
                            f"Downloading package files... ({downloaded_count}/{total_packages})"
                            if total_packages > 1
                            else "Downloading package files..."
                        )
                        _safe_progress(dl_stage, dl_msg)
                    elif (
                        "installing" in l_lower
                        or "processing package" in l_lower
                        or "upgrading" in l_lower
                        or "reinstalling" in l_lower
                        or (action == "remove" and "removing" in l_lower)
                    ):
                        installed_count = max(installed_count + 1, cur_idx if cur_idx else installed_count + 1)
                        inst_stage = min(0.90, 0.72 + (installed_count / max(1, total_packages)) * 0.18)
                        if action == "remove":
                            inst_msg = f"Removing {pkg_name}..."
                        elif pkg_name and total_packages <= 1 and not is_system_upgrade:
                            inst_msg = f"Installing {pkg_name}..."
                        else:
                            inst_msg = f"Installing packages... ({installed_count}/{total_packages})"
                        _safe_progress(inst_stage, inst_msg)
                    elif (
                        "post-transaction hooks" in l_lower
                        or "running hooks" in l_lower
                        or "running post-transaction" in l_lower
                    ):
                        _safe_progress(0.92, "Running post-transaction desktop hooks...")
                    elif (
                        "conditionneedsupdate" in l_lower
                        or "desktop file" in l_lower
                        or "mime" in l_lower
                        or "icon" in l_lower
                        or "font" in l_lower
                        or "finalizing" in l_lower
                    ):
                        _safe_progress(0.96, "Finalizing application environment...")

                proc.wait()
                success = proc.returncode == 0
                if success:
                    with self._lock:
                        if action in ["upgrade", "update"]:
                            if is_system_upgrade:
                                self.upgradable_list = []
                                try:
                                    UPDATES_CACHE_FILE.unlink(missing_ok=True)
                                except Exception:
                                    pass
                            elif pkg_name:
                                self.upgradable_list = [u for u in self.upgradable_list if u.get("name") != pkg_name]
                                try:
                                    with open(UPDATES_CACHE_FILE, "w") as f:
                                        json.dump(self.upgradable_list, f)
                                except Exception:
                                    pass
                    self.refresh_installed()
                    with self._action_lock:
                        if self.active_transaction:
                            self.active_transaction["progress"] = 1.0
                            self.active_transaction["status"] = "Installation complete!"
                            self.active_transaction["status_msg"] = "Installation complete!"
                    progress_cb(1.0, "Installation complete!")
                    complete_cb(True, action, pkg_name, "")
                    # Background check for remaining updates
                    threading.Thread(target=self.check_updates, daemon=True).start()

                    target_tx = self.active_transaction
                    def _clear_tx():
                        time.sleep(1.0)
                        with self._action_lock:
                            if self.active_transaction is target_tx:
                                self.active_transaction = None
                                self._last_progress = 0.0

                    threading.Thread(target=_clear_tx, daemon=True).start()
                else:
                    err_msg = "\n".join(error_lines[-3:]) if error_lines else f"Exited with code {proc.returncode}"
                    if any("password" in l.lower() or "auth" in l.lower() for l in error_lines):
                        PackageManager.clear_auth_cache()
                    with self._action_lock:
                        self.active_transaction = None
                        self._last_progress = 0.0
                    progress_cb(0.0, f"Error: {err_msg}")
                    complete_cb(False, action, pkg_name, err_msg)

            except Exception as e:
                err_msg = str(e)
                with self._action_lock:
                    self.active_transaction = None
                    self._last_progress = 0.0
                progress_cb(0.0, f"Error: {err_msg}")
                complete_cb(False, action, pkg_name, err_msg)

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def get_vault_path() -> Path:
        return Path.home() / ".local" / "share" / "aura" / ".aura_vault"

    @classmethod
    def is_passwordless_configured(cls) -> bool:
        """Check if sudo pacman runs without password or vault has cached key."""
        try:
            res = subprocess.run(["sudo", "-n", "pacman", "-V"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            if res.returncode == 0:
                return True
        except Exception:
            pass
        vault = cls.get_vault_path()
        if vault.exists():
            try:
                txt = vault.read_text().strip()
                if txt:
                    return True
            except Exception:
                pass
        return False

    @classmethod
    def configure_passwordless(cls, password: str) -> Tuple[bool, str]:
        """Test password and configure lifetime passwordless operation."""
        clean_pwd = password.strip()
        if not clean_pwd:
            return False, "Password cannot be empty."

        # Verify password via sudo -S -v
        try:
            proc = subprocess.run(
                ["sudo", "-S", "-v"],
                input=f"{clean_pwd}\n",
                text=True,
                capture_output=True,
                timeout=5
            )
            if proc.returncode != 0:
                return False, "Incorrect password. Please verify and try again."
        except Exception as e:
            return False, f"Authentication test failed: {e}"

        # 1. Save to secure local vault (0600)
        try:
            vault = cls.get_vault_path()
            vault.parent.mkdir(parents=True, exist_ok=True)
            old_umask = os.umask(0o077)
            try:
                vault.write_text(clean_pwd)
            finally:
                os.umask(old_umask)
            vault.chmod(0o600)
        except Exception as e:
            return False, f"Failed writing to vault: {e}"

        # 2. Attempt to write /etc/sudoers.d/99-aura-pacman for true system-level passwordless operations
        user = os.environ.get("USER") or "wheel"
        sudoers_cmd = f'echo "{user} ALL=(ALL) NOPASSWD: /usr/bin/pacman, /usr/bin/paru" > /etc/sudoers.d/99-aura-pacman && chmod 0440 /etc/sudoers.d/99-aura-pacman'
        try:
            subprocess.run(
                ["sudo", "-S", "sh", "-c", sudoers_cmd],
                input=f"{clean_pwd}\n",
                text=True,
                capture_output=True,
                timeout=5
            )
        except Exception:
            pass

        return True, "Lifetime passwordless mode configured! Aura will never prompt for a password again."

    @staticmethod
    def clear_auth_cache():
        """Clear cached session password on exit or failure."""
        try:
            token = Path(f"/run/user/{os.getuid()}/aura_auth.token")
            token.unlink(missing_ok=True)
        except Exception:
            pass


class ContainerManager:
    """
    Manages OCI container applications and host desktop shortcut integration.
    Features:
    - Auto-detection of container runtime (distrobox / docker).
    - Auto-installation of distrobox via pacman if missing.
    - Auto-creation of dedicated 'aura-box' container.
    - Installation of container applications.
    - Automatic creation and deletion of host desktop shortcuts in ~/.local/share/applications.
    - Independent search that does not pollute default system search.
    """
    CONTAINER_NAME = "aura-box"
    SHORTCUTS_DIR = Path.home() / ".local" / "share" / "applications"

    CURATED_CONTAINER_APPS = [
        {
            "id": "blender",
            "name": "Blender",
            "desc": "Professional 3D modeling, animation, rendering and VFX suite",
            "category": "Graphics & Media",
            "icon": "blender",
            "binary": "blender",
            "pkg": "blender",
            "size": "280 MB",
        },
        {
            "id": "code",
            "name": "Visual Studio Code",
            "desc": "Sandboxed developer code editor and language runtime environment",
            "category": "Development",
            "icon": "code",
            "binary": "code",
            "pkg": "code",
            "size": "95 MB",
        },
        {
            "id": "obs-studio",
            "name": "OBS Studio",
            "desc": "Isolated video recording and live streaming workstation",
            "category": "Graphics & Media",
            "icon": "com.obsproject.Studio",
            "binary": "obs",
            "pkg": "obs-studio",
            "size": "70 MB",
        },
        {
            "id": "discord",
            "name": "Discord",
            "desc": "Sandboxed voice, video, and text communication platform",
            "category": "Social & Communication",
            "icon": "discord",
            "binary": "discord",
            "pkg": "discord",
            "size": "85 MB",
        },
        {
            "id": "gimp",
            "name": "GNU Image Manipulation (GIMP)",
            "desc": "High-powered image retouching, composition and digital artwork",
            "category": "Graphics & Media",
            "icon": "gimp",
            "binary": "gimp",
            "pkg": "gimp",
            "size": "140 MB",
        },
        {
            "id": "inkscape",
            "name": "Inkscape Vector Graphics",
            "desc": "Professional vector graphics editor for diagrams and illustrations",
            "category": "Graphics & Media",
            "icon": "org.inkscape.Inkscape",
            "binary": "inkscape",
            "pkg": "inkscape",
            "size": "110 MB",
        },
        {
            "id": "libreoffice-fresh",
            "name": "LibreOffice Fresh",
            "desc": "Comprehensive office suite (Writer, Calc, Impress) in container",
            "category": "Productivity",
            "icon": "libreoffice-startcenter",
            "binary": "libreoffice",
            "pkg": "libreoffice",
            "size": "450 MB",
        },
        {
            "id": "telegram-desktop",
            "name": "Telegram Desktop",
            "desc": "Fast, cloud-based messaging with cross-device sync",
            "category": "Social & Communication",
            "icon": "telegram",
            "binary": "telegram-desktop",
            "pkg": "telegram-desktop",
            "size": "55 MB",
        },
        {
            "id": "firefox-developer-edition",
            "name": "Firefox Developer Edition",
            "desc": "Developer-tailored desktop browser with web inspector and debugging tools",
            "category": "Internet & Network",
            "icon": "firefox-developer-edition",
            "binary": "firefox",
            "pkg": "firefox",
            "size": "95 MB",
        },
        {
            "id": "kdenlive",
            "name": "Kdenlive Video Editor",
            "desc": "Multi-track non-linear video editing workstation",
            "category": "Graphics & Media",
            "icon": "kdenlive",
            "binary": "kdenlive",
            "pkg": "kdenlive",
            "size": "190 MB",
        },
        {
            "id": "audacity",
            "name": "Audacity Audio Editor",
            "desc": "Multi-track audio recorder and waveform sound editor",
            "category": "Graphics & Media",
            "icon": "audacity",
            "binary": "audacity",
            "pkg": "audacity",
            "size": "45 MB",
        },
        {
            "id": "vlc",
            "name": "VLC Media Player",
            "desc": "Universal media player supporting virtually all video formats",
            "category": "Graphics & Media",
            "icon": "vlc",
            "binary": "vlc",
            "pkg": "vlc",
            "size": "40 MB",
        },
    ]

    def __init__(self):
        self._lock = threading.Lock()
        self.SHORTCUTS_DIR.mkdir(parents=True, exist_ok=True)
        self.active_container_installs: Set[str] = set()

    def is_app_installing(self, app_id: str) -> bool:
        return app_id in self.active_container_installs

    def get_status(self) -> Dict[str, Any]:
        """Check container runtime and container existence."""
        has_docker = shutil.which("docker") is not None
        daemon_running = False
        container_exists = False
        container_running = False

        if has_docker:
            try:
                proc = subprocess.run(["docker", "info"], capture_output=True, timeout=3)
                if proc.returncode == 0:
                    daemon_running = True
            except Exception:
                pass
        
        if daemon_running:
            try:
                proc = subprocess.run(
                    ["docker", "inspect", "-f", "{{.State.Running}}", self.CONTAINER_NAME],
                    capture_output=True, text=True, timeout=3
                )
                if proc.returncode == 0:
                    container_exists = True
                    if proc.stdout.strip() == "true":
                        container_running = True
            except Exception:
                pass

        if container_running:
            status_code = "ready"
            status_text = f"Container: {self.CONTAINER_NAME} (Active)"
        elif container_exists:
            status_code = "stopped"
            status_text = f"Container: {self.CONTAINER_NAME} (Stopped)"
        elif daemon_running:
            status_code = "missing_container"
            status_text = f"Engine Ready (Container '{self.CONTAINER_NAME}' Not Found)"
        else:
            status_code = "missing_engine"
            status_text = "Container Engine Not Running or Not Installed"

        return {
            "has_docker": has_docker,
            "daemon_running": daemon_running,
            "container_exists": container_exists,
            "container_running": container_running,
            "container_name": self.CONTAINER_NAME,
            "status_code": status_code,
            "status_text": status_text,
        }

    def ensure_container_configured(self, progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        """Auto create and start Docker container if not running."""
        status = self.get_status()
        if status["status_code"] == "ready":
            return True, "Container already configured and active."
        
        if status["status_code"] == "missing_engine":
            return False, "Docker is not installed or daemon is not running."

        if status["status_code"] == "stopped":
            if progress_callback:
                progress_callback(f"Starting existing '{self.CONTAINER_NAME}' container...")
            proc = subprocess.run(["docker", "start", self.CONTAINER_NAME], capture_output=True, text=True, timeout=10)
            if proc.returncode == 0:
                if progress_callback:
                    progress_callback("Installing baseline GUI libraries (mesa, x11, fonts)...")
                subprocess.run(["docker", "exec", self.CONTAINER_NAME, "apk", "add", "--no-cache", "mesa-gl", "mesa-dri-gallium", "mesa-egl", "libx11", "font-noto"], capture_output=True, timeout=60)
                return True, "Started existing aura-box container."
            return False, f"Failed to start container: {proc.stderr}"

        # Create and start new container
        if progress_callback:
            progress_callback(f"Initializing '{self.CONTAINER_NAME}' container environment (Alpine Linux)...")

        uid = os.getuid()
        gid = os.getgid()
        home = str(Path.home())
        runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        display = os.environ.get("DISPLAY", ":0")
        wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")

        cmd = [
            "docker", "run", "-d",
            "--name", self.CONTAINER_NAME,
            "--restart", "always",
            "--ipc=host",
            "--net=host",
            "-v", "/tmp/.X11-unix:/tmp/.X11-unix:ro",
            "-v", f"{runtime_dir}:{runtime_dir}:ro",
            "-v", f"{home}:{home}",
            "-e", f"DISPLAY={display}",
            "-e", f"WAYLAND_DISPLAY={wayland}",
            "-e", f"XDG_RUNTIME_DIR={runtime_dir}",
            "-e", f"HOME={home}",
            "alpine:latest",
            "tail", "-f", "/dev/null"
        ]
        
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return False, f"Failed to create container: {proc.stderr}"

        if progress_callback:
            progress_callback("Installing baseline GUI libraries (mesa, x11, fonts)...")
        subprocess.run(["docker", "exec", self.CONTAINER_NAME, "apk", "add", "--no-cache", "mesa-gl", "mesa-dri-gallium", "mesa-egl", "libx11", "font-noto"], capture_output=True, timeout=60)

        if progress_callback:
            progress_callback("Container environment configured successfully!")

        return True, "Container successfully initialized and ready."

    def list_apps(self, query: str = "") -> List[Dict[str, Any]]:
        """List container apps: curated + installed shortcuts + Docker Hub search."""
        q = query.strip().lower()
        results = []
        seen_ids = set()

        # 1. Curated apps (filtered by query if present)
        for app in self.CURATED_CONTAINER_APPS:
            if q:
                match = (
                    q in app["name"].lower() or
                    q in app["desc"].lower() or
                    q in app["category"].lower() or
                    q in app["id"].lower()
                )
                if not match:
                    continue

            shortcut = self.SHORTCUTS_DIR / f"aura-box-{app['id']}.desktop"
            item = dict(app)
            item["is_installed"] = shortcut.exists()
            item["shortcut_path"] = str(shortcut)
            item["is_installing"] = self.is_app_installing(app["id"])
            results.append(item)
            seen_ids.add(app["id"])

        # 2. Detect installed container apps from desktop shortcuts (not in curated list)
        try:
            for desktop_file in self.SHORTCUTS_DIR.glob("aura-box-*.desktop"):
                app_id = desktop_file.stem.replace("aura-box-", "", 1)
                if app_id in seen_ids:
                    continue
                if q and q not in app_id.lower():
                    continue
                # Parse basic info from .desktop file
                name = app_id.replace("-", " ").title()
                desc = "Container application"
                icon = "application-x-executable"
                try:
                    content = desktop_file.read_text()
                    for line in content.splitlines():
                        if line.startswith("Name="):
                            name = line.split("=", 1)[1].strip()
                        elif line.startswith("GenericName="):
                            desc = line.split("=", 1)[1].strip()
                        elif line.startswith("Icon="):
                            icon = line.split("=", 1)[1].strip()
                except Exception:
                    pass
                results.append({
                    "id": app_id,
                    "name": name,
                    "desc": desc,
                    "category": "Container",
                    "icon": icon,
                    "binary": app_id,
                    "pkg": app_id,
                    "is_installed": True,
                    "is_installing": self.is_app_installing(app_id),
                    "shortcut_path": str(desktop_file),
                })
                seen_ids.add(app_id)
        except Exception:
            pass

        # 3. If searching and few results, also query Docker Hub
        if q and len(results) < 3:
            try:
                proc = subprocess.run(
                    ["docker", "search", "--limit", "8", "--format", "{{.Name}}\t{{.Description}}\t{{.StarCount}}", q],
                    capture_output=True, text=True, timeout=8
                )
                if proc.returncode == 0:
                    for line in proc.stdout.strip().splitlines():
                        parts = line.split("\t", 2)
                        if len(parts) < 2:
                            continue
                        docker_name = parts[0].strip()
                        docker_desc = parts[1].strip() if len(parts) > 1 else "Docker Hub image"
                        # Create a simple app_id from docker name
                        app_id = docker_name.replace("/", "-").replace(".", "-")
                        if app_id in seen_ids:
                            continue
                        stars = parts[2].strip() if len(parts) > 2 else "0"
                        results.append({
                            "id": app_id,
                            "name": docker_name,
                            "desc": f"{docker_desc[:80]}" + (f" ⭐ {stars}" if stars != "0" else ""),
                            "category": "Docker Hub",
                            "icon": "application-x-executable",
                            "binary": docker_name,
                            "pkg": docker_name,
                            "is_installed": False,
                            "is_installing": self.is_app_installing(app_id),
                            "shortcut_path": "",
                        })
                        seen_ids.add(app_id)
            except Exception:
                pass

        return results

    def is_app_installed(self, app_id: str) -> bool:
        shortcut = self.SHORTCUTS_DIR / f"aura-box-{app_id}.desktop"
        return shortcut.exists()

    def install_app(self, app_id: str, progress_callback: Optional[Callable[[str], None]] = None, completion_callback: Optional[Callable[[bool, str], None]] = None):
        """Install app inside container and auto-create desktop shortcut."""
        def _task():
            self.active_container_installs.add(app_id)
            try:
                app_meta = next((a for a in self.CURATED_CONTAINER_APPS if a["id"] == app_id), None)
                if not app_meta:
                    app_meta = {
                        "id": app_id,
                        "name": app_id.capitalize(),
                        "desc": f"Container application ({app_id})",
                        "category": "Utility",
                        "icon": "application-x-executable",
                        "binary": app_id,
                        "pkg": app_id,
                    }

                # 1. Ensure container is ready
                ok, msg = self.ensure_container_configured(progress_callback)
                if not ok:
                    if completion_callback:
                        completion_callback(False, f"Container setup failed: {msg}")
                    return

                # 2. Install inside container
                if progress_callback:
                    progress_callback(f"Installing {app_meta['name']} inside '{self.CONTAINER_NAME}'...")
                
                pkg = app_meta.get("pkg", app_id)
                cmd = ["docker", "exec", self.CONTAINER_NAME, "apk", "add", "--no-cache", pkg]
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if proc.returncode != 0:
                        if completion_callback:
                            completion_callback(False, f"Installation failed: {proc.stderr}")
                        return
                except Exception as e:
                    if completion_callback:
                        completion_callback(False, f"Installation error: {e}")
                    return

                # 3. Auto-create desktop shortcut
                if progress_callback:
                    progress_callback("Generating host desktop shortcut...")

                shortcut_path = self.SHORTCUTS_DIR / f"aura-box-{app_id}.desktop"
                shortcut_content = f"""[Desktop Entry]
Version=1.0
Type=Application
Name={app_meta['name']} (Docker)
GenericName={app_meta.get('desc', '')}
Comment=Sandboxed Docker container application
Exec=sh -c 'docker exec -d -e DISPLAY=\"$DISPLAY\" -e WAYLAND_DISPLAY=\"$WAYLAND_DISPLAY\" -e XDG_RUNTIME_DIR=\"$XDG_RUNTIME_DIR\" {self.CONTAINER_NAME} {app_meta["binary"]} %U'
Icon={app_meta.get('icon', 'application-x-executable')}
Terminal=false
Categories={app_meta.get('category', 'Utility')};
StartupNotify=true
X-Aura-Docker=true
X-Aura-AppId={app_id}
"""
                try:
                    shortcut_path.write_text(shortcut_content)
                    shortcut_path.chmod(0o755)
                    # Update desktop database
                    subprocess.run(["update-desktop-database", str(self.SHORTCUTS_DIR)], capture_output=True, timeout=3)
                except Exception as e:
                    if completion_callback:
                        completion_callback(False, f"Failed creating shortcut: {e}")
                    return

                if progress_callback:
                    progress_callback(f"Shortcut created: {shortcut_path.name}")

                if completion_callback:
                    completion_callback(True, f"Installed {app_meta['name']} with desktop shortcut created!")
            finally:
                self.active_container_installs.discard(app_id)

        threading.Thread(target=_task, daemon=True).start()

    def uninstall_app(self, app_id: str, progress_callback: Optional[Callable[[str], None]] = None, completion_callback: Optional[Callable[[bool, str], None]] = None):
        """Remove shortcut and uninstall from container."""
        def _task():
            app_meta = next((a for a in self.CURATED_CONTAINER_APPS if a["id"] == app_id), None)
            name = app_meta["name"] if app_meta else app_id

            if progress_callback:
                progress_callback(f"Removing {name} desktop shortcut...")

            shortcut_path = self.SHORTCUTS_DIR / f"aura-box-{app_id}.desktop"
            if shortcut_path.exists():
                try:
                    shortcut_path.unlink()
                    subprocess.run(["update-desktop-database", str(self.SHORTCUTS_DIR)], capture_output=True, timeout=3)
                except Exception:
                    pass

            # Optional container cleanup
            status = self.get_status()
            if status["container_exists"]:
                pkg = app_meta.get("pkg", app_id) if app_meta else app_id
                cmd = ["docker", "exec", self.CONTAINER_NAME, "apk", "del", pkg]
                try:
                    subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                except Exception:
                    pass

            if completion_callback:
                completion_callback(True, f"Removed {name} and removed desktop shortcut.")

        threading.Thread(target=_task, daemon=True).start()

    def launch_app(self, app_id: str):
        """Launch container app or its desktop shortcut."""
        shortcut_name = f"aura-box-{app_id}.desktop"
        try:
            subprocess.Popen(["gtk-launch", shortcut_name])
            return
        except Exception:
            pass

        app_meta = next((a for a in self.CURATED_CONTAINER_APPS if a["id"] == app_id), None)
        binary = app_meta["binary"] if app_meta else app_id
        display = os.environ.get("DISPLAY", ":0")
        wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")
        subprocess.Popen(["docker", "exec", "-d", "-e", f"DISPLAY={display}", "-e", f"WAYLAND_DISPLAY={wayland}", self.CONTAINER_NAME, binary])

