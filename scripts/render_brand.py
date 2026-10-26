#!/usr/bin/env python3
"""Write Tiresias brand SVGs and matching PNGs from one set of shapes.

PNGs need Pillow (`uv run --with pillow python scripts/render_brand.py`).
SVGs are written either way. Geometry lives in a 128×128 view-box; icons
are fills only (no hairline strokes) so they still read at 32 px.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PALETTE = json.loads((ROOT / "assets/brand/palette.json").read_text())
BRONZE = PALETTE["bronze"]
CELESTE = PALETTE["celeste"]
PARCHMENT = PALETTE["parchment"]
STONE = PALETTE["stone"]

LOGO_PX = 1024
CHAR_PX = 512
VIEW = 128.0


def _svg(body: str, label: str) -> str:
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" '
        f'role="img" aria-label="{label}">\n'
        f"  <title>{label}</title>\n"
        f"{body}"
        f"</svg>\n"
    )


def _rect(x: float, y: float, w: float, h: float, fill: str, rx: float = 0) -> str:
    r = f' rx="{rx}" ry="{rx}"' if rx else ""
    return f'  <rect x="{x}" y="{y}" width="{w}" height="{h}"{r} fill="{fill}"/>\n'


def _circle(cx: float, cy: float, r: float, fill: str) -> str:
    return f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}"/>\n'


def _polygon(points: list[tuple[float, float]], fill: str) -> str:
    pts = " ".join(f"{x},{y}" for x, y in points)
    return f'  <polygon points="{pts}" fill="{fill}"/>\n'


def _veiled_head(cx: float, cy: float, r: float, fill: str, band_h: float, mask_id: str) -> str:
    """Disc with a transparent eye-veil (mask, so it reads on light and dark grounds)."""
    band_y = cy - band_h / 2
    left = cx - r
    return (
        f'  <defs>\n'
        f'    <mask id="{mask_id}" maskUnits="userSpaceOnUse">\n'
        f'      <rect x="0" y="0" width="128" height="128" fill="white"/>\n'
        f'      <rect x="{left}" y="{band_y}" width="{2 * r}" height="{band_h}" fill="black"/>\n'
        f"    </mask>\n"
        f"  </defs>\n"
        f'  <circle cx="{cx}" cy="{cy}" r="{r}" fill="{fill}" mask="url(#{mask_id})"/>\n'
    )


def logo_svg() -> str:
    return _svg(
        _rect(28, 14, 14, 100, BRONZE, rx=7)
        + _veiled_head(86, 58, 36, BRONZE, 14, "veil-logo"),
        "Tiresias",
    )


def tiresias_svg() -> str:
    cloak = [(44, 70), (40, 120), (118, 120), (102, 70)]
    return _svg(
        _rect(16, 8, 14, 112, BRONZE, rx=7)
        + _polygon(cloak, BRONZE)
        + _veiled_head(82, 46, 26, BRONZE, 12, "veil-tiresias"),
        "Tiresias",
    )


def sphinx_svg() -> str:
    return _svg(
        _polygon([(36, 44), (50, 6), (64, 44)], CELESTE)
        + _polygon([(64, 44), (78, 6), (92, 44)], CELESTE)
        + _circle(64, 62, 30, CELESTE)
        + _polygon([(90, 48), (122, 40), (122, 56), (94, 60)], CELESTE),
        "The Sphinx",
    )


def you_svg() -> str:
    return _svg(
        _rect(18, 16, 12, 100, STONE, rx=3)
        + _rect(98, 16, 12, 100, STONE, rx=3)
        + _rect(18, 16, 92, 12, STONE, rx=3)
        + _circle(64, 58, 16, PARCHMENT)
        + _rect(48, 76, 32, 40, PARCHMENT, rx=8),
        "You",
    )


SHAPES = {
    "assets/brand/logo.svg": logo_svg,
    "assets/characters/tiresias.svg": tiresias_svg,
    "assets/characters/sphinx.svg": sphinx_svg,
    "assets/characters/you.svg": you_svg,
}

PNGS = {
    "assets/brand/logo.svg": ("assets/brand/logo-1024.png", LOGO_PX),
    "assets/characters/tiresias.svg": ("assets/characters/tiresias.png", CHAR_PX),
    "assets/characters/sphinx.svg": ("assets/characters/sphinx.png", CHAR_PX),
    "assets/characters/you.svg": ("assets/characters/you.png", CHAR_PX),
}


def write_svgs() -> None:
    for rel, fn in SHAPES.items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fn())


def _pil():
    try:
        from PIL import Image, ImageDraw
    except ImportError as exc:
        raise SystemExit(
            "Pillow is required to write PNGs. "
            "Run: cd services/agent && uv run --with pillow python ../../scripts/render_brand.py"
        ) from exc
    return Image, ImageDraw


def _scale(px: int, *vals: float) -> tuple[float, ...]:
    k = px / VIEW
    return tuple(v * k for v in vals)


def _draw_rect(draw, px, x, y, w, h, fill, rx=0) -> None:
    x0, y0, x1, y1, r = _scale(px, x, y, x + w, y + h, rx)
    if r:
        draw.rounded_rectangle([x0, y0, x1, y1], radius=r, fill=fill)
    else:
        draw.rectangle([x0, y0, x1, y1], fill=fill)


def _draw_circle(draw, px, cx, cy, r, fill) -> None:
    x0, y0, x1, y1 = _scale(px, cx - r, cy - r, cx + r, cy + r)
    draw.ellipse([x0, y0, x1, y1], fill=fill)


def _draw_polygon(draw, px, points, fill) -> None:
    k = px / VIEW
    draw.polygon([(x * k, y * k) for x, y in points], fill=fill)


def _punch_band(img, px, cx, cy, r, band_h) -> None:
    left, top, right, bottom = _scale(px, cx - r, cy - band_h / 2, cx + r, cy + band_h / 2)
    pixels = img.load()
    x0, y0 = max(0, int(left)), max(0, int(top))
    x1, y1 = min(img.size[0], int(right) + 1), min(img.size[1], int(bottom) + 1)
    for y in range(y0, y1):
        for x in range(x0, x1):
            pixels[x, y] = (0, 0, 0, 0)


def render_png(kind: str, dest: Path, px: int) -> None:
    Image, ImageDraw = _pil()
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if kind.endswith("logo.svg"):
        _draw_rect(draw, px, 28, 14, 14, 100, BRONZE, rx=7)
        _draw_circle(draw, px, 86, 58, 36, BRONZE)
        _punch_band(img, px, 86, 58, 36, 14)
    elif kind.endswith("tiresias.svg"):
        _draw_rect(draw, px, 16, 8, 14, 112, BRONZE, rx=7)
        _draw_polygon(draw, px, [(44, 70), (40, 120), (118, 120), (102, 70)], BRONZE)
        _draw_circle(draw, px, 82, 46, 26, BRONZE)
        _punch_band(img, px, 82, 46, 26, 12)
    elif kind.endswith("sphinx.svg"):
        _draw_polygon(draw, px, [(36, 44), (50, 6), (64, 44)], CELESTE)
        _draw_polygon(draw, px, [(64, 44), (78, 6), (92, 44)], CELESTE)
        _draw_circle(draw, px, 64, 62, 30, CELESTE)
        _draw_polygon(draw, px, [(90, 48), (122, 40), (122, 56), (94, 60)], CELESTE)
    elif kind.endswith("you.svg"):
        _draw_rect(draw, px, 18, 16, 12, 100, STONE, rx=3)
        _draw_rect(draw, px, 98, 16, 12, 100, STONE, rx=3)
        _draw_rect(draw, px, 18, 16, 92, 12, STONE, rx=3)
        _draw_circle(draw, px, 64, 58, 16, PARCHMENT)
        _draw_rect(draw, px, 48, 76, 32, 40, PARCHMENT, rx=8)
    else:
        raise ValueError(kind)
    dest.parent.mkdir(parents=True, exist_ok=True)
    img.save(dest, "PNG")


def write_pngs() -> None:
    _pil()
    for src, (dest, px) in PNGS.items():
        render_png(src, ROOT / dest, px)


def main() -> None:
    write_svgs()
    write_pngs()
    print("wrote", ", ".join(SHAPES) + ",", ", ".join(p for p, _ in PNGS.values()))


if __name__ == "__main__":
    sys.exit(main())
