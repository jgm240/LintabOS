#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Copies the installer/partitioner/updater code and the notices into payload/ (what the lintabos-core
# package ships). Run before building the package.
set -euo pipefail
cd "$(dirname "$0")/.."
OVERLAY=payload
rm -rf "$OVERLAY/usr/lib/python3/dist-packages/lintab"
mkdir -p "$OVERLAY/usr/lib/python3/dist-packages" "$OVERLAY/usr/bin"
cp -r installer/lintab "$OVERLAY/usr/lib/python3/dist-packages/lintab"
find "$OVERLAY/usr/lib/python3/dist-packages/lintab" -name __pycache__ -prune -exec rm -rf {} +
install -m 755 installer/bin/* "$OVERLAY/usr/bin/"
# notices and licence texts that the image must carry (attribution, trademarks, GPL source offer)
DOC="$OVERLAY/usr/share/doc/lintabos"
mkdir -p "$DOC/LICENSES"
cp NOTICE.md "$DOC/NOTICE"
cp LICENSES/* "$DOC/LICENSES/"
[ -f LICENSE ] && cp LICENSE "$DOC/LICENSE"
echo "installer staged"
