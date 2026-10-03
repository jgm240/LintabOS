# Notices

LintabOS is an **unofficial** Debian-based operating system. It is not affiliated with, endorsed by, or sponsored
by Debian, Software in the Public Interest, Lenovo, Microsoft, Mozilla, Intel, the GNOME Foundation or the Linux
Foundation.

## Trademarks

- Debian is a registered trademark owned by Software in the Public Interest, Inc. LintabOS uses the name only to
  say truthfully what it is based on.
- Linux is the registered trademark of Linus Torvalds in the U.S. and other countries.
- Windows and BitLocker are trademarks of Microsoft Corporation. They are named here only to describe what
  LintabOS can read, install beside, and boot.
- Microsoft, Word, Excel, PowerPoint, OneDrive, Teams, Microsoft 365 and Office are trademarks of the Microsoft group of
  companies. LintabOS only offers shortcuts, which you add yourself from LintabOS Extras, that open Microsoft's own web sites in a window; it ships no Microsoft software,
  logos or icons (generic desktop icons are used), and is not affiliated with or endorsed by Microsoft.
- LibreOffice is a trademark of The Document Foundation; Teams for Linux is an unofficial community project (MIT), neither
  is shipped in the image: the optional Office Pack downloads them from Flathub.
- Chromium is a project of Google and contributors; the browser is Debian's unmodified Chromium package, used only to
  show the web apps. Nintendo, Joy-Con and Switch are trademarks of Nintendo; they are not used in LintabOS, whose
  keyboard snap sound is synthesised from scratch.
- KDE and Plasma are trademarks of KDE e.V.; Xfce is a trademark of its developers. Neither desktop is shipped in the ISO:
  the installer downloads Debian's unmodified packages only when you choose them.
- Lenovo and IdeaPad are trademarks of Lenovo. They are named only to say which hardware LintabOS is built for.
- Firefox is a trademark of the Mozilla Foundation. The browser is Debian's unmodified Firefox ESR package.
- GNOME is a trademark of the GNOME Foundation. Intel is a trademark of Intel Corporation.

## Credits

- **Tux**, the Linux penguin, was created by Larry Ewing (lewing@isc.tamu.edu) using The GIMP, and released with the
  permission "to use and/or modify this image … provided you acknowledge me … and The GIMP". The LintabOS logo is
  an original drawing of that character, standing in front of a tablet.
- The GRUB menu fonts are bitmap conversions of **DejaVu Sans** (derived from Bitstream Vera, © Bitstream, Inc.;
  DejaVu changes are public domain). Their license is in `LICENSES/Bitstream-Vera-DejaVu.txt` and is also
  installed next to the fonts.
- BitLocker support uses **dislocker** (GPL-2.0-or-later, with BSD-3-Clause parts) and, optionally, **libbde**
  (LGPL-3.0-or-later). The recovery-key rules and the volume layout were checked against dislocker's source and
  the libbde format documentation; no code was copied from either.

- The optional touch boot menu uses **rEFInd** by Roderick W. Smith (GPL-3.0-or-later, with BSD/FreeBSD-licensed parts;
  installed from Debian's `refind` package on request). Its Windows icon comes from that package.

- The optional Xfce touch setup uses **Onboard** (GPL-3.0-or-later) as its on-screen keyboard and the optional KDE setup uses
  **Maliit** (LGPL-2.1); both are Debian packages downloaded on request, with their own license texts.

- The optional **LintabOS Extras** download, on request and from their own sources: Rnote (GPL-3.0-or-later), Xournal++ (GPL-2.0-or-later),
  LibreOffice (MPL-2.0), Teams for Linux (GPL-3.0, unofficial), Bottles (GPL-3.0-or-later, runs Wine, LGPL-2.1-or-later) from Flathub, and
  **Waydroid** (GPL-3.0-or-later) from Debian backports. Waydroid downloads an Android system image from the Waydroid project; Google's
  apps are not included or enabled. None of these are in the ISO.
- **cage** (MIT), a one-app Wayland compositor, is installed with Waydroid to show Android full screen in Android mode.
- Android is a trademark of Google LLC, Wine is a trademark of the Wine project, Rnote, Xournal++, Bottles and Waydroid belong to their
  authors; they are named only to say what the Extras app can download.

- The **Computer Mode** Android app (`android/computermode`, MIT) is built from this repository's source with `aapt`, `apksigner` and
  `zipalign` from Debian and, at build time only, the Android API jar (Apache-2.0) and the R8/D8 compiler (BSD-3-Clause/Apache-2.0) from Maven;
  none of those are shipped. It is signed with a throw-away key that is published in the repository (it protects nothing).

## License of LintabOS's own code

The original files of this project (installer, partitioner, BitLocker tools, scripts, configuration and artwork) are
under the **MIT License** (`LICENSE`). Third-party material keeps its own license, as listed below. The Tux
credit above stays required wherever the logo is used.

## Software licenses

LintabOS is a collection of separate programs, each under its own license: about 1,560 Debian packages, mostly
GPL, LGPL, MIT, BSD and Apache-2.0 licenses, plus a few redistributable firmware blobs from Debian's
`non-free-firmware` section, and one MIT-licensed package from `non-free` (`intel-media-va-driver-non-free`, which only ships pre-built GPU kernels without source). Each package's
license is in `/usr/share/doc/<package>/copyright` on the installed system; full license texts are in
`/usr/share/common-licenses/`. The exact package list and versions are in `live/filesystem.packages` on the
installation medium.

Putting those programs on one medium is "mere aggregation" and does not bring any of them under another's
license (see GPL-2.0, section 2). The LintabOS installer and tools call dislocker and the other programs as
separate processes.

## Source code offer (GPL-2.0 §3(b), GPL-3.0 §6, LGPL)

For at least three years after you receive this image, you may obtain the complete corresponding source code of
the GPL- and LGPL-licensed programs in it, for no more than the cost of physically providing it, from:

    https://github.com/jgm240/LintabOS/issues  (open an issue titled "Source request")

The source packages are the Debian source packages with the versions listed in `live/filesystem.packages`
(Debian 13 "trixie" plus `trixie-backports`), and the LintabOS build files are the `live/` directory and
scripts of this project. `scripts/fetch-sources.sh` downloads exactly that set.
