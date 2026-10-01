#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Dry-run the image's package install with the same apt sources and pins, so
# dependency conflicts show up in seconds instead of an hour into the build.
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD:/lintab:ro" lintabos-builder bash -c '
cat > /etc/apt/sources.list.d/debian.sources <<EOF
Types: deb
URIs: http://deb.debian.org/debian
Suites: trixie trixie-updates trixie-backports
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://security.debian.org/debian-security
Suites: trixie-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
mkdir -p /etc/apt/preferences.d
cp /lintab/live/config/includes.chroot_before_packages/etc/apt/preferences.d/* /etc/apt/preferences.d/
apt-get update -qq >/dev/null 2>&1
pkgs=$(cat /lintab/live/config/package-lists/*.list.chroot | sed "s/#.*//" | tr -s " \n" "\n" | sort -u)
apt-get -s install $pkgs > /tmp/sim.txt 2>&1 || { tail -40 /tmp/sim.txt; echo "SIMULATION FAILED"; exit 1; }
echo "simulation OK: $(grep -c "^Inst " /tmp/sim.txt) packages"
for p in linux-image-amd64 libcamera-ipa pipewire firmware-intel-misc wireplumber gstreamer1.0-pipewire; do
  printf "%-26s %s\n" "$p" "$(grep "^Inst $p " /tmp/sim.txt | sed "s/^Inst [^ ]* (\([^ ]*\) .*/\1/")"
done
echo "kernel images: $(grep -o "^Inst linux-image-[0-9][^ ]*" /tmp/sim.txt | tr "\n" " ")"
echo "unexpected backports pulls:"
grep "^Inst" /tmp/sim.txt | grep "~bpo13" | grep -vE "^Inst (linux-|libcamera|pipewire|libpipewire|libspa|wireplumber|libwireplumber|gstreamer1.0-(libcamera|pipewire)|gir1.2-wp|firmware-|intel-microcode)" | cut -d" " -f2-3 || true' 2>&1 | grep -v WARNING
