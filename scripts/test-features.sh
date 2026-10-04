#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Tests for tablet mode, school mode, Windows files and the touch boot menu (fast; no hardware, no network).
set -euo pipefail
cd "$(dirname "$0")/.."
docker run --rm -v "$PWD:/lintab" -w /lintab lintabos-builder bash -c 'python3 -m pytest -v tests/test_features.py tests/test_desktops.py tests/test_uninstall.py tests/test_hwreport.py tests/test_extras.py tests/test_comfort.py tests/test_battery.py tests/test_wifi.py tests/test_android.py tests/test_winmod.py tests/test_sleepmode.py tests/test_disks_path.py tests/test_winrecovery.py tests/test_winterm.py "$@"' _ "$@"
