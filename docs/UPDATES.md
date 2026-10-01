# How LintabOS updates work

LintabOS has two kinds of updates:

| What | How | Where from |
|---|---|---|
| **Debian's packages** (kernel, GNOME, security fixes, Firefox…) | Normal apt / **Software** | Debian's mirrors |
| **LintabOS's own parts** (tablet tuning, installer, BitLocker tools, boot menu entry, branding) | **LintabOS Updates** (`lintab-update`) | Signed releases on [github.com/jgm240/LintabOS](https://github.com/jgm240/LintabOS/releases) |

The second kind is one Debian package, `lintabos-core`. The ISO installs it the same way an update does.

## Using it

- Once a day (and 15 minutes after boot) an *unprivileged* background check asks GitHub whether a newer release exists and
  shows a notification. It downloads and installs nothing.
- Open **LintabOS Updates** from the app grid, read the release notes, press **Update now**, enter your password.
- From a terminal: `lintab-update check`, `sudo lintab-update apply`, `lintab-update status`.
- Settings are in `/etc/lintabos/update.conf`: `channel = any|stable` (pre-releases or not) and `auto_check = true|false`.

**Privacy:** a check sends an ordinary HTTPS request to `api.github.com` (so GitHub sees your IP address and that you use
the LintabOS updater, via its User-Agent). Nothing else is sent. Set `auto_check = false` to stop the daily check; it then
only contacts GitHub when you press **Check again**.

## Why a hostile network or GitHub account can't push you a bad update

1. **Signatures.** Every release's package is signed with the maintainer's [minisign](https://jedisct1.github.io/minisign/)
   key. The matching public key, `packaging/update-key.pub`, is installed at `/usr/share/lintabos/update-key.pub`. The updater
   refuses any package that doesn't verify. GitHub only delivers the files; it can't forge a signature.
2. **Identity.** The package must be named `lintabos-core`, and its version must equal the release's version.
3. **No downgrades or replays.** A package that isn't strictly newer than the installed one is refused, so an old (genuinely
   signed) release can't be replayed.
4. **Trusted hosts only.** Downloads must be HTTPS from GitHub's own hosts, and every redirect is re-checked. Size limits apply.
5. **You decide.** Checking is read-only; installing needs your password through polkit.

These cases are tested in `tests/test_updater.py` (wrong key, tampered file, missing signature, swapped package, version
mismatch, downgrade, untrusted redirect, no network).

What it does **not** protect against: someone who has the **private signing key**. Protect it.

## The signing key

- Public key: `packaging/update-key.pub` (in the repo and in every image).
- Private key: `~/.lintabos-signing/update.minisec` on the maintainer's computer. It is **not** in the repository, is
  unencrypted (so releases can be signed without a prompt), and has `0600` permissions. **Back it up somewhere safe and
  private.** If it is lost, existing installs can never receive another update (they only trust this key), and a new image
  with a new key would have to be released. If it leaks, anyone can sign updates your users will accept; revoke by shipping a new
  image with a new key and tell people.
- Consider adding a passphrase (`minisign -C -s update.minisec`) at the cost of typing it when releasing.

## Releasing (maintainer)

```bash
./scripts/release.sh 0.1.1            # builds lintabos-core, signs it, publishes a GitHub release
./scripts/release.sh 0.2.0 --stable   # same, as a full release instead of a pre-release
```

A release carries `lintabos-core_<version>_all.deb` and its `.minisig` (what the updater uses), plus `SHA256SUMS` and,
when present, the ISO. Bump the `VERSION` file first; the script checks it matches.
