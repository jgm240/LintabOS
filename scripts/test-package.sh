#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Install, upgrade and remove lintabos-core in a clean Debian 13 container (needs Docker and network).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p out
docker run --rm -v "$PWD:/lintab" -w /lintab lintabos-builder bash -euo pipefail -c '
  ./scripts/build-deb.sh out >/dev/null
  VERSION=0.1.1 ./scripts/build-deb.sh out >/dev/null
  apt-get update -qq >/dev/null 2>&1
  export DEBIAN_FRONTEND=noninteractive
  echo "== first install of 0.1.0 (resolves dependencies from Debian)"
  apt-get install -y -qq --no-install-recommends ./out/lintabos-core_0.1.0_all.deb >/tmp/install.log 2>&1 || { tail -20 /tmp/install.log; exit 1; }
  dpkg-query -W -f="installed: \${Package} \${Version}\n" lintabos-core
  for f in /usr/bin/lintab-update /usr/lib/python3/dist-packages/lintab/update.py /etc/grub.d/11_lintab_windows \
           /usr/share/lintabos/update-key.pub /usr/share/polkit-1/actions/org.lintabos.update.policy \
           /usr/lib/systemd/user/lintab-update-check.timer /etc/lintabos/update.conf /usr/share/lintabos/VERSION; do
    [ -e "$f" ] && echo "ok   $f" || { echo "MISSING $f"; exit 1; }
  done
  echo "== postinst effects"
  [ -e /etc/dconf/db/local ] && echo "ok   dconf database compiled" || echo "WARN dconf database missing"
  grep -q "^user_allow_other" /etc/fuse.conf && echo "ok   fuse.conf: user_allow_other enabled" || { echo "FAIL fuse.conf"; exit 1; }
  echo "== no file belongs to two packages"
  shared=$(dpkg -L lintabos-core | while read -r f; do [ -f "$f" ] && dpkg -S "$f" 2>/dev/null; done | grep -v "^lintabos-core: " || true)
  [ -z "$shared" ] && echo "ok   every file is owned by lintabos-core alone" || { echo "FAIL shared files:"; echo "$shared"; exit 1; }
  echo "== the tools start"
  lintab-update status
  python3 - <<PY
import sys; sys.path.insert(0, "/usr/lib/python3/dist-packages")
import lintab.update, lintab.update_gui, lintab.bitlocker_gui, lintab.gui
print("ok   python modules import (GTK stack present)")
PY
  echo "== upgrade 0.1.0 -> 0.1.1"
  apt-get install -y -qq --no-install-recommends ./out/lintabos-core_0.1.1_all.deb >/tmp/upgrade.log 2>&1 || { tail -20 /tmp/upgrade.log; exit 1; }
  dpkg-query -W -f="installed: \${Package} \${Version}\n" lintabos-core
  echo "== remove"
  apt-get remove -y -qq lintabos-core >/dev/null 2>&1 && [ ! -e /usr/bin/lintab-update ] && echo "ok   removed cleanly"
  rm -f out/lintabos-core_0.1.1_all.deb
'
