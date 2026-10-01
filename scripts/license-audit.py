#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Audit the licenses of every package in a LintabOS image.

Input is a directory holding what ``unsquashfs`` extracted from the image's filesystem.squashfs:
``var/lib/dpkg/status`` and ``usr/share/doc/*/copyright`` (see docs/LEGAL.md for the command).
Output: a summary on stdout and, with ``--json``, the raw per-package data.

It reads each package's machine-readable (DEP-5) ``License:`` fields where they exist and falls back to
recognizing well-known license texts. It is a screening tool, not a legal opinion: anything it flags
(non-free sections, restrictive wording, unrecognized licenses) needs a human to read the copyright file.
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import sys

FREE_SHORT = {
    # normalized short names we treat as ordinary free-software licenses
    "GPL-2", "GPL-2+", "GPL-3", "GPL-3+", "LGPL-2", "LGPL-2+", "LGPL-2.1", "LGPL-2.1+", "LGPL-3", "LGPL-3+",
    "AGPL-3", "AGPL-3+", "Apache-2.0", "MIT", "Expat", "BSD-2-clause", "BSD-3-clause", "BSD-4-clause", "ISC",
    "MPL-1.1", "MPL-2.0", "Zlib", "Artistic", "Artistic-2.0", "OFL-1.1", "CC0-1.0", "CC-BY-SA-3.0", "CC-BY-SA-4.0",
    "CC-BY-3.0", "CC-BY-4.0", "public-domain", "Python-2.0", "PSF-2", "X11", "Unlicense", "BSL-1.0", "FTL", "IJG",
    "libpng", "OpenSSL", "Ruby", "GFDL-1.3", "GFDL-1.2", "GFDL-1.3+", "GFDL-1.2+", "curl", "HPND", "NTP", "Info-ZIP",
    "WTFPL", "Vim", "Bitstream-Vera", "Libpng", "SIL-OFL-1.1", "BSD", "GPL", "LGPL", "Boost", "MS-PL", "EPL-1.0",
    "EPL-2.0", "CDDL-1.0", "OLDAP-2.8", "Qhull", "TCL", "Unicode", "Unicode-DFS-2016", "PostgreSQL", "BlueOak-1.0.0",
}

RESTRICTIVE = re.compile(
    r"non-?commercial|no commercial|not for commercial|may not be (re)?distributed|redistribution (is )?(not|prohibited)|"
    r"prohibited from|reverse engineer|disassembl|decompil|click|accept the terms|evaluation only|"
    r"personal use only|without (prior )?(written )?permission|all rights reserved\.? *$",
    re.I | re.M)

NORMALIZE = [
    (re.compile(r"^GPL-?v?2\+|GPL-?2\.0\+|GPL-?2(\.0)?-or-later|GPL-2\+", re.I), "GPL-2+"),
    (re.compile(r"^GPL-?v?2$|^GPL-?2\.0$|GPL-2(\.0)?-only", re.I), "GPL-2"),
    (re.compile(r"^GPL-?v?3\+|GPL-?3(\.0)?-or-later", re.I), "GPL-3+"),
    (re.compile(r"^GPL-?v?3$|GPL-3(\.0)?-only", re.I), "GPL-3"),
    (re.compile(r"^LGPL-?2\.1\+|LGPL-2\.1-or-later", re.I), "LGPL-2.1+"),
    (re.compile(r"^LGPL-?2\.1$", re.I), "LGPL-2.1"),
    (re.compile(r"^LGPL-?3\+|LGPL-3(\.0)?-or-later", re.I), "LGPL-3+"),
    (re.compile(r"^LGPL-?3$", re.I), "LGPL-3"),
    (re.compile(r"^LGPL-?2\+", re.I), "LGPL-2+"),
    (re.compile(r"^LGPL-?2$", re.I), "LGPL-2"),
    (re.compile(r"^Apache-?2(\.0)?$", re.I), "Apache-2.0"),
    (re.compile(r"^(MIT|Expat|MIT/X11|X11)$", re.I), "MIT"),
    (re.compile(r"^BSD-?2", re.I), "BSD-2-clause"),
    (re.compile(r"^BSD-?3", re.I), "BSD-3-clause"),
    (re.compile(r"^BSD-?4", re.I), "BSD-4-clause"),
    (re.compile(r"^MPL-?2", re.I), "MPL-2.0"),
    (re.compile(r"^MPL-?1\.1", re.I), "MPL-1.1"),
    (re.compile(r"^OFL", re.I), "OFL-1.1"),
    (re.compile(r"public[- ]domain", re.I), "public-domain"),
]


def normalize(name: str) -> str:
    name = name.strip().strip(",;")
    for pattern, short in NORMALIZE:
        if pattern.search(name):
            return short
    return name


def parse_status(path: str) -> dict[str, dict]:
    packages: dict[str, dict] = {}
    with open(path, errors="replace") as f:
        for block in f.read().split("\n\n"):
            fields = dict(re.findall(r"^([A-Za-z-]+): (.*)$", block, re.M))
            if fields.get("Package") and "install ok installed" in fields.get("Status", ""):
                packages[fields["Package"]] = fields
    return packages


def component(section: str) -> str:
    if "/" in section:
        return section.split("/", 1)[0]
    return "main"


def licenses_from_copyright(text: str) -> tuple[set[str], bool]:
    """Return (normalized license names, machine-readable?)."""
    found: set[str] = set()
    machine = text.lstrip().startswith("Format:") or "\nFormat:" in text[:400]
    for m in re.finditer(r"^License:\s*(.+)$", text, re.M):
        expr = m.group(1)
        for part in re.split(r"\s+(?:and|or|with)\s+|,|\s*&\s*", expr):
            part = part.strip()
            if part and not part.startswith("[") and len(part) < 60:
                found.add(normalize(part))
    if not found:  # free-form copyright file: recognize the usual common-license references
        for name, short in (("GPL-3", "GPL-3+"), ("GPL-2", "GPL-2+"), ("LGPL-3", "LGPL-3+"), ("LGPL-2.1", "LGPL-2.1+"),
                            ("LGPL-2", "LGPL-2+"), ("Apache-2", "Apache-2.0"), ("MPL-2.0", "MPL-2.0"), ("GFDL", "GFDL")):
            if f"common-licenses/{name}" in text:
                found.add(short)
        if re.search(r"Permission is hereby granted, free of charge", text):
            found.add("MIT")
        if re.search(r"Redistribution and use in source and binary forms", text):
            found.add("BSD-style")
    return found, machine


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", help="directory extracted from filesystem.squashfs")
    ap.add_argument("--json", help="write per-package data here")
    args = ap.parse_args()

    status = parse_status(os.path.join(args.root, "var/lib/dpkg/status"))
    rows = []
    for name, fields in sorted(status.items()):
        path = os.path.join(args.root, "usr/share/doc", name.split(":")[0], "copyright")
        text = ""
        if os.path.exists(path):
            try:
                text = open(path, errors="replace").read()
            except OSError:
                pass
        licenses, machine = licenses_from_copyright(text) if text else (set(), False)
        flags = []
        if not text:
            flags.append("no copyright file")
        elif not licenses:
            flags.append("licence not recognized")
        unknown = sorted(l for l in licenses if l not in FREE_SHORT and l != "BSD-style")
        if unknown:
            flags.append("other licence text: " + ", ".join(unknown[:4]))
        if RESTRICTIVE.search(text):
            flags.append("restrictive wording present")
        comp = component(fields.get("Section", ""))
        if comp != "main":
            flags.append(f"archive component: {comp}")
        rows.append({"package": name, "version": fields.get("Version", ""), "component": comp,
                     "licenses": sorted(licenses), "machine_readable": machine, "flags": flags})

    by_comp = collections.Counter(r["component"] for r in rows)
    print(f"{len(rows)} packages installed")
    print("by Debian archive component:", dict(by_comp))

    counter = collections.Counter(l for r in rows for l in r["licenses"])
    print("\nmost common licences (a package can carry several):")
    for lic, n in counter.most_common(25):
        print(f"  {n:5d}  {lic}")

    print("\nNOT from Debian main (needs an individual look):")
    for r in rows:
        if r["component"] != "main":
            print(f"  [{r['component']}] {r['package']} {r['version']}: {', '.join(r['licenses']) or '?'}")

    print("\nflagged for review (excluding the non-main ones above):")
    for r in rows:
        extra = [f for f in r["flags"] if not f.startswith("archive component")]
        if extra and r["component"] == "main":
            print(f"  {r['package']}: {'; '.join(extra)}")

    if args.json:
        with open(args.json, "w") as f:
            json.dump(rows, f, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
