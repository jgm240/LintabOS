#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Build, sign and publish a LintabOS release on GitHub (needs Docker, gh, and ~/.lintabos-signing/update.minisec).
#
#   ./scripts/release.sh 0.1.1                 # pre-release (default while LintabOS is young)
#   ./scripts/release.sh 0.2.0 --stable        # full release
#   ./scripts/release.sh 0.1.0 --with-iso      # also upload out/lintabos-amd64.iso (must be a fresh, verified build)
set -euo pipefail
cd "$(dirname "$0")/.."

VERSION=${1:?usage: release.sh <version> [--stable] [--with-iso]}
shift
PRE=--prerelease; ISO=0
for a in "$@"; do
  case "$a" in --stable) PRE="" ;; --with-iso) ISO=1 ;; *) echo "unknown option $a" >&2; exit 2 ;; esac
done

KEY="$HOME/.lintabos-signing/update.minisec"
REPO=jgm240/LintabOS
[ "$(cat VERSION)" = "$VERSION" ] || { echo "VERSION file says $(cat VERSION), not $VERSION" >&2; exit 1; }
[ -f "$KEY" ] || { echo "signing key not found at $KEY" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "commit your changes first (git status is not clean)" >&2; exit 1; }
git fetch -q origin && [ "$(git rev-parse HEAD)" = "$(git rev-parse origin/main)" ] || { echo "push main first" >&2; exit 1; }

DIST=out/release-$VERSION
rm -rf "$DIST"; mkdir -p "$DIST"

echo "== build the package"
docker run --rm -v "$PWD:/lintab" -w /lintab lintabos-builder ./scripts/build-deb.sh "$DIST" >/dev/null
DEB="$DIST/lintabos-core_${VERSION}_all.deb"

echo "== sign it and check the signature against the public key in the repo"
docker run --rm -v "$PWD/$DIST:/d" -v "$KEY:/key/update.minisec:ro" -v "$PWD/packaging:/pkg:ro" lintabos-builder bash -euc "
  minisign -S -s /key/update.minisec -m /d/$(basename "$DEB") -x /d/$(basename "$DEB").minisig -t 'lintabos-core $VERSION' >/dev/null
  minisign -V -q -p /pkg/update-key.pub -m /d/$(basename "$DEB") -x /d/$(basename "$DEB").minisig && echo signature ok"

ASSETS=("$DEB" "$DEB.minisig")
if [ "$ISO" = 1 ]; then
  [ -f out/lintabos-amd64.iso ] || { echo "out/lintabos-amd64.iso missing" >&2; exit 1; }
  (cd out && shasum -a 256 -c lintabos-amd64.iso.sha256 >/dev/null) || { echo "ISO checksum mismatch" >&2; exit 1; }
  ln -f out/lintabos-amd64.iso "$DIST/lintabos-$VERSION-amd64.iso"
  ASSETS+=("$DIST/lintabos-$VERSION-amd64.iso")
fi
(cd "$DIST" && shasum -a 256 $(for a in "${ASSETS[@]}"; do basename "$a"; done) > SHA256SUMS)
ASSETS+=("$DIST/SHA256SUMS")

NOTES=docs/releases/$VERSION.md
[ -f "$NOTES" ] || { echo "write $NOTES first" >&2; exit 1; }

echo "== publish v$VERSION on $REPO"
gh release create "v$VERSION" "${ASSETS[@]}" --repo "$REPO" --target main \
   --title "LintabOS $VERSION" --notes-file "$NOTES" $PRE
echo "https://github.com/$REPO/releases/tag/v$VERSION"
