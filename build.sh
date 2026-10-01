#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Builds the LintabOS live/installer ISO (Debian 13, amd64, UEFI) inside Docker.
#
#   ./build.sh            build out/lintabos-amd64.iso
#   ./build.sh --clean    also throw away the cached chroot first
#
# The live-build work tree lives in a Docker volume (it needs a real Linux
# filesystem for device nodes and permissions); only the finished ISO lands in ./out.
# On Apple Silicon this runs the amd64 toolchain under emulation, so expect a
# long first build (an hour or more).
#
# Caching: live-build keeps downloaded .debs and the debootstrap base in cache/,
# inside the work volume. (It hard-links cached packages into the chroot, so the
# cache and chroot must share one filesystem: no separate cache volume.)
# `--clean` wipes the chroot and image but keeps cache/, so later builds only
# download what changed. `--purge-cache` deletes the whole volume, cache included.
set -euo pipefail
cd "$(dirname "$0")"

VOLUME=lintabos-build
CLEAN=0
PURGE=0
case "${1:-}" in
    --clean)       CLEAN=1 ;;
    --purge-cache) PURGE=1 ;;
esac

docker build -q -t lintabos-builder docker >/dev/null
mkdir -p out
[ "$PURGE" = 1 ] && docker volume rm -f "$VOLUME" >/dev/null

docker run --rm --privileged \
    -v "$PWD:/src:ro" -v "$VOLUME:/build" -v "$PWD/out:/out" \
    lintabos-builder bash -euo pipefail -c '
  mkdir -p /build/src /build/lb
  rsync -a --delete --exclude /out --exclude /tests/shots --exclude .git /src/ /build/src/

  cd /build/src
  # The LintabOS parts are one Debian package, installed in the image exactly like a later update will be.
  rm -rf live/config/packages.chroot && mkdir -p live/config/packages.chroot
  ./scripts/build-deb.sh live/config/packages.chroot

  rsync -a --delete /build/src/live/config/ /build/lb/config/
  rsync -a --delete /build/src/live/auto/   /build/lb/auto/
  cd /build/lb
  # Always rebuild the chroot/image from scratch; keep cache/ (downloads, bootstrap).
  lb clean --binary --chroot >/dev/null 2>&1 || true
  rm -rf /build/lb/chroot /build/lb/binary /build/lb/.build
  lb config
  # apt can repeat one harmless warning millions of times if the network drops (e.g. the Mac sleeps); drop it
  lb build 2>&1 | grep --line-buffered -v "Tried to start delayed item" | tee /out/build.log

  iso=$(ls -1 *.iso | head -n1)
  cp "$iso" /out/lintabos-amd64.iso
  (cd /out && sha256sum lintabos-amd64.iso > lintabos-amd64.iso.sha256)
  ls -lh /out/lintabos-amd64.iso
'
echo "Done: out/lintabos-amd64.iso"
