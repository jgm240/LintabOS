#!/usr/bin/env python3
# SPDX-License-Identifier: MIT
"""Synthesise the two "snap" sounds played when the folio keyboard is attached or detached.

They are generated from scratch (a noise click, a ringing resonance and a low thunk), not sampled from anything, so
they carry no third-party copyright. Standard library only.

    gen_keyboard_sounds.py OUTPUT_DIR      # writes keyboard-attached.wav and keyboard-detached.wav
"""

import math
import os
import random
import struct
import sys
import wave

RATE = 48000


def _env(t: float, decay: float) -> float:
    return math.exp(-t * decay)


def render(kind: str) -> list[float]:
    """Samples in [-1, 1]. "attached" is a bright, firm snap; "detached" a softer, lower pop."""
    rng = random.Random(7 if kind == "attached" else 11)
    bright = kind == "attached"
    length = 0.16 if bright else 0.14
    ring, thunk = (2300.0, 330.0) if bright else (1500.0, 240.0)
    out = []
    for i in range(int(RATE * length)):
        t = i / RATE
        click = (rng.random() * 2 - 1) * _env(t, 900) * 0.9                       # the contact: a few ms of noise
        ping = math.sin(2 * math.pi * ring * t) * _env(t, 160) * 0.45              # the latch ringing
        body = math.sin(2 * math.pi * thunk * t) * _env(t, 55) * (0.7 if bright else 0.55)  # the magnet seating
        sample = click + ping + body
        out.append(sample * min(1.0, t * 4000))                                    # 0.25 ms fade-in, no speaker pop
    peak = max(abs(s) for s in out)
    return [0.8 * s / peak for s in out]


def write_wav(path: str, samples: list[float]) -> None:
    with wave.open(path, "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(RATE)
        w.writeframes(b"".join(struct.pack("<h", int(max(-1, min(1, s)) * 32767)) for s in samples))


def main(argv: list[str]) -> int:
    out = argv[1] if len(argv) > 1 else "."
    os.makedirs(out, exist_ok=True)
    for kind in ("attached", "detached"):
        write_wav(os.path.join(out, f"keyboard-{kind}.wav"), render(kind))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
