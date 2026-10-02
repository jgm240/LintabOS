#!/usr/bin/env bash
# SPDX-License-Identifier: MIT
# Generates every raster/font asset from branding/logo.svg. Runs inside the
# builder container (needs rsvg-convert, ImageMagick, grub-mkfont).
set -euo pipefail
cd "$(dirname "$0")/.."

CHROOT=payload
BOOT=live/config/bootloaders/grub-pc
LOGO=branding/logo.svg
FONT=/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf
FONT_BOLD=/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf

mkdir -p "$CHROOT/usr/share/lintabos" "$CHROOT/usr/share/backgrounds/lintabos" \
         "$CHROOT/usr/share/grub/themes/lintabos" "$CHROOT/usr/share/icons/hicolor/scalable/apps" \
         "$CHROOT/usr/share/pixmaps" "$BOOT/live-theme"

# --- logo at the sizes the desktop, GDM and GRUB want -------------------------
for s in 64 128 256 512; do
  rsvg-convert -w "$s" -h "$s" "$LOGO" -o "$CHROOT/usr/share/lintabos/logo-$s.png"
done
cp "$LOGO" "$CHROOT/usr/share/lintabos/logo.svg"
cp "$LOGO" "$CHROOT/usr/share/icons/hicolor/scalable/apps/lintabos.svg"
cp "$LOGO" "$CHROOT/usr/share/icons/hicolor/scalable/apps/org.lintabos.Installer.svg"
cp "$CHROOT/usr/share/lintabos/logo-128.png" "$CHROOT/usr/share/pixmaps/lintabos.png"

# --- wallpaper + boot backgrounds: dark gradient, faint logo, hex dots ----------
python3 - "$LOGO" <<'PY'
import re, sys
logo = open(sys.argv[1]).read()
inner = re.sub(r'^.*?<svg[^>]*>', '', logo, count=1, flags=re.S).rsplit('</svg>', 1)[0]
def bg(w, h, logo_size, logo_x, logo_y, opacity):
    hexes = "".join(
        f'<circle cx="{x}" cy="{y}" r="2" fill="#ffffff" opacity="0.06"/>'
        for y in range(40, h, 64) for x in range(40 + (32 if (y // 64) % 2 else 0), w, 64))
    return f'''<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}">
<defs><radialGradient id="bg" cx="0.75" cy="0.35" r="1.0">
<stop offset="0" stop-color="#1c2f6b"/><stop offset="0.55" stop-color="#0d1838"/><stop offset="1" stop-color="#060b1c"/>
</radialGradient></defs>
<rect width="{w}" height="{h}" fill="url(#bg)"/>{hexes}
<svg x="{logo_x}" y="{logo_y}" width="{logo_size}" height="{logo_size}" viewBox="0 0 512 512" opacity="{opacity}">{inner}</svg>
</svg>'''
open('/tmp/wallpaper.svg', 'w').write(bg(2000, 1200, 900, 1000, 150, 0.22))
open('/tmp/splash.svg', 'w').write(bg(1920, 1200, 1, -10, -10, 0))   # no big logo: the theme draws it
PY
rsvg-convert /tmp/wallpaper.svg -o "$CHROOT/usr/share/backgrounds/lintabos/lintabos.png"
rsvg-convert /tmp/splash.svg -o "$CHROOT/usr/share/grub/themes/lintabos/background.png"
cp "$CHROOT/usr/share/grub/themes/lintabos/background.png" "$BOOT/splash.png"

# --- GRUB theme: logo, selection bar, big fonts -----------------------------------
T="$CHROOT/usr/share/grub/themes/lintabos"
cp branding/grub-theme/theme.txt "$T/theme.txt"
rsvg-convert -w 256 -h 256 "$LOGO" -o "$T/logo.png"
convert -size 8x8 xc:none -fill "#2a4fb8" -draw "roundrectangle 0,0 7,7 3,3" "$T/select_c.png"
# 9-slice style needs select_{c,n,s,e,w,ne,nw,se,sw}.png
for p in n s e w ne nw se sw; do cp "$T/select_c.png" "$T/select_$p.png"; done
for size in 28; do
  grub-mkfont -s "$size" -n "DejaVu Sans" -o "$T/dejavu_regular_$size.pf2" "$FONT"
  grub-mkfont -s "$size" -n "DejaVu Sans" -o "$T/dejavu_bold_$size.pf2"    "$FONT_BOLD"
done
grub-mkfont -s 40 -n "DejaVu Sans" -o "$T/dejavu_bold_40.pf2" "$FONT_BOLD"

# --- live ISO menu: same look, files live next to grub.cfg ---------------------------
cp branding/live-theme/theme.txt "$BOOT/live-theme/theme.txt"
cp "$T/logo.png" "$BOOT/logo.png"
cp "$T"/dejavu_*.pf2 "$BOOT/"
# --- Plymouth boot splash (replaces desktop-base's Debian-branded one) ---------------------------
PLY="$CHROOT/usr/share/plymouth/themes/lintabos"
mkdir -p "$PLY"
cp branding/plymouth/lintabos.plymouth branding/plymouth/lintabos.script "$PLY/"
rsvg-convert -w 256 -h 256 "$LOGO" -o "$PLY/logo.png"

# --- licence texts that must travel with what we derived from them -----------------------------------
cp LICENSES/Bitstream-Vera-DejaVu.txt "$T/FONT-LICENSE.txt"   # the GRUB .pf2 fonts are converted from DejaVu
# --- touch boot menu (rEFInd) icon + background; the Windows icon comes from the refind package -------------
RF="$CHROOT/usr/share/lintabos/refind"
mkdir -p "$RF"
rsvg-convert -w 256 -h 256 "$LOGO" -o "$RF/lintabos.png"
cp "$T/background.png" "$RF/background.png"

# --- the snap played when the folio keyboard is attached or detached (synthesised, see the script) ---------
python3 scripts/gen_keyboard_sounds.py "$CHROOT/usr/share/lintabos/sounds"
echo "assets generated"
