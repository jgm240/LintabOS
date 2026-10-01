#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Download the exact Debian source packages for every package in a built LintabOS image, to satisfy the
# GPL/LGPL source obligations (see NOTICE.md). Needs Docker and roughly 10 GB of disk.
#
#   ./scripts/fetch-sources.sh out/lintabos-amd64.iso out/sources
#
# It reads live/filesystem.packages from the ISO (package + version) and fetches each source package
# at that version from Debian trixie / trixie-backports (falling back to snapshot.debian.org).
set -euo pipefail
cd "$(dirname "$0")/.."

ISO=${1:?usage: fetch-sources.sh <iso> <output-dir>}
OUT=${2:?usage: fetch-sources.sh <iso> <output-dir>}
mkdir -p "$OUT"

docker run --rm -v "$PWD/$(dirname "$ISO"):/iso:ro" -v "$PWD/$OUT:/out" lintabos-builder bash -euo pipefail -c '
  cat > /etc/apt/sources.list.d/src.sources <<EOF
Types: deb-src
URIs: http://deb.debian.org/debian
Suites: trixie trixie-updates trixie-backports
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb-src
URIs: http://security.debian.org/debian-security
Suites: trixie-security
Components: main contrib non-free non-free-firmware
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
EOF
  apt-get update -qq
  xorriso -osirrox on -indev /iso/'"$(basename "$ISO")"' -extract /live/filesystem.packages /tmp/pk.txt >/dev/null 2>&1
  cd /out
  : > fetched.txt; : > missing.txt
  while IFS=$'"'"'\t'"'"' read -r pkg ver; do
    pkg=${pkg%%:*}
    if apt-get source --download-only -qq "$pkg=$ver" >/dev/null 2>&1; then echo "$pkg $ver" >> fetched.txt
    else echo "$pkg $ver" >> missing.txt; fi
  done < /tmp/pk.txt
  cp /tmp/pk.txt packages-in-image.txt
  echo "fetched: $(wc -l < fetched.txt)   not found in the current archive (use snapshot.debian.org): $(wc -l < missing.txt)"
'
