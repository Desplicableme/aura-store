# Maintainer: Arka <https://github.com/Desplicableme/aura-store>
pkgname=aura-appstore-git
pkgver=1.0.0
pkgrel=1
pkgdesc="Mac-inspired modern App Store tailored exclusively for Hyprland on Arch Linux"
arch=('any')
url="https://github.com/Desplicableme/aura-store"
license=('GPL-3.0-or-later')
depends=(
    'python'
    'python-gobject'
    'gtk4'
    'libadwaita'
    'pacman-contrib'
)
optdepends=(
    'paru: AUR package helper integration'
    'yay: Alternative AUR package helper'
    'docker: Isolated OCI sandbox applications'
)
provides=('aura')
conflicts=('aura')
source=()

package() {
    install -dm755 "${pkgdir}/usr/lib/aura"
    install -dm755 "${pkgdir}/usr/bin"
    install -dm755 "${pkgdir}/usr/share/applications"
    install -dm755 "${pkgdir}/usr/share/icons/hicolor/scalable/apps"
    install -dm755 "${pkgdir}/usr/share/metainfo"
    install -dm755 "${pkgdir}/usr/share/aura"

    install -m755 aura.py "${pkgdir}/usr/lib/aura/aura.py"
    install -m644 aura_backend.py "${pkgdir}/usr/lib/aura/aura_backend.py"
    install -m644 aura_ui.py "${pkgdir}/usr/lib/aura/aura_ui.py"
    install -m755 aura-askpass "${pkgdir}/usr/lib/aura/aura-askpass"

    # Launcher wrapper
    ln -sf "/usr/lib/aura/aura.py" "${pkgdir}/usr/bin/aura"

    # Desktop, Metadata & Hyprland config
    install -m644 data/io.github.aura.desktop "${pkgdir}/usr/share/applications/io.github.aura.desktop"
    install -m644 data/io.github.aura.metainfo.xml "${pkgdir}/usr/share/metainfo/io.github.aura.metainfo.xml"
    install -m644 data/icons/io.github.aura.svg "${pkgdir}/usr/share/icons/hicolor/scalable/apps/io.github.aura.svg"
    install -m644 data/icons/docker-symbolic.svg "${pkgdir}/usr/share/icons/hicolor/scalable/apps/docker-symbolic.svg"
    install -m644 data/hyprland-aura.conf "${pkgdir}/usr/share/aura/hyprland-aura.conf"
}
