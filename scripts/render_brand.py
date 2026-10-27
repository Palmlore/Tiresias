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


def logo_svg() -> str:
    """Product mark. Same linen sleep mask as the Tiresias icon.

    The file is a square SVG (exported at 1024). It is shown at 32 px in the
    header and at 150 px on the cover, so it is not a 20 px asset. The shape
    is the one that already reads at 20 px.
    """
    return tiresias_svg()


# Sleep-mask eye cover for a 20 px slot. One linen shape: a wide oval with a
# nose notch bitten out of the bottom. No face around it.
_TIRESIAS_MASK = (64, 58, 56, 36)  # cx, cy, rx, ry
_TIRESIAS_NOTCH = (64, 100, 26)  # cx, cy, r — cuts the nose opening


def tiresias_svg() -> str:
    """Linen sleep-mask eye cover. No face, no staff."""
    cx, cy, rx, ry = _TIRESIAS_MASK
    nx, ny, nr = _TIRESIAS_NOTCH
    return _svg(
        "  <defs>\n"
        '    <mask id="eye-cover" maskUnits="userSpaceOnUse">\n'
        '      <rect x="0" y="0" width="128" height="128" fill="black"/>\n'
        f'      <ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="white"/>\n'
        f'      <circle cx="{nx}" cy="{ny}" r="{nr}" fill="black"/>\n'
        "    </mask>\n"
        "  </defs>\n"
        f'  <rect x="0" y="0" width="128" height="128" fill="{PARCHMENT}" mask="url(#eye-cover)"/>\n',
        "Tiresias",
    )


# Nemes head for a 20 px slot. Wider at the lappets, a large face, and three
# bold horizontal gaps on each side. A gap is 11 units, about 2 px at 22.
# The beard hangs below the lappets, inside the viewBox. Ending it on the
# viewBox edge clips the tab at 16 px, which is why it disappeared. Higher
# gaps sit on a lappet too narrow to survive at that size, so all three stay
# on the wide lower part.
_SPHINX_NEMES = [
    (14, 108), (12, 62), (28, 34), (48, 14), (80, 14), (100, 34), (116, 62), (114, 108),
]
_SPHINX_FACE = (64, 50, 18, 20)  # cx, cy, rx, ry
_SPHINX_BEARD = (46, 68, 36, 56)  # x, y, w, h — ends at 124, hangs 16 below the lappets
_SPHINX_STRIPES = [
    (22, 64, 24, 10), (20, 82, 26, 10), (20, 100, 26, 10),
    (82, 64, 24, 10), (82, 82, 26, 10), (82, 100, 26, 10),
]


def sphinx_svg() -> str:
    """Sphinx head: bronze nemes, open face, beard hanging past the lappets, three stripes each side."""
    cx, cy, rx, ry = _SPHINX_FACE
    gaps = "".join(
        f'      <rect x="{x}" y="{y}" width="{w}" height="{h}" fill="black"/>\n'
        for x, y, w, h in _SPHINX_STRIPES
    )
    return _svg(
        "  <defs>\n"
        '    <mask id="sphinx-head" maskUnits="userSpaceOnUse">\n'
        '      <rect x="0" y="0" width="128" height="128" fill="black"/>\n'
        f"      {_polygon(_SPHINX_NEMES, 'white').strip()}\n"
        f'      <ellipse cx="{cx}" cy="{cy}" rx="{rx}" ry="{ry}" fill="black"/>\n'
        f"{gaps}"
        "    </mask>\n"
        "  </defs>\n"
        f'  <rect x="0" y="0" width="128" height="128" fill="{BRONZE}" mask="url(#sphinx-head)"/>\n'
        + _rect(*_SPHINX_BEARD, BRONZE, rx=4),
        "The Sphinx",
    )


def you_svg() -> str:
    """A stone gate, open at the bottom, and a parchment spark. No figure."""
    return _svg(
        _rect(18, 16, 12, 104, STONE, rx=3)
        + _rect(98, 16, 12, 104, STONE, rx=3)
        + _rect(18, 16, 92, 12, STONE, rx=3)
        + _rect(60, 44, 8, 40, PARCHMENT, rx=4),
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


def _draw_ellipse(draw, px, cx, cy, rx, ry, fill) -> None:
    x0, y0, x1, y1 = _scale(px, cx - rx, cy - ry, cx + rx, cy + ry)
    draw.ellipse([x0, y0, x1, y1], fill=fill)


def _draw_polygon(draw, px, points, fill) -> None:
    k = px / VIEW
    draw.polygon([(x * k, y * k) for x, y in points], fill=fill)


def _punch_rect(img, px, x, y, w, h) -> None:
    left, top, right, bottom = _scale(px, x, y, x + w, y + h)
    pixels = img.load()
    x0, y0 = max(0, int(left)), max(0, int(top))
    x1, y1 = min(img.size[0], int(right) + 1), min(img.size[1], int(bottom) + 1)
    for yy in range(y0, y1):
        for xx in range(x0, x1):
            pixels[xx, yy] = (0, 0, 0, 0)


def _punch_ellipse(img, px, cx, cy, rx, ry) -> None:
    """Clear an ellipse given in 128-space, the face opening of the Sphinx head."""
    x0, y0, x1, y1 = _scale(px, cx - rx, cy - ry, cx + rx, cy + ry)
    pixels = img.load()
    cx_p, cy_p = (x0 + x1) / 2, (y0 + y1) / 2
    rx_p, ry_p = max((x1 - x0) / 2, 0.5), max((y1 - y0) / 2, 0.5)
    for y in range(max(0, int(y0)), min(img.size[1], int(y1) + 1)):
        for x in range(max(0, int(x0)), min(img.size[0], int(x1) + 1)):
            if ((x - cx_p) / rx_p) ** 2 + ((y - cy_p) / ry_p) ** 2 <= 1:
                pixels[x, y] = (0, 0, 0, 0)


def render_png(kind: str, dest: Path, px: int) -> None:
    Image, ImageDraw = _pil()
    img = Image.new("RGBA", (px, px), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    if kind.endswith("logo.svg") or kind.endswith("tiresias.svg"):
        _draw_ellipse(draw, px, *_TIRESIAS_MASK, PARCHMENT)
        nx, ny, nr = _TIRESIAS_NOTCH
        _punch_ellipse(img, px, nx, ny, nr, nr)
    elif kind.endswith("sphinx.svg"):
        _draw_polygon(draw, px, _SPHINX_NEMES, BRONZE)
        _punch_ellipse(img, px, *_SPHINX_FACE)
        for band in _SPHINX_STRIPES:
            _punch_rect(img, px, *band)
        _draw_rect(draw, px, *_SPHINX_BEARD, BRONZE, rx=4)
    elif kind.endswith("you.svg"):
        _draw_rect(draw, px, 18, 16, 12, 104, STONE, rx=3)
        _draw_rect(draw, px, 98, 16, 12, 104, STONE, rx=3)
        _draw_rect(draw, px, 18, 16, 92, 12, STONE, rx=3)
        _draw_rect(draw, px, 60, 44, 8, 40, PARCHMENT, rx=4)
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
