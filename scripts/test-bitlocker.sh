#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# BitLocker unlock + decrypt tests against real dislocker (needs Docker; FUSE needs a privileged container).
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm --privileged -v /dev:/dev -v "$PWD:/lintab" -w /lintab lintabos-builder bash -c '
    cleanup() { umount -R /tmp/pytest-of-root 2>/dev/null || true; rm -rf /run/lintab; }
    trap cleanup EXIT
    python3 -m pytest -v tests/test_bitlocker.py "$@"' _ "$@"
