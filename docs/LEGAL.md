# Legal, copyright and license review of LintabOS

**This is a research report, not legal advice.** It was prepared by reading the licenses and policies named below
and auditing what is actually inside the built ISO (`scripts/license-audit.py`). The author is not a lawyer. Before
distributing LintabOS publicly or commercially, have a qualified lawyer in *your* country confirm the points marked
**ask a lawyer**. Facts that could not be verified are marked **unverified**.

Audit basis: the ISO built on 2026-10-01 (Debian 13 "trixie" + `trixie-backports`), 1,564 installed packages.

---

## 1. Summary

| Question | Result | Risk |
|---|---|---|
| Is distributing BitLocker unlock tools legal? | The tools are ordinary, widely distributed free software (dislocker is in Debian's main archive). They need the owner's recovery key and are not a cracking tool. Details in §2. | Low |
| Is Tux copyrighted? | Yes. Larry Ewing's 1996 image carries a permissive grant that requires credit. LintabOS's logo is an original drawing of the character and credits him. Trademark status of Tux itself: **unverified**. Details in §3. | Low |
| Are the program licenses compatible with each other? | Yes. They are separate programs on one medium ("mere aggregation"), and LintabOS's own tools call them as separate processes. Details in §4. | Low |
| Obligations when you distribute the ISO | Source code offer, license texts, trademark acknowledgements, a license for your own code. Details in §5 and §6. | **Action needed** |
| The name "LintabOS" | A quick web search found no conflicting brand, which is *not* a trademark search. Details in §7. | Unknown |

Things found and fixed during this review:

- **The boot splash showed a Debian logo** (from the `desktop-base` package, which a derivative inherits if it installs
  it unmodified). Debian's trademark policy says "Do not use the Debian logos as part of your company logo or product logo
  or branding itself" and forbids implying affiliation, but it does **not** specifically address boot or desktop artwork.
  Replacing the splash is therefore a **precaution, not a clear requirement**: a "debian 13" screen is the first thing users
  see when they start a product called LintabOS, which can look like official Debian or like Debian's logo as part of your
  branding. LintabOS now ships its own splash.
- **The GRUB fonts are converted from DejaVu**, whose license requires its notice to accompany copies. The notice is
  now installed next to the fonts.
- **No attribution, trademark or source-offer notice existed.** `NOTICE.md` now provides them (you must fill in the
  contact address).
- **LintabOS's own code has no license file**, which legally means *nobody* may copy or redistribute it. This is your
  decision: see §6.

---

## 2. BitLocker tools

### What LintabOS ships

- **dislocker** 0.7.3 (GPL-2.0-or-later with BSD-3-Clause parts) and **libbde-utils** (LGPL-3.0-or-later), both
  unmodified Debian packages from the main archive, which means Debian's maintainers reviewed their licensing.
- LintabOS's own code (`lintab-bitlocker`, the installer step, the "Unlock BitLocker Drive" app). It calls dislocker as
  a separate process, requires the **48-digit recovery key**, and contains no key-guessing or key-extraction code.
- No Microsoft code or documentation is included in the ISO.

### Why this is generally lawful (summary of the sources read)

- **United States, DMCA §1201.** Circumvention is defined as bypassing a protection measure "without the authority of the
  copyright owner" (17 U.S.C. §1201(a)(3)(A)). A person unlocking their own drive with their own recovery key is
  exercising authority, not circumventing. §1201(f) additionally protects reverse engineering to achieve
  interoperability, and permits sharing the means "solely for the purpose of enabling interoperability" (§1201(f)(3)).
- **European Union.** Directive 2009/24/EC Art. 6 lets a lawful user decompile a program where indispensable to obtain
  the information needed for interoperability, provided the information was not readily available, with limits on
  reuse (Art. 6(2)). Germany implements this as §69e UrhG.
- **Germany, §202c StGB** ("Hackerparagraf", up to two years) punishes preparing a data-espionage offence by making
  or distributing programs "whose purpose is the commission of such an offence". The Federal Constitutional Court held
  in 2009 (2 BvR 2233/07) that **dual-use tools are not covered**; only programs developed to commit such offences
  are, with intent. A tool that requires the owner's key and is documented for recovering one's own data does not fit.
- **Austria, §126c StGB** is similar: it requires a program whose special nature makes it designed for the listed
  offences, and intent that it be used for them.
- **Precedent in practice:** dislocker is packaged by Debian, Ubuntu, Fedora and others, and libbde is a widely used
  forensics library.

### Keep it that way (practical rules)

1. **Never add key-cracking.** No brute-forcing of PINs or passwords, no extraction of keys from memory or the TPM.
   That would change the legal picture in every jurisdiction above.
2. Describe the feature as what it is: *reading or decrypting your own drive with your recovery key.* The UI and README
   already say so. Avoid marketing it as "bypassing" or "breaking" BitLocker.
3. The test-only volume generator (`tests/bitlocker_image.py`) makes BitLocker-format images with a *known* key. It is
   not in the ISO. It was written from published format documentation and tested against dislocker; it contains no
   Microsoft code.

### Open points

- **Windows license terms (EULA)** restrict reverse engineering of Windows *software*. LintabOS neither includes nor
  modifies Windows code; it reads and checksums files on the user's own disk. The EULA text was not retrieved for this
  review (**unverified**).
- **Patents.** No BitLocker-specific patent claims against read access were found, but patent searches are outside
  what could be done here (**unverified; ask a lawyer** if distributing commercially in the US).
- **Export rules for cryptography** (US EAR, EU dual-use regulation) generally exempt publicly available open-source
  software, and Debian distributes the same packages, but **ask a lawyer** if you distribute commercially from the US.
- **In-place decryption** changes the user's data on request. It is documented with warnings, but you may want a
  disclaimer of liability in your distribution terms (**ask a lawyer** how effective that is where you live; in the EU,
  consumer law limits what can be disclaimed).

---

## 2b. Microsoft 365 shortcuts, OneDrive, Teams (added in 0.2.0)

- LintabOS ships **launchers** that open Microsoft's own web apps (`office.com/launch/word`, `…/excel`, `…/powerpoint`,
  `…/onedrive`, `teams.microsoft.com`) in a Chromium window. No Microsoft code, logo or icon is copied; the launchers use
  generic icons from the desktop icon theme and say "(web)" in their names. Using those sites needs the user's own Microsoft
  account and is under Microsoft's terms, not ours. The names Word/Excel/PowerPoint/OneDrive/Teams are used only to tell people
  what each shortcut opens (nominative use); see the trademark list in `NOTICE.md`.
- The **Office Pack** is a user-initiated download from Flathub of LibreOffice (MPL-2.0) and Teams for Linux (MIT,
  unofficial). Nothing is redistributed in the ISO, and the dialog says they are not made by Microsoft.
- **School mode** only switches off hardware the owner already controls; it is not a surveillance feature. Turning it off needs
  an administrator, which is the point of the feature. It should not be used to stop someone using their own device without
  their knowledge.
- **Chromium** (BSD-style plus bundled third-party licenses) and **rEFInd** (GPL-3.0-or-later) come from Debian's packages
  and add to the source offer in `NOTICE.md`. The keyboard snap sound is generated by `scripts/gen_keyboard_sounds.py` (MIT)
  and does not use any recording.

## 3. The Tux logo

- **Copyright:** Tux was created by Larry Ewing in 1996 with The GIMP. Per Wikipedia and a second source, he released it with
  the permission *"to use and/or modify this image … provided you acknowledge me [lewing@isc.tamu.edu] and The GIMP if
  someone asks."* (The original page, `isc.tamu.edu/~lewing/linux/`, no longer loads, so the exact wording is from
  secondary sources.) That permits commercial and non-commercial use and modification with credit.
- **LintabOS's logo** (`branding/logo.svg`) is an original vector drawing that depicts the Tux character in front of a
  generic tablet. It does not contain Ewing's image file. Whether a fresh drawing of the character counts as a derivative
  is unsettled, but either way it falls within his permission, provided you credit him, which `NOTICE.md` does.
- **Trademark:** the Linux Foundation states that the **"Linux" mark is owned by Linus Torvalds** and administered by the
  Linux Mark Institute. Its page says nothing about Tux, so whether Tux is a registered mark is **unverified**.
  Mitigation: the name "LintabOS" does not contain "Linux", and `NOTICE.md` carries the standard acknowledgement.
- **Tablet drawing / Lenovo:** the tablet is a generic rounded rectangle, not a copy of Lenovo's industrial design or
  logo. "Lenovo IdeaPad Duet 3" appears only as text identifying the supported hardware (nominative use), and `NOTICE.md`
  says LintabOS is not affiliated with Lenovo.

---

## 4. License compatibility

### What is in the image (from `scripts/license-audit.py`)

| Component | Packages |
|---|---|
| Debian `main` | 1,554 |
| `non-free-firmware` | 9 (Intel/Realtek/MediaTek firmware, Intel microcode, SOF audio firmware) |
| `non-free` | 1 (`intel-media-va-driver-non-free`; its copyright file says it is in `non-free` only because it ships pre-built GPU kernels without source. The license is MIT/Expat, which allows redistribution, even sale) |
| `contrib` | 0 |

Most common licenses (a package can carry several): GPL-2+, MIT, LGPL-2.1+, BSD-3-Clause, GPL-3+, GPL-2-only,
BSD-2-Clause, public domain, Apache-2.0, ISC, LGPL-3+, MPL.

### Why they do not conflict

1. **Separate programs.** GPL-2.0 §2 states that *"mere aggregation of another work not based on the Program with the
   Program … on a volume of a storage or distribution medium does not bring the other work under the scope of this
   License."* A Linux distribution is this kind of aggregation. The GPL-2-only kernel, GPL-3 programs (GRUB, live-boot)
   and MIT/Apache programs coexist in every distribution.
2. **dislocker (GPL-2+) and Apache-2.0 mbedTLS.** GPL-2 and Apache-2.0 are incompatible *in a single combined work*, but
   Debian's mbedTLS is dual-licensed Apache-2.0 **or** GPL-2+, so dislocker uses it under the GPL option. Debian ships
   exactly this combination.
3. **LintabOS code and dislocker.** LintabOS's Python code runs `dislocker-fuse` as a subprocess and talks to it over a
   terminal. It does not link against it. The GPL FAQ treats exec/pipes between separate programs as separate works
   unless the exchange is intimate enough to make one program. Here it is a password and a mount point.
4. **LintabOS code and GTK/libadwaita/PyGObject.** These are LGPL-2.1+; any license may use LGPL libraries.
5. **cryptsetup** (GPL-2+) carries an explicit OpenSSL exception. **Firefox** is MPL-2.0/other, **fonts** are OFL/Apache/
   Bitstream Vera: all independent of the above.
6. **GPL-3 "installation information"** (anti-lock-down) applies to consumer "User Products" that block modified
   software. LintabOS boots under Secure Boot with Debian's signed shim, and the user can disable Secure Boot or enroll
   their own keys, so there is no lock-down.

### Third-party notices that must be kept

- **DejaVu / Bitstream Vera fonts:** the license requires the copyright and permission notice to be included in copies,
  and forbids selling the font by itself. The GRUB `.pf2` fonts are conversions of DejaVu; the notice is installed with
  them (`FONT-LICENSE.txt`) and in `LICENSES/`.
- **Intel microcode** (`non-free-firmware`): redistribution in binary form without modification is allowed if the
  copyright notice and conditions are reproduced and there is no reverse engineering. The image keeps Debian's unmodified
  package and its `copyright` file, which satisfies this. This is the same arrangement as Debian's own official images
  (which include `non-free-firmware`).
- **Firmware** under `binary-redist-*` licenses: redistributable by definition of Debian's `non-free-firmware` section;
  license texts are in each package's `copyright` file, which stays in the image.

### Patents and codecs (risk, not a conflict)

The image contains `libavcodec` and GStreamer's `ugly`/`bad` plug-ins, which implement patent-encumbered codecs (e.g.
H.264, HEVC, AAC). Debian ships these in its main archive, and software patents are not enforceable in most countries, but
**they can be in the United States**. If you distribute from or to the US commercially, **ask a lawyer**, or remove
`gstreamer1.0-plugins-ugly`, `gstreamer1.0-libav` and the HEVC pieces of `intel-media-va-driver-non-free`.

---

## 5. Obligations when you distribute the ISO

1. **Source code.** GPL-2.0 §3 requires, for binaries, that you either (a) accompany them with the complete source,
   or (b) accompany them with a **written offer, valid for at least three years**, to give any third party the source. Option
   (c) (just passing on someone else's offer) is **only for non-commercial** distribution. Pointing people at Debian's
   servers is therefore *not* enough for general or commercial distribution. `NOTICE.md` contains the written offer
   (**you must fill in your contact address**), and `scripts/fetch-sources.sh` downloads the exact source packages.
   **Keep that source archive available for at least three years.**
2. **License texts and notices:** `NOTICE.md`, `LICENSES/` and each package's `copyright` are installed on the image.
3. **Trademark acknowledgements** (Debian policy requires acknowledging SPI's ownership prominently and stating non-affiliation):
   in `NOTICE.md` and the README. Put the same sentence on your download page.
4. **Do not make the Debian logo part of your own branding, and do not imply you are Debian.** The Open Use Logo's
   *copyright* licenses (LGPL-3+ or CC-BY-SA-3.0) allow copying it; the restriction is *trademark* law. Saying truthfully
   that LintabOS is based on Debian (README, `os-release`) is fine and encouraged. LintabOS's splash, wallpaper, GRUB theme
   and login screen use LintabOS artwork. (`desktop-base` is still installed because GNOME's metapackage recommends it;
   it is no longer what you see at boot.)
5. **Firefox:** Mozilla's policy allows the Firefox name and logo on **unmodified** redistributed binaries. Do not
   rebuild or patch Firefox without removing Mozilla branding.
6. **Your own license** (next section).

---

## 6. A license for LintabOS's own code

**Decision: MIT** (chosen by the author; see `LICENSE`). Before that choice there was none, which meant "all rights reserved": nobody may legally copy, modify or redistribute the installer,
partitioner and BitLocker tools. Pick one before publishing. Compatible options:

| License | Effect | Fit |
|---|---|---|
| **GPL-3.0-or-later** | Anyone may use and change it; changes stay open. Compatible with dislocker (GPL-2+), GRUB (GPL-3), GTK (LGPL). | **Recommended.** Removes any doubt about the parts of the code that were informed by reading GPL dislocker's source (rules and constants, not copied code). |
| GPL-2.0-or-later | Same, slightly older. | Fine. |
| MIT / BSD-2-Clause / Apache-2.0 | Anyone may use it, including in closed products. | Legally fine here because the code only *calls* GPL programs. Slightly more exposure on the "informed by GPL source" question. |

Whichever you choose, add a `LICENSE` file and an SPDX header such as `# SPDX-License-Identifier: GPL-3.0-or-later` to the
source files. The artwork (`branding/`) can carry its own license, e.g. CC-BY-SA-4.0 with the Tux credit kept.

---

## 7. The name "LintabOS"

A web search found no company or product called "Lintab", which is only a weak signal. Before investing in the name,
check the trademark registers (EUIPO TMview, DPMA, ÖPA, USPTO) for "LintabOS" and "Lintab" in the software, computer
and operating-system classes (9 and 42). Domain and package-name conflicts are a separate check.

---

## 8. Checklist before publishing

- [x] `LICENSE` (MIT) and SPDX headers added; replace "The LintabOS authors" in `LICENSE` with your own name if you prefer (§6)
- [ ] Fill in the source-offer contact in `NOTICE.md` (§5.1)
- [ ] Run `scripts/fetch-sources.sh`, keep the archive for 3+ years (§5.1)
- [ ] Trademark search for the name (§7)
- [ ] Decide about patent-encumbered codecs if you distribute commercially from/to the US (§4)
- [ ] Have a lawyer review the points marked **ask a lawyer**
- [ ] Re-run `scripts/license-audit.py` for every release

## Sources read

- Larry Ewing's Tux permission, via [Wikipedia](https://en.wikipedia.org/wiki/Tux_(mascot)) and a search result summary
- [Debian trademark policy](https://www.debian.org/trademark) and [Debian logo licenses](https://www.debian.org/logos/)
- [Linux Foundation trademark pages](https://www.linuxfoundation.org/legal/trademark-usage)
- [Mozilla trademark policy](https://www.mozilla.org/en-US/foundation/trademarks/policy/)
- [17 U.S.C. §1201](https://www.law.cornell.edu/uscode/text/17/1201)
- [Directive 2009/24/EC, Art. 6](https://www.legislation.gov.uk/eudr/2009/24/article/6) (UK-published copy of the EU text)
- [§202c StGB](https://www.gesetze-im-internet.de/stgb/__202c.html), [§69e UrhG](https://www.gesetze-im-internet.de/urhg/__69e.html),
  [§126c StGB (AT)](https://www.jusline.at/gesetz/stgb/paragraf/126c)
- [BVerfG 2 BvR 2233/07 (2009)](https://www.hrr-strafrecht.de/hrr/bverfg/07/2-bvr-2233-07.pdf), via search summaries
- [dislocker](https://github.com/Aorimn/dislocker) and [libbde](https://github.com/libyal/libbde) project pages
- GPL-2.0 text shipped in the image (`/usr/share/common-licenses/GPL-2`), and the per-package copyright files audited
