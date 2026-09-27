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
SYNC_DIR = Path("/var/lib/pacman/sync")
ASKPASS_SCRIPT = Path.home() / ".local" / "share" / "aura" / "aura-askpass"

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
        ]
    },
    {
        "id": "productivity",
        "category": "Productivity",
        "subtitle": "Office suites, knowledge bases, and focused writing",
        "icon": "x-office-document-symbolic",
        "apps": [
            {"name": "libreoffice-fresh", "title": "LibreOffice", "desc": "Office suite for docs and sheets", "source": "pacman"},
            {"name": "obsidian", "title": "Obsidian", "desc": "Knowledge base and markdown notes", "source": "aur"},
            {"name": "thunar", "title": "Thunar", "desc": "Fast lightweight file manager", "source": "pacman"},
            {"name": "micro", "title": "Micro Editor", "desc": "Intuitive modern terminal editor", "source": "pacman"},
            {"name": "fastfetch", "title": "Fastfetch", "desc": "Fast system information display", "source": "pacman"},
            {"name": "pavucontrol", "title": "Volume Control", "desc": "Advanced audio device control", "source": "pacman"},
        ]
    },
    {
        "id": "privacy",
        "category": "Internet & Privacy",
        "subtitle": "Secure browsers, encrypted messaging, and privacy tools",
        "icon": "security-high-symbolic",
        "apps": [
            {"name": "brave-bin", "title": "Brave Browser", "desc": "Privacy browser with ad-blocking", "source": "aur"},
            {"name": "zen-browser-bin", "title": "Zen Browser", "desc": "Modern tabbed privacy browser", "source": "aur"},
            {"name": "chromium", "title": "Chromium", "desc": "Open-source web browser engine", "source": "pacman"},
            {"name": "torbrowser-launcher", "title": "Tor Browser", "desc": "Anonymous secure web browsing", "source": "pacman"},
            {"name": "telegram-desktop", "title": "Telegram", "desc": "Fast secure desktop messaging", "source": "pacman"},
            {"name": "signal-desktop", "title": "Signal", "desc": "End-to-end encrypted messaging", "source": "pacman"},
        ]
    },
    {
        "id": "media",
        "category": "Media & Creative",
        "subtitle": "Audio, video streaming, recording, and digital artistry",
        "icon": "applications-graphics-symbolic",
        "apps": [
            {"name": "obs-studio", "title": "OBS Studio", "desc": "Screen recording & live streaming", "source": "pacman"},
            {"name": "vlc", "title": "VLC Media Player", "desc": "Multi-format media player", "source": "pacman"},
            {"name": "gimp", "title": "GIMP", "desc": "Advanced image editing suite", "source": "pacman"},
            {"name": "blender", "title": "Blender", "desc": "Professional 3D creation suite", "source": "pacman"},
            {"name": "kdenlive", "title": "Kdenlive", "desc": "Non-linear video editor", "source": "pacman"},
            {"name": "audacity", "title": "Audacity", "desc": "Multi-track audio editor & recorder", "source": "pacman"},
        ]
    },
    {
        "id": "system",
        "category": "System & Tools",
        "subtitle": "Hardware monitors, fast terminal utilities, and tools",
        "icon": "system-run-symbolic",
        "apps": [
            {"name": "btop", "title": "btop Monitor", "desc": "Modern system resource monitor", "source": "pacman"},
            {"name": "fastfetch", "title": "Fastfetch", "desc": "Fast system information display", "source": "pacman"},
            {"name": "foot", "title": "Foot Terminal", "desc": "Lightweight Wayland terminal", "source": "pacman"},
            {"name": "htop", "title": "htop", "desc": "Interactive process viewer", "source": "pacman"},
            {"name": "nvtop", "title": "nvtop", "desc": "GPU task and performance monitor", "source": "pacman"},
            {"name": "thunar", "title": "Thunar", "desc": "Fast lightweight file manager", "source": "pacman"},
        ]
    },
    {
        "id": "games",
        "category": "Gaming",
        "subtitle": "Game platforms, launchers, emulators, and gaming utilities",
        "icon": "applications-games-symbolic",
        "apps": [
            {"name": "steam", "title": "Steam", "desc": "Gaming platform and game launcher", "source": "pacman"},
            {"name": "lutris", "title": "Lutris Gaming", "desc": "Unified Linux gaming platform", "source": "pacman"},
            {"name": "heroic", "title": "Heroic Games Launcher", "desc": "Epic Games and GOG launcher", "source": "aur"},
            {"name": "retroarch", "title": "RetroArch", "desc": "Multi-system game engine & emulator", "source": "pacman"},
            {"name": "bottles", "title": "Bottles", "desc": "Run Windows software & games easily", "source": "pacman"},
            {"name": "mangohud", "title": "MangoHud", "desc": "Vulkan and OpenGL overlay monitor", "source": "pacman"},
        ]
    }
]
FEATURED_APPS = CURATED_CATEGORIES

# Human-friendly Apple App Store display names
APP_DISPLAY_NAMES: Dict[str, str] = {
    "firefox": "Firefox",
    "firefox-pure": "Firefox",
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
    "telegram-desktop": "Telegram",
    "signal-desktop": "Signal",
    "obs-studio": "OBS Studio",
    "vlc": "VLC Media Player",
    "lutris": "Lutris",
    "btop": "btop Monitor",
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
    "htop": "htop",
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
        self.active_transaction: Optional[str] = None
        self.is_checking_updates = False
        self.updates_checked = False
        self.container_mgr = ContainerManager()
        # Load installed packages synchronously so cards immediately reflect installed status
        self.refresh_installed()

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
        def _worker():
            self.active_transaction = pkg_name
            env = os.environ.copy()
            if ASKPASS_SCRIPT.exists():
                env["SUDO_ASKPASS"] = str(ASKPASS_SCRIPT)
            env["LC_ALL"] = "C"

            is_system_upgrade = pkg_name in ["system", "--all", "all", ""] or not pkg_name
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

            progress_cb(0.08, "Authenticating & preparing...")

            error_lines = []
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

                    if "error" in l_lower or "failed" in l_lower:
                        error_lines.append(clean)

                    if "resolving dependencies" in l_lower:
                        progress_cb(0.18, "Resolving dependencies...")
                    elif "looking for conflicting" in l_lower:
                        progress_cb(0.24, "Checking for conflicts...")
                    elif "checking keyring" in l_lower:
                        progress_cb(0.32, "Checking keyring...")
                    elif "checking package integrity" in l_lower:
                        progress_cb(0.42, "Verifying package integrity...")
                    elif "retrieving packages" in l_lower or "downloading" in l_lower:
                        progress_cb(0.58, f"Downloading package files...")
                    elif "checking available disk space" in l_lower:
                        progress_cb(0.70, "Checking disk space...")
                    elif "installing" in l_lower or "processing package" in l_lower:
                        progress_cb(0.85, f"Installing {pkg_name}...")
                    elif "post-transaction hooks" in l_lower or "running hooks" in l_lower:
                        progress_cb(0.94, "Finalizing installation...")

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
                    self.active_transaction = None
                    progress_cb(1.0, "Completed!")
                    complete_cb(True, action, pkg_name, "")
                    # Background check for remaining updates
                    threading.Thread(target=self.check_updates, daemon=True).start()
                else:
                    self.active_transaction = None
                    err_msg = "\n".join(error_lines[-3:]) if error_lines else f"Exited with code {proc.returncode}"
                    if any("password" in l.lower() or "auth" in l.lower() for l in error_lines):
                        PackageManager.clear_auth_cache()
                    progress_cb(0.0, f"Error: {err_msg}")
                    complete_cb(False, action, pkg_name, err_msg)

            except Exception as e:
                self.active_transaction = None
                complete_cb(False, action, pkg_name, str(e))

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
            "name": "GNU Image Manipulation",
            "desc": "High-powered image retouching, composition and digital artwork",
            "category": "Graphics & Media",
            "icon": "gimp",
            "binary": "gimp",
            "pkg": "gimp",
            "size": "140 MB",
        },
        {
            "id": "inkscape",
            "name": "Inkscape Vector Editor",
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
            "id": "steam",
            "name": "Steam Gaming",
            "desc": "Sandboxed digital gaming platform and Proton game launcher",
            "category": "Gaming",
            "icon": "steam",
            "binary": "steam",
            "pkg": "steam",
            "size": "65 MB",
        },
        {
            "id": "neovim",
            "name": "Neovim IDE",
            "desc": "Hyperextensible terminal-based editor with isolated toolchains",
            "category": "Development",
            "icon": "nvim",
            "binary": "nvim",
            "pkg": "neovim",
            "size": "30 MB",
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
        {
            "id": "retroarch",
            "name": "RetroArch",
            "desc": "Cross-platform frontend for classic video game emulators",
            "category": "Gaming",
            "icon": "retroarch",
            "binary": "retroarch",
            "pkg": "retroarch",
            "size": "50 MB",
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
            "name": "Audacity",
            "desc": "Multi-track audio recorder and waveform sound editor",
            "category": "Graphics & Media",
            "icon": "audacity",
            "binary": "audacity",
            "pkg": "audacity",
            "size": "45 MB",
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

