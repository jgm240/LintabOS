#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build the lintabos-core Debian package from payload/ (plus the installer code). Runs inside the builder
# container when called from build.sh; also fine on any Debian machine with dpkg-dev and fakeroot.
#
#   ./scripts/build-deb.sh [output-dir]      # version comes from the VERSION file (or $VERSION)
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=${VERSION:-$(cat VERSION)}
OUT=${1:-out}
mkdir -p "$OUT"

./scripts/make-assets.sh >/dev/null
./scripts/stage-installer.sh >/dev/null

STAGE=$(mktemp -d)
trap 'rm -rf "$STAGE"' EXIT
cp -a payload/. "$STAGE/"

install -D -m 644 VERSION "$STAGE/usr/share/lintabos/VERSION"
install -D -m 644 packaging/update-key.pub "$STAGE/usr/share/lintabos/update-key.pub"

mkdir -p "$STAGE/DEBIAN"
sed "s/@VERSION@/$VERSION/" packaging/DEBIAN/control > "$STAGE/DEBIAN/control"
cp packaging/DEBIAN/conffiles packaging/DEBIAN/postinst packaging/DEBIAN/postrm "$STAGE/DEBIAN/"
chmod 755 "$STAGE/DEBIAN/postinst" "$STAGE/DEBIAN/postrm"

# Normalize permissions: directories 755, files 644, executables 755.
find "$STAGE" -path "$STAGE/DEBIAN" -prune -o -type d -exec chmod 755 {} +
find "$STAGE" -path "$STAGE/DEBIAN" -prune -o -type f -exec chmod 644 {} +
chmod 755 "$STAGE"/usr/bin/lintab-* "$STAGE"/usr/libexec/lintab/* "$STAGE"/etc/grub.d/11_lintab_windows

# Installed-Size (KiB) and checksums
SIZE=$(du -sk --exclude=DEBIAN "$STAGE" | cut -f1)
echo "Installed-Size: $SIZE" >> "$STAGE/DEBIAN/control"
(cd "$STAGE" && find . -path ./DEBIAN -prune -o -type f -print | sed 's|^\./||' | sort | xargs md5sum > DEBIAN/md5sums)

DEB="$OUT/lintabos-core_${VERSION}_all.deb"
fakeroot dpkg-deb --root-owner-group -Zxz --build "$STAGE" "$DEB" >/dev/null
echo "$DEB"
