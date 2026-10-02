<p align="center"><img src="branding/logo.svg" width="180" alt="LintabOS logo: Tux in front of a tablet"></p>

# LintabOS

Debian 13 + GNOME + GDM, tuned for the **Lenovo IdeaPad Duet 3** (built around the
**11IAN8**, Intel N100/N200, UFS, fingerprint reader in the power button).
It boots from a USB stick, installs with a touch-friendly installer, and can
**shrink Windows and dual-boot with it** through GRUB, which has a
**Boot into Windows** entry.

## What's in it

| Feature | How it's done |
|---|---|
| Debian 13 "trixie", GNOME 48, GDM | live-build image; kernel, libcamera, PipeWire and firmware from `trixie-backports` (kernel 7.x) because the Duet's cameras, UFS and sensor hub need newer code than stable ships |
| Screen rotation | Intel Integrated Sensor Hub → `hid-sensor-*` → IIO → `iio-sensor-proxy` → GNOME. Rotation lock is in Quick Settings |
| On-screen keyboard | GNOME's built-in OSK, forced on (`screen-keyboard-enabled`) in the session **and** at the GDM login screen, because the detachable folio keyboard is a USB device |
| Fingerprint | `fprintd` + `libpam-fprintd`; libfprint 1.94.9 lists the Goodix `27c6:6512` used in the 11IAN8. Enroll in *Settings → System → Users → Fingerprint Login*; GDM offers it at login |
| Camera | Intel IPU6 (`8086:462e`) kernel driver + libcamera 0.7 software ISP + PipeWire; GNOME Snapshot and Firefox (via the camera portal) |
| LED | Camera privacy LED is driven by the kernel camera driver while streaming. Other LEDs the kernel exposes are controllable with `lintab-led` |
| Bootloader | GRUB (UEFI, Secure Boot via Debian shim), themed, with a **Boot into Windows** entry and a *Restart into Windows* app (`grub-reboot`, one-shot) |
| Dual-boot partitioner | `lintab-partitioner` (CLI) and the installer's disk page, see below |
| BitLocker | dislocker installed and set up (FUSE, polkit, menu entry): **Unlock BitLocker Drive** app to open an encrypted Windows drive with its recovery key, and an installer step that can turn BitLocker off so Windows can be shrunk |
| Updates | **LintabOS Updates** (`lintab-update`) installs signed releases of LintabOS's own parts from this repository's GitHub releases; Debian's packages update through Software. See [docs/UPDATES.md](docs/UPDATES.md) |
| Memory | zram swap (no swapping to the UFS) |
| Automatic tablet mode | `lintab-tablet-mode` watches udev: folio keyboard (or any USB/Bluetooth keyboard) attached → on-screen keyboard off, detached → on. A **"Keyboard Attached" / "Keyboard Detached"** notification and a short snap sound (synthesised in `scripts/gen_keyboard_sounds.py`) play each time. `lintab-tablet-mode auto\|tablet\|laptop` overrides it |
| School mode | **School Mode** app / `lintab-school-mode on`: blocks the camera drivers, makes every `/dev/video*` and `/dev/media*` node unopenable, and de-authorizes USB webcams. Turning it on needs no password; **turning it off needs an administrator's password** (polkit). `lintab-school-mode verify` checks that no camera is reachable |
| Windows files in the sidebar | When installed next to Windows, the Windows drive shows as **Windows** in Files (read-only, mounted on first click, via `/etc/fstab` + `x-gvfs-show`). `lintab-windows-files enable\|disable [--read-write]` for existing installs; read-write is refused if Windows left the drive unclean. A BitLocker drive appears after **Unlock BitLocker Drive** |
| Touch boot menu | **Touch Boot Menu** app / `lintab-boot-menu enable`: installs rEFInd next to GRUB with big LintabOS and Windows icons and touch enabled. Own boot entry, first in line; GRUB and Windows are not touched and `disable` undoes it. **Refuses while Secure Boot is on**; touch also depends on the tablet's firmware |
| Word, Excel, PowerPoint, OneDrive, Teams | Microsoft's own **web apps** in their own windows (Chromium app mode, one shared sign-in), plus *Connect OneDrive to Files* (GNOME Online Accounts). **There is no native Office or Teams for Linux.** The optional **Office Pack** downloads LibreOffice (edits .docx/.xlsx/.pptx offline) and the unofficial Teams for Linux from Flathub |

## Download

Get the latest ISO from the [releases page](https://github.com/jgm240/LintabOS/releases) and verify it against `SHA256SUMS`
(`shasum -a 256 -c SHA256SUMS`). Releases are marked **pre-release** until LintabOS has been tested on the tablet.

## Updates

LintabOS's own parts are one Debian package, `lintabos-core`. **LintabOS Updates** checks this repository's releases once a
day (an unprivileged request to `api.github.com`; turn it off in `/etc/lintabos/update.conf`), shows a notification, and installs
a new release only when you press **Update now**. Every package is signature-checked against a key shipped in the OS, and
downgrades are refused. Details, the threat model and key handling: [docs/UPDATES.md](docs/UPDATES.md).

## Build the ISO

Needs Docker. On Apple Silicon the amd64 toolchain runs emulated: the first build takes a
long time and ~15 GB of disk.

```bash
./build.sh          # → out/lintabos-amd64.iso (+ .sha256)
```

Write it to a USB stick (double-check the device!):

```bash
diskutil list                                   # find the stick, e.g. /dev/disk5
diskutil unmountDisk /dev/disk5
sudo dd if=out/lintabos-amd64.iso of=/dev/rdisk5 bs=4m status=progress
```

Boot the tablet from it: hold **Vol-down** (or **Fn+F12**/Novo button) while powering on
and choose the USB stick. Secure Boot can stay on (Debian's shim is Microsoft-signed).

## Installing next to Windows

**Windows must not be encrypted, because an encrypted partition can't be shrunk safely.** Windows 11 on the
11IAN8 usually has BitLocker ("Device encryption") on. The installer detects this and shows a
**Turn off BitLocker** step with two routes:

- **Safest, recommended: turn it off in Windows.** *Settings → Privacy & security → Device encryption → Off*
  (Pro: *BitLocker Drive Encryption → Turn off*, or `manage-bde -off C:`), wait until decryption finishes, run
  `powercfg /h off`, shut down fully (hold Shift while clicking Shut down), and start the installer again.
- **From the installer, with your 48-digit recovery key.** See [Turning BitLocker off from the installer](#turning-bitlocker-off-from-the-installer).

Also, before shrinking: turn off Fast Startup (`powercfg /h off`), make sure Windows was shut down cleanly
(`chkdsk C: /f` if not), and back up anything you can't lose.

Then boot the USB stick, choose **Install alongside Windows**, and drag the slider.
What the installer does to the disk, in order (the exact commands are shown before you confirm):

1. Saves the partition table (`sfdisk --dump`) and keeps a copy in the installed system at
   `/var/lib/lintab/partition-table-before-install.sfdisk`.
2. Dry-runs `ntfsresize --no-action`, then shrinks the **NTFS filesystem**.
3. Shrinks the **partition** to match (`sfdisk -N`, which keeps its type, GUID and name).
4. Creates the LintabOS partition in the freed space. The MSR, recovery partition and Windows'
   EFI partition are not modified; GRUB goes into its own `EFI/LintabOS` folder on the existing ESP.

The first time Windows starts afterwards it runs a disk check. That is expected: `ntfsresize`
sets the "check disk" flag on purpose.

At boot, GRUB shows **LintabOS** and **Boot into Windows**. Inside LintabOS, the
*Restart into Windows* app reboots straight into Windows once.

Other options on the same page: use existing free space, or erase the whole disk.

### Keeping Windows bootable

That is the point of this project, so, concretely:

- The partitioner never touches Windows' EFI files, MSR or recovery partition; GRUB goes in its own `EFI/LintabOS`
  folder. The Windows partition keeps its start, GUID, type and name, and is only made smaller (tested).
- After installing GRUB the installer checks that Windows' boot manager file is still on the EFI partition and that the
  GRUB menu has **Boot into Windows**, and refuses to report success otherwise.
- **Keep BitLocker off.** Windows with BitLocker on and a TPM measures how it was started; starting it from GRUB (rather
  than straight from the firmware) changes that measurement, so Windows asks for the recovery key at every boot. If
  Windows turns Device encryption back on later (it can do that after you sign in with a Microsoft account), turn it off
  again in Settings.
- If Windows (or its updates) moves itself to the front of the firmware boot order, pick LintabOS from the firmware boot
  menu once, or run `sudo efibootmgr --bootorder ...`.

### Turning BitLocker off from the installer

The step asks for the recovery key and runs every check **without writing anything**: the key must unlock the drive,
the drive must be fully encrypted and healthy, the charger must be plugged in, and the EFI partition needs room for a
recovery journal. Only then, after you switch on "I've backed up…" and type `DECRYPT`, does it start.

How it protects your data (all of it exercised by tests, including simulated power cuts):

- It reads the drive through dislocker (a read-only decrypted view) and writes the plaintext back over the same sectors.
  **Before each chunk is overwritten, its plaintext is saved to a journal on the EFI partition and synced**; progress is
  recorded after. If power fails at *any* point (mid-write, after journaling, after the header, while scrubbing),
  starting the installer again resumes, and the result is byte-identical to the original NTFS volume.
- The BitLocker header, whose real NTFS boot sector lives in a backup area, is replaced **last**, and only if the
  restored sector looks like NTFS. Until then the drive is still a valid BitLocker volume.
- Before the first write it records checksums of the files Windows needs to boot (`winload.efi`, `ntoskrnl.exe`, the
  registry hives, …) through the decrypted view; afterwards it re-reads them from the plain volume and requires an exact
  match. Everything written is also read back and compared with recorded checksums, and the file system is checked.

What it **cannot** promise: that Windows boots afterwards. The tests use synthetic volumes that real dislocker
unlocks, not an actual Windows install. This rewrites the whole Windows partition, so back up first; turning BitLocker
off inside Windows remains the safest route.

From a terminal:

```bash
sudo lintab-bitlocker list
sudo lintab-bitlocker decrypt /dev/sda3 --journal-dir /boot/efi --check-only   # checks only, changes nothing
sudo lintab-bitlocker unlock /dev/sda3 --mount                                 # open the files read-only
sudo lintab-bitlocker lock /dev/sda3
```

The recovery key is typed at a prompt (or piped with `--key-stdin`) and passed to dislocker over a pseudo-terminal, never
on a command line. The desktop app **Unlock BitLocker Drive** wraps the same tool. GNOME Files can also unlock
BitLocker drives itself through cryptsetup.

### The partitioner on its own

```bash
sudo lintab-partitioner list                                   # disks, Windows, how much can be freed
sudo lintab-partitioner plan  /dev/sda --mode dualboot --size-gb 50   # prints the plan, writes nothing
sudo lintab-partitioner apply /dev/sda --mode dualboot --size-gb 50 --dry-run
sudo lintab-partitioner apply /dev/sda --mode dualboot --size-gb 50 --yes I-UNDERSTAND
```

It refuses: BitLocker volumes, hibernated or dirty NTFS, non-GPT disks, a missing Windows boot
manager on the ESP, an ESP with < 16 MB free, and asking for more than Windows can give up
(it always leaves Windows its minimum size + 4 GB).

## Checking the hardware

On the installed system (or the live session):

```bash
lintab-hwcheck
```

It tests each layer (sensor hub → IIO → iio-sensor-proxy, USB fingerprint reader → fprintd,
IPU6 → libcamera, LEDs, OSK) and says which one is missing.

## Honest status: what has and hasn't been tested

Tested in this repository, automatically, in the builder container:

- **Partitioner**: a fake Windows tablet disk (ESP + MSR + NTFS + WinRE) is shrunk with the real
  tools at both 512-byte and 4096-byte sectors (the 11IAN8's UFS uses 4K). The Windows data
  checksum, the other partitions' GUIDs/offsets, and `ntfsfix` consistency are verified. BitLocker
  and over-asking are refused. `./scripts/test-partitioner.sh`
- **GRUB "Boot into Windows" generator**: emits the entry only when Windows' boot manager is on an ESP.
- **BitLocker (unlock + decrypt in place)**: 42 tests against the *real* `dislocker`, using a BitLocker volume generator
  checked against dislocker itself (AES-XTS and AES-CBC): right/wrong recovery keys, the key never appearing on a command
  line, byte-identical results, Windows boot-file checksums, and recovery from simulated power loss at eight different
  points. `./scripts/test-bitlocker.sh`
- **Installer GUI**: renders and walks through all pages, including the BitLocker step, against synthetic disks.
  `./scripts/test-gui.sh`
- Package names: every package in the image list has a candidate in Debian 13 + backports.
- **Updater and package**: `lintabos-core` installs, upgrades and removes cleanly in a clean Debian 13 container
  (`./scripts/test-package.sh`), and the updater is tested against a fake GitHub with real signatures, covering a wrong key, a
  tampered file, a swapped package, a downgrade and an untrusted redirect (`./scripts/test-updater.sh`). It has not yet
  updated a running installed system.

**Not tested on a real Duet 3 (I don't have one).** These depend on hardware details I could only
read from upstream sources and other people's probes, so treat them as expected-to-work, not verified:

- **Camera**: libcamera needs a supported sensor *and* IPU6 pipeline support. The 11IAN8's exact sensors
  are not documented upstream; public probes show the ISP but not whether the front/rear sensors
  work. If `lintab-hwcheck` shows no cameras, that is the gap.
- **Rotation direction**: if the screen rotates the wrong way, the sensor's mount matrix needs an entry:
  ```
  # /etc/udev/hwdb.d/61-sensor-local.hwdb
  sensor:modalias:platform:HID-SENSOR-200073:dmi:*:svnLENOVO:*:pvrIdeaPadDuet311IAN8*
   ACCEL_MOUNT_MATRIX=0, 1, 0; -1, 0, 0; 0, 0, 1
  ```
  then `sudo systemd-hwdb update && sudo udevadm trigger`. Try the matrices in `monitor-sensor --accel`
  output order until up is up. If the *panel itself* is sideways at boot, add
  `video=eDP-1:panel_orientation=right_side_up` (check the connector name in `/sys/class/drm`) to
  `GRUB_CMDLINE_LINUX_DEFAULT` in `/etc/default/grub.d/lintab.cfg`.
- **Fingerprint**: the USB ID is supported by libfprint's driver list; enrollment hasn't been tried
  on the hardware.
- **LED**: the 11IAN8 datasheet lists no user LED. I assumed "the LED" means the camera privacy LED
  (handled by the kernel camera driver) and any LEDs under `/sys/class/leds` (`lintab-led list`).
  If you meant something else, say so.
- **GRUB has no touch input**: the default boot menu needs the folio keyboard (or Bluetooth keyboard paired
  in firmware). The optional touch boot menu (rEFInd) only works with Secure Boot off, and whether the tablet's firmware
  passes touch to it is untested.
- **0.2.0 features** (tablet mode, snap sound, school mode, Windows sidebar, touch boot menu, Microsoft 365 apps): the
  decision logic and generated files are tested (`./scripts/test-features.sh`, 24 tests; school-mode udev rules checked with
  `udevadm verify`; the boot menu run against a fake firmware and ESP). Not tested on hardware: whether the folio's
  detach/attach is seen as a keyboard appearing and disappearing, whether a camera is really dead in school mode on the Duet,
  rEFInd's look and touch on the tablet, and the Microsoft web apps (they need a Microsoft account and depend on Microsoft's sites).
- **BitLocker on real Windows**: the volumes above follow the published format and real dislocker accepts them, but they
  are not Windows-made. Windows' own BitLocker variants (4K-sector drives, "used space only" encryption, Elephant
  diffuser on older volumes) are handled by dislocker for reading; the in-place decryption has only run on the
  synthetic 512-byte-sector volumes. Whether Windows boots afterwards is untested.
- The ISO boots in UEFI under QEMU, reaches the GNOME desktop and starts the installer by itself; **it has not yet been
  booted on the tablet**. The GRUB menu of the live USB currently shows GRUB's default look instead of the LintabOS theme (cosmetic).

Also supported in principle: the **10IGL5** (Celeron/Pentium, eMMC). It has no fingerprint reader and
a different camera/sensor stack (the kernel already carries its panel-rotation quirk), so only the
installer/partitioner/GRUB parts are the same.

## License

LintabOS's own code is under the [MIT License](LICENSE). The software installed in the image keeps its own licenses; see
[NOTICE.md](NOTICE.md) and [docs/LEGAL.md](docs/LEGAL.md).

## Layout

```
branding/        logo.svg, GRUB and boot-splash themes
docker/          builder image (Debian 13, amd64)
installer/       lintab/ (disks, plan, install, bitlocker, bitlocker_decrypt, update, gui, cli) and bin/ launchers
payload/         files of the lintabos-core package (/etc, /usr tree): system tuning, polkit, units, assets
packaging/       Debian control files and the public update key
live/            live-build config: package lists, hooks, live-session extras, ISO GRUB menu
scripts/         build-deb.sh, release.sh, make-assets.sh, check-packages.sh, license-audit.py, fetch-sources.sh and the test runners
docs/            LEGAL.md (license/copyright review), UPDATES.md, release notes
tests/           loop-device partitioner tests, GUI smoke test
build.sh         build the ISO
```

LintabOS is an unofficial spin of Debian and is not affiliated with Debian or Lenovo. Debian is a registered trademark owned by
Software in the Public Interest, Inc. See [NOTICE.md](NOTICE.md) for credits (including Tux, by Larry Ewing), trademarks and the
source-code offer, and [docs/LEGAL.md](docs/LEGAL.md) for the license, copyright and legality review.
