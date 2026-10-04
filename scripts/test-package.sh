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
           /usr/share/lintabos/extras/microsoft/lintab-word.desktop /usr/share/lintabos/extras/microsoft/lintab-teams.desktop \
           /usr/bin/lintab-touch-setup /usr/bin/lintab-xfce-rotate /etc/xdg/autostart/lintab-touch-setup-xfce.desktop \
           /etc/xdg/autostart/lintab-touch-setup-kde.desktop /etc/xdg/autostart/lintab-xfce-rotate.desktop \
           /etc/xdg/autostart/lintab-tablet-mode-xfce.desktop \
           /usr/bin/lintab-uninstall /usr/bin/lintab-uninstall-gui /usr/bin/lintab-uninstall-launch /usr/bin/lintab-hwreport \
           /usr/bin/lintab-extras /usr/bin/lintab-reading-mode /usr/bin/lintab-battery /usr/bin/lintab-battery-gui \
           /usr/bin/lintab-tablet-settings /usr/bin/lintab-wifi-fix /usr/bin/lintab-android /usr/bin/lintab-android-session /usr/share/lintabos/android/ComputerMode.apk \
           /usr/libexec/lintab/install-chromium /usr/libexec/lintab/remove-waydroid \
           /usr/bin/lintab-winmod /usr/libexec/lintab/winmod-remount /usr/share/applications/lintab-winmod.desktop \
           /usr/share/polkit-1/actions/org.lintabos.winmod.policy /usr/share/polkit-1/actions/org.lintabos.windows-files.policy \
           /usr/bin/lintab-sleep-mode /usr/bin/lintab-sleep-mode-gui /usr/libexec/lintab/sleep-mode-set \
           /usr/libexec/lintab/sleep-mode-apply /usr/libexec/lintab/sleep-guard-run \
           /usr/lib/systemd/system/lintab-sleep-mode-apply.service /usr/lib/systemd/system/lintab-sleep-guard.service \
           /usr/share/polkit-1/actions/org.lintabos.sleep-mode.policy /usr/share/applications/lintab-sleep-mode.desktop \
           /usr/bin/lintab-restart-windows /usr/bin/lintab-restart-windows-gui /usr/bin/lintab-winterm \
           /usr/share/applications/lintab-winterm.desktop \
           /usr/share/wayland-sessions/lintab-android.desktop /usr/share/applications/lintab-android.desktop /usr/libexec/lintab/install-waydroid /usr/libexec/lintab/battery-limit \
           /usr/lib/systemd/system/lintab-battery-limit.service /usr/share/polkit-1/actions/org.lintabos.extras.policy \
           /usr/share/polkit-1/actions/org.lintabos.battery.policy /usr/share/applications/lintab-hwreport.desktop \
           /usr/share/applications/lintab-extras.desktop /usr/share/applications/lintab-battery.desktop \
           /usr/share/applications/lintab-reading-mode.desktop /usr/share/applications/lintab-tablet-settings.desktop; do
    [ -e "$f" ] && echo "ok   $f" || { echo "MISSING $f"; exit 1; }
  done
  echo "== postinst effects"
  [ -e /etc/dconf/db/local ] && echo "ok   dconf database compiled" || echo "WARN dconf database missing"
  grep -q "^user_allow_other" /etc/fuse.conf && echo "ok   fuse.conf: user_allow_other enabled" || { echo "FAIL fuse.conf"; exit 1; }
  echo "== no file belongs to two packages"
  shared=$(dpkg -L lintabos-core | while read -r f; do [ -f "$f" ] && dpkg -S "$f" 2>/dev/null; done | grep -v "^lintabos-core: " || true)
  [ -z "$shared" ] && echo "ok   every file is owned by lintabos-core alone" || { echo "FAIL shared files:"; echo "$shared"; exit 1; }
  echo "== new executables are executable, desktop files are valid, services enabled"
  for f in /usr/bin/lintab-webapp /usr/libexec/lintab/school-mode-on /usr/libexec/lintab/school-mode-off /usr/libexec/lintab/install-waydroid /usr/libexec/lintab/battery-limit /usr/bin/lintab-android-session /usr/libexec/lintab/install-chromium /usr/libexec/lintab/remove-waydroid /usr/libexec/lintab/winmod-remount \
           /usr/libexec/lintab/sleep-mode-set /usr/libexec/lintab/sleep-mode-apply /usr/libexec/lintab/sleep-guard-run; do [ -x "$f" ] || { echo "NOT EXECUTABLE $f"; exit 1; }; done
  apt-get install -y -qq --no-install-recommends desktop-file-utils >/dev/null 2>&1
  desktop-file-validate /usr/share/applications/lintab-*.desktop /etc/xdg/autostart/lintab-*.desktop /usr/share/wayland-sessions/lintab-android.desktop /usr/share/lintabos/extras/microsoft/*.desktop && echo "ok   desktop files validate"
  ls /etc/systemd/user/graphical-session.target.wants/lintab-tablet-mode.service >/dev/null && echo "ok   tablet-mode service enabled for all users"
  lintab-tablet-mode status || true
  lintab-school-mode status
  lintab-boot-menu status
  lintab-battery status || true
  lintab-reading-mode status
  lintab-extras list
  lintab-android status
  lintab-sleep-mode status
  systemctl list-unit-files lintab-sleep-mode-apply.service lintab-sleep-guard.service
  lintab-uninstall list || true
  bash -n /usr/bin/lintab-hwcheck && echo "ok   hwcheck parses"
  echo "== the tools start"
  lintab-update status
  python3 - <<PY
import sys; sys.path.insert(0, "/usr/lib/python3/dist-packages")
import lintab.update, lintab.update_gui, lintab.bitlocker_gui, lintab.gui, lintab.desktops, lintab.touchsetup, lintab.xfce_rotate, lintab.uninstall, lintab.uninstall_gui, lintab.hwreport, lintab.extras, lintab.comfort, lintab.battery, lintab.battery_gui, lintab.tablet_gui, lintab.wifi, lintab.android, lintab.winmod, lintab.winmod_gui, lintab.sleepmode, lintab.sleepmode_gui, lintab.winrecovery, lintab.restartwindows_gui, lintab.winterm
print("ok   python modules import (GTK stack present)")
PY
  echo "== upgrade $V -> $NEXT"
  apt-get install -y -qq --no-install-recommends ./out/lintabos-core_${NEXT}_all.deb >/tmp/upgrade.log 2>&1 || { tail -20 /tmp/upgrade.log; exit 1; }
  dpkg-query -W -f="installed: \${Package} \${Version}\n" lintabos-core
  echo "== remove"
  apt-get remove -y -qq lintabos-core >/dev/null 2>&1 && [ ! -e /usr/bin/lintab-update ] && echo "ok   removed cleanly"
  rm -f out/lintabos-core_${NEXT}_all.deb
'
