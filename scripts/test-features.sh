#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Tests for tablet mode, school mode, Windows files and the touch boot menu (fast; no hardware, no network).
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD:/lintab" -w /lintab lintabos-builder bash -c 'python3 -m pytest -v tests/test_features.py tests/test_desktops.py "$@"' _ "$@"
