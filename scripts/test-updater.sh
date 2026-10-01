#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Updater tests: fake GitHub, real minisign signatures, real .deb installs (Docker, root in the container).
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD:/lintab" -w /lintab lintabos-builder bash -c 'python3 -m pytest -v tests/test_updater.py "$@"' _ "$@"
