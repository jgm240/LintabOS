#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Install, upgrade and remove lintabos-core in a clean Debian 13 container (needs Docker and network).
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p out
docker run --rm -v "$PWD:/lintab" -w /lintab lintabos-builder bash -euo pipefail -c '
  V=$(cat VERSION); NEXT=$V.1
  ./scripts/build-deb.sh out >/dev/null
  VERSION=$NEXT ./scripts/build-deb.sh out >/dev/null
  apt-get update -qq >/dev/null 2>&1
  export DEBIAN_FRONTEND=noninteractive
  echo "== first install of $V (resolves dependencies from Debian)"
  apt-get install -y -qq --no-install-recommends ./out/lintabos-core_${V}_all.deb >/tmp/install.log 2>&1 || { tail -20 /tmp/install.log; exit 1; }
  dpkg-query -W -f="installed: \${Package} \${Version}\n" lintabos-core
  for f in /usr/bin/lintab-update /usr/lib/python3/dist-packages/lintab/update.py /etc/grub.d/11_lintab_windows \
           /usr/share/lintabos/update-key.pub /usr/share/polkit-1/actions/org.lintabos.update.policy \
           /usr/lib/systemd/user/lintab-update-check.timer /etc/lintabos/update.conf /usr/share/lintabos/VERSION \
           /usr/bin/lintab-tablet-mode /usr/bin/lintab-school-mode /usr/bin/lintab-windows-files /usr/bin/lintab-boot-menu \
           /usr/bin/lintab-webapp /usr/bin/lintab-office-pack /usr/bin/lintab-boot-menu-setup \
           /usr/libexec/lintab/school-mode-on /usr/libexec/lintab/school-mode-off \
           /usr/share/polkit-1/actions/org.lintabos.school-mode.policy /usr/lib/systemd/user/lintab-tablet-mode.service \
           /usr/share/lintabos/sounds/keyboard-attached.wav /usr/share/lintabos/sounds/keyboard-detached.wav \
           /usr/share/lintabos/refind/lintabos.png /usr/share/lintabos/refind/background.png \
           /usr/share/applications/lintab-word.desktop /usr/share/applications/lintab-teams.desktop; do
    [ -e "$f" ] && echo "ok   $f" || { echo "MISSING $f"; exit 1; }
  done
  echo "== postinst effects"
  [ -e /etc/dconf/db/local ] && echo "ok   dconf database compiled" || echo "WARN dconf database missing"
  grep -q "^user_allow_other" /etc/fuse.conf && echo "ok   fuse.conf: user_allow_other enabled" || { echo "FAIL fuse.conf"; exit 1; }
  echo "== no file belongs to two packages"
  shared=$(dpkg -L lintabos-core | while read -r f; do [ -f "$f" ] && dpkg -S "$f" 2>/dev/null; done | grep -v "^lintabos-core: " || true)
  [ -z "$shared" ] && echo "ok   every file is owned by lintabos-core alone" || { echo "FAIL shared files:"; echo "$shared"; exit 1; }
  echo "== new executables are executable, desktop files are valid, services enabled"
  for f in /usr/bin/lintab-webapp /usr/libexec/lintab/school-mode-on /usr/libexec/lintab/school-mode-off; do [ -x "$f" ] || { echo "NOT EXECUTABLE $f"; exit 1; }; done
  apt-get install -y -qq --no-install-recommends desktop-file-utils >/dev/null 2>&1
  desktop-file-validate /usr/share/applications/lintab-*.desktop && echo "ok   desktop files validate"
  ls /etc/systemd/user/graphical-session.target.wants/lintab-tablet-mode.service >/dev/null && echo "ok   tablet-mode service enabled for all users"
  lintab-tablet-mode status || true
  lintab-school-mode status
  lintab-boot-menu status
  echo "== the tools start"
  lintab-update status
  python3 - <<PY
import sys; sys.path.insert(0, "/usr/lib/python3/dist-packages")
import lintab.update, lintab.update_gui, lintab.bitlocker_gui, lintab.gui
print("ok   python modules import (GTK stack present)")
PY
  echo "== upgrade $V -> $NEXT"
  apt-get install -y -qq --no-install-recommends ./out/lintabos-core_${NEXT}_all.deb >/tmp/upgrade.log 2>&1 || { tail -20 /tmp/upgrade.log; exit 1; }
  dpkg-query -W -f="installed: \${Package} \${Version}\n" lintabos-core
  echo "== remove"
  apt-get remove -y -qq lintabos-core >/dev/null 2>&1 && [ ! -e /usr/bin/lintab-update ] && echo "ok   removed cleanly"
  rm -f out/lintabos-core_${NEXT}_all.deb
'
