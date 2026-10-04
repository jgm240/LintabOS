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
| KDE Plasma and Xfce (optional) | Switch them on in the installer's account page; they are **downloaded during setup** (about 450 MB and 70 MB; the tablet must be online) from Debian after GRUB is installed, so a failed download can never cost you the boot. GDM stays the login screen and GNOME the default; pick the others from the gear icon. **Touch**: Plasma gets the Maliit on-screen keyboard and a 56 px panel (KWin already rotates from the accelerometer); Xfce gets a 48 px panel, bigger cursor and interface, the Onboard keyboard, and `lintab-xfce-rotate` (xrandr + touch-matrix rotation, since Xfce has none). `lintab-tablet-mode` switches each desktop's keyboard when the folio attaches or detaches |
| Folio not working | **Tablet & Folio** app (or `lintab-tablet-mode assist`): the on-screen keyboard stays available even with the folio attached, and the folio keeps working. For the many folios that ship with a few dead keys. No mode ever turns the folio off |
| Remove LintabOS | From the live USB: **Remove LintabOS** (welcome page, or `lintab-uninstall`). Deletes only the partition it can identify as LintabOS, grows Windows back into the space when that is safe (not BitLocker, not hibernated or flagged), cleans LintabOS's folder and boot entries from the EFI partition and puts Windows Boot Manager first. Refuses if no Windows is on the disk. Needs the phrase `REMOVE-LINTABOS` on the command line |
| Update rollback | **LintabOS Updates** keeps a signature-checked copy of the version it replaces, runs a health check after installing, and goes back by itself if the new version is broken (`lintab-update rollback` does it on request) |
| Hardware report | **Hardware Report** app (or `lintab-hwreport`): Wi-Fi, Bluetooth, fingerprint, sensors, cameras, folio, battery, storage, sleep. MAC/IP addresses, UUIDs, serial numbers, and your user and computer names are masked, Wi-Fi names are never collected, nothing is sent; you read it first and paste it into a GitHub issue |
| LinWinMod | Edit the Windows **files** (read-write, same Fast-Startup/BitLocker checks as *Windows Files* --read-write) and **browse**
  the Windows **registry** (SOFTWARE, SYSTEM, DEFAULT, each user's NTUSER.DAT/UsrClass.dat). **Read-only, no exceptions**: there
  is no write path to the registry at all. **SAM and SECURITY are refused outright** — not hidden from a menu, refused at the
  function that opens a hive, however the path is spelled — because they hold account password hashes and cached
  credentials/LSA secrets, i.e. credential material, not configuration. See [docs/LEGAL.md](docs/LEGAL.md) for why no subset
  of the registry can be safely offered as "editable" |
| Android mode | After installing *Android apps* from Extras, **Android Mode** turns the whole tablet into a full-screen Android tablet (Waydroid inside `cage`, the setup Waydroid's own docs describe). It selects an "Android" login session for your next login and logs you out. Inside Android, the **Computer Mode** app (a small app LintabOS installs into Android) ends the session and brings back your normal desktop; **the power button is Android's power button**. The way out is checked first: if the Computer Mode listener can't start, the session ends instead of trapping you; a keyboard can still use Ctrl+Alt+F3. Untested on the Duet 3 |
| LintabOS Extras | Optional downloads in one place, **each removable again** (Remove extras…; your own documents stay): **Rnote and Xournal++** (drawing, notes), the **Office pack**, **Waydroid** (Android apps) and **Bottles** (Windows programs through Wine). Flathub apps install for you only, without a password; Waydroid asks for an administrator. Not in the ISO, which stays under GitHub's 2 GiB limit |
| Reading mode and auto-brightness | **Reading Mode** (GNOME): warm screen all day until switched off, your own Night Light settings put back afterwards; `--grey` is an experimental greyscale. Auto-brightness from the light sensor and an evening warm-up are on by default |
| Battery health | **Battery Health** app / `lintab-battery`: health as a percent of original capacity, cycles, and a charge limit (80 %) where the tablet's firmware exposes one; it says so when it doesn't |
| Word, Excel, PowerPoint, OneDrive, Teams | **Optional**: add them from **LintabOS Extras → Microsoft 365 web apps** (and remove them there again). Microsoft's own **web apps** in their own windows (Chromium app mode, one shared sign-in), plus *Connect OneDrive to Files* (GNOME Online Accounts). Added for your user only; if Chromium isn't installed it is downloaded first. **There is no native Office or Teams for Linux.** The optional **Office Pack** downloads LibreOffice (edits .docx/.xlsx/.pptx offline) and the unofficial Teams for Linux from Flathub |

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

## Known issues reported from a real Duet 3

These were reported by someone running LintabOS on a real IdeaPad Duet 3 and are **not fixed yet**; the cause of each is still unknown:

- **The tablet doesn't turn back on after the power button or closing the folio** — Fixed (0.3.3) as far as software can, in
  two parts. (1) Where the firmware offers real "deep" (S3) sleep as well as "s2idle", LintabOS selects "deep" at every boot:
  this is the single most common fix for exactly this symptom on this class of Intel hardware, because s2idle depends on
  every driver's own resume code behaving correctly, where deep/S3 instead powers most of the system off and back on. (2)
  **Sleep & Power Button** lets you choose what the power button and folio-close actually do (Suspend / Lock the screen only
  / Do nothing); LintabOS's own small service (`lintab-sleep-guard`) takes them over directly, the same way GNOME itself
  takes them over from systemd-logind, because GNOME hardcodes "tablets always suspend" for the power button and has no
  setting for the lid switch at all. If suspend still doesn't come back reliably on your tablet even with deep sleep
  preferred, switch both to "Lock the screen only" — it can't get stuck, because it never suspends. **Not verified on real
  hardware** (no Duet 3 to test on); the `mem_sleep` and GNOME-hardcoding facts this is built on were checked against the
  kernel's own sysfs documentation and `gnome-settings-daemon`'s own schema, not guessed.
- **Wi-Fi disappeared.** `sudo lintab-wifi-fix` (0.3.0) undoes everything in software that can stop Wi-Fi (rfkill blocks, a disabled radio, stopped or masked services, a driver that didn't load) and reports config or missing firmware it can't change on its own. It can't help if the Wi-Fi chip isn't detected at all.
- **Fingerprint login doesn't work.**
- **Pressing the power button or closing the folio leaves the tablet dead until it is force-restarted.** Real hibernation can't work (LintabOS uses zram only, no swap file), so this is most likely suspend that never resumes. Until it is fixed, `sudo systemctl mask sleep.target suspend.target hibernate.target hybrid-sleep.target` and setting the power button to "interactive" keep the tablet from sleeping at all.

Please open the **Hardware Report** app on an affected tablet and paste the result into a [hardware-report issue](https://github.com/jgm240/LintabOS/issues/new?template=hardware-report.md); it collects exactly what is needed to find these.

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
- **LinWinMod**: SAM/SECURITY refusal (by name, case-insensitively, however the path is spelled, even for a file that is a
  genuinely valid hive), hive discovery (never lists SAM/SECURITY even when present on disk), and reading real registry data —
  every value type, nested keys, an empty key, a missing path, search — are tested against a real hive built from the `hivex`
  project's own upstream test fixture (`tests/fixtures/sample.hive`); the privileged remount helper only ever touches the one
  mount point `lintab-windows-files` already declared. **Not tested**: a real Windows installation's hives (sizes, depth,
  encodings beyond what the fixture covers), and the file manager against a real NTFS mount.
- **Android mode**: the login-session switching and restoring, the Computer Mode listener (answers only `/exit`, only on the Waydroid
  bridge address, fails closed), the app install step, and the session files are unit-tested against fakes; the Computer Mode APK is
  built, signed and checked with `aapt`/`apksigner`. **Not tested**: Waydroid itself on this tablet, cage and Android with touch and the
  panel's orientation, whether Android can reach the listener on a real Waydroid network (a firewall could block it), and whether the
  power key reaches Android as its power button.
- **Restart into Windows: Recovery and Safe Mode (0.3.9, confirmed on real hardware in 0.3.10)**. The shortcut opens
  a small chooser: a normal restart (unchanged), **Restart into Windows Recovery**, and **Restart into Windows Safe
  Mode**. Recovery sets the standard UEFI `OsIndications` variable's documented "start OS recovery" bit before
  rebooting (the same firmware variable systemd-boot and GNOME already use for "reboot to firmware setup", just a
  different bit) — **tested on a real Duet 3: this does not open the Troubleshoot/Advanced Options menu.** It
  triggers Windows' own Startup Repair check instead (a few seconds of "Automatic Repair on drive C:"), which finds
  nothing wrong and boots normally when Windows is healthy — a real, confirmed effect, harmless either way, kept
  because it's a genuine (if different) Windows diagnostic rather than a silent no-op. The reliable way in (Settings
  → Recovery → Advanced startup, or holding Shift while choosing Restart *from within Windows* — a Windows feature,
  unrelated to this UEFI variable) is shown alongside it every time, not as a fallback for a silent failure. Safe
  Mode makes the same attempt, since it lives inside Windows' own recovery menu rather than being a separate boot
  choice; reaching it directly needs editing Windows' Boot Configuration Data, a binary registry hive — see
  **"Windows Boot Configuration (Experimental)" (0.3.11)** below for the one place LintabOS now does exactly that,
  and the safety net around it.
- **Windows Boot Configuration (Experimental) (0.3.11)**: the one write LintabOS makes to the Windows registry,
  added specifically because Shift+Restart / `shutdown /r /o` *inside* Windows already writes to this exact same
  BCD element (`onetimeadvancedoptions`) — this just offers the same flag from outside Windows, with a real safety
  net every time it runs: refuses without AC power and at least 40% battery (the write is too fast, milliseconds,
  to meaningfully catch a power-unplug event mid-write and abort it, and a torn write to the non-journaled FAT32
  ESP can corrupt more than one file); always backs up the live BCD first; re-opens and verifies the result in a
  *fresh* hivex handle rather than trusting that the write call not raising means it worked; and automatically
  restores the backup the instant that verification fails, for any reason. A separate "Restore backed-up BCD"
  action works independent of any write attempt too. Ships as its own clearly-labeled "(Experimental)" tool, not
  folded into the existing Recovery button: the write path was validated during development against real BCD
  samples (the actual test fixture in this repo is synthetic — `tests/fixtures/synthetic-bcd-sample/`, built from
  hivex's own upstream test hive plus invented data, not extracted from any real Windows install) but not yet
  confirmed to open the Troubleshoot menu on a real, already-installed Windows system. See `docs/LEGAL.md` and
  `docs/releases/0.3.11.md`.
- **WinTermMod**: a terminal, positioned at your mounted Windows drive (same Fast-Startup/BitLocker checks as
  LinWinMod), for `find`/`grep`/scripting against your Windows files directly. **It is not Windows `cmd.exe`** — a
  native Windows program can't run outside Windows at all, and running one against your real Windows system drive
  through a compatibility layer would be, in effect, a way to act on Windows without logging into it, the same risk
  LinWinMod's registry browser was built to avoid. It's an ordinary Linux shell, just started in the right place.
- **LinWinMod partition picker broadened to any Microsoft-data partition (0.3.8)**: `list_ntfs_partitions()` matched by
  *probed filesystem content* (`fstype == "ntfs"`), which misses a real Windows partition whenever that content probe
  doesn't come back clean — which does happen. Renamed to `list_microsoft_data_partitions()` and matched by *GPT
  partition type* instead (the same Microsoft-data GUID `windows_partitions()` already keys off), which doesn't depend
  on content probing at all. This also makes the mount itself filesystem-aware: it was previously hardcoded to the
  `ntfs3` driver regardless of what's actually there; it now uses `exfat` for an exFAT volume and keeps `ntfs3` as the
  sensible default otherwise (an NTFS volume whose probe came back blank is still overwhelmingly likely to be NTFS).
  The EFI partition, LintabOS's own root, and swap can never appear in the picker — they carry different GPT types,
  by construction, regardless of what's on them. **BitLocker is still excluded**, and that exclusion doesn't depend on
  the content probe either (`is_bitlocker()` reads the real boot sector directly).
- **Touchscreen diagnostics in Hardware Report (0.3.7)**: a reported touchscreen problem has no real diagnostic yet
  (unlike Wi-Fi, fingerprint and sleep). The report now shows whether udev tags any device as a touchscreen, what
  `libinput` (what GNOME actually uses for input) sees, and the kernel's own messages about the touch controller.
  **Not fixed** — there's no data yet to know what's actually wrong; this is step one, same as the Wi-Fi tool before it.
- **LinWinMod: pick any NTFS partition by hand (0.3.6)**. A real user's tablet had no partition `disks.find_windows()`
  recognised as "the Windows install" (it needs a `\Windows\System32` folder found via a real mount, or to be the
  biggest Microsoft-data partition on a disk whose ESP has a Windows boot manager — an unusual layout, or a secondary
  NTFS data partition, can miss both). LinWinMod's Files tab now also lists **every** NTFS partition on the tablet in
  a dropdown, with a "Use this partition" button, regardless of whether it looks like "the" Windows install. The same
  Fast-Startup/dirty safety check still applies before anything is mounted read-write. Verified against a real NTFS
  partition on a loop device, without a `\Windows\System32` folder, that automatic detection correctly misses and the
  new picker correctly finds.
- **LinWinMod bug fix: "sfdisk doesn't exist" (0.3.5)**. The 0.3.4 fix above made LinWinMod show its real error instead
  of hanging — and a real user's tablet then reported exactly that error. The cause: `sfdisk` (and `blkid`, `mount`, and
  every other partitioning tool) live in `/sbin` or `/usr/sbin`; `sudo`/`pkexec` add those to `PATH` for anything that
  elevates first (the installer, Remove LintabOS), but LinWinMod's disk scan deliberately runs **unprivileged** — it's
  read-only, so it shouldn't need a password just to open the app — and a plain desktop session's `PATH` often doesn't
  include `/sbin`/`/usr/sbin` at all. `sfdisk` was genuinely installed; it just couldn't be found by name. `disks.run()`
  (used throughout the partitioner) now resolves a tool's full path, trying the inherited `PATH` first and falling back
  to the usual sbin directories, so a tool that's actually there is actually found — verified by reproducing the exact
  failure (a `PATH` with no sbin directories) and confirming `sfdisk` now resolves and runs.
- **LinWinMod bug fix (0.3.4)**: the Files and Registry pages could get stuck on their loading message forever if disk or
  hive discovery raised an exception — a background thread dying silently before the GUI callback that updates the label
  ever ran. A real user hit exactly this ("stuck at Looking for Windows..."). All four background workers in the window now
  catch their own exceptions and show a message instead of hanging; a structural test (parses the module's own source)
  checks every function that calls `GLib.idle_add` has a surrounding `try`/`except`, so this bug class can't silently return.
- **0.3.0 features**: the safety rules and real behaviour of *Remove LintabOS* are tested on loop-device disks with real
  `sfdisk`/`ntfsresize` at 512- and 4096-byte sectors (Windows' data checksum, partitions, boot files and firmware entries all checked;
  space left alone when Windows is flagged, BitLocker-encrypted or no Windows is present); update rollback, the automatic rollback after a
  failed health check and refusal of a tampered saved copy are tested with a fake GitHub and real signatures; the hardware report's masking,
  its refusal to collect secrets, Extras' install logic, reading mode, and battery maths are unit-tested. **Not tested**: any of this on the
  tablet, Waydroid (the kernel offers Android's binder as a module, which should work, but nobody has tried), Bottles, Rnote and Xournal++
  with a pen, the light sensor, and whether the Duet's firmware offers a charge limit.
- **Sleep & Power Button**: the deep-sleep preference (every realistic `/sys/power/mem_sleep` format, and the three cases —
  switches, already selected, no alternative offered), power-button/lid-switch device detection (including a device with only
  one handler, which a first version of this got wrong and a test caught), event classification (press vs. release vs.
  auto-repeat, lid close vs. open), and the dispatch loop (surviving a device it can't open, never raising out of the action
  it performs) are all tested against fakes. **Not tested**: a real power button or lid switch, and whether deep sleep (where
  offered) actually fixes the wake-up problem on this tablet — that can only be confirmed on the hardware.
- **KDE and Xfce**: the install order, GDM-stays-default guard, failure handling, DNS handling, touch settings, rotation maths and
  per-desktop keyboard commands are tested (`tests/test_desktops.py`); the Xfce xfconf keys and Onboard's gsettings keys were checked
  against the real packages in Debian 13, and both package lists resolve there. **Not tested**: an actual KDE or Xfce session on the
  tablet, Plasma's virtual-keyboard D-Bus switch, and whether Plasma's panel script takes effect on first login.
- **0.2.0 features** (tablet mode, snap sound, school mode, Windows sidebar, touch boot menu, Microsoft 365 apps): the
  decision logic and generated files are tested (`./scripts/test-features.sh`; school-mode udev rules checked with
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
