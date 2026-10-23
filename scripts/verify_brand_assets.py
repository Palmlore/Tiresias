#!/usr/bin/env python3
"""Stdlib checks for the brand mark and character icons (no Pillow)."""

from __future__ import annotations

import json
import re
import struct
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PALETTE = json.loads((ROOT / "assets/brand/palette.json").read_text())
INK = PALETTE["ink"]

FILES = {
    ROOT / "assets/brand/logo.svg": None,
    ROOT / "assets/brand/logo-1024.png": (1024, 1024),
    ROOT / "assets/characters/tiresias.svg": None,
    ROOT / "assets/characters/tiresias.png": (512, 512),
    ROOT / "assets/characters/sphinx.svg": None,
    ROOT / "assets/characters/sphinx.png": (512, 512),
    ROOT / "assets/characters/you.svg": None,
    ROOT / "assets/characters/you.png": (512, 512),
}

# Body copy on ink must be AA (4.5:1). Large UI text / marks: 3:1.
MIN_BODY = 4.5
MIN_LARGE = 3.0


def lum(h: str) -> float:
    def f(c: float) -> float:
        c = c / 255
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    r, g, b = int(h[1:3], 16), int(h[3:5], 16), int(h[5:7], 16)
    return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b)


def contrast(a: str, b: str) -> float:
    lo, hi = sorted((lum(a), lum(b)))
    return (hi + 0.05) / (lo + 0.05)


def png_ihdr(path: Path) -> tuple[int, int, int]:
    data = path.read_bytes()
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise SystemExit(f"{path}: not a PNG")
    if data[12:16] != b"IHDR":
        raise SystemExit(f"{path}: missing IHDR")
    width, height, _bit, color = struct.unpack(">IIBB", data[16:26])
    return width, height, color


def main() -> None:
    for path, size in FILES.items():
        if not path.is_file():
            raise SystemExit(f"missing {path.relative_to(ROOT)}")
        text = path.read_text(errors="replace") if path.suffix == ".svg" else ""
        if re.search(r"\$|GE Health|NVIDIA|Zetaris|Meterless", text):
            raise SystemExit(f"{path.name}: figures or third-party names are not allowed")
        if path.suffix == ".svg":
            if 'viewBox="0 0 128 128"' not in text:
                raise SystemExit(f"{path.name}: expected viewBox 0 0 128 128")
            if "<image" in text:
                raise SystemExit(f"{path.name}: raster embeds are not allowed")
        if path.suffix == ".png":
            width, height, color = png_ihdr(path)
            if (width, height) != size:
                raise SystemExit(f"{path.name}: {width}x{height}, expected {size[0]}x{size[1]}")
            if color != 6:
                raise SystemExit(f"{path.name}: colour type {color}, expected 6 (RGBA)")
            if path.stat().st_size > 1_000_000:
                raise SystemExit(f"{path.name}: larger than 1 MB")

    for name in ("text", "parchment", "bronze", "celeste"):
        ratio = contrast(PALETTE[name], INK)
        need = MIN_BODY if name in {"text", "parchment"} else MIN_LARGE
        if ratio < need:
            raise SystemExit(f"{name} on ink is {ratio:.2f}:1, need {need}:1")
        listed = PALETTE["contrast_on_ink"][name]
        if abs(listed - round(ratio, 2)) > 0.05 and abs(listed - ratio) > 0.05:
            raise SystemExit(f"palette.json contrast_on_ink.{name}={listed}, measured {ratio:.2f}")

    logo = (ROOT / "assets/brand/logo.svg").read_text()
    if PALETTE["bronze"] not in logo:
        raise SystemExit("logo.svg does not use bronze")
    sphinx = (ROOT / "assets/characters/sphinx.svg").read_text()
    if PALETTE["celeste"] not in sphinx:
        raise SystemExit("sphinx.svg does not use celeste")
    you = (ROOT / "assets/characters/you.svg").read_text()
    if PALETTE["parchment"] not in you:
        raise SystemExit("you.svg does not use parchment")
    print("brand assets ok")


if __name__ == "__main__":
    sys.exit(main())
