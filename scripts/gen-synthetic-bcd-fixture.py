#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Regenerates tests/fixtures/synthetic-bcd-sample/fake-bcd: a synthetic, BCD-shaped hive used to test
lintab.bcdbackup, built entirely from tests/fixtures/sample.hive (hivex's own upstream open-source test fixture)
plus data made up for this fixture. Nothing here is extracted from a real Windows installation or any Microsoft
binary - the GUIDs/element IDs are either UEFI-spec-constant identifiers or invented.

Needs python3-hivex: run inside the builder container, e.g.
    docker run --rm -v "$PWD:/lintab" -w /lintab lintabos-builder python3 scripts/gen-synthetic-bcd-fixture.py
"""

import os
import shutil

import hivex

ROOT = os.path.join(os.path.dirname(__file__), "..")
BASE_HIVE = os.path.join(ROOT, "tests", "fixtures", "sample.hive")
OUT_DIR = os.path.join(ROOT, "tests", "fixtures", "synthetic-bcd-sample")
OUT_PATH = os.path.join(OUT_DIR, "fake-bcd")

BOOTMGR_GUID = "{9dea862c-5cdd-4e70-acc1-f32b344d4795}"       # UEFI-spec-constant {bootmgr} GUID - an identifier
DEFAULT_OS_GUID = "{aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee}"     # made up for this fixture, not a real install's GUID


def _set_string(h, node, value: str) -> None:
    h.node_set_value(node, {"key": "Element", "t": 1, "value": value.encode("utf-16le") + b"\x00\x00"})


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    shutil.copy2(BASE_HIVE, OUT_PATH)

    h = hivex.Hivex(OUT_PATH, write=True)
    root = h.root()
    objects = h.node_add_child(root, "Objects")

    bootmgr = h.node_add_child(objects, BOOTMGR_GUID)
    bootmgr_elements = h.node_add_child(bootmgr, "Elements")
    default_obj_el = h.node_add_child(bootmgr_elements, "23000003")   # BcdBootMgrObject_DefaultObject
    _set_string(h, default_obj_el, DEFAULT_OS_GUID)

    default_entry = h.node_add_child(objects, DEFAULT_OS_GUID)
    default_elements = h.node_add_child(default_entry, "Elements")
    # A couple of harmless pre-existing elements, so a test can check they survive the onetimeadvancedoptions write.
    description = h.node_add_child(default_elements, "12000004")
    _set_string(h, description, "Fake synthetic description")
    dummy_flag = h.node_add_child(default_elements, "16000060")
    h.node_set_value(dummy_flag, {"key": "Element", "t": 3, "value": bytes([0])})

    h.commit(None)
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
