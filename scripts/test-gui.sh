#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Headless GUI smoke test; screenshots land in tests/shots/.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p tests/shots
docker run --rm -v "$PWD:/lintab" -w /lintab -e SHOTS=/lintab/tests/shots -e NO_AT_BRIDGE=1 -e GSK_RENDERER=cairo -e LIBGL_ALWAYS_SOFTWARE=1 lintabos-builder \
  timeout 180 xvfb-run -a -s "-screen 0 1000x820x24" python3 -u ${SMOKE:-tests/gui_smoke.py}
