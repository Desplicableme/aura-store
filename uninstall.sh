#!/usr/bin/env bash
# =============================================================================
# Aura Package Hub - Clean Uninstaller
# =============================================================================

set -e

BOLD="\033[1m"
GREEN="\033[1;32m"
BLUE="\033[1;34m"
YELLOW="\033[1;33m"
RESET="\033[0m"

echo -e "${BOLD}Aura Package Hub - Uninstaller${RESET}\n"

read -p "Are you sure you want to uninstall Aura? [y/N] " -n 1 -r
echo
if [[ ! $REPLY =~ ^[Yy]$ ]]; then
    echo "Aborted."
    exit 0
fi

echo -e "${BLUE}==>${RESET} Removing application files..."
rm -rf "${HOME}/.local/share/aura"
rm -f "${HOME}/.local/bin/aura"
rm -f "${HOME}/.local/share/applications/io.github.aura.desktop"
rm -f "${HOME}/.local/share/icons/hicolor/scalable/apps/io.github.aura.svg"
rm -f "${HOME}/.local/share/icons/hicolor/scalable/apps/docker-symbolic.svg"

# Optional vault cleanup
if [ -f "${HOME}/.aura_vault" ]; then
    read -p "Remove stored passwordless credentials vault (~/.aura_vault)? [y/N] " -n 1 -r
    echo
    if [[ $REPLY =~ ^[Yy]$ ]]; then
        rm -f "${HOME}/.aura_vault"
        echo "Removed vault."
    fi
fi

# Update desktop & icon databases
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "${HOME}/.local/share/applications" &>/dev/null || true
fi
if command -v gtk-update-icon-cache &>/dev/null; then
    gtk-update-icon-cache -f -t "${HOME}/.local/share/icons/hicolor" &>/dev/null || true
fi

echo -e "\n${GREEN}${BOLD}✓ Aura has been uninstalled.${RESET}"
