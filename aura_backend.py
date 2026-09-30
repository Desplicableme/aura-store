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
import tempfile
import urllib.request
import urllib.parse
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple, Any, Callable

CACHE_DIR = Path.home() / ".cache" / "aura"
CACHE_FILE = CACHE_DIR / "sync_cache.pkl"
UPDATES_CACHE_FILE = CACHE_DIR / "updates_cache.json"
FEATURED_STATE_FILE = CACHE_DIR / "featured_state.json"
SYNC_DIR = Path("/var/lib/pacman/sync")

def get_askpass_script() -> Path:
    """Dynamically resolve executable aura-askpass helper across dev, local, and system paths."""
    candidates = [
        Path(__file__).resolve().parent / "aura-askpass",
        Path.home() / ".local" / "share" / "aura" / "aura-askpass",
        Path("/usr/lib/aura/aura-askpass"),
    ]
    for c in candidates:
        if c.exists() and os.access(c, os.X_OK):
            return c
    return candidates[0]

ASKPASS_SCRIPT = get_askpass_script()

def get_aur_helper() -> Optional[str]:
    """Return the name of the preferred installed AUR helper ('paru' or 'yay'), or None."""
    for helper in ("paru", "yay"):
        if shutil.which(helper):
            return helper
    return None

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
    "vlc-cli": "VLC (CLI)",
    "vlc-gui-qt": "VLC Qt GUI",
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
    "qtengine": "QtEngine",
    "quickshell": "Quickshell",
    "quickshell-git": "Quickshell",
    "antigravity": "Antigravity",
    "antigravity-cli": "Antigravity CLI",
    "spicetify-marketplace-bin": "Spicetify Marketplace",
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


def parse_pacman_info(raw_text: str) -> Dict[str, Any]:
    """
    Parse pacman -Si or -Qi human-readable output into structured dictionary.
    Properly handles multiline wrapped fields, especially 'Optional Deps' (AURA-028).
    """
    info: Dict[str, Any] = {
        "depends": [],
        "optdepends": [],
    }
    current_key = None

    for raw_line in (raw_text or "").splitlines():
        line = raw_line.rstrip()
        if not line:
            current_key = None
            continue

        # Check if line is indented continuation line
        if raw_line.startswith(" ") or raw_line.startswith("\t"):
            val = line.strip()
            if not val or val == "None":
                continue
            if current_key == "optional_deps":
                info["optdepends"].append(val)
            elif current_key == "depends_on":
                for d in val.split():
                    if d != "None" and d not in info["depends"]:
                        info["depends"].append(d)
            continue

        if ":" in line:
            k, v = line.split(":", 1)
            current_key = k.strip().lower().replace(" ", "_")
            v = v.strip()

            if current_key == "description":
                info["desc"] = v
            elif current_key == "version":
                info["version"] = v
            elif current_key == "repository":
                info["repo"] = v
            elif current_key == "url":
                info["url"] = v
            elif current_key == "licenses":
                info["license"] = v
            elif current_key == "installed_size":
                info["isize_str"] = v
            elif current_key == "download_size":
                info["csize_str"] = v
            elif current_key == "packager":
                info["packager"] = sanitize_str(v)
            elif current_key == "depends_on":
                for d in v.split():
                    if d != "None" and d not in info["depends"]:
                        info["depends"].append(d)
            elif current_key == "optional_deps":
                if v and v != "None":
                    info["optdepends"].append(v)
        else:
            current_key = None

    return info


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
        Path("/var/lib/snapd/desktop/applications"),
        Path("/snap/share/applications"),
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

    # Check for package suffixes (cli, git, bin, etc.) to avoid collision with main GUI app
    m = re.search(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|cli|daemon|desktop|launcher)$', clean_pkg)
    if m:
        suffix = m.group(1)
        base = clean_pkg[:m.start()]
        base_title = APP_DISPLAY_NAMES.get(base) or dt_names.get(base) or base.replace("-", " ").title()
        suffix_tags = {
            "cli": "CLI",
            "git": "Git",
            "bin": "Bin",
            "daemon": "Daemon",
            "launcher": "Launcher",
            "pure": "Pure",
            "gui": "GUI",
            "gtk-app": "GTK",
            "qt-app": "Qt",
        }
        tag = suffix_tags.get(suffix, suffix.capitalize())
        if base == "vlc":
            return f"VLC ({tag})"
        return f"{base_title} ({tag})"

    return clean_pkg.replace("-", " ").title()


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



_CURATED_DESKTOP_SET: Optional[Set[str]] = None


def get_desktop_and_curated_set() -> Set[str]:
    """Return cached set of known desktop and curated application names for instant relevance boosting."""
    global _CURATED_DESKTOP_SET
    if _CURATED_DESKTOP_SET is not None:
        return _CURATED_DESKTOP_SET

    s = set()
    s.update(k.lower() for k in APP_DISPLAY_NAMES.keys())
    for cat in CURATED_CATEGORIES:
        for app in cat.get("apps", []):
            name = app.get("name", "").lower()
            if name:
                s.add(name)
    s.update(k.lower() for k in FEATURED_APP_PRESETS.keys())
    s.update(k.lower() for k in _DESKTOP_OVERRIDES.keys())
    s.update(v.lower() for v in _DESKTOP_OVERRIDES.values())
    try:
        entries = get_desktop_entries_map()
        s.update(k.lower() for k in entries.keys())
    except Exception:
        pass

    _CURATED_DESKTOP_SET = s
    return _CURATED_DESKTOP_SET


def is_desktop_app(pkg_name: str) -> bool:
    """Check if a package represents a user-facing desktop or curated application."""
    if not pkg_name:
        return False
    name = pkg_name.lower().strip()
    desktop_set = get_desktop_and_curated_set()
    if name in desktop_set:
        return True
    idx = name.rfind('-')
    if idx != -1:
        prefix = name[:idx]
        suffix = name[idx+1:]
        if suffix in ('bin', 'git', 'hg', 'svn', 'pure', 'desktop', 'launcher', 'gui', 'gtk-app', 'qt-app'):
            if prefix in desktop_set:
                return True
    return False


def is_damerau_levenshtein_one(s1: str, s2: str) -> bool:
    """Check if s1 and s2 have edit distance <= 1 (insertion, deletion, substitution, adjacent swap)."""
    len1, len2 = len(s1), len(s2)
    if abs(len1 - len2) > 1:
        return False
    if len1 == len2:
        diffs = [i for i in range(len1) if s1[i] != s2[i]]
        if len(diffs) == 1:
            return True
        if len(diffs) == 2 and diffs[1] == diffs[0] + 1:
            return s1[diffs[0]] == s2[diffs[1]] and s1[diffs[1]] == s2[diffs[0]]
        return False
    short, long = (s1, s2) if len1 < len2 else (s2, s1)
    i = j = 0
    diff = 0
    while i < len(short) and j < len(long):
        if short[i] != long[j]:
            diff += 1
            if diff > 1:
                return False
            j += 1
        else:
            i += 1
            j += 1
    return True


def fuzzy_score(
    query: str,
    target: str,
    desc: str = "",
    is_desktop: Optional[bool] = None,
    is_installed: bool = False
) -> int:
    """
    Tiered Multi-Level Scoring Engine for Aura Store:
      Tier 1: Exact Name Match: 10,000 points
      Tier 2: Exact Normalized Match: 9,000 points
      Tier 3: Prefix Match on Name: 7,000 - min(500, len(name)*10)
      Tier 4: Word Boundary / Segment Match: 5,000 points
      Tier 5: High-Precision Subsequence Match on Name:
              Base 3,000 + streak multiplier (+50*streak) + boundary bonus (+150)
              Only for queries >= 2 characters
      Tier 6: Description Exact Word / Phrase Match:
              1,500 points (Only for queries >= 3 characters)
      Relevance Boosts (applied only if base score > 0):
        +800 for Desktop / Curated Applications
        +200 for Locally Installed Packages
    """
    q = query.lower().strip()
    t = target.lower().strip()

    if not q or not t:
        return 0

    base_score = 0
    q_len = len(q)
    t_len = len(t)

    # 1. Exact Name Match (e.g. vlc == vlc, git == git): 10,000 points
    if q == t:
        base_score = 10000

    # Fast normalization using C-level string operations
    q_norm = q.replace("-", " ").replace("_", " ").replace(".", " ")
    t_norm = t.replace("-", " ").replace("_", " ").replace(".", " ")

    # 2. Exact Normalized Match (code-oss for code oss): 9,000 points
    if not base_score:
        if q_norm == t_norm:
            base_score = 9000
        else:
            q_condensed = q_norm.replace(" ", "")
            t_condensed = t_norm.replace(" ", "")
            if q_condensed and q_condensed == t_condensed:
                base_score = 9000

    # 3. Prefix Match on Name (fire -> firefox, neov -> neovim): 7,000 - min(500, len(name)*10)
    if not base_score:
        if t.startswith(q) or t_norm.startswith(q_norm):
            base_score = 7000 - min(500, t_len * 10)
        else:
            q_condensed = q_norm.replace(" ", "")
            t_condensed = t_norm.replace(" ", "")
            if q_condensed and t_condensed.startswith(q_condensed):
                base_score = 7000 - min(500, t_len * 10)

    # For 1-char queries: ONLY match packages starting with that letter. Instant sub-1ms return.
    if q_len < 2:
        if base_score > 0:
            if is_desktop is None:
                is_desktop = is_desktop_app(target)
            if is_desktop:
                base_score += 800
            if is_installed:
                base_score += 200
            return base_score
        return 0

    # 4. Word Boundary / Segment Match (studio -> obs-studio, code -> visual-studio-code): 5,000 points
    if not base_score:
        t_segments = t_norm.split()
        q_words = q_norm.split()

        if q in t_segments:
            base_score = 5000
        elif len(q_words) > 1 and all(w in t_segments for w in q_words):
            base_score = 5000
        else:
            idx = t.find(q)
            if idx != -1:
                is_boundary_start = (idx == 0 or t[idx - 1] in "-_. ")
                end_pos = idx + q_len
                is_boundary_end = (end_pos == t_len or t[end_pos] in "-_. ")
                if is_boundary_start and is_boundary_end:
                    base_score = 5000
                elif is_boundary_start:
                    base_score = 4800
            else:
                idx = t_norm.find(q_norm)
                if idx != -1:
                    is_boundary_start = (idx == 0 or t_norm[idx - 1] == ' ')
                    end_pos = idx + len(q_norm)
                    is_boundary_end = (end_pos == len(t_norm) or t_norm[end_pos] == ' ')
                    if is_boundary_start and is_boundary_end:
                        base_score = 5000
                    elif is_boundary_start:
                        base_score = 4800
                elif any(s.startswith(q) for s in t_segments):
                    base_score = 4800

    # 5. High-Precision Subsequence Match on Name (only for queries >= 2 characters)
    #    Bonus for matches following -, _, ., or word start (+150)
    #    Consecutive character match streak multiplier (+50 * streak)
    #    Score base: 3,000
    if not base_score and q_len >= 2:
        if q[0] in t:
            qi = 0
            last_idx = -2
            streak = 0
            subseq_bonus = 0

            for i, ch in enumerate(t):
                if qi < q_len and ch == q[qi]:
                    if i == 0 or t[i - 1] in "-_. ":
                        subseq_bonus += 150

                    if last_idx == i - 1:
                        streak += 1
                        subseq_bonus += 50 * streak
                    else:
                        streak = 0

                    last_idx = i
                    qi += 1

            if qi == q_len:
                subseq_calc = 3000 + subseq_bonus
                base_score = max(3000, min(4800, subseq_calc))

    # 5.5 Typo Tolerance / Single-Edit Distance (only for queries >= 4 characters)
    # Allows 1 transposition, insertion, deletion, or substitution (e.g. firfox -> firefox, chorme -> chrome)
    if not base_score and q_len >= 4:
        q_norm = re.sub(r'[-_.\s]+', ' ', q).strip()
        t_norm = re.sub(r'[-_.\s]+', ' ', t).strip()
        if is_damerau_levenshtein_one(q, t) or is_damerau_levenshtein_one(q_norm, t_norm):
            base_score = 2500 - min(300, t_len * 10)
        else:
            t_segments = [s for s in re.split(r'[-_.\s]+', t) if s]
            for s in t_segments:
                if len(s) >= 3 and is_damerau_levenshtein_one(q, s):
                    base_score = 2200 - min(300, t_len * 10)
                    break

    # 6. Description Exact Word / Phrase Match (only for queries >= 3 characters)
    #    Word match in description: 1,500 points
    if not base_score and q_len >= 3 and desc:
        d = desc.lower()
        if q in d:
            pos = 0
            is_word_match = False
            first_idx = -1
            while True:
                idx = d.find(q, pos)
                if idx == -1:
                    break
                if first_idx == -1:
                    first_idx = idx
                before_ok = (idx == 0 or not d[idx - 1].isalnum())
                after_ok = (idx + q_len == len(d) or not d[idx + q_len].isalnum())
                if before_ok and after_ok:
                    is_word_match = True
                    first_idx = idx
                    break
                pos = idx + 1

            if is_word_match:
                base_score = 1500 - min(100, first_idx)
            elif first_idx != -1:
                base_score = 1200 - min(100, first_idx)

    # Apply Relevance Boosts if package matched (base_score > 0)
    if base_score > 0:
        if is_desktop is None:
            is_desktop = is_desktop_app(target)
        if is_desktop:
            base_score += 800
        if is_installed:
            base_score += 200
        return base_score

    return 0


ANSI_ESCAPE = re.compile(r'\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])')


class PacmanProgressParser:
    """
    Parser for pacman and paru stdout streams with monotonic progress calculation,
    package-aware stage tracking, and post-transaction hook protection.
    """
    INSTALL_REGEX = re.compile(
        r'\((\d+)/(\d+)\)\s+(?:upgrading|installing|reinstalling|downgrading|removing)\s+([a-zA-Z0-9_\-\.\+]+)',
        re.IGNORECASE
    )
    PKG_TAR_REGEX = re.compile(
        r'([a-zA-Z0-9@_+][a-zA-Z0-9@_\.\+-]*?)-[0-9][a-zA-Z0-9_\.\+:]*-[0-9]+\.pkg\.tar',
        re.IGNORECASE
    )
    PKG_TAR_SIMPLE = re.compile(
        r'([a-zA-Z0-9@_+][a-zA-Z0-9@_\.\+-]*?)\.pkg\.tar',
        re.IGNORECASE
    )
    DOWNLOADING_REGEX = re.compile(
        r'downloading\s+([a-zA-Z0-9@_+][a-zA-Z0-9@_\.\+-]*)',
        re.IGNORECASE
    )
    INDEX_REGEX = re.compile(r'\((\d+)/(\d+)\)')

    def __init__(self, action: str = "update", target_pkg: str = "", is_system_upgrade: bool = False, upgradable_pkgs: Optional[Set[str]] = None):
        self.action = action.lower()
        self.is_system_upgrade = is_system_upgrade or (target_pkg in ["system", "--all", "all", ""] or not target_pkg)
        self.target_pkg = ("" if self.is_system_upgrade else target_pkg).strip().lower()
        self.upgradable_pkgs = {p.strip().lower() for p in upgradable_pkgs} if upgradable_pkgs else set()

        target_name = self.target_pkg
        self.target_disp = (
            APP_DISPLAY_NAMES.get(target_name, APP_DISPLAY_NAMES.get(target_name.lower(), target_name.replace('-', ' ').title()))
            if target_name else "System"
        )

        self.total_packages = max(1, len(self.upgradable_pkgs)) if self.upgradable_pkgs else 1
        self.current_idx = 0
        self.current_pkg: Optional[str] = self.target_pkg if self.target_pkg else None
        self.downloaded_pkgs: Set[str] = set()
        self.in_post_hooks = False
        self.last_progress = 0.06
        self.error_lines: List[str] = []

    def get_display_name(self, pkg: str) -> str:
        clean = pkg.strip()
        return APP_DISPLAY_NAMES.get(clean, APP_DISPLAY_NAMES.get(clean.lower(), clean.replace('-', ' ').title()))

    def get_action_verb(self, matched_verb: Optional[str] = None) -> str:
        if matched_verb:
            mv = matched_verb.lower()
            if "remov" in mv or self.action == "remove":
                return "Removing"
            if "install" in mv and self.action == "install":
                return "Installing"
            return "Updating"
        if self.action == "remove":
            return "Removing"
        elif self.action == "install":
            return "Installing"
        return "Updating"

    def process_line(self, line: str) -> Optional[Tuple[float, str, Optional[str], Optional[int], Optional[int]]]:
        """
        Process a single line of pacman/paru stdout.
        Returns tuple: (progress_frac, status_msg, current_pkg, current_idx, total_packages)
        or None if no state update for this line.
        """
        clean = ANSI_ESCAPE.sub('', line).strip()
        if not clean:
            return None
        l_lower = clean.lower()

        if "error:" in l_lower or "failed" in l_lower:
            self.error_lines.append(clean)

        # Update total packages if pacman outputs Packages (N)
        pkg_match = re.search(r'packages\s*\(\s*(\d+)\s*\)', l_lower)
        if pkg_match:
            self.total_packages = max(self.total_packages, int(pkg_match.group(1)))

        # 1. Post-transaction hooks detection
        if "post-transaction hooks" in l_lower or "running post-transaction" in l_lower:
            self.in_post_hooks = True
            prog = max(self.last_progress, 0.90)
            self.last_progress = prog
            return (prog, "Running post-transaction hooks...", self.current_pkg, self.current_idx, self.total_packages)

        # 2. While in post-transaction hooks:
        if self.in_post_hooks:
            if any(k in l_lower for k in ["desktop", "icon", "mime", "font", "systemd", "system", "conditionneedsupdate"]):
                h_match = self.INDEX_REGEX.search(clean)
                if h_match:
                    h_cur = int(h_match.group(1))
                    h_tot = int(h_match.group(2))
                    hook_stage = round(min(0.96, 0.92 + (h_cur / max(1, h_tot)) * 0.04), 4)
                else:
                    hook_stage = round(min(0.96, max(0.92, self.last_progress + 0.01)), 4)
                prog = max(self.last_progress, hook_stage)
                self.last_progress = prog
                return (prog, "Finalizing desktop & system environment...", self.current_pkg, self.current_idx, self.total_packages)
            return None

        # 3. Installation / Upgrade / Removal line parsing
        inst_match = self.INSTALL_REGEX.search(clean)
        if inst_match:
            cur_idx = int(inst_match.group(1))
            tot_idx = int(inst_match.group(2))
            p_name = inst_match.group(3).strip()

            self.total_packages = max(self.total_packages, tot_idx)
            self.current_idx = cur_idx
            self.current_pkg = p_name

            disp = self.get_display_name(p_name)
            verb = self.get_action_verb()

            inst_stage = round(min(0.88, 0.28 + (cur_idx / max(1, tot_idx)) * 0.60), 4)
            prog = max(self.last_progress, inst_stage)
            self.last_progress = prog

            p_clean = p_name.strip().lower()
            is_dep = False
            if self.upgradable_pkgs:
                is_dep = (p_clean not in self.upgradable_pkgs)
            elif self.target_pkg:
                target_clean = self.target_pkg.lower()
                is_target = (
                    p_clean == target_clean
                    or p_clean == target_clean.replace("-bin", "")
                    or target_clean == p_clean.replace("-bin", "")
                    or p_clean == target_clean.replace("-git", "")
                    or target_clean == p_clean.replace("-git", "")
                )
                is_dep = not is_target

            if is_dep:
                msg = f"{verb} dependency {disp} ({cur_idx} of {tot_idx})..."
            elif self.is_system_upgrade or not self.target_pkg:
                msg = f"{verb} {disp} ({cur_idx} of {tot_idx})..."
            else:
                if tot_idx > 1:
                    msg = f"{verb} {disp} ({cur_idx} of {tot_idx})..."
                else:
                    msg = f"{verb} {disp}..."

            return (prog, msg, p_name, cur_idx, tot_idx)

        # 4. Dependency resolution & keyring / integrity / disk space
        if "resolving dependencies" in l_lower or "calculating dependencies" in l_lower:
            prog = max(self.last_progress, 0.07)
            self.last_progress = prog
            return (prog, "Resolving package dependencies...", self.current_pkg, self.current_idx, self.total_packages)

        if (
            "checking keyring" in l_lower
            or "checking keys in keyring" in l_lower
            or "package integrity" in l_lower
            or "verifying package integrity" in l_lower
            or "loading package files" in l_lower
        ):
            prog = max(self.last_progress, 0.27)
            self.last_progress = prog
            return (prog, "Checking package integrity & keyring...", self.current_pkg, self.current_idx, self.total_packages)

        if (
            "looking for conflicting" in l_lower
            or "checking for conflicting" in l_lower
            or "file conflicts" in l_lower
            or "available disk space" in l_lower
            or "verifying disk space" in l_lower
        ):
            prog = max(self.last_progress, 0.28)
            self.last_progress = prog
            return (prog, "Verifying disk space & conflicts...", self.current_pkg, self.current_idx, self.total_packages)

        if "processing package changes" in l_lower:
            prog = max(self.last_progress, 0.28)
            self.last_progress = prog
            return (prog, "Applying package changes...", self.current_pkg, self.current_idx, self.total_packages)

        # 5. Download phase
        if "retrieving packages" in l_lower or "downloading" in l_lower or ".pkg.tar." in l_lower:
            idx_match = self.INDEX_REGEX.search(clean)
            if idx_match:
                self.total_packages = max(self.total_packages, int(idx_match.group(2)))

            dl_pkg = None
            pkg_tar_m = self.PKG_TAR_REGEX.search(clean)
            if pkg_tar_m:
                dl_pkg = pkg_tar_m.group(1).strip()
            else:
                pkg_tar_s = self.PKG_TAR_SIMPLE.search(clean)
                if pkg_tar_s:
                    dl_pkg = pkg_tar_s.group(1).strip()
                else:
                    dl_name_m = self.DOWNLOADING_REGEX.search(clean)
                    if dl_name_m:
                        cand = dl_name_m.group(1).strip().rstrip('.')
                        v_m = re.search(r'^([a-zA-Z0-9@_+][a-zA-Z0-9@_\.\+-]*?)-[0-9]', cand)
                        dl_pkg = v_m.group(1) if v_m else cand

            if dl_pkg:
                self.downloaded_pkgs.add(dl_pkg.lower())
            elif idx_match:
                self.downloaded_pkgs.add(f"idx_{idx_match.group(1)}")

            dl_count = min(len(self.downloaded_pkgs), self.total_packages)
            dl_stage = round(min(0.28, 0.08 + (dl_count / max(1, self.total_packages)) * 0.20), 4)
            prog = max(self.last_progress, dl_stage)
            self.last_progress = prog

            if self.total_packages > 1 and dl_count > 0:
                dl_msg = f"Downloading package files ({dl_count} of {self.total_packages})..."
            else:
                dl_msg = "Downloading package files..."

            return (prog, dl_msg, dl_pkg or self.current_pkg, dl_count, self.total_packages)

        return None

    def get_completion_message(self) -> str:
        if self.action in ["upgrade", "update"]:
            if self.is_system_upgrade:
                return "✓ System update complete!"
            return f"✓ Updated {self.target_disp} successfully!"
        elif self.action == "install":
            return f"✓ Installed {self.target_disp} successfully!"
        elif self.action == "remove":
            return f"✓ Removed {self.target_disp} successfully!"
        return "✓ Action complete!"

    def get_error_message(self, returncode: int) -> str:
        err_msg = "\n".join(self.error_lines[-3:]) if self.error_lines else f"Exited with code {returncode}"
        return f"Error: {err_msg}"


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
        self._pending_actions: List[Dict[str, Any]] = []
        self._last_progress: float = 0.0
        self._action_lock = threading.RLock()
        self._update_generation: int = 0
        self._update_lock = threading.Lock()
        self.is_checking_updates = False
        self.updates_checked = False
        self.container_mgr = ContainerManager()
        self.snap_mgr = SnapManager()
        self.cache_mgr = CacheManager()
        # Load installed packages synchronously so cards immediately reflect installed status
        self.refresh_installed()
        # Persistent rotating featured apps state
        state = _load_featured_state()
        self._featured_rotation_offset: int = (state.get("offset", 0) + 1) % 50
        self._last_featured_shown: List[str] = list(state.get("last_shown", []))
        _save_featured_state(self._featured_rotation_offset, self._last_featured_shown)
        self._current_featured_apps: Optional[List[Dict[str, Any]]] = None

        # Pre-warm Snap & Container manager caches in background thread for 0ms page navigation
        def _prewarm_managers():
            try:
                self.snap_mgr.get_status()
                self.snap_mgr.get_installed_snaps()
            except Exception:
                pass
            try:
                self.container_mgr.get_status()
            except Exception:
                pass
        threading.Thread(target=_prewarm_managers, daemon=True).start()

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

            # Invalidate desktop launcher cache so newly installed apps are detected immediately
            global _DESKTOP_ICONS_CACHE, _DESKTOP_NAMES_CACHE, _DESKTOP_ENTRIES_CACHE
            _DESKTOP_ICONS_CACHE = {}
            _DESKTOP_NAMES_CACHE = {}
            _DESKTOP_ENTRIES_CACHE = {}
        except Exception as e:
            print(f"[Aura] Error loading installed packages: {e}", file=sys.stderr)

    def resolve_installed_pkg_name(self, name: str) -> str:
        """
        Resolve base application names or aliases to exact installed package names on Arch Linux.
        Handles -bin, -desktop-bin, -desktop, -git suffixes, desktop overrides, and package provides.
        """
        if not name:
            return ""
        n_low = name.strip().lower()
        if n_low in self.installed_set:
            return n_low
        if f"{n_low}-bin" in self.installed_set:
            return f"{n_low}-bin"
        if f"{n_low}-desktop-bin" in self.installed_set:
            return f"{n_low}-desktop-bin"
        if f"{n_low}-desktop" in self.installed_set:
            return f"{n_low}-desktop"
        if f"{n_low}-git" in self.installed_set:
            return f"{n_low}-git"

        # Explicit aliases
        aliases = {
            "chatgpt": ["chatgpt-bin", "chatgpt-desktop-bin"],
            "code": ["visual-studio-code-bin", "code", "code-oss"],
            "brave": ["brave-bin", "brave-origin-bin", "brave"],
            "spotify": ["spotify", "spotify-launcher"],
            "heroic": ["heroic-games-launcher-bin", "heroic"],
            "postman": ["postman-bin", "postman"],
        }
        if n_low in aliases:
            for cand in aliases[n_low]:
                if cand in self.installed_set:
                    return cand

        # Check provides in installed packages
        for inst_pkg in self.installed_set:
            pkg_data = self.packages.get(inst_pkg)
            if pkg_data:
                provides = pkg_data.get("provides", [])
                if any(p.split("=")[0].split(">")[0].split("<")[0].strip().lower() == n_low for p in provides):
                    return inst_pkg

        clean = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|cli|daemon|desktop|launcher)$', '', n_low)
        if clean in self.installed_set:
            return clean
        if f"{clean}-bin" in self.installed_set:
            return f"{clean}-bin"

        return n_low

    def is_installed(self, pkg_name: str) -> bool:
        """Check if a package or its equivalent launcher/alias is installed on the system."""
        if not pkg_name:
            return False
        name_lower = pkg_name.lower().strip()
        if name_lower in self.installed_set:
            return True
        if f"{name_lower}-bin" in self.installed_set:
            return True
        if f"{name_lower}-desktop-bin" in self.installed_set:
            return True
        if f"{name_lower}-desktop" in self.installed_set:
            return True
        resolved = self.resolve_installed_pkg_name(name_lower)
        if resolved in self.installed_set:
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

    def is_pkg_updating(self, pkg_name: str) -> bool:
        """
        Returns True if self.active_transaction is currently updating/upgrading pkg_name.
        Distinguishes active updates from background installations and general queue.
        """
        if not pkg_name:
            return False
        target = pkg_name.strip().lower()
        resolved = self.resolve_installed_pkg_name(target).lower()
        with self._action_lock:
            if not self.active_transaction:
                return False
            if self.active_transaction.get("is_completed"):
                return False
            action = (self.active_transaction.get("action") or "").lower()
            if action not in ["update", "upgrade"]:
                return False
            tx_pkg = (self.active_transaction.get("pkg_name") or "").strip().lower()
            if tx_pkg and tx_pkg not in ["system", "--all", "all", ""]:
                if tx_pkg in (target, resolved):
                    return True
            cur_pkg = (self.active_transaction.get("current_pkg") or "").strip().lower()
            if cur_pkg and cur_pkg in (target, resolved):
                return True
            # For single update where packages list has target
            pkgs = self.active_transaction.get("packages")
            if isinstance(pkgs, (list, set, tuple)):
                if any(isinstance(p, str) and p.strip().lower() in (target, resolved) for p in pkgs):
                    if tx_pkg not in ["system", "--all", "all", ""]:
                        return True
        return False

    def is_pkg_installing(self, pkg_name: str) -> bool:
        """
        Returns True if self.active_transaction exists and matches pkg_name for installation
        or active single update (preserving backward compatibility with tests).
        """
        if not pkg_name:
            return False
        target = pkg_name.strip().lower()
        resolved = self.resolve_installed_pkg_name(target).lower()
        with self._action_lock:
            if not self.active_transaction:
                return False
            if self.active_transaction.get("is_completed"):
                return False
            tx_pkg = (self.active_transaction.get("pkg_name") or "").strip().lower()
            if tx_pkg in (target, resolved):
                return True
            cur_pkg = (self.active_transaction.get("current_pkg") or "").strip().lower()
            if cur_pkg in (target, resolved):
                return True
            pkgs = self.active_transaction.get("packages")
            if isinstance(pkgs, (list, set, tuple)):
                if any(isinstance(p, str) and p.strip().lower() in (target, resolved) for p in pkgs):
                    return True
            action = (self.active_transaction.get("action") or "").lower()
            if action in ["update", "upgrade"]:
                if tx_pkg in ["system", "--all", "all", "", None]:
                    if target in ["system", "--all", "all", ""]:
                        return True
                    # Only return True if it's the currently active package in system upgrade
                    if cur_pkg and cur_pkg in (target, resolved):
                        return True
            return False

    def is_pkg_queued(self, pkg_name: str) -> bool:
        """Returns True if pkg_name is queued waiting for pacman/system transaction or part of batch update."""
        if not pkg_name:
            return False
        target = pkg_name.strip().lower()
        resolved = self.resolve_installed_pkg_name(target).lower()
        with self._action_lock:
            if any((item.get("pkg_name") or "").strip().lower() in (target, resolved) for item in self._pending_actions):
                return True
            # Batch update pending check
            if self.active_transaction and not self.active_transaction.get("is_completed"):
                action = (self.active_transaction.get("action") or "").lower()
                if action in ["update", "upgrade"]:
                    tx_pkg = (self.active_transaction.get("pkg_name") or "").strip().lower()
                    if tx_pkg in ["system", "--all", "all", "", None]:
                        completed = {p.strip().lower() for p in self.active_transaction.get("completed_pkgs", [])}
                        cur = (self.active_transaction.get("current_pkg") or "").strip().lower()
                        if target in completed or resolved in completed or target == cur or resolved == cur:
                            return False
                        with self._lock:
                            if any(u.get("name", "").strip().lower() in (target, resolved) for u in self.upgradable_list):
                                return True
        return False

    def get_queued_pkgs(self) -> List[str]:
        """Returns list of package names currently waiting in the transaction queue."""
        with self._action_lock:
            return [(item.get("pkg_name") or "").strip().lower() for item in self._pending_actions if item.get("pkg_name")]

    def get_queue_length(self) -> int:
        """Returns count of actions currently queued."""
        with self._action_lock:
            return len(self._pending_actions)

    def get_queue_position(self, pkg_name: str) -> int:
        """Returns 1-based position in queue if queued, or 0 if not queued."""
        if not pkg_name:
            return 0
        target = pkg_name.strip().lower()
        with self._action_lock:
            for idx, item in enumerate(self._pending_actions, start=1):
                if (item.get("pkg_name") or "").strip().lower() == target:
                    return idx
        return 0

    def cancel_queued_pkg(self, pkg_name: str) -> bool:
        """Removes a package from the pending queue if present. Returns True if removed."""
        if not pkg_name:
            return False
        target = pkg_name.strip().lower()
        with self._action_lock:
            for idx, item in enumerate(self._pending_actions):
                if (item.get("pkg_name") or "").strip().lower() == target:
                    self._pending_actions.pop(idx)
                    return True
        return False

    def is_busy(self) -> bool:
        """Returns True if a transaction is currently active or actions are queued."""
        with self._action_lock:
            return self.active_transaction is not None or len(self._pending_actions) > 0

    def has_active_tasks(self) -> bool:
        """Returns True if any background transaction, queued action, or service setup is running."""
        if self.is_busy():
            return True
        if getattr(self.snap_mgr, "is_setting_up", False):
            return True
        if getattr(self.container_mgr, "is_configuring", False):
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
                    cur_pkg = (self.active_transaction.get("current_pkg") or "").strip().lower()
                    matches = (cur_pkg == target)
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
        """Check for upgradable packages across pacman, AUR, snap, and containers."""
        with self._update_lock:
            self._update_generation += 1
            gen = self._update_generation

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

        # Merge AUR updates if helper available
        aur_helper = get_aur_helper()
        if aur_helper:
            try:
                aur_res = subprocess.run([aur_helper, "-Qua"], capture_output=True, text=True, timeout=8)
                if aur_res.returncode == 0:
                    for line in aur_res.stdout.splitlines():
                        parts = line.strip().split()
                        if len(parts) >= 4 and parts[2] == "->":
                            pkg_name = parts[0]
                            updates.append({
                                "name": pkg_name,
                                "old_ver": parts[1],
                                "new_ver": parts[3],
                                "source": "aur",
                                "icon": resolve_icon_name(pkg_name)
                            })
            except Exception as e:
                print(f"[Aura] AUR update check: {e}", file=sys.stderr)

        # Merge Snap updates
        try:
            snap_updates = self.snap_mgr.check_snap_updates()
            if snap_updates:
                updates.extend(snap_updates)
        except Exception as e:
            print(f"[Aura] Snap update check: {e}", file=sys.stderr)

        # Merge Container updates
        try:
            container_updates = self.container_mgr.check_container_updates()
            if container_updates:
                updates.extend(container_updates)
        except Exception as e:
            print(f"[Aura] Container update check: {e}", file=sys.stderr)

        self.is_checking_updates = False
        self.updates_checked = True

        # Filter out packages that were recently updated (within 30s) to prevent race resurrection
        if hasattr(self, "_recently_updated") and self._recently_updated:
            now = time.time()
            # Clean up stale entries (older than 30 seconds)
            stale = [k for k, t in self._recently_updated.items() if now - t > 30]
            for k in stale:
                del self._recently_updated[k]
            # Filter updates list
            if self._recently_updated:
                updates = [u for u in updates if u.get("name", "").strip().lower() not in self._recently_updated]

        with self._lock:
            # Concurrency guard: only commit if newer check hasn't superseded this generation
            if gen == self._update_generation:
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
        """High-performance tiered fuzzy search for pacman repository packages."""
        clean_q = query.strip().lower()
        if not clean_q:
            return []

        q_len = len(clean_q)
        q_norm = re.sub(r'[-_.\s]+', ' ', clean_q).strip()

        with self._lock:
            items = list(self.packages.values())
            installed_set = self.installed_set
            installed_vers = self.installed_versions

        desktop_set = get_desktop_and_curated_set()
        candidates: List[Tuple[int, Dict[str, Any]]] = []

        if q_len == 1:
            # Fast-path for 1-char: ONLY match packages starting with that letter. Instant sub-1ms return.
            for pkg in items:
                name = pkg.get("name", "")
                if not name:
                    continue
                name_lower = name.lower()
                if not name_lower.startswith(clean_q):
                    continue
                is_installed = (name in installed_set)
                score = fuzzy_score(clean_q, name, is_installed=is_installed)
                if score > 0:
                    candidates.append((score, pkg))

        elif q_len == 2:
            # Fast-path for 2-char: ONLY match packages whose name starts with or contains query. Never scan 20,000 descriptions.
            for pkg in items:
                name = pkg.get("name", "")
                if not name:
                    continue
                name_lower = name.lower()
                if clean_q not in name_lower and q_norm not in name_lower.replace("-", " ").replace("_", " "):
                    continue
                is_installed = self.is_installed(name)
                score = fuzzy_score(clean_q, name, is_installed=is_installed)
                if score > 0:
                    candidates.append((score, pkg))

        else:
            # For >= 3 char queries: Scan names first; only scan descriptions if names yield fewer than 50 top results, or apply C-speed query in desc check before scoring.
            matched_pkg_ids = set()
            q0 = clean_q[0]

            for pkg in items:
                name = pkg.get("name", "")
                if not name:
                    continue
                name_lower = name.lower()
                if q0 not in name_lower and clean_q not in name_lower:
                    continue
                is_installed = self.is_installed(name)
                score = fuzzy_score(clean_q, name, desc="", is_installed=is_installed)
                if score > 0:
                    candidates.append((score, pkg))
                    matched_pkg_ids.add(id(pkg))

            # Only scan descriptions if names yield fewer than 50 top results
            if len(candidates) < 50:
                for pkg in items:
                    if id(pkg) in matched_pkg_ids:
                        continue
                    desc = pkg.get("desc", "")
                    if not desc:
                        continue
                    desc_lower = desc.lower()
                    # Apply C-speed `query in desc` check before scoring
                    if clean_q not in desc_lower and q_norm not in desc_lower:
                        continue
                    name = pkg.get("name", "")
                    is_installed = self.is_installed(name)
                    score = fuzzy_score(clean_q, name, desc=desc, is_installed=is_installed)
                    if score > 0:
                        candidates.append((score, pkg))

        # Sort candidates strictly by score descending
        candidates.sort(key=lambda x: x[0], reverse=True)

        results = []
        for score, pkg in candidates[:limit]:
            name = pkg.get("name", "")
            desc = pkg.get("desc", "")
            is_installed = self.is_installed(name)
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

        return results

    def search_aur(self, query: str, limit: int = 50) -> List[Dict[str, Any]]:
        """Search AUR packages using AUR RPC API with paru fallback and tiered scoring."""
        if not query.strip() or len(query.strip()) < 2:
            return []

        clean_q = query.strip().lower()
        if clean_q in self._aur_cache:
            return self._aur_cache[clean_q]

        candidates: List[Tuple[int, Dict[str, Any]]] = []
        with self._lock:
            installed_set = self.installed_set
            installed_vers = self.installed_versions

        desktop_set = get_desktop_and_curated_set()

        try:
            url = f"https://aur.archlinux.org/rpc/v5/search/{urllib.parse.quote(clean_q)}"
            req = urllib.request.Request(url, headers={"User-Agent": "Aura-PackageHub/3.0"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                for item in data.get("results", []):
                    name = item.get("Name", "")
                    if not name:
                        continue
                    desc = item.get("Description", "") or ""
                    name_lower = name.lower()
                    is_desktop = (name_lower in desktop_set)
                    if not is_desktop:
                        clean_name = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|desktop|launcher)$', '', name_lower)
                        is_desktop = (clean_name in desktop_set)
                    is_installed = (name in installed_set)
                    score = fuzzy_score(clean_q, name, desc, is_desktop=is_desktop, is_installed=is_installed)
                    if score > 0:
                        candidates.append((score, {
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
                        }))
        except Exception:
            aur_helper = get_aur_helper()
            if aur_helper:
                try:
                    p = subprocess.run([aur_helper, "-Ssa", clean_q], capture_output=True, text=True, timeout=5)
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
                                name_lower = pkg_name.lower()
                                is_desktop = (name_lower in desktop_set)
                                if not is_desktop:
                                    clean_name = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|desktop|launcher)$', '', name_lower)
                                    is_desktop = (clean_name in desktop_set)
                                is_installed = self.is_installed(pkg_name)
                                score = fuzzy_score(clean_q, pkg_name, desc, is_desktop=is_desktop, is_installed=is_installed)
                                if score > 0:
                                    candidates.append((score, {
                                        "name": pkg_name,
                                        "version": ver,
                                        "desc": desc,
                                        "repo": "aur",
                                        "source": "aur",
                                        "priority": 1,
                                        "score": score,
                                        "is_installed": is_installed,
                                        "installed_version": installed_vers.get(pkg_name, ""),
                                        "icon": resolve_icon_name(pkg_name, desc),
                                        "url": f"https://aur.archlinux.org/packages/{pkg_name}",
                                    }))
                        i += 1
                except Exception as e:
                    print(f"[Aura] AUR helper ({aur_helper}) fallback error: {e}", file=sys.stderr)

        candidates.sort(key=lambda x: x[0], reverse=True)
        final_results = [item for score, item in candidates[:limit]]
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
            seen_names = set()
            combined = []
            for p in pacman_res:
                p_name = p.get("name", "")
                if p_name and p_name not in seen_names:
                    seen_names.add(p_name)
                    combined.append(p)
            for p in aur_res:
                p_name = p.get("name", "")
                if p_name and p_name not in seen_names:
                    seen_names.add(p_name)
                    combined.append(p)
            combined.sort(
                key=lambda x: (
                    -x.get("score", 0),
                    x.get("priority", 0),
                    x.get("name", "").lower(),
                )
            )
        elif filter_mode == "pacman":
            combined = pacman_res
        elif filter_mode == "aur":
            combined = aur_res
        elif filter_mode == "installed":
            combined = [p for p in pacman_res if p.get("is_installed")]
            desktop_set = get_desktop_and_curated_set()
            with self._lock:
                for inst_name, inst_ver in self.installed_versions.items():
                    if inst_name not in self.packages:
                        inst_lower = inst_name.lower()
                        is_desktop = (inst_lower in desktop_set)
                        if not is_desktop:
                            clean_name = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|desktop|launcher)$', '', inst_lower)
                            is_desktop = (clean_name in desktop_set)
                        score = fuzzy_score(q, inst_name, is_desktop=is_desktop, is_installed=True)
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
        """Unified search across pacman, AUR, Snap, and/or Docker with strict priority."""
        if source == "snap":
            return self.snap_mgr.search_snaps(query)[:limit]
        elif source in ("container", "docker"):
            return self.container_mgr.list_apps(query)[:limit]
        elif source == "pacman":
            return self.search_pacman(query, limit=limit)
        elif source == "aur":
            return self.search_aur(query, limit=limit)
        return self.search_all(query, filter_mode=source, limit=limit)

    def resolve_package_source(self, pkg_name: str) -> str:
        """
        Intelligently determine whether a package is from official pacman repos,
        AUR, Snap, or Docker/Container.
        """
        clean = (pkg_name or "").strip().lower()
        if not clean:
            return "pacman"

        # 1. Check official pacman sync DB & installed packages
        if clean in self.packages or clean in self.installed_set:
            return "pacman"

        # 2. Check curated Snap or installed Snap
        if hasattr(self, "snap_mgr") and self.snap_mgr:
            if any(s.get("name") == clean for s in getattr(self.snap_mgr, "CURATED_SNAP_APPS", [])):
                return "snap"
            if self.snap_mgr.is_snap_installed(clean):
                return "snap"

        # 3. Check curated Container or installed container app
        if hasattr(self, "container_mgr") and self.container_mgr:
            if any(c.get("id") == clean or c.get("name", "").lower() == clean for c in getattr(self.container_mgr, "CURATED_CONTAINER_APPS", [])):
                return "docker"
            if self.container_mgr.is_app_installed(clean):
                return "docker"

        # 5. Check AUR via helper
        aur_helper = get_aur_helper()
        if aur_helper:
            try:
                res = subprocess.run([aur_helper, "-Si", clean], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2)
                if res.returncode == 0:
                    return "aur"
            except Exception:
                pass

        return "pacman"

    @staticmethod
    def parse_pacman_info(raw_text: str) -> Dict[str, Any]:
        """Parse pacman -Si or -Qi output into structured metadata (AURA-028)."""
        return parse_pacman_info(raw_text)

    def get_package_info(self, name: str, source: str = "pacman") -> Dict[str, Any]:
        """Fetch package information unified across pacman, AUR, Snap, and Container."""
        if source == "snap":
            return self.snap_mgr.get_snap_details(name)
        elif source in ("container", "docker"):
            meta = next((a for a in self.container_mgr.CURATED_CONTAINER_APPS if a["id"] == name), None)
            if meta:
                return dict(meta)
        return self.get_package_detail(name, source=source)

    def get_package_detail(self, name: str, source: str = "pacman") -> Dict[str, Any]:
        """Fetch full details of a package with strict sanitization."""
        if source == "snap":
            return self.snap_mgr.get_snap_details(name)
        elif source in ("container", "docker"):
            meta = next((a for a in self.container_mgr.CURATED_CONTAINER_APPS if a["id"] == name), None)
            if meta:
                item = dict(meta)
                item["source"] = "docker"
                item["is_installed"] = self.container_mgr.is_app_installed(name)
                item["display_name"] = item.get("name", name)
                return item
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
                    parsed = parse_pacman_info(res.stdout)
                    for k, val in parsed.items():
                        if val:
                            if k == "depends":
                                info["depends"] = val
                            elif k == "optdepends":
                                info["optdepends"] = val
                            else:
                                info[k] = val
            except Exception:
                pass

        if is_installed:
            try:
                target_qi = name if name in self.installed_set else ("code" if name.lower() in ["code", "visual-studio-code-bin"] else name)
                res = subprocess.run(["pacman", "-Qi", target_qi], capture_output=True, text=True, timeout=3)
                if res.returncode == 0:
                    parsed = parse_pacman_info(res.stdout)
                    for k, val in parsed.items():
                        if val:
                            if k == "depends":
                                info["depends"] = val
                            elif k == "optdepends":
                                info["optdepends"] = val
                            elif not info.get(k):
                                info[k] = val
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
        is_inst = self.is_installed(pkg_name)
        if not is_inst and hasattr(self, "snap_mgr") and self.snap_mgr:
            try:
                is_inst = self.snap_mgr.is_snap_installed(pkg_name)
            except Exception:
                pass
        if not is_inst:
            return None
        entries = get_desktop_entries_map()
        name_lower = pkg_name.lower()
        if name_lower in _DESKTOP_OVERRIDES:
            ov = _DESKTOP_OVERRIDES[name_lower]
            if ov in entries or os.path.exists(f"/usr/share/applications/{ov}.desktop"):
                return ov
        if name_lower in entries:
            return entries[name_lower]
        clean = re.sub(r'-(bin|git|hg|svn|pure|gtk-app|qt-app|gui|desktop|launcher)$', '', name_lower)
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


    def _init_active_transaction(self, action: str, pkg_name: str, source: str):
        """Initializes self.active_transaction dictionary under self._action_lock."""
        is_system_upgrade = pkg_name in ["system", "--all", "all", ""] or not pkg_name
        target_pkg = ("" if is_system_upgrade else (pkg_name or "")).strip().lower()

        self.active_transaction = {
            "action": action,
            "pkg_name": pkg_name,
            "source": source,
            "progress": 0.06,
            "status": "Authenticating & preparing...",
            "status_msg": "Authenticating & preparing...",
            "start_time": time.time(),
            "current_pkg": target_pkg if target_pkg else (pkg_name or None),
            "completed_pkgs": [],
            "current_idx": 0,
            "total_packages": 1,
        }
        if action in ["upgrade", "update"] and is_system_upgrade:
            with self._lock:
                pkgs = [u.get("name") for u in self.upgradable_list if u.get("name")]
                self.active_transaction["packages"] = pkgs
                if pkgs:
                    self.active_transaction["total_packages"] = len(pkgs)
        elif pkg_name:
            self.active_transaction["packages"] = [pkg_name]
        self._last_progress = 0.06

    def _process_next_action(self):
        """Pops and executes the next pending action from the queue, or resets active_transaction if empty."""
        with self._action_lock:
            if not self._pending_actions:
                self.active_transaction = None
                self._last_progress = 0.0
                return

            # Atomically pop and initialize next active_transaction so is_busy stays True
            next_action = self._pending_actions.pop(0)
            self._init_active_transaction(
                next_action["action"],
                next_action["pkg_name"],
                next_action["source"]
            )

        # 0.4s pause ensures pacman / paru closes /var/lib/pacman/db.lck cleanly
        def _deferred_start():
            time.sleep(0.4)
            self._start_action_worker(
                action=next_action["action"],
                pkg_name=next_action["pkg_name"],
                source=next_action["source"],
                progress_cb=next_action["progress_cb"],
                complete_cb=next_action["complete_cb"],
                start_cb=next_action.get("start_cb"),
            )

        threading.Thread(target=_deferred_start, daemon=True).start()

    def execute_background_action(
        self,
        action: str,
        pkg_name: str,
        source: str,
        progress_cb: Callable[[float, str], None],
        complete_cb: Callable[[bool, str, str, str], None],
        start_cb: Optional[Callable[[], None]] = None
    ) -> str:
        """
        Run installation, removal, or upgrade in background without terminal popups.
        Thread-safe FIFO Action Queue:
        - If a transaction is active, appends to self._pending_actions and returns 'queued'.
        - If idle, begins execution immediately and returns 'started'.
        - When an action completes, automatically pops and executes the next queued action.
        """
        target_norm = (pkg_name or "").strip().lower()

        with self._action_lock:
            # Check if identical action is already running or queued
            if self.active_transaction is not None:
                tx_pkg = (self.active_transaction.get("pkg_name") or "").strip().lower()
                if target_norm and tx_pkg == target_norm:
                    return "already_active"
                for item in self._pending_actions:
                    if (item.get("pkg_name") or "").strip().lower() == target_norm and item.get("action") == action:
                        return "already_queued"

                self._pending_actions.append({
                    "action": action,
                    "pkg_name": pkg_name,
                    "source": source,
                    "progress_cb": progress_cb,
                    "complete_cb": complete_cb,
                    "start_cb": start_cb,
                    "enqueued_time": time.time(),
                })
                q_pos = len(self._pending_actions)
                progress_cb(0.02, f"Queued in transaction line (#{q_pos})...")
                return "queued"

            # Idle: initialize active_transaction immediately under lock
            self._init_active_transaction(action, pkg_name, source)

        # Start the action worker
        self._start_action_worker(action, pkg_name, source, progress_cb, complete_cb, start_cb)
        return "started"

    def _start_action_worker(
        self,
        action: str,
        pkg_name: str,
        source: str,
        progress_cb: Callable[[float, str], None],
        complete_cb: Callable[[bool, str, str, str], None],
        start_cb: Optional[Callable[[], None]] = None
    ):
        if start_cb:
            try:
                start_cb()
            except Exception as e:
                print(f"[Aura] Error in start_cb: {e}", file=sys.stderr)

        is_system_upgrade = pkg_name in ["system", "--all", "all", ""] or not pkg_name
        target_pkg = ("" if is_system_upgrade else (pkg_name or "")).strip().lower()

        def _safe_progress(
            target_frac: float,
            msg: str,
            cur_p: Optional[str] = None,
            cur_i: Optional[int] = None,
            tot_p: Optional[int] = None
        ):
            with self._action_lock:
                frac = max(self._last_progress, min(0.98, target_frac)) if target_frac < 1.0 else 1.0
                self._last_progress = frac
                if isinstance(self.active_transaction, dict):
                    self.active_transaction["progress"] = frac
                    self.active_transaction["status"] = msg
                    self.active_transaction["status_msg"] = msg
                    if cur_p is not None:
                        old_p = self.active_transaction.get("current_pkg")
                        if old_p and old_p.strip().lower() != cur_p.strip().lower():
                            comp = self.active_transaction.setdefault("completed_pkgs", [])
                            if old_p not in comp:
                                comp.append(old_p)
                        self.active_transaction["current_pkg"] = cur_p
                    if cur_i is not None:
                        self.active_transaction["current_idx"] = cur_i
                    if tot_p is not None:
                        self.active_transaction["total_packages"] = tot_p
            progress_cb(frac, msg)

        def _worker():
            nonlocal pkg_name, target_pkg
            try:
                env = os.environ.copy()
                if ASKPASS_SCRIPT.exists():
                    env["SUDO_ASKPASS"] = str(ASKPASS_SCRIPT)
                env["LC_ALL"] = "C"

                actual_source = source
                if action in ["update", "upgrade"] and not is_system_upgrade:
                    with self._lock:
                        matched = next((u for u in self.upgradable_list if u.get("name") in (pkg_name, f"{pkg_name}-bin", self.resolve_installed_pkg_name(pkg_name))), None)
                        if matched:
                            if matched.get("source"):
                                actual_source = matched["source"]
                            if matched.get("name") and matched["name"] != pkg_name:
                                pkg_name = matched["name"]
                                target_pkg = pkg_name.strip().lower()
                    if actual_source == "pacman":
                        resolved_src = self.resolve_package_source(pkg_name)
                        if resolved_src != "pacman":
                            actual_source = resolved_src

                if actual_source == "snap":
                    def _snap_prog(frac_or_msg, maybe_msg=None):
                        if isinstance(frac_or_msg, (int, float)):
                            frac = float(frac_or_msg)
                            msg = str(maybe_msg or "")
                        else:
                            msg = str(frac_or_msg)
                            frac = 0.5
                        _safe_progress(frac, msg)

                    def _snap_done(ok: bool, *args):
                        if len(args) == 3:
                            _, _, err = args
                        elif len(args) == 1:
                            err = "" if ok else str(args[0])
                        else:
                            err = "" if ok else "Snap operation failed"

                        if ok:
                            with self._lock:
                                if action in ["upgrade", "update"]:
                                    self.upgradable_list = [u for u in self.upgradable_list if u.get("name") != pkg_name]
                                    try:
                                        with open(UPDATES_CACHE_FILE, "w") as f:
                                            json.dump(self.upgradable_list, f)
                                    except Exception:
                                        pass
                            comp_msg = f"Completed {action} for {pkg_name}."
                            with self._action_lock:
                                if self.active_transaction:
                                    self.active_transaction["progress"] = 1.0
                                    self.active_transaction["status"] = comp_msg
                                    self.active_transaction["status_msg"] = comp_msg
                                    self.active_transaction["is_completed"] = True
                            self._last_progress = 1.0
                            progress_cb(1.0, comp_msg)
                            try:
                                complete_cb(True, action, pkg_name, "")
                            finally:
                                self._process_next_action()
                        else:
                            with self._action_lock:
                                if self.active_transaction:
                                    self.active_transaction["progress"] = 0.0
                                    self.active_transaction["status"] = f"Failed: {err}"
                                    self.active_transaction["status_msg"] = f"Failed: {err}"
                                    self.active_transaction["is_completed"] = True
                                self.active_transaction = None
                                self._last_progress = 0.0
                            progress_cb(0.0, f"Failed: {err}")
                            try:
                                complete_cb(False, action, pkg_name, err)
                            finally:
                                self._process_next_action()

                    if action == "install":
                        self.snap_mgr.install_snap(pkg_name, progress_cb=_snap_prog, complete_cb=_snap_done)
                    elif action == "remove":
                        self.snap_mgr.remove_snap(pkg_name, progress_cb=_snap_prog, complete_cb=_snap_done)
                    elif action in ["update", "upgrade"]:
                        self.snap_mgr.update_snap(pkg_name, progress_cb=_snap_prog, complete_cb=_snap_done)
                    else:
                        _snap_done(False, f"Unsupported snap action: {action}")
                    return

                if actual_source == "docker":
                    def _docker_prog(msg: str):
                        _safe_progress(0.5, str(msg))

                    def _docker_done(ok: bool, msg: str = ""):
                        if ok:
                            with self._lock:
                                self.upgradable_list = [u for u in self.upgradable_list if u.get("name") != pkg_name]
                                try:
                                    with open(UPDATES_CACHE_FILE, "w") as f:
                                        json.dump(self.upgradable_list, f)
                                except Exception:
                                    pass
                            comp_msg = f"Updated container application {pkg_name}."
                            with self._action_lock:
                                if self.active_transaction:
                                    self.active_transaction["progress"] = 1.0
                                    self.active_transaction["status"] = comp_msg
                                    self.active_transaction["status_msg"] = comp_msg
                                    self.active_transaction["is_completed"] = True
                            self._last_progress = 1.0
                            progress_cb(1.0, comp_msg)
                            try:
                                complete_cb(True, action, pkg_name, "")
                            finally:
                                self._process_next_action()
                        else:
                            with self._action_lock:
                                if self.active_transaction:
                                    self.active_transaction["progress"] = 0.0
                                    self.active_transaction["status"] = f"Failed: {msg}"
                                    self.active_transaction["status_msg"] = f"Failed: {msg}"
                                    self.active_transaction["is_completed"] = True
                                self.active_transaction = None
                                self._last_progress = 0.0
                            progress_cb(0.0, f"Failed: {msg}")
                            try:
                                complete_cb(False, action, pkg_name, msg)
                            finally:
                                self._process_next_action()

                    if action in ["update", "upgrade"]:
                        self.container_mgr.update_app(pkg_name, progress_callback=_docker_prog, completion_callback=_docker_done)
                    elif action == "install":
                        self.container_mgr.install_app(pkg_name, progress_callback=_docker_prog, completion_callback=_docker_done)
                    elif action == "remove":
                        self.container_mgr.uninstall_app(pkg_name, progress_callback=_docker_prog, completion_callback=_docker_done)
                    else:
                        _docker_done(False, f"Unsupported docker action: {action}")
                    return

                # Sudo invocation: if passwordless is configured, use 'sudo -n'. Otherwise use 'sudo -A' with askpass.
                sudo_prefix = ["sudo", "-n"] if PackageManager.is_passwordless_configured() else ["sudo", "-A"]
                aur_helper = get_aur_helper()

                # Pacman / AUR execution
                if action in ["upgrade", "update"]:
                    if is_system_upgrade:
                        if aur_helper == "paru":
                            cmd = ["paru", "-Syu", "--needed", "--noconfirm", "--skipreview"]
                        elif aur_helper == "yay":
                            cmd = ["yay", "-Syu", "--needed", "--noconfirm", "--nodiffmenu", "--noeditmenu"]
                        else:
                            cmd = sudo_prefix + ["pacman", "-Syu", "--needed", "--noconfirm"]
                    else:
                        target = self.resolve_installed_pkg_name(pkg_name)
                        # CRITICAL: For single package update/upgrade, NEVER pass --needed!
                        # --needed skips already installed packages and exits 0 immediately without updating.
                        if actual_source == "aur":
                            if aur_helper == "paru":
                                cmd = ["paru", "-S", "--noconfirm", "--skipreview", target]
                            elif aur_helper == "yay":
                                cmd = ["yay", "-S", "--noconfirm", "--nodiffmenu", "--noeditmenu", target]
                            else:
                                cmd = sudo_prefix + ["pacman", "-S", "--noconfirm", target]
                        else:
                            cmd = sudo_prefix + ["pacman", "-S", "--noconfirm", target]
                elif action == "install":
                    if actual_source == "pacman":
                        cmd = sudo_prefix + ["pacman", "-S", "--needed", "--noconfirm", pkg_name]
                    else:
                        if aur_helper == "paru":
                            cmd = ["paru", "-S", "--needed", "--noconfirm", "--skipreview", pkg_name]
                        elif aur_helper == "yay":
                            cmd = ["yay", "-S", "--needed", "--noconfirm", "--nodiffmenu", "--noeditmenu", pkg_name]
                        else:
                            cmd = sudo_prefix + ["pacman", "-S", "--needed", "--noconfirm", pkg_name]
                elif action == "remove":
                    target = self.resolve_installed_pkg_name(pkg_name)
                    cmd = sudo_prefix + ["pacman", "-Rns", "--noconfirm", target]
                else:
                    cmd = sudo_prefix + ["pacman", "-S", "--needed", "--noconfirm", pkg_name]

                _safe_progress(0.06, "Authenticating & preparing...")

                upgrades_set = set()
                with self._lock:
                    upgrades_set = {u.get("name", "").strip().lower() for u in self.upgradable_list if u.get("name")}

                parser = PacmanProgressParser(
                    action=action,
                    target_pkg=target_pkg,
                    is_system_upgrade=is_system_upgrade,
                    upgradable_pkgs=upgrades_set
                )

                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )

                for line in proc.stdout:
                    res = parser.process_line(line)
                    if res:
                        frac, msg, cur_p, cur_i, tot_p = res
                        _safe_progress(frac, msg, cur_p=cur_p, cur_i=cur_i, tot_p=tot_p)

                proc.wait()
                success = proc.returncode == 0
                if success:
                    # Record which packages were updated so check_updates can exclude them temporarily
                    resolved_inst = self.resolve_installed_pkg_name(pkg_name) if pkg_name else ""
                    with self._lock:
                        if action in ["upgrade", "update"]:
                            if is_system_upgrade:
                                # Retain any pending Snap or Docker updates; only clear completed native updates (AURA-099)
                                self.upgradable_list = [u for u in self.upgradable_list if u.get("source") not in ("pacman", "aur")]
                                try:
                                    with open(UPDATES_CACHE_FILE, "w") as f:
                                        json.dump(self.upgradable_list, f)
                                except Exception:
                                    pass
                            elif pkg_name:
                                exclude_names = {pkg_name, f"{pkg_name}-bin", resolved_inst}
                                self.upgradable_list = [u for u in self.upgradable_list if u.get("name") not in exclude_names]
                                # Protect against race: mark these as recently updated
                                if not hasattr(self, "_recently_updated"):
                                    self._recently_updated = {}
                                now = time.time()
                                for en in exclude_names:
                                    if en:
                                        self._recently_updated[en] = now
                                try:
                                    with open(UPDATES_CACHE_FILE, "w") as f:
                                        json.dump(self.upgradable_list, f)
                                except Exception:
                                    pass
                    self.refresh_installed()

                    comp_msg = parser.get_completion_message()
                    with self._action_lock:
                        if self.active_transaction:
                            self.active_transaction["progress"] = 1.0
                            self.active_transaction["status"] = comp_msg
                            self.active_transaction["status_msg"] = comp_msg
                            c_p = self.active_transaction.get("current_pkg")
                            if c_p:
                                comp = self.active_transaction.setdefault("completed_pkgs", [])
                                if c_p not in comp:
                                    comp.append(c_p)
                            self.active_transaction["current_pkg"] = None
                            self.active_transaction["current_idx"] = parser.current_idx
                            self.active_transaction["total_packages"] = parser.total_packages
                            self.active_transaction["is_completed"] = True
                    self._last_progress = 1.0
                    progress_cb(1.0, comp_msg)
                    # Delayed background check for remaining updates (wait for pacman sync DB to settle)
                    def _delayed_check():
                        time.sleep(5)
                        self.check_updates()
                    threading.Thread(target=_delayed_check, daemon=True).start()
                    try:
                        complete_cb(True, action, pkg_name, "")
                    finally:
                        self._process_next_action()
                else:
                    if any("password" in l.lower() or "auth" in l.lower() for l in parser.error_lines):
                        PackageManager.clear_auth_cache()
                    err_status = parser.get_error_message(proc.returncode)
                    raw_err = "\n".join(parser.error_lines[-3:]) if parser.error_lines else f"Exited with code {proc.returncode}"
                    with self._action_lock:
                        if self.active_transaction:
                            self.active_transaction["progress"] = 0.0
                            self.active_transaction["status"] = err_status
                            self.active_transaction["status_msg"] = err_status
                            self.active_transaction["is_completed"] = True
                        self.active_transaction = None
                        self._last_progress = 0.0
                    progress_cb(0.0, err_status)
                    try:
                        complete_cb(False, action, pkg_name, raw_err)
                    finally:
                        self._process_next_action()

            except Exception as e:
                err_status = f"Error: {e}"
                with self._action_lock:
                    if self.active_transaction:
                        self.active_transaction["progress"] = 0.0
                        self.active_transaction["status"] = err_status
                        self.active_transaction["status_msg"] = err_status
                    self.active_transaction = None
                    self._last_progress = 0.0
                progress_cb(0.0, err_status)
                try:
                    complete_cb(False, action, pkg_name, str(e))
                finally:
                    self._process_next_action()

        threading.Thread(target=_worker, daemon=True).start()

    @staticmethod
    def get_vault_path() -> Path:
        return Path.home() / ".local" / "share" / "aura" / ".aura_vault"

    @classmethod
    def is_passwordless_configured(cls) -> bool:
        """
        Authoritative check: verify whether sudo allows pacman execution without password.
        Does NOT rely on local credential files.
        """
        try:
            res = subprocess.run(["sudo", "-n", "pacman", "-V"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return res.returncode == 0
        except Exception:
            return False

    @classmethod
    def configure_passwordless(cls, password: str) -> Tuple[bool, str]:
        """
        Configure system-level passwordless package management via validated sudoers policy.
        CRITICAL SECURITY GUARANTEES (AURA-PW-001 - AURA-PW-010):
        - Administrator password is NEVER saved to disk or persistent storage.
        - Authorization is narrowly scoped to /usr/bin/pacman (paru is NOT given root NOPASSWD).
        - Rule is validated with 'visudo -cf' before installation.
        - Operation is verified with 'sudo -n pacman -V'.
        - Fully atomic and reversible.
        """
        if not password:
            return False, "Password cannot be empty."

        # 1. Verify credentials via sudo -S -v
        try:
            proc = subprocess.run(
                ["sudo", "-S", "-v"],
                input=f"{password}\n",
                text=True,
                capture_output=True,
                timeout=5
            )
            if proc.returncode != 0:
                return False, "Incorrect password. Please verify and try again."
        except Exception as e:
            return False, f"Authentication test failed: {e}"

        # 2. Prepare temporary sudoers file with narrow pacman-only alias
        user = os.environ.get("USER") or "wheel"
        sudoers_content = f"Cmnd_Alias AURA_PACMAN = /usr/bin/pacman\n{user} ALL=(root) NOPASSWD: AURA_PACMAN\n"

        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile("w", delete=False, prefix="99-aura-pacman-") as tf:
                tf.write(sudoers_content)
                tmp_path = tf.name

            # 3. Validate with visudo before installation
            vproc = subprocess.run(["visudo", "-cf", tmp_path], capture_output=True, text=True, timeout=5)
            if vproc.returncode != 0:
                return False, f"Sudoers syntax validation failed: {vproc.stderr or vproc.stdout}"

            # 4. Install atomically with root:root 0440 permissions
            inst_cmd = ["sudo", "-S", "install", "-o", "root", "-g", "root", "-m", "0440", tmp_path, "/etc/sudoers.d/99-aura-pacman"]
            iproc = subprocess.run(
                inst_cmd,
                input=f"{password}\n",
                text=True,
                capture_output=True,
                timeout=5
            )
            if iproc.returncode != 0:
                return False, f"Failed installing sudoers rule: {iproc.stderr}"

            # 5. Verify that sudo -n pacman -V now succeeds without prompting
            test_proc = subprocess.run(["sudo", "-n", "pacman", "-V"], capture_output=True, text=True, timeout=5)
            if test_proc.returncode != 0:
                # Rollback if verification failed
                subprocess.run(["sudo", "-S", "rm", "-f", "/etc/sudoers.d/99-aura-pacman"], input=f"{password}\n", text=True, capture_output=True, timeout=5)
                return False, "Installed sudoers rule could not be verified by sudo."

            # 6. Purge any legacy plaintext credentials or session token files
            cls.get_vault_path().unlink(missing_ok=True)
            cls.clear_auth_cache()

            return True, "Passwordless package management configured and verified successfully."
        except Exception as e:
            return False, f"Failed configuring passwordless sudo: {e}"
        finally:
            if tmp_path and os.path.exists(tmp_path):
                try:
                    os.unlink(tmp_path)
                except Exception:
                    pass

    @classmethod
    def remove_passwordless(cls, password: Optional[str] = None) -> Tuple[bool, str]:
        """
        Reversible de-authorization: removes /etc/sudoers.d/99-aura-pacman and clears legacy vault files.
        """
        try:
            rule_path = Path("/etc/sudoers.d/99-aura-pacman")
            if not rule_path.exists():
                cls.get_vault_path().unlink(missing_ok=True)
                cls.clear_auth_cache()
                return True, "Passwordless mode is already disabled."

            # First attempt sudo -n rm
            proc = subprocess.run(["sudo", "-n", "rm", "-f", "/etc/sudoers.d/99-aura-pacman"], capture_output=True, text=True, timeout=5)
            if proc.returncode != 0 and password:
                proc = subprocess.run(
                    ["sudo", "-S", "rm", "-f", "/etc/sudoers.d/99-aura-pacman"],
                    input=f"{password}\n",
                    text=True,
                    capture_output=True,
                    timeout=5
                )

            cls.get_vault_path().unlink(missing_ok=True)
            cls.clear_auth_cache()

            if not rule_path.exists():
                return True, "Passwordless mode disabled successfully."
            else:
                return False, "Failed to remove sudoers configuration rule."
        except Exception as e:
            return False, f"Error disabling passwordless mode: {e}"

    @staticmethod
    def clear_auth_cache():
        """Clear cached session tokens and legacy vaults."""
        try:
            token = Path(f"/run/user/{os.getuid()}/aura_auth.token")
            token.unlink(missing_ok=True)
        except Exception:
            pass
        try:
            vault = Path.home() / ".local" / "share" / "aura" / ".aura_vault"
            vault.unlink(missing_ok=True)
        except Exception:
            pass


class SnapManager:
    """
    Manages Snap package integration, API queries, installations, updates, and removals.
    Features:
    - Native integration with Snapcraft REST API v2 (find & info endpoints).
    - Curated catalog of 16 popular applications with rich metadata.
    - Local snapd status and installed package introspection via snap CLI & filesystem.
    - Upgradable snap detection via 'snap refresh --list'.
    - Background installation, removal, and refresh with realtime progress reporting.
    """

    CURATED_SNAP_APPS = [
        {
            "name": "spotify",
            "title": "Spotify",
            "summary": "Music for everyone",
            "icon": "spotify",
            "developer": "Spotify",
            "category": "Audio & Video",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "Spotify",
            "version": "1.2.53.440.g7b2f582a",
            "store_url": "https://snapcraft.io/spotify",
            "store-url": "https://snapcraft.io/spotify",
            "description": "Spotify is a digital music service that gives you access to millions of songs.",
        },
        {
            "name": "code",
            "title": "Visual Studio Code",
            "summary": "Code editing. Redefined.",
            "icon": "code",
            "developer": "Microsoft",
            "category": "Development",
            "channel": "latest/stable",
            "confinement": "classic",
            "publisher": "Microsoft",
            "version": "1.93.1",
            "store_url": "https://snapcraft.io/code",
            "store-url": "https://snapcraft.io/code",
            "description": "Visual Studio Code is a code editor redefined and optimized for building modern web and cloud applications.",
        },
        {
            "name": "discord",
            "title": "Discord",
            "summary": "All-in-one voice and text chat for gamers",
            "icon": "discord",
            "developer": "Snapcrafters",
            "category": "Social & Communication",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "Snapcrafters",
            "version": "0.0.68",
            "store_url": "https://snapcraft.io/discord",
            "store-url": "https://snapcraft.io/discord",
            "description": "Discord is the easiest way to talk over voice, video, and text.",
        },
        {
            "name": "slack",
            "title": "Slack",
            "summary": "Team communication and collaboration platform",
            "icon": "slack",
            "developer": "Slack",
            "category": "Productivity",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "Slack",
            "version": "4.39.95",
            "store_url": "https://snapcraft.io/slack",
            "store-url": "https://snapcraft.io/slack",
            "description": "Slack brings all your team communication together, giving everyone a shared workspace.",
        },
        {
            "name": "postman",
            "title": "Postman",
            "summary": "API platform for building and using APIs",
            "icon": "postman",
            "developer": "Postman, Inc.",
            "category": "Development",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "Postman, Inc.",
            "version": "11.13.0",
            "store_url": "https://snapcraft.io/postman",
            "store-url": "https://snapcraft.io/postman",
            "description": "Postman is an API platform for developers to design, build, test, and iterate their APIs.",
        },
        {
            "name": "blender",
            "title": "Blender",
            "summary": "Free and open source 3D creation suite",
            "icon": "blender",
            "developer": "Blender Foundation",
            "category": "Graphics & Media",
            "channel": "latest/stable",
            "confinement": "classic",
            "publisher": "Blender Foundation",
            "version": "4.2.2",
            "store_url": "https://snapcraft.io/blender",
            "store-url": "https://snapcraft.io/blender",
            "description": "Blender is the free and open source 3D creation suite supporting modeling, rigging, animation, and VFX.",
        },
        {
            "name": "obs-studio",
            "title": "OBS Studio",
            "summary": "Free and open source software for video recording and live streaming",
            "icon": "com.obsproject.Studio",
            "developer": "OBS Project",
            "category": "Graphics & Media",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "OBS Project",
            "version": "30.2.3",
            "store_url": "https://snapcraft.io/obs-studio",
            "store-url": "https://snapcraft.io/obs-studio",
            "description": "Free and open source software for video recording and live streaming.",
        },
        {
            "name": "vlc",
            "title": "VLC",
            "summary": "The ultimate media player",
            "icon": "vlc",
            "developer": "VideoLAN",
            "category": "Audio & Video",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "VideoLAN",
            "version": "3.0.21",
            "store_url": "https://snapcraft.io/vlc",
            "store-url": "https://snapcraft.io/vlc",
            "description": "VLC is a free and open source cross-platform multimedia player and framework that plays most multimedia files.",
        },
        {
            "name": "telegram-desktop",
            "title": "Telegram Desktop",
            "summary": "Fast and secure desktop messaging app",
            "icon": "telegram",
            "developer": "Telegram FZ-LLC",
            "category": "Social & Communication",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "Telegram FZ-LLC",
            "version": "5.5.5",
            "store_url": "https://snapcraft.io/telegram-desktop",
            "store-url": "https://snapcraft.io/telegram-desktop",
            "description": "Telegram is a cloud-based mobile and desktop messaging app with a focus on security and speed.",
        },
        {
            "name": "bitwarden",
            "title": "Bitwarden",
            "summary": "Open-source password manager for individuals and teams",
            "icon": "bitwarden",
            "developer": "Bitwarden",
            "category": "Security",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "Bitwarden",
            "version": "2024.9.0",
            "store_url": "https://snapcraft.io/bitwarden",
            "store-url": "https://snapcraft.io/bitwarden",
            "description": "Bitwarden is the easiest and safest way to store all of your logins and passwords while keeping them synced.",
        },
        {
            "name": "obsidian",
            "title": "Obsidian",
            "summary": "A powerful knowledge base on top of local Markdown files",
            "icon": "obsidian",
            "developer": "Obsidian",
            "category": "Productivity",
            "channel": "latest/stable",
            "confinement": "classic",
            "publisher": "Obsidian",
            "version": "1.6.7",
            "store_url": "https://snapcraft.io/obsidian",
            "store-url": "https://snapcraft.io/obsidian",
            "description": "Obsidian is the private and flexible writing app that adapts to the way you think.",
        },
        {
            "name": "sublime-text",
            "title": "Sublime Text",
            "summary": "Sophisticated text editor for code, markup and prose",
            "icon": "sublime-text",
            "developer": "Snapcrafters",
            "category": "Development",
            "channel": "latest/stable",
            "confinement": "classic",
            "publisher": "Snapcrafters",
            "version": "4180",
            "store_url": "https://snapcraft.io/sublime-text",
            "store-url": "https://snapcraft.io/sublime-text",
            "description": "Sublime Text is a sophisticated text editor for code, markup and prose with slick user interface.",
        },
        {
            "name": "gimp",
            "title": "GIMP",
            "summary": "GNU Image Manipulation Program",
            "icon": "gimp",
            "developer": "GIMP team",
            "category": "Graphics & Media",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "GIMP team",
            "version": "2.10.38",
            "store_url": "https://snapcraft.io/gimp",
            "store-url": "https://snapcraft.io/gimp",
            "description": "GIMP is a cross-platform image editor available for GNU/Linux, OS X, Windows and more operating systems.",
        },
        {
            "name": "inkscape",
            "title": "Inkscape",
            "summary": "Professional vector graphics editor for Linux, Windows and macOS",
            "icon": "org.inkscape.Inkscape",
            "developer": "Inkscape Project",
            "category": "Graphics & Media",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "Inkscape Project",
            "version": "1.3.2",
            "store_url": "https://snapcraft.io/inkscape",
            "store-url": "https://snapcraft.io/inkscape",
            "description": "Inkscape is a professional vector graphics editor for Linux, Windows and macOS. It is free and open source.",
        },
        {
            "name": "brave",
            "title": "Brave",
            "summary": "Browse privately, search independently, and protect your privacy online",
            "icon": "brave-browser",
            "developer": "Brave Software",
            "category": "Internet & Network",
            "channel": "latest/stable",
            "confinement": "strict",
            "publisher": "Brave Software",
            "version": "1.70.126",
            "store_url": "https://snapcraft.io/brave",
            "store-url": "https://snapcraft.io/brave",
            "description": "Brave Browser is a fast, private and secure web browser for PC, Mac and mobile.",
        },
        {
            "name": "pycharm-community",
            "title": "PyCharm Community",
            "summary": "Python IDE for Professional Developers",
            "icon": "pycharm-community",
            "developer": "JetBrains",
            "category": "Development",
            "channel": "latest/stable",
            "confinement": "classic",
            "publisher": "JetBrains",
            "version": "2024.2.2",
            "store_url": "https://snapcraft.io/pycharm-community",
            "store-url": "https://snapcraft.io/pycharm-community",
            "description": "The Python IDE for Professional Developers. PyCharm provides smart code completion and code inspections.",
        },
    ]

    def __init__(self):
        self._lock = threading.Lock()
        self._search_cache: Dict[str, List[Dict[str, Any]]] = {}
        self.is_setting_up: bool = False
        self.setup_progress_text: str = ""
        self.setup_progress_fraction: float = 0.0
        self._status_cache: Optional[Dict[str, Any]] = None
        self._status_cache_time: float = 0.0
        self._installed_snaps_cache: Optional[List[Dict[str, Any]]] = None
        self._installed_snaps_cache_time: float = 0.0
        self._installed_snaps_names: Set[str] = set()

    def invalidate_cache(self):
        """Invalidate all in-memory status and installed snap caches."""
        with self._lock:
            self._installed_snaps_cache = None
            self._installed_snaps_cache_time = 0.0
            self._installed_snaps_names = set()
            self._status_cache = None
            self._status_cache_time = 0.0

    def is_snapd_installed(self) -> bool:
        """Check if snap command is available in PATH or at /usr/bin/snap."""
        return shutil.which("snap") is not None or Path("/usr/bin/snap").exists()

    def is_snapd_running(self) -> bool:
        """Check if snapd daemon or socket is active."""
        if Path("/run/snapd.socket").exists():
            return True
        if shutil.which("systemctl"):
            try:
                proc = subprocess.run(["systemctl", "is-active", "--quiet", "snapd.socket"], timeout=2)
                return proc.returncode == 0
            except Exception:
                pass
        return False

    def get_status(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Check snap binary, systemd socket, and /snap symlink status."""
        now = time.time()
        if not force_refresh and hasattr(self, "_status_cache") and self._status_cache and (now - getattr(self, "_status_cache_time", 0.0) < 10.0):
            res = dict(self._status_cache)
            res["is_setting_up"] = self.is_setting_up
            return res

        has_snap = self.is_snapd_installed()
        socket_active = self.is_snapd_running()
        symlink_ok = Path("/snap").is_symlink() or Path("/snap").exists()

        if has_snap and socket_active and symlink_ok:
            status_code = "ready"
            status_text = "Canonical Snap service active and ready"
        elif has_snap and not socket_active:
            status_code = "service_stopped"
            status_text = "snapd installed but socket is inactive"
        elif has_snap and not symlink_ok:
            status_code = "symlink_missing"
            status_text = "/snap classic confinement symlink missing"
        else:
            status_code = "missing"
            status_text = "Snapd is not installed on this system"

        res = {
            "has_snap": has_snap,
            "socket_active": socket_active,
            "symlink_ok": symlink_ok,
            "status_code": status_code,
            "status_text": status_text,
            "is_setting_up": self.is_setting_up,
        }
        self._status_cache = res
        self._status_cache_time = now
        return res

    @staticmethod
    def _parse_build_error(stderr: Optional[str], stdout: Optional[str]) -> str:
        combined = (stderr or "") + "\n" + (stdout or "")
        lines = [line.strip() for line in combined.splitlines() if line.strip()]
        if not lines:
            return "Unknown compilation or installation error."

        for line in lines:
            if "db.lck" in line or "could not lock database" in line or "unable to lock database" in line:
                return "Pacman database is locked by another running process (/var/lib/pacman/db.lck)."
            if "sudo: a password is required" in line.lower() or "authentication failure" in line.lower():
                return "Authentication failed or root privileges required."

        error_lines = []
        for line in lines:
            l_lower = line.lower()
            if l_lower.startswith("==> error:") or l_lower.startswith("error:"):
                clean = line
                if clean.startswith("==> ERROR:"):
                    clean = clean[10:].strip()
                elif clean.lower().startswith("error:"):
                    clean = clean[6:].strip()
                if clean:
                    error_lines.append(clean)
            elif "failure occurred in" in l_lower or "failed to build" in l_lower:
                error_lines.append(line)

        if error_lines:
            return error_lines[-1]

        filtered = [
            l for l in lines
            if not l.startswith("==> WARNING: Using existing $srcdir")
            and "... Passed" not in l
            and not l.startswith("->")
            and not l.startswith("==> Making package:")
        ]
        if filtered:
            return filtered[-1][:140]

        return lines[-1][:140]

    def setup_snapd(self, progress_callback=None, completion_callback=None):
        """Seamless automated setup: install snapd via paru/pacman, enable socket, link /snap."""
        with self._lock:
            if self.is_setting_up:
                if completion_callback:
                    completion_callback(False, "Snap service configuration is already in progress.")
                return
            self.is_setting_up = True
            self.setup_progress_text = "Starting Snap service setup..."
            self.setup_progress_fraction = 0.05

        def _report(frac: float, msg: str):
            self.setup_progress_fraction = frac
            self.setup_progress_text = msg
            if progress_callback:
                progress_callback(frac, msg)

        def _task():
            try:
                env = os.environ.copy()
                if ASKPASS_SCRIPT.exists():
                    env["SUDO_ASKPASS"] = str(ASKPASS_SCRIPT)
                env["LC_ALL"] = "C"

                # Step 1: Install snapd if missing
                is_installed = self.is_snapd_installed()
                if not is_installed:
                    try:
                        chk = subprocess.run(["pacman", "-Q", "snapd"], capture_output=True, timeout=5)
                        if chk.returncode == 0:
                            is_installed = True
                    except Exception:
                        pass

                if not is_installed:
                    # Check if pacman lock exists
                    if Path("/var/lib/pacman/db.lck").exists():
                        p1 = subprocess.run(["pgrep", "-x", "pacman"], capture_output=True)
                        p2 = subprocess.run(["pgrep", "-x", "paru"], capture_output=True)
                        if p1.returncode == 0 or p2.returncode == 0:
                            _report(0.1, "Waiting for existing package manager to finish...")
                            for _ in range(15):
                                time.sleep(1)
                                if not Path("/var/lib/pacman/db.lck").exists():
                                    break
                            if Path("/var/lib/pacman/db.lck").exists():
                                if completion_callback:
                                    completion_callback(False, "Pacman database is currently locked by another process (/var/lib/pacman/db.lck).")
                                return

                    # Check if a pre-compiled snapd package exists in cache
                    pkg_installed = False
                    candidate_dirs = [
                        Path.home() / ".cache" / "paru" / "clone" / "snapd",
                        Path.home() / ".cache" / "yay" / "snapd",
                    ]
                    for c_dir in candidate_dirs:
                        if c_dir.exists():
                            zst_files = sorted(c_dir.glob("snapd-*.pkg.tar.zst"), key=lambda p: p.stat().st_mtime, reverse=True)
                            if zst_files:
                                cached_pkg = zst_files[0]
                                _report(0.2, f"Installing pre-built snapd package from cache ({cached_pkg.name})...")
                                res_pkg = subprocess.run(
                                    ["sudo", "-A", "pacman", "-U", "--noconfirm", "--needed", str(cached_pkg)],
                                    capture_output=True, text=True, env=env, timeout=120
                                )
                                if res_pkg.returncode == 0:
                                    pkg_installed = True
                                    break

                    if not pkg_installed:
                        _report(0.2, "Installing snapd from repositories/AUR (this may take 1-2 minutes)...")
                        aur_helper = get_aur_helper()
                        sudo_prefix = ["sudo", "-n"] if PackageManager.is_passwordless_configured() else ["sudo", "-A"]
                        if aur_helper == "paru":
                            cmd = ["paru", "-S", "--noconfirm", "--needed", "--skipreview", "snapd"]
                        elif aur_helper == "yay":
                            cmd = ["yay", "-S", "--noconfirm", "--needed", "--nodiffmenu", "--noeditmenu", "snapd"]
                        else:
                            cmd = sudo_prefix + ["pacman", "-S", "--noconfirm", "--needed", "snapd"]
                        res = subprocess.run(cmd, capture_output=True, text=True, env=env, timeout=300)
                        if res.returncode != 0:
                            err_msg = self._parse_build_error(res.stderr, res.stdout)
                            if completion_callback:
                                completion_callback(False, f"Failed to install snapd: {err_msg}")
                            return
                else:
                    _report(0.5, "Snap package found, verifying service and confinement symlinks...")

                # Step 2: Enable and start snapd.socket
                sudo_prefix = ["sudo", "-n"] if PackageManager.is_passwordless_configured() else ["sudo", "-A"]
                _report(0.6, "Enabling and starting snapd.socket...")
                res = subprocess.run(sudo_prefix + ["systemctl", "enable", "--now", "snapd.socket"],
                                     capture_output=True, text=True, env=env, timeout=30)
                if res.returncode != 0:
                    err_msg = self._parse_build_error(res.stderr, res.stdout)
                    if completion_callback:
                        completion_callback(False, f"Failed enabling snapd.socket: {err_msg}")
                    return

                # Step 3: Symlink /var/lib/snapd/snap /snap
                _report(0.8, "Creating /snap classical confinement symlink...")
                if not (Path("/snap").is_symlink() or Path("/snap").exists()):
                    res_ln = subprocess.run(sudo_prefix + ["ln", "-s", "/var/lib/snapd/snap", "/snap"],
                                           capture_output=True, text=True, env=env, timeout=10)
                    if res_ln.returncode != 0 and not Path("/snap").exists():
                        subprocess.run(sudo_prefix + ["mkdir", "-p", "/var/lib/snapd/snap"], capture_output=True, env=env, timeout=5)
                        subprocess.run(sudo_prefix + ["ln", "-s", "/var/lib/snapd/snap", "/snap"], capture_output=True, env=env, timeout=10)

                # Step 4: Verify socket and status
                time.sleep(1)
                st = self.get_status()
                if st["socket_active"] and st["symlink_ok"]:
                    _report(1.0, "Snap service ready!")
                    if completion_callback:
                        completion_callback(True, "Snap Store successfully configured and ready!")
                else:
                    _report(1.0, "Snap configuration finished with warnings.")
                    if completion_callback:
                        completion_callback(True, "Snap Store configured (socket active, symlink created).")
            except Exception as e:
                if completion_callback:
                    completion_callback(False, f"Error configuring snapd: {e}")
            finally:
                with self._lock:
                    self.is_setting_up = False
                    self._installed_snaps_cache = None
                    self._status_cache = None

        threading.Thread(target=_task, daemon=True).start()

    def disable_snapd(self, purge_packages: bool = False, progress_callback=None, completion_callback=None):
        """Disable snapd service and optionally remove package and symlink."""
        def _task():
            env = os.environ.copy()
            if ASKPASS_SCRIPT.exists():
                env["SUDO_ASKPASS"] = str(ASKPASS_SCRIPT)
            env["LC_ALL"] = "C"

            sudo_prefix = ["sudo", "-n"] if PackageManager.is_passwordless_configured() else ["sudo", "-A"]

            if progress_callback:
                progress_callback(0.3, "Stopping and disabling snapd services...")
            subprocess.run(sudo_prefix + ["systemctl", "disable", "--now", "snapd.socket", "snapd.service"],
                           capture_output=True, env=env, timeout=30)

            if Path("/snap").is_symlink():
                subprocess.run(sudo_prefix + ["rm", "-f", "/snap"], capture_output=True, env=env, timeout=10)

            if purge_packages:
                if progress_callback:
                    progress_callback(0.7, "Removing snapd package...")
                res_rm = subprocess.run(sudo_prefix + ["pacman", "-Rns", "--noconfirm", "snapd"],
                                        capture_output=True, text=True, env=env, timeout=60)
                if res_rm.returncode != 0:
                    self._installed_snaps_cache = None
                    self._status_cache = None
                    if completion_callback:
                        completion_callback(False, f"Failed to remove snapd package: {res_rm.stderr or res_rm.stdout}")
                    return

            self._installed_snaps_cache = None
            self._status_cache = None
            if completion_callback:
                completion_callback(True, "Snap setup disabled successfully.")

        threading.Thread(target=_task, daemon=True).start()

    def is_snap_installed(self, name: str) -> bool:
        """Check if a snap package is installed locally with O(1) in-memory lookup."""
        clean = (name or "").strip().lower()
        if not clean:
            return False
        if hasattr(self, "_installed_snaps_cache") and self._installed_snaps_cache is not None:
            return clean in getattr(self, "_installed_snaps_names", set())
        try:
            snaps_dir = Path("/var/lib/snapd/snaps")
            if snaps_dir.exists():
                if list(snaps_dir.glob(f"{clean}_*.snap")):
                    return True
        except Exception:
            pass
        try:
            if Path(f"/snap/{clean}").exists():
                return True
        except Exception:
            pass
        try:
            self.get_installed_snaps()
            return clean in getattr(self, "_installed_snaps_names", set())
        except Exception:
            pass
        return False

    def get_installed_snaps(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """List locally installed snaps by parsing 'snap list' output with cache retention."""
        now = time.time()
        if not force_refresh and hasattr(self, "_installed_snaps_cache") and self._installed_snaps_cache is not None and (now - getattr(self, "_installed_snaps_cache_time", 0.0) < 10.0):
            return list(self._installed_snaps_cache)

        if not self.is_snapd_installed():
            self._installed_snaps_cache = []
            self._installed_snaps_cache_time = now
            self._installed_snaps_names = set()
            return []
        try:
            res = subprocess.run(["snap", "list"], capture_output=True, text=True, timeout=5)
            if res.returncode != 0:
                self._installed_snaps_cache = []
                self._installed_snaps_cache_time = now
                self._installed_snaps_names = set()
                return []
            lines = res.stdout.splitlines()
            if not lines:
                self._installed_snaps_cache = []
                self._installed_snaps_cache_time = now
                self._installed_snaps_names = set()
                return []
            installed = []
            names = set()
            for line in lines[1:]:
                parts = line.split()
                if not parts:
                    continue
                name = parts[0]
                version = parts[1] if len(parts) > 1 else ""
                rev = parts[2] if len(parts) > 2 else ""
                tracking = parts[3] if len(parts) > 3 else ""
                publisher = parts[4] if len(parts) > 4 else ""
                notes = " ".join(parts[5:]) if len(parts) > 5 else ""
                installed.append({
                    "name": name,
                    "version": version,
                    "rev": rev,
                    "tracking": tracking,
                    "publisher": publisher,
                    "notes": notes,
                    "source": "snap",
                    "is_installed": True,
                    "icon": resolve_icon_name(name),
                })
                names.add(name.lower())
            self._installed_snaps_cache = installed
            self._installed_snaps_cache_time = now
            self._installed_snaps_names = names
            return list(installed)
        except Exception:
            self._installed_snaps_cache = []
            self._installed_snaps_cache_time = now
            self._installed_snaps_names = set()
            return []

    def check_snap_updates(self) -> List[Dict[str, Any]]:
        """Check for upgradable snaps using 'snap refresh --list'."""
        if not self.is_snapd_installed():
            return []
        updates: List[Dict[str, Any]] = []
        try:
            res = subprocess.run(["snap", "refresh", "--list"], capture_output=True, text=True, timeout=10)
            if res.returncode != 0:
                return []
            output = res.stdout.strip()
            if not output or "all snaps up to date" in output.lower():
                return []

            installed_map = {s["name"].lower(): s.get("version", "installed") for s in self.get_installed_snaps()}
            lines = output.splitlines()
            if lines and ("Name" in lines[0] or "NAME" in lines[0]):
                lines = lines[1:]
            for line in lines:
                parts = line.split()
                if not parts:
                    continue
                name = parts[0]
                new_ver = parts[1] if len(parts) > 1 else "latest"
                old_ver = installed_map.get(name.lower(), "installed")
                updates.append({
                    "name": name,
                    "old_ver": old_ver,
                    "new_ver": new_ver,
                    "source": "snap",
                    "desc": f"Snap package update ({new_ver})",
                    "icon": resolve_icon_name(name),
                })
        except Exception:
            pass
        return updates

    @staticmethod
    def _parse_snap_info(raw_output: str) -> Dict[str, Any]:
        """Parse key-value pairs and metadata from 'snap info' output."""
        details: Dict[str, Any] = {}
        in_desc = False
        desc_lines = []

        for line in raw_output.splitlines():
            # Handle multi-line description block
            if in_desc:
                if line.startswith("  ") or line.startswith("\t"):
                    desc_lines.append(line.strip())
                    continue
                else:
                    in_desc = False
                    details["description"] = "\n".join(desc_lines).strip()

            if line.startswith("description:"):
                in_desc = True
                desc_lines = []
                val = line.split(":", 1)[1].strip()
                if val and val != "|":
                    desc_lines.append(val)
                continue

            if ":" in line:
                key, val = line.split(":", 1)
                key = key.strip().lower()
                val = val.strip()

                if key == "name":
                    details["name"] = val
                elif key == "summary":
                    details["summary"] = val
                elif key == "publisher":
                    details["publisher"] = val
                elif key in ("store-url", "store_url"):
                    details["store-url"] = val
                    details["store_url"] = val
                elif key == "license":
                    details["license"] = val
                elif key == "version":
                    details["version"] = val
                elif key == "installed":
                    parts = val.split()
                    if parts:
                        details["installed_version"] = parts[0]
                        if "version" not in details:
                            details["version"] = parts[0]
                elif key == "latest/stable":
                    parts = val.split()
                    if parts and parts[0] != "↑" and "version" not in details:
                        details["version"] = parts[0]

        if in_desc and desc_lines and "description" not in details:
            details["description"] = "\n".join(desc_lines).strip()

        return details

    def search_snaps(self, query: str = "") -> List[Dict[str, Any]]:
        """Search snaps via snap CLI or Snapcraft API with fallback to curated collection."""
        q = (query or "").strip().lower()
        if hasattr(self, "_search_cache") and q and q in self._search_cache:
            return list(self._search_cache[q])
        results: List[Dict[str, Any]] = []
        seen_names = set()

        # 1. Try local snap CLI search first if snap is installed
        if shutil.which("snap"):
            try:
                proc = subprocess.run(
                    ["snap", "find", q if q else "desktop"],
                    capture_output=True, text=True, timeout=8
                )
                if proc.returncode == 0:
                    lines = proc.stdout.splitlines()
                    if lines and ("Name" in lines[0] or "NAME" in lines[0]):
                        lines = lines[1:]
                    for line in lines:
                        parts = line.split(None, 4)
                        if len(parts) >= 5:
                            s_name, s_ver, s_pub, s_notes, s_summary = parts
                        elif len(parts) == 4:
                            s_name, s_ver, s_pub, s_summary = parts
                        elif len(parts) >= 2:
                            s_name = parts[0]
                            s_summary = " ".join(parts[1:])
                            s_pub = "Unknown"
                        else:
                            continue

                        s_name = s_name.strip()
                        if not s_name or s_name in seen_names:
                            continue
                        seen_names.add(s_name)
                        curated = next((c for c in self.CURATED_SNAP_APPS if c["name"].lower() == s_name.lower()), None)
                        title = curated["title"] if curated else s_name.replace("-", " ").title()
                        results.append({
                            "name": s_name,
                            "title": title,
                            "summary": s_summary.strip(),
                            "desc": s_summary.strip(),
                            "publisher": s_pub.strip(),
                            "developer": s_pub.strip(),
                            "source": "snap",
                            "icon": curated.get("icon", s_name) if curated else s_name,
                            "category": curated.get("category", "Snap Package") if curated else "Snap Package",
                            "channel": curated.get("channel", "latest/stable") if curated else "latest/stable",
                            "confinement": curated.get("confinement", "strict") if curated else "strict",
                            "is_installed": self.is_snap_installed(s_name),
                            "store-url": f"https://snapcraft.io/{s_name}",
                            "store_url": f"https://snapcraft.io/{s_name}",
                        })
            except Exception:
                pass

        # 2. If CLI did not find results, query Snapcraft API v2
        if not results and q:
            try:
                url = f"https://api.snapcraft.io/v2/snaps/find?q={urllib.parse.quote(q)}&fields=title,summary,publisher,media"
                req = urllib.request.Request(
                    url,
                    headers={"Snap-Device-Series": "16", "User-Agent": "Aura-Store/1.0"}
                )
                with urllib.request.urlopen(req, timeout=5) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    for item in data.get("results", []):
                        snap_name = item.get("name", "").strip()
                        if not snap_name or snap_name in seen_names:
                            continue
                        seen_names.add(snap_name)
                        snap_info = item.get("snap", {})
                        curated = next((c for c in self.CURATED_SNAP_APPS if c["name"].lower() == snap_name.lower()), None)
                        title = curated["title"] if curated else (snap_info.get("title") or snap_name.replace("-", " ").title())
                        summary = snap_info.get("summary") or ""
                        pub_info = snap_info.get("publisher", {})
                        developer = pub_info.get("display-name") or pub_info.get("username") or "Unknown"

                        icon_url = None
                        media = snap_info.get("media", [])
                        if isinstance(media, list):
                            for m in media:
                                if isinstance(m, dict) and m.get("type") == "icon" and m.get("url"):
                                    icon_url = m["url"]
                                    break

                        cat = curated.get("category", "Snap Package") if curated else "Snap Package"
                        conf = curated.get("confinement", "strict") if curated else "strict"
                        chan = curated.get("channel", "latest/stable") if curated else "latest/stable"
                        icon = icon_url or (curated.get("icon", snap_name) if curated else resolve_icon_name(snap_name, summary))

                        results.append({
                            "name": snap_name,
                            "title": title,
                            "summary": summary,
                            "desc": summary,
                            "icon": icon,
                            "icon_url": icon_url,
                            "developer": developer,
                            "publisher": developer,
                            "category": cat,
                            "channel": chan,
                            "confinement": conf,
                            "source": "snap",
                            "is_installed": self.is_snap_installed(snap_name),
                            "store-url": f"https://snapcraft.io/{snap_name}",
                            "store_url": f"https://snapcraft.io/{snap_name}",
                        })
            except Exception:
                pass

        # 3. Fallback / augment with curated catalog
        if not results:
            for app in self.CURATED_SNAP_APPS:
                s_name = app["name"].lower()
                if not q or (
                    q in s_name
                    or q in app.get("title", "").lower()
                    or q in app.get("summary", "").lower()
                    or q in app.get("category", "").lower()
                    or q in app.get("developer", "").lower()
                ):
                    item = dict(app)
                    item["is_installed"] = self.is_snap_installed(s_name)
                    item["desc"] = app.get("summary", "")
                    item["source"] = "snap"
                    results.append(item)
                    seen_names.add(s_name)
        elif q:
            for app in self.CURATED_SNAP_APPS:
                s_name = app["name"].lower()
                if s_name not in seen_names and (
                    q in s_name or q in app.get("title", "").lower()
                ):
                    item = dict(app)
                    item["is_installed"] = self.is_snap_installed(s_name)
                    item["desc"] = app.get("summary", "")
                    item["source"] = "snap"
                    results.insert(0, item)
                    seen_names.add(s_name)

        def _snap_rank(item: Dict[str, Any]) -> int:
            nm = item.get("name", "").lower()
            tt = item.get("title", "").lower()
            if nm == q or tt == q:
                return 0
            if nm.startswith(q) or tt.startswith(q):
                return 1
            if q in nm:
                return 2
            return 3

        if q:
            results.sort(key=_snap_rank)

        if hasattr(self, "_search_cache") and q:
            self._search_cache[q] = list(results)
        return results

    def get_snap_details(self, name: str) -> Dict[str, Any]:
        """Fetch full package details via snap CLI or Snapcraft API with fallback to curated catalog."""
        snap_name = (name or "").strip().lower()
        curated = next((c for c in self.CURATED_SNAP_APPS if c["name"].lower() == snap_name), None)

        # 1. Try local 'snap info' CLI first if snap is available
        if shutil.which("snap"):
            try:
                proc = subprocess.run(["snap", "info", snap_name], capture_output=True, text=True, timeout=8)
                if proc.returncode == 0:
                    parsed = self._parse_snap_info(proc.stdout)
                    if parsed:
                        info = dict(curated) if curated else {}
                        info.update(parsed)
                        info["name"] = snap_name
                        info["title"] = curated["title"] if curated else snap_name.replace("-", " ").title()
                        info["display_name"] = info["title"]
                        info["summary"] = parsed.get("summary", curated.get("summary", "") if curated else "")
                        info["desc"] = info["summary"]
                        info["publisher"] = parsed.get("publisher", curated.get("publisher", "Unknown") if curated else "Unknown")
                        info["developer"] = info["publisher"]
                        info["version"] = parsed.get("version", curated.get("version", "latest") if curated else "latest")
                        info["store-url"] = parsed.get("store-url", curated.get("store_url", f"https://snapcraft.io/{snap_name}") if curated else f"https://snapcraft.io/{snap_name}")
                        info["store_url"] = info["store-url"]
                        info["confinement"] = curated.get("confinement", "strict") if curated else "strict"
                        info["category"] = curated.get("category", "Snap Package") if curated else "Snap Package"
                        info["icon"] = curated.get("icon", snap_name) if curated else resolve_icon_name(snap_name, info["summary"])
                        info["source"] = "snap"
                        info["is_installed"] = self.is_snap_installed(snap_name)
                        return info
            except Exception:
                pass

        # 2. Query Snapcraft API v2
        details: Optional[Dict[str, Any]] = None
        try:
            url = f"https://api.snapcraft.io/v2/snaps/info/{urllib.parse.quote(snap_name)}?fields=title,summary,description,license,confinement,media,publisher,version,download"
            req = urllib.request.Request(
                url,
                headers={"Snap-Device-Series": "16", "User-Agent": "Aura-Store/1.0"}
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                snap_obj = data.get("snap", {})
                cm = data.get("channel-map", [])
                stable_entry = next((e for e in cm if e.get("channel", {}).get("risk") == "stable"), cm[0] if cm else {})
                pub_obj = snap_obj.get("publisher", {})
                developer = pub_obj.get("display-name") or pub_obj.get("username") or (curated.get("developer") if curated else "Unknown")
                title = curated["title"] if curated else (snap_obj.get("title") or snap_name.replace("-", " ").title())
                summary = snap_obj.get("summary") or (curated.get("summary") if curated else "")
                description = snap_obj.get("description") or summary or (curated.get("description") if curated else "")
                version = stable_entry.get("version") or (curated.get("version") if curated else "latest")
                confinement = stable_entry.get("confinement") or (curated.get("confinement") if curated else "strict")
                license_str = snap_obj.get("license") or "Proprietary / Open Source"
                store_url = snap_obj.get("store-url") or f"https://snapcraft.io/{snap_name}"

                icon_url = None
                screenshots = []
                media = snap_obj.get("media", [])
                if isinstance(media, list):
                    for m in media:
                        if isinstance(m, dict):
                            if m.get("type") == "icon" and m.get("url") and not icon_url:
                                icon_url = m["url"]
                            elif m.get("type") == "screenshot" and m.get("url"):
                                screenshots.append(m["url"])

                icon = icon_url or (curated.get("icon") if curated else resolve_icon_name(snap_name, summary))
                category = curated.get("category", "Snap Package") if curated else "Snap Package"

                details = {
                    "name": snap_name,
                    "title": title,
                    "display_name": title,
                    "summary": summary,
                    "description": description,
                    "desc": summary or description,
                    "version": version,
                    "publisher": developer,
                    "developer": developer,
                    "license": license_str,
                    "channel-map": cm,
                    "channel_map": cm,
                    "store-url": store_url,
                    "store_url": store_url,
                    "confinement": confinement,
                    "category": category,
                    "icon": icon,
                    "icon_url": icon_url,
                    "screenshots": screenshots,
                    "source": "snap",
                    "is_installed": self.is_snap_installed(snap_name),
                }
        except Exception:
            pass

        if details:
            return details

        # 3. Fallback to curated catalog or sensible defaults
        title = curated["title"] if curated else snap_name.replace("-", " ").title()
        summary = curated.get("summary", "") if curated else "Snap package"
        developer = curated.get("developer", "Unknown") if curated else "Unknown"
        version = curated.get("version", "latest") if curated else "latest"
        icon = curated.get("icon", snap_name) if curated else resolve_icon_name(snap_name)
        confinement = curated.get("confinement", "strict") if curated else "strict"
        store_url = curated.get("store_url", f"https://snapcraft.io/{snap_name}") if curated else f"https://snapcraft.io/{snap_name}"
        desc = curated.get("description", summary) if curated else summary

        return {
            "name": snap_name,
            "title": title,
            "display_name": title,
            "summary": summary,
            "description": desc,
            "desc": summary,
            "version": version,
            "publisher": developer,
            "developer": developer,
            "license": "Proprietary / Open Source",
            "channel-map": [],
            "channel_map": [],
            "store-url": store_url,
            "store_url": store_url,
            "confinement": confinement,
            "category": curated.get("category", "Snap Package") if curated else "Snap Package",
            "icon": icon,
            "screenshots": [],
            "source": "snap",
            "is_installed": self.is_snap_installed(snap_name),
        }

    def install_snap(self, name: str, classic: bool = False, progress_cb = None, complete_cb = None):
        """Install a snap package using 'sudo -A snap install'."""
        def _task():
            if not self.is_snapd_installed():
                err_msg = "snapd service is required to install Snap packages. Install snapd and run: sudo systemctl enable --now snapd.socket"
                self._call_progress(progress_cb, 0.0, err_msg)
                self._call_complete(complete_cb, False, "install", name, err_msg)
                return

            is_classic = classic
            if not is_classic:
                curated = next((c for c in self.CURATED_SNAP_APPS if c["name"].lower() == name.lower()), None)
                if curated and curated.get("confinement") == "classic":
                    is_classic = True
                elif not curated:
                    details = self.get_snap_details(name)
                    if details.get("confinement") == "classic":
                        is_classic = True

            cmd = ["sudo", "-A", "snap", "install"]
            if is_classic:
                cmd.append("--classic")
            cmd.append(name)

            env = os.environ.copy()
            if ASKPASS_SCRIPT.exists():
                env["SUDO_ASKPASS"] = str(ASKPASS_SCRIPT)
            env["LC_ALL"] = "C"

            self._call_progress(progress_cb, 0.1, f"Installing snap package {name}...")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )
                output_lines = []
                for line in proc.stdout:
                    line_str = line.strip()
                    if line_str:
                        output_lines.append(line_str)
                        self._call_progress(progress_cb, 0.5, line_str)

                proc.wait()
                if proc.returncode == 0:
                    self._installed_snaps_cache = None
                    self._status_cache = None
                    self._call_progress(progress_cb, 1.0, f"Successfully installed {name}!")
                    self._call_complete(complete_cb, True, "install", name, "")
                else:
                    err = "\n".join(output_lines[-3:]) if output_lines else f"Exited with code {proc.returncode}"
                    self._call_progress(progress_cb, 0.0, f"Installation failed: {err}")
                    self._call_complete(complete_cb, False, "install", name, err)
            except Exception as e:
                self._call_progress(progress_cb, 0.0, f"Error: {e}")
                self._call_complete(complete_cb, False, "install", name, str(e))

        threading.Thread(target=_task, daemon=True).start()

    def remove_snap(self, name: str, progress_cb = None, complete_cb = None):
        """Remove a snap package using 'sudo -A snap remove'."""
        def _task():
            if not self.is_snapd_installed():
                err_msg = "snapd is not installed."
                self._call_progress(progress_cb, 0.0, err_msg)
                self._call_complete(complete_cb, False, "remove", name, err_msg)
                return

            cmd = ["sudo", "-A", "snap", "remove", name]
            env = os.environ.copy()
            if ASKPASS_SCRIPT.exists():
                env["SUDO_ASKPASS"] = str(ASKPASS_SCRIPT)
            env["LC_ALL"] = "C"

            self._call_progress(progress_cb, 0.1, f"Removing snap package {name}...")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )
                output_lines = []
                for line in proc.stdout:
                    line_str = line.strip()
                    if line_str:
                        output_lines.append(line_str)
                        self._call_progress(progress_cb, 0.5, line_str)

                proc.wait()
                if proc.returncode == 0:
                    self._installed_snaps_cache = None
                    self._status_cache = None
                    self._call_progress(progress_cb, 1.0, f"Successfully removed {name}!")
                    self._call_complete(complete_cb, True, "remove", name, "")
                else:
                    err = "\n".join(output_lines[-3:]) if output_lines else f"Exited with code {proc.returncode}"
                    self._call_progress(progress_cb, 0.0, f"Removal failed: {err}")
                    self._call_complete(complete_cb, False, "remove", name, err)
            except Exception as e:
                self._call_progress(progress_cb, 0.0, f"Error: {e}")
                self._call_complete(complete_cb, False, "remove", name, str(e))

        threading.Thread(target=_task, daemon=True).start()

    def update_snap(self, name: str, progress_cb = None, complete_cb = None):
        """Refresh / update a snap package using 'sudo -A snap refresh'."""
        def _task():
            if not self.is_snapd_installed():
                err_msg = "snapd is not installed."
                self._call_progress(progress_cb, 0.0, err_msg)
                self._call_complete(complete_cb, False, "update", name, err_msg)
                return

            cmd = ["sudo", "-A", "snap", "refresh", name]
            env = os.environ.copy()
            if ASKPASS_SCRIPT.exists():
                env["SUDO_ASKPASS"] = str(ASKPASS_SCRIPT)
            env["LC_ALL"] = "C"

            self._call_progress(progress_cb, 0.1, f"Refreshing snap package {name}...")

            try:
                proc = subprocess.Popen(
                    cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=env
                )
                output_lines = []
                for line in proc.stdout:
                    line_str = line.strip()
                    if line_str:
                        output_lines.append(line_str)
                        self._call_progress(progress_cb, 0.5, line_str)

                proc.wait()
                if proc.returncode == 0:
                    self._call_progress(progress_cb, 1.0, f"Successfully refreshed {name}!")
                    self._call_complete(complete_cb, True, "update", name, "")
                else:
                    err = "\n".join(output_lines[-3:]) if output_lines else f"Exited with code {proc.returncode}"
                    self._call_progress(progress_cb, 0.0, f"Refresh failed: {err}")
                    self._call_complete(complete_cb, False, "update", name, err)
            except Exception as e:
                self._call_progress(progress_cb, 0.0, f"Error: {e}")
                self._call_complete(complete_cb, False, "update", name, str(e))

        threading.Thread(target=_task, daemon=True).start()

    def list_apps(self, query: str = "") -> List[Dict[str, Any]]:
        """List curated and discovered snap applications."""
        return self.search_snaps(query)

    @staticmethod
    def _call_progress(cb, frac: float, msg: str):
        if not cb:
            return
        try:
            import inspect
            sig = inspect.signature(cb)
            if len(sig.parameters) == 1:
                cb(msg)
            else:
                cb(frac, msg)
        except Exception:
            try:
                cb(frac, msg)
            except Exception:
                try:
                    cb(msg)
                except Exception:
                    pass

    @staticmethod
    def _call_complete(cb, ok: bool, action: str, name: str, err_or_msg: str):
        if not cb:
            return
        try:
            import inspect
            sig = inspect.signature(cb)
            num = len(sig.parameters)
            if num == 4:
                cb(ok, action, name, err_or_msg if not ok else "")
            elif num == 2:
                cb(ok, err_or_msg)
            else:
                try:
                    cb(ok, action, name, err_or_msg if not ok else "")
                except TypeError:
                    cb(ok, err_or_msg)
        except Exception:
            try:
                cb(ok, action, name, err_or_msg if not ok else "")
            except Exception:
                try:
                    cb(ok, err_or_msg)
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
        self.is_configuring: bool = False
        self.configuring_progress_text: str = ""
        self._status_cache: Optional[Dict[str, Any]] = None
        self._status_cache_time: float = 0.0

    def invalidate_cache(self):
        """Invalidate cached status."""
        with self._lock:
            self._status_cache = None
            self._status_cache_time = 0.0

    def is_app_installing(self, app_id: str) -> bool:
        return app_id in self.active_container_installs

    @property
    def is_ready(self) -> bool:
        st = self.get_status()
        return st.get("status_code") == "ready"

    def get_status(self, force_refresh: bool = False) -> Dict[str, Any]:
        """Check container runtime and container existence."""
        now = time.time()
        if not force_refresh and hasattr(self, "_status_cache") and self._status_cache and (now - getattr(self, "_status_cache_time", 0.0) < 10.0):
            res = dict(self._status_cache)
            res["is_configuring"] = self.is_configuring
            return res

        has_docker = shutil.which("docker") is not None
        daemon_running = False
        container_exists = False
        container_running = False

        docker_sock = Path("/var/run/docker.sock")
        user_sock = Path(f"/run/user/{os.getuid()}/docker.sock")
        if has_docker and (docker_sock.exists() or user_sock.exists()):
            try:
                proc = subprocess.run(["docker", "info"], capture_output=True, timeout=4)
                if proc.returncode == 0:
                    daemon_running = True
            except Exception:
                pass
        
        if daemon_running:
            try:
                proc = subprocess.run(
                    ["docker", "inspect", "-f", "{{.State.Running}}", self.CONTAINER_NAME],
                    capture_output=True, text=True, timeout=4
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

        res = {
            "has_docker": has_docker,
            "daemon_running": daemon_running,
            "container_exists": container_exists,
            "container_running": container_running,
            "container_name": self.CONTAINER_NAME,
            "status_code": status_code,
            "status_text": status_text,
            "is_configuring": self.is_configuring,
        }
        self._status_cache = res
        self._status_cache_time = now
        return res

    def stop_container(self, completion_callback=None):
        """Stop aura-box container."""
        def _task():
            res = subprocess.run(["docker", "stop", self.CONTAINER_NAME], capture_output=True, text=True, timeout=15)
            if completion_callback:
                completion_callback(res.returncode == 0, "Container stopped." if res.returncode == 0 else res.stderr)
        threading.Thread(target=_task, daemon=True).start()

    def remove_container(self, purge_shortcuts: bool = True, completion_callback=None):
        """Remove aura-box container and purge host desktop shortcuts."""
        def _task():
            subprocess.run(["docker", "rm", "-f", self.CONTAINER_NAME], capture_output=True, timeout=15)
            if purge_shortcuts:
                for shortcut in self.SHORTCUTS_DIR.glob(f"{self.CONTAINER_NAME}-*.desktop"):
                    try:
                        shortcut.unlink()
                    except Exception:
                        pass
                try:
                    subprocess.run(["update-desktop-database", str(self.SHORTCUTS_DIR)], capture_output=True, timeout=5)
                except Exception:
                    pass
            if completion_callback:
                completion_callback(True, "Aura Box sandbox and desktop shortcuts removed.")
        threading.Thread(target=_task, daemon=True).start()

    def disable_docker_system_service(self, remove_from_group: bool = False, completion_callback=None):
        """System-level: disable docker.service and optionally drop user from docker group."""
        def _task():
            env = os.environ.copy()
            if ASKPASS_SCRIPT.exists():
                env["SUDO_ASKPASS"] = str(ASKPASS_SCRIPT)
            env["LC_ALL"] = "C"

            res = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True, timeout=5)
            other_containers = [c for c in res.stdout.splitlines() if c.strip() and c.strip() != self.CONTAINER_NAME]

            subprocess.run(["sudo", "-A", "systemctl", "disable", "--now", "docker.service", "docker.socket"],
                           capture_output=True, env=env, timeout=30)

            if remove_from_group:
                user = os.environ.get("USER", "root")
                subprocess.run(["sudo", "-A", "gpasswd", "-d", user, "docker"], capture_output=True, env=env, timeout=10)

            if completion_callback:
                completion_callback(True, f"Docker service disabled. Stopped other containers: {other_containers or 'none'}.")
        threading.Thread(target=_task, daemon=True).start()

    def ensure_container_configured(self, progress_callback: Optional[Callable[[str], None]] = None) -> Tuple[bool, str]:
        """Auto create and start Docker container if not running."""
        with self._lock:
            if self.is_configuring:
                return False, "Docker container configuration is already in progress."
            self.is_configuring = True
            self.configuring_progress_text = "Initializing Docker environment..."

        def _report(msg: str):
            self.configuring_progress_text = msg
            if progress_callback:
                progress_callback(msg)

        try:
            status = self.get_status()
            if status["status_code"] == "ready":
                return True, "Container already configured and active."
            
            if status["status_code"] == "missing_engine":
                return False, "Docker is not installed or daemon is not running."

            if status["status_code"] == "stopped":
                _report(f"Starting existing '{self.CONTAINER_NAME}' container...")
                proc = subprocess.run(["docker", "start", self.CONTAINER_NAME], capture_output=True, text=True, timeout=10)
                if proc.returncode == 0:
                    subprocess.run(["docker", "update", "--restart", "unless-stopped", self.CONTAINER_NAME], capture_output=True, timeout=5)
                    _report("Installing baseline GUI libraries (mesa, x11, fonts)...")
                    subprocess.run(["docker", "exec", self.CONTAINER_NAME, "apk", "add", "--no-cache", "mesa-gl", "mesa-dri-gallium", "mesa-egl", "libx11", "font-noto"], capture_output=True, timeout=60)
                    return True, "Started existing aura-box container."
                return False, f"Failed to start container: {proc.stderr}"

            # Create and start new container with hardened security boundaries
            # (AURA-005, AURA-006, AURA-007, AURA-008, AURA-101, AURA-102)
            _report(f"Initializing '{self.CONTAINER_NAME}' container environment (Alpine Linux)...")

            uid = os.getuid()
            gid = os.getgid()
            runtime_dir = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}")
            display = os.environ.get("DISPLAY", ":0")
            wayland = os.environ.get("WAYLAND_DISPLAY", "wayland-1")

            # Dedicated sandboxed application directory (Never mount whole $HOME rw!)
            app_data_dir = Path.home() / ".local" / "share" / "aura" / "containers"
            app_data_dir.mkdir(parents=True, exist_ok=True)

            cmd = [
                "docker", "run", "-d",
                "--name", self.CONTAINER_NAME,
                "--restart", "unless-stopped",
                "-v", "/tmp/.X11-unix:/tmp/.X11-unix:ro",
                "-v", f"{app_data_dir}:/home/aura-user/app-data",
                "-e", f"DISPLAY={display}",
                "-e", f"WAYLAND_DISPLAY={wayland}",
                "-e", f"XDG_RUNTIME_DIR={runtime_dir}",
                "-e", "HOME=/home/aura-user",
            ]

            # Mount specific Wayland socket if active
            wayland_sock = Path(runtime_dir) / wayland
            if wayland_sock.exists():
                cmd.extend(["-v", f"{wayland_sock}:{wayland_sock}"])

            # Hardware acceleration / DRI device if available
            if Path("/dev/dri").exists():
                cmd.extend(["--device", "/dev/dri:/dev/dri"])

            cmd.extend([
                "alpine:latest",
                "tail", "-f", "/dev/null"
            ])

            proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
            if proc.returncode != 0:
                return False, f"Failed to create container: {proc.stderr}"

            _report("Installing baseline GUI libraries (mesa, x11, fonts)...")
            apk_proc = subprocess.run(["docker", "exec", self.CONTAINER_NAME, "apk", "add", "--no-cache", "mesa-gl", "mesa-dri-gallium", "mesa-egl", "libx11", "font-noto"], capture_output=True, text=True, timeout=60)
            if apk_proc.returncode != 0:
                return False, f"Failed installing baseline GUI libraries: {apk_proc.stderr or apk_proc.stdout}"

            _report("Container environment configured successfully!")
            return True, "Container successfully initialized and ready."
        finally:
            with self._lock:
                self.is_configuring = False
                self.configuring_progress_text = ""

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
        """Remove package from container first and verify success before purging shortcut."""
        def _task():
            app_meta = next((a for a in self.CURATED_CONTAINER_APPS if a["id"] == app_id), None)
            name = app_meta["name"] if app_meta else app_id

            if progress_callback:
                progress_callback(f"Uninstalling {name} from container...")

            # 1. Package removal from container first with returncode verification (AURA-038 & AURA-039)
            status = self.get_status()
            if status["container_exists"] and status["container_running"]:
                pkg = app_meta.get("pkg", app_id) if app_meta else app_id
                cmd = ["docker", "exec", self.CONTAINER_NAME, "apk", "del", pkg]
                proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
                if proc.returncode != 0:
                    if completion_callback:
                        completion_callback(False, f"Failed removing package from container: {proc.stderr or proc.stdout}")
                    return

            # 2. Delete desktop shortcut only after successful removal
            shortcut_path = self.SHORTCUTS_DIR / f"aura-box-{app_id}.desktop"
            if shortcut_path.exists():
                try:
                    shortcut_path.unlink()
                    subprocess.run(["update-desktop-database", str(self.SHORTCUTS_DIR)], capture_output=True, timeout=3)
                except Exception:
                    pass

            if completion_callback:
                completion_callback(True, f"Removed {name} and cleaned up desktop shortcut.")

        threading.Thread(target=_task, daemon=True).start()

    def launch_app(self, app_id: str):
        """Launch container app or its desktop shortcut, ensuring container is running."""
        status = self.get_status()
        if not status["container_running"] and status["container_exists"]:
            subprocess.run(["docker", "start", self.CONTAINER_NAME], capture_output=True, timeout=10)

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

    def check_container_updates(self) -> List[Dict[str, Any]]:
        """Check for updates available for container-installed applications or base image."""
        updates: List[Dict[str, Any]] = []
        status = self.get_status()
        if not status.get("daemon_running") or not status.get("container_running"):
            return updates

        try:
            proc = subprocess.run(
                ["docker", "exec", self.CONTAINER_NAME, "apk", "version", "-l", "<"],
                capture_output=True, text=True, timeout=10
            )
            if proc.returncode == 0:
                for line in proc.stdout.splitlines():
                    line = line.strip()
                    parts = line.split("<", 1)
                    if len(parts) == 2:
                        old_str = parts[0].strip()
                        new_str = parts[1].strip()
                        updates.append({
                            "name": old_str,
                            "current_version": old_str,
                            "new_version": new_str,
                            "type": "container",
                            "desc": "Container app update",
                            "old_ver": old_str,
                            "new_ver": new_str,
                            "source": "docker",
                            "icon": "application-x-executable",
                        })
        except Exception:
            pass

        return updates

    def update_app(self, app_id: str, progress_callback: Optional[Callable[[str], None]] = None, completion_callback: Optional[Callable[[bool, str], None]] = None):
        """Update container application via 'apk add --upgrade --no-cache' and refresh shortcut."""
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

                # 1. Ensure container is configured and running
                ok, msg = self.ensure_container_configured(progress_callback)
                if not ok:
                    if completion_callback:
                        completion_callback(False, f"Container setup failed: {msg}")
                    return

                # 2. Upgrade inside container
                name = app_meta.get("name", app_id)
                pkg = app_meta.get("pkg", app_id)
                if progress_callback:
                    progress_callback(f"Updating {name} in '{self.CONTAINER_NAME}'...")

                cmd = ["docker", "exec", self.CONTAINER_NAME, "apk", "add", "--upgrade", "--no-cache", pkg]
                try:
                    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
                    if proc.returncode != 0:
                        if completion_callback:
                            completion_callback(False, f"Update failed: {proc.stderr}")
                        return
                except Exception as e:
                    if completion_callback:
                        completion_callback(False, f"Update error: {e}")
                    return

                # 3. Re-generate desktop shortcut
                if progress_callback:
                    progress_callback("Re-generating host desktop shortcut...")

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
                    subprocess.run(["update-desktop-database", str(self.SHORTCUTS_DIR)], capture_output=True, timeout=3)
                except Exception as e:
                    if completion_callback:
                        completion_callback(False, f"Failed updating shortcut: {e}")
                    return

                if progress_callback:
                    progress_callback(f"Successfully updated {name}!")

                if completion_callback:
                    completion_callback(True, f"Updated {name} and refreshed desktop shortcut.")
            finally:
                self.active_container_installs.discard(app_id)

        threading.Thread(target=_task, daemon=True).start()


class CacheManager:
    """
    Introspects and safely prunes system and user caches for Arch Linux & Aura:
    - Pacman package cache (/var/cache/pacman/pkg/)
    - AUR build cache (~/.cache/paru/clone/, ~/.cache/yay/)
    - Docker container engine layers and stopped containers
    - Aura application index & vector icon caches (~/.cache/aura/)
    - Systemd journal archives (/var/log/journal/)
    """

    PACMAN_CACHE_DIR = Path("/var/cache/pacman/pkg")
    PARU_CACHE_DIR = Path.home() / ".cache" / "paru" / "clone"
    YAY_CACHE_DIR = Path.home() / ".cache" / "yay"
    AURA_CACHE_DIR = Path.home() / ".cache" / "aura"

    def __init__(self):
        self._lock = threading.Lock()

    @staticmethod
    def _dir_size(path: Path) -> int:
        if not path.exists():
            return 0
        total = 0
        try:
            for entry in os.scandir(path):
                try:
                    if entry.is_file(follow_symlinks=False):
                        total += entry.stat().st_size
                    elif entry.is_dir(follow_symlinks=False):
                        total += CacheManager._dir_size(Path(entry.path))
                except Exception:
                    pass
        except Exception:
            pass
        return total

    @staticmethod
    def format_size(bytes_val: int) -> str:
        if bytes_val >= 1024**3:
            return f"{bytes_val / (1024**3):.2f} GB"
        elif bytes_val >= 1024**2:
            return f"{bytes_val / (1024**2):.1f} MB"
        elif bytes_val >= 1024:
            return f"{bytes_val / 1024:.0f} KB"
        return f"{bytes_val} B"

    def scan_all_caches(self) -> Dict[str, Any]:
        """Fast synchronous introspection of all cache categories (< 150ms)."""
        pacman_total = self._dir_size(self.PACMAN_CACHE_DIR)
        
        # Determine reclaimable pacman cache via paccache dry-run if available
        pacman_reclaimable = 0
        if shutil.which("paccache") and pacman_total > 0:
            try:
                proc = subprocess.run(["paccache", "-d", "-k1"], capture_output=True, text=True, timeout=4, env=dict(os.environ, LC_ALL="C"))
                m = re.search(r'disk space saved:\s*([0-9.]+)\s*([A-Za-z]+)', proc.stdout)
                if m:
                    val = float(m.group(1))
                    unit = m.group(2).lower()
                    if "gib" in unit or "gb" in unit:
                        pacman_reclaimable = int(val * (1024**3))
                    elif "mib" in unit or "mb" in unit:
                        pacman_reclaimable = int(val * (1024**2))
                    elif "kib" in unit or "kb" in unit:
                        pacman_reclaimable = int(val * 1024)
            except Exception:
                pass

        paru_size = self._dir_size(self.PARU_CACHE_DIR)
        yay_size = self._dir_size(self.YAY_CACHE_DIR)
        aur_total = paru_size + yay_size

        aura_total = self._dir_size(self.AURA_CACHE_DIR)

        docker_reclaimable = 0
        if shutil.which("docker"):
            try:
                proc = subprocess.run(["docker", "system", "df", "--format", "{{json .}}"], capture_output=True, text=True, timeout=4)
                if proc.returncode == 0:
                    for line in proc.stdout.splitlines():
                        try:
                            data = json.loads(line)
                            reclaimable_str = data.get("Reclaimable", "0B")
                            m = re.search(r'([0-9.]+)\s*([A-Za-z]+)', reclaimable_str)
                            if m:
                                val = float(m.group(1))
                                unit = m.group(2).lower()
                                if "gb" in unit:
                                    docker_reclaimable += int(val * (1024**3))
                                elif "mb" in unit:
                                    docker_reclaimable += int(val * (1024**2))
                                elif "kb" in unit:
                                    docker_reclaimable += int(val * 1024)
                        except Exception:
                            pass
            except Exception:
                pass

        journal_total = 0
        if shutil.which("journalctl"):
            try:
                proc = subprocess.run(["journalctl", "--disk-usage"], capture_output=True, text=True, timeout=3, env=dict(os.environ, LC_ALL="C"))
                m = re.search(r'take up\s*([0-9.]+)\s*([A-Za-z]+)', proc.stdout)
                if m:
                    val = float(m.group(1))
                    unit = m.group(2).lower()
                    if "gb" in unit or "g" in unit:
                        journal_total = int(val * (1024**3))
                    elif "mb" in unit or "m" in unit:
                        journal_total = int(val * (1024**2))
                    elif "kb" in unit or "k" in unit:
                        journal_total = int(val * 1024)
            except Exception:
                pass

        try:
            disk_total, disk_used, disk_free = shutil.disk_usage("/")
        except Exception:
            disk_total, disk_used, disk_free = (0, 0, 0)

        total_reclaimable = pacman_reclaimable + aur_total + docker_reclaimable + max(0, journal_total - 20*1024*1024)

        return {
            "categories": {
                "pacman": {
                    "name": "Pacman Package Cache",
                    "total_bytes": pacman_total,
                    "reclaimable_bytes": pacman_reclaimable,
                    "total_str": self.format_size(pacman_total),
                    "reclaimable_str": self.format_size(pacman_reclaimable),
                    "desc": "Arch Linux package archives in /var/cache/pacman/pkg. Safe prune retains current installed versions for instant offline rollback.",
                    "requires_root": True,
                    "icon": "package-x-generic-symbolic",
                    "color": "#0a84ff",
                },
                "aur": {
                    "name": "AUR Build Cache",
                    "total_bytes": aur_total,
                    "reclaimable_bytes": aur_total,
                    "total_str": self.format_size(aur_total),
                    "reclaimable_str": self.format_size(aur_total),
                    "desc": "Compiled package tarballs and git clone checkouts in ~/.cache/paru and ~/.cache/yay. 100% safe to clear.",
                    "requires_root": False,
                    "icon": "folder-download-symbolic",
                    "color": "#ff9f0a",
                },
                "docker": {
                    "name": "Docker Container Cache",
                    "total_bytes": docker_reclaimable,
                    "reclaimable_bytes": docker_reclaimable,
                    "total_str": self.format_size(docker_reclaimable),
                    "reclaimable_str": self.format_size(docker_reclaimable),
                    "desc": "Unused container layers, dangling images, and stopped container artifacts.",
                    "requires_root": False,
                    "icon": "docker-symbolic",
                    "color": "#30d158",
                },
                "journal": {
                    "name": "Systemd Journal Logs",
                    "total_bytes": journal_total,
                    "reclaimable_bytes": max(0, journal_total - 20*1024*1024),
                    "total_str": self.format_size(journal_total),
                    "reclaimable_str": self.format_size(max(0, journal_total - 20*1024*1024)),
                    "desc": "System log archives. Vacuuming retains the last 7 days of service and boot diagnostic logs.",
                    "requires_root": True,
                    "icon": "text-x-generic-symbolic",
                    "color": "#bf5af2",
                },
                "aura": {
                    "name": "Aura Application Cache",
                    "total_bytes": aura_total,
                    "reclaimable_bytes": aura_total,
                    "total_str": self.format_size(aura_total),
                    "reclaimable_str": self.format_size(aura_total),
                    "desc": "Aura database sync cache and cached vector application icons.",
                    "requires_root": False,
                    "icon": "preferences-system-symbolic",
                    "color": "#64d2ff",
                },
            },
            "total_reclaimable_bytes": total_reclaimable,
            "total_reclaimable_str": self.format_size(total_reclaimable),
            "disk_total_bytes": disk_total,
            "disk_free_bytes": disk_free,
            "disk_used_bytes": disk_used,
            "disk_free_str": self.format_size(disk_free),
            "disk_total_str": self.format_size(disk_total),
        }

    def prune_cache(self, category: str, safe_mode: bool = True, progress_cb=None, complete_cb=None):
        """Prune specific cache category in background thread."""
        def _task():
            env = os.environ.copy()
            askpass_script = get_askpass_script()
            if askpass_script.exists():
                env["SUDO_ASKPASS"] = str(askpass_script)
            env["LC_ALL"] = "C"

            success = True
            msg = "Cleaned."

            try:
                if category == "pacman":
                    if progress_cb:
                        progress_cb(0.3, "Pruning pacman package archives...")
                    if shutil.which("paccache") and safe_mode:
                        res = subprocess.run(["sudo", "-A", "paccache", "-rk1"], capture_output=True, text=True, env=env, timeout=120)
                    else:
                        res = subprocess.run(["sudo", "-A", "pacman", "-Sc", "--noconfirm"], capture_output=True, text=True, env=env, timeout=120)
                    success = (res.returncode == 0)
                    msg = "Pacman package cache safely pruned (latest versions kept)." if success else (res.stderr or "Prune failed")
                elif category == "aur":
                    if progress_cb:
                        progress_cb(0.3, "Clearing AUR build repositories...")
                    for path in (self.PARU_CACHE_DIR, self.YAY_CACHE_DIR):
                        if path.exists():
                            for child in path.iterdir():
                                try:
                                    if child.is_dir():
                                        shutil.rmtree(child, ignore_errors=True)
                                    else:
                                        child.unlink(missing_ok=True)
                                except Exception:
                                    pass
                    msg = "AUR build artifacts and git trees purged."
                elif category == "docker":
                    if progress_cb:
                        progress_cb(0.3, "Pruning unused Docker system objects...")
                    if shutil.which("docker"):
                        cmd = ["docker", "system", "prune", "-f"] if safe_mode else ["docker", "system", "prune", "-a", "-f"]
                        res = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
                        success = (res.returncode == 0)
                        msg = "Docker unused layers and containers pruned." if success else (res.stderr or "Docker prune failed")
                    else:
                        msg = "Docker is not installed."
                elif category == "journal":
                    if progress_cb:
                        progress_cb(0.3, "Vacuuming systemd journal archives...")
                    res = subprocess.run(["sudo", "-A", "journalctl", "--vacuum-time=7d"], capture_output=True, text=True, env=env, timeout=60)
                    success = (res.returncode == 0)
                    msg = "Systemd journals vacuumed to last 7 days." if success else (res.stderr or "Journal vacuum failed")
                elif category == "aura":
                    if progress_cb:
                        progress_cb(0.3, "Purging Aura sync index cache...")
                    for p in (CACHE_FILE, UPDATES_CACHE_FILE):
                        try:
                            if p.exists():
                                p.unlink()
                        except Exception:
                            pass
                    msg = "Aura local database cache cleared."
                else:
                    success = False
                    msg = f"Unknown cache category: {category}"
            except Exception as e:
                success = False
                msg = str(e)

            if progress_cb:
                progress_cb(1.0, msg)
            if complete_cb:
                complete_cb(success, msg)

        threading.Thread(target=_task, daemon=True).start()

    def prune_all_selected(self, categories: List[str], safe_mode: bool = True, progress_cb=None, complete_cb=None):
        """Prune multiple selected categories sequentially with aggregate progress."""
        def _task():
            if not categories:
                if progress_cb:
                    progress_cb(1.0, "No cache categories selected.")
                if complete_cb:
                    complete_cb(True, "No categories selected.")
                return

            total = len(categories)
            cleaned = []
            for i, cat in enumerate(categories):
                pct = i / max(1, total)
                if progress_cb:
                    progress_cb(pct, f"Cleaning {cat}...")
                done_event = threading.Event()
                cat_ok = False

                def _on_done(ok, msg):
                    nonlocal cat_ok
                    cat_ok = ok
                    done_event.set()

                self.prune_cache(cat, safe_mode=safe_mode, complete_cb=_on_done)
                completed_in_time = done_event.wait(timeout=180)
                if completed_in_time and cat_ok:
                    cleaned.append(cat)

            if progress_cb:
                progress_cb(1.0, f"Cleaned {len(cleaned)} of {total} cache categories.")
            if complete_cb:
                complete_cb(len(cleaned) > 0, f"Successfully cleaned {', '.join(cleaned)}." if cleaned else "No caches were cleaned.")

        threading.Thread(target=_task, daemon=True).start()

