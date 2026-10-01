# SPDX-License-Identifier: MIT
# Build + test environment for LintabOS. Always amd64: the target is an x86-64
# tablet (Intel N100/N200), so on Apple Silicon this runs under emulation.
FROM --platform=linux/amd64 debian:trixie

ENV DEBIAN_FRONTEND=noninteractive
RUN apt-get update && apt-get install -y --no-install-recommends \
        live-build debootstrap squashfs-tools xorriso mtools dosfstools \
        grub-efi-amd64-bin grub-pc-bin grub-common shim-signed grub-efi-amd64-signed \
        librsvg2-bin imagemagick fonts-dejavu-core \
        python3 python3-pytest shellcheck \
        util-linux parted gdisk ntfs-3g e2fsprogs dosfstools udev kmod cryptsetup-bin \
        ca-certificates curl rsync git \
    && rm -rf /var/lib/apt/lists/*

# sfdisk moved to its own package in Debian 13
RUN apt-get update && apt-get install -y --no-install-recommends fdisk \
    && rm -rf /var/lib/apt/lists/*

# GTK4/libadwaita + a headless display, to smoke-test the installer GUI
RUN apt-get update && apt-get install -y --no-install-recommends \
        python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 libgtk-4-bin xvfb xauth \
        adwaita-icon-theme librsvg2-common fonts-cantarell imagemagick \
    && rm -rf /var/lib/apt/lists/*

# dislocker + FUSE + python crypto: BitLocker unlock/decrypt tests (tests/bitlocker_image.py)
RUN apt-get update && apt-get install -y --no-install-recommends dislocker fuse3 python3-cryptography \
    && rm -rf /var/lib/apt/lists/*

# packaging and signing for the lintabos-core updater
RUN apt-get update && apt-get install -y --no-install-recommends dpkg-dev fakeroot minisign xz-utils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /lintab
