#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Runs the partitioner tests in the builder container. The tests create
# loop-device disk images, so the container is privileged and shares the Docker
# VM's /dev (that is how loop partition nodes appear without udev).
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm --privileged -v /dev:/dev -v "$PWD:/lintab" -w /lintab \
  lintabos-builder bash -c '
    cleanup() {
      for d in $(losetup -a | cut -d: -f1); do losetup -d "$d" 2>/dev/null || true; done
      rm -f /dev/loop[0-9]*p[0-9]*   # stale partition nodes break the next partprobe
    }
    cleanup; trap cleanup EXIT
    python3 -m pytest -v tests/test_partitioner_loop.py "$@"' _ "$@"
