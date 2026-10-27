#!/usr/bin/env python3
"""Write the Tiresias illustrations, cover, social card and phase icons as SVG.

Built from the same character geometry as ``render_brand.py`` (the three icons are placed
with a transform, never redrawn), on the palette in ``assets/brand/palette.json``. No raster,
no third-party artwork, no figures or company names. PNGs of the cover and social card are
rendered from these SVGs with headless Chrome (``--png``), so the text uses a real font.

    python3 scripts/render_art.py          # SVGs only (stdlib)
    python3 scripts/render_art.py --png    # also assets/brand/cover.png, social.png (needs Chrome)
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import render_brand  # same folder: geometry of the mark and the characters

P = json.loads((ROOT / "assets/brand/palette.json").read_text())
INK, BRONZE, CELESTE, PARCHMENT, STONE, TEXT = (
    P["ink"],
    P["bronze"],
    P["celeste"],
    P["parchment"],
    P["stone"],
    P["text"],
)
INK_2 = "#1A1F29"  # panel ink, as in the web UI (app.css --ink-2)
INK_3 = "#242A36"  # raised ink (app.css --ink-3)
LINE = "#313847"  # hairline on ink (app.css --line)

SANS = "Inter, 'SF Pro Display', system-ui, -apple-system, 'Segoe UI', Helvetica, Arial, sans-serif"

CHROME = os.environ.get("CHROME", "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome")


# ---- helpers ----------------------------------------------------------------------------------


def _inner(svg: str) -> str:
    """The shapes of a 128×128 icon from render_brand, without its <svg>/<title> wrapper."""
    body = svg.split("</title>\n", 1)[1]
    return body.rsplit("</svg>", 1)[0]


def place(icon: str, x: float, y: float, size: float, suffix: str = "") -> str:
    """Place a 128×128 icon body at (x, y), scaled to ``size`` px; mask ids get ``suffix``."""
    body = _inner(icon)
    if suffix:
        body = re.sub(r'(id="|url\(#)(veil-[a-z]+)', rf"\1\2-{suffix}", body)
    k = size / 128.0
    return f'<g transform="translate({x:g} {y:g}) scale({k:g})">\n{body}</g>\n'


def doc(w: int, h: int, label: str, body: str, bg: str | None = INK) -> str:
    fill = f'  <rect width="{w}" height="{h}" fill="{bg}"/>\n' if bg else ""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}" '
        f'role="img" aria-label="{label}">\n  <title>{label}</title>\n{fill}{body}</svg>\n'
    )


def icon_doc(label: str, body: str) -> str:
    """128×128 icon in currentColor, so the UI picks its colour (default: stone)."""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 128 128" '
        f'role="img" aria-label="{label}" fill="currentColor" color="{STONE}">\n'
        f"  <title>{label}</title>\n{body}</svg>\n"
    )


def text(x, y, s, size, fill, weight=400, anchor="start", spacing=0.0, opacity=1.0) -> str:
    extra = f' letter-spacing="{spacing:g}"' if spacing else ""
    op = f' opacity="{opacity:g}"' if opacity != 1 else ""
    return (
        f'  <text x="{x:g}" y="{y:g}" font-family="{SANS}" font-size="{size:g}" '
        f'font-weight="{weight}" fill="{fill}" text-anchor="{anchor}"{extra}{op}>{s}</text>\n'
    )


def ring(cx, cy, r_out, r_in, fill, opacity=1.0) -> str:
    op = f' opacity="{opacity:g}"' if opacity != 1 else ""
    return (
        f'  <path fill-rule="evenodd" fill="{fill}"{op} d="M{cx - r_out:g},{cy:g}'
        f"a{r_out:g},{r_out:g} 0 1,0 {2 * r_out:g},0a{r_out:g},{r_out:g} 0 1,0 {-2 * r_out:g},0z"
        f"M{cx - r_in:g},{cy:g}a{r_in:g},{r_in:g} 0 1,0 {2 * r_in:g},0"
        f'a{r_in:g},{r_in:g} 0 1,0 {-2 * r_in:g},0z"/>\n'
    )


def edge(x1, y1, x2, y2, color, width, dash: str | None = None, opacity=1.0) -> str:
    """An elbow connector parent (bottom) → child (top): down, across, down."""
    my = (y1 + y2) / 2
    d = f' stroke-dasharray="{dash}"' if dash else ""
    op = f' opacity="{opacity:g}"' if opacity != 1 else ""
    return (
        f'  <path d="M{x1:g},{y1:g} V{my:g} H{x2:g} V{y2:g}" fill="none" stroke="{color}" '
        f'stroke-width="{width:g}" stroke-linecap="round" stroke-linejoin="round"{d}{op}/>\n'
    )


# ---- the why-tree motif -----------------------------------------------------------------------
# One root, three candidate causes; the middle one is accepted and drills one level deeper to the
# evidence (a small table of rows). Pruned causes are stone and dashed and end in a cross. The
# cover and the social card use this motif. Labels default off, so only the cover names the parts.


def why_tree(
    ox: float, oy: float, s: float, dim: float = 1.0, rows: bool = True, labels: bool = False
) -> str:
    """Why-tree drawn in a 600×520 box at (ox, oy), scaled by s."""

    def X(v):
        return ox + v * s

    def Y(v):
        return oy + v * s

    out = []
    root = (300, 60)
    kids = [(110, 230), (300, 230), (490, 230)]
    leaf = (300, 390)
    # edges first, so nodes sit on top
    for i, (kx, ky) in enumerate(kids):
        on_path = i == 1
        out.append(
            edge(
                X(root[0]),
                Y(root[1] + 34),
                X(kx),
                Y(ky - 30),
                BRONZE if on_path else STONE,
                (8 if on_path else 4) * s,
                None if on_path else f"{10 * s:g} {12 * s:g}",
                dim if on_path else 0.55 * dim,
            )
        )
    out.append(edge(X(300), Y(230 + 30), X(leaf[0]), Y(leaf[1] - 26), BRONZE, 8 * s, None, dim))
    # root: the figure that moved (parchment ring around a bronze core)
    out.append(ring(X(root[0]), Y(root[1]), 34 * s, 22 * s, PARCHMENT, dim))
    out.append(
        f'  <circle cx="{X(root[0]):g}" cy="{Y(root[1]):g}" r="{12 * s:g}" fill="{BRONZE}" opacity="{dim:g}"/>\n'
    )
    # candidate causes
    for i, (kx, ky) in enumerate(kids):
        if i == 1:
            out.append(
                f'  <circle cx="{X(kx):g}" cy="{Y(ky):g}" r="{30 * s:g}" fill="{BRONZE}" opacity="{dim:g}"/>\n'
            )
        else:
            out.append(ring(X(kx), Y(ky), 26 * s, 18 * s, STONE, 0.6 * dim))
            c, h = X(kx), Y(ky)
            arm = 9 * s
            for sign in (1, -1):  # the two strokes of a cross: pruned
                out.append(
                    f'  <line x1="{c - arm:g}" y1="{h - sign * arm:g}" x2="{c + arm:g}" y2="{h + sign * arm:g}" '
                    f'stroke="{STONE}" stroke-width="{4 * s:g}" stroke-linecap="round" opacity="{0.6 * dim:g}"/>\n'
                )
    # evidence: a small table of rows under the accepted cause
    lx, ly = X(leaf[0] - 90), Y(leaf[1] - 26)
    w, h = 180 * s, 112 * s
    out.append(
        f'  <rect x="{lx:g}" y="{ly:g}" width="{w:g}" height="{h:g}" rx="{12 * s:g}" fill="{INK_3}" stroke="{BRONZE}" stroke-width="{4 * s:g}" opacity="{dim:g}"/>\n'
    )
    if rows:
        for r in range(3):
            ry = ly + (24 + r * 30) * s
            out.append(
                f'  <rect x="{lx + 18 * s:g}" y="{ry:g}" width="{44 * s:g}" height="{10 * s:g}" rx="{5 * s:g}" fill="{CELESTE}" opacity="{0.9 * dim:g}"/>\n'
            )
            out.append(
                f'  <rect x="{lx + 74 * s:g}" y="{ry:g}" width="{88 * s:g}" height="{10 * s:g}" rx="{5 * s:g}" fill="{PARCHMENT}" opacity="{0.55 * dim:g}"/>\n'
            )
    if labels:
        # ~22px on the cover (s=1.30). y is the alphabetic baseline.
        label_size = 22 * s / 1.30
        # Right of the solid bronze middle node, clear of the connector under it.
        out.append(text(X(344), Y(236), "accepted", label_size, TEXT, 600))
        # Once, in the crook of the left dashed branch, above its cross.
        out.append(text(X(156), Y(182), "pruned", label_size, TEXT, 600))
        # Right of the evidence table.
        out.append(text(X(406), Y(426), "rows", label_size, TEXT, 600))
    return "".join(out)


# ---- assets -----------------------------------------------------------------------------------


def cast_row(x: float, y: float, size: float, gap: float, labels: bool, label_size: float) -> str:
    """Tiresias, the Sphinx and You side by side, each with its role (as in the UI cast strip)."""
    cast = [
        (render_brand.tiresias_svg(), "Tiresias proposes", BRONZE),
        (render_brand.sphinx_svg(), "The Sphinx tests", CELESTE),
        (render_brand.you_svg(), "You decide", PARCHMENT),
    ]
    out = []
    for i, (svg, label, color) in enumerate(cast):
        cx = x + i * (size + gap)
        out.append(place(svg, cx, y, size, suffix=f"c{i}"))
        if labels:
            out.append(
                text(
                    cx + size / 2,
                    y + size + label_size * 1.6,
                    label,
                    label_size,
                    color,
                    600,
                    "middle",
                )
            )
    return "".join(out)


def cover_svg() -> str:
    w, h = 1920, 1080
    body = []
    body.append(why_tree(1110, 170, 1.30, dim=1.0, labels=True))
    body.append(place(render_brand.logo_svg(), 150, 150, 150))
    body.append(text(150, 430, "Tiresias", 150, BRONZE, 700, spacing=-2))
    body.append(text(156, 520, "Ask why. Get the evidence.", 58, TEXT, 600))
    body.append(
        text(
            156,
            590,
            "An evidence-backed “5 Whys” agent for financial analysts on SEC filings.",
            32,
            STONE,
        )
    )
    body.append(cast_row(170, 680, 136, 190, True, 28))
    body.append(f'  <rect x="0" y="{h - 84}" width="{w}" height="84" fill="{INK_2}"/>\n')
    body.append(
        text(
            156,
            h - 32,
            "Open Agent Hackathon 2026  ·  Reasoning Architecture",
            28,
            STONE,
            500,
            spacing=1,
        )
    )
    return doc(w, h, "Tiresias: ask why, get the evidence", "".join(body))


def social_svg() -> str:
    w, h = 1280, 640
    body = []
    body.append(why_tree(760, 110, 0.82, dim=1.0))
    body.append(place(render_brand.logo_svg(), 96, 110, 110))
    body.append(text(96, 330, "Tiresias", 110, BRONZE, 700, spacing=-1))
    body.append(text(100, 400, "Ask why. Get the evidence.", 44, TEXT, 600))
    body.append(text(100, 472, "An evidence-backed “5 Whys” agent for", 28, STONE))
    body.append(text(100, 512, "financial analysts on SEC filings.", 28, STONE))
    body.append(
        text(
            100,
            h - 40,
            "Open Agent Hackathon 2026 · Reasoning Architecture",
            22,
            STONE,
            500,
            spacing=1,
        )
    )
    return doc(w, h, "Tiresias: ask why, get the evidence", "".join(body))


def empty_state_svg() -> str:
    """The question node alone: parchment ring, bronze core, before any cause. No text."""
    w, h = 640, 360
    cx, cy, s = w / 2, h / 2, 2.8
    body = ring(cx, cy, 34 * s, 22 * s, PARCHMENT) + (
        f'  <circle cx="{cx:g}" cy="{cy:g}" r="{12 * s:g}" fill="{BRONZE}"/>\n'
    )
    return doc(w, h, "An empty why-tree: the question, before any cause", body, bg=None)


def why_tree_svg() -> str:
    w, h = 600, 520
    return doc(
        w,
        h,
        "A why-tree: one accepted cause drilled to its evidence, two pruned",
        why_tree(0, 0, 1.0),
        bg=None,
    )


# Phase icons for a progress stepper (understand → recall → drill down → new source → reconcile).
# Fills only, in currentColor, readable at 24 px.


def phase_understand() -> str:  # a lens: read the question, check scope, frame the figure
    return icon_doc(
        "Understand",
        ring(54, 54, 36, 24, "currentColor")
        + '  <rect x="76" y="70" width="18" height="48" rx="9" transform="rotate(-45 85 94)"/>\n',
    )


def phase_recall() -> str:  # stacked lessons
    return icon_doc(
        "Recall",
        '  <rect x="20" y="20" width="88" height="22" rx="8"/>\n'
        '  <rect x="20" y="53" width="88" height="22" rx="8" opacity="0.75"/>\n'
        '  <rect x="20" y="86" width="88" height="22" rx="8" opacity="0.5"/>\n',
    )


def phase_drill() -> str:  # a small tree
    return icon_doc(
        "Drill down",
        '  <circle cx="64" cy="22" r="14"/>\n'
        '  <rect x="58" y="34" width="12" height="22"/>\n'
        '  <rect x="26" y="52" width="76" height="12" rx="6"/>\n'
        '  <rect x="26" y="58" width="12" height="22"/>\n'
        '  <rect x="90" y="58" width="12" height="22"/>\n'
        '  <circle cx="32" cy="94" r="14"/>\n'
        '  <circle cx="96" cy="94" r="14"/>\n',
    )


def phase_source() -> str:  # a new source: a data cylinder with a plus
    return icon_doc(
        "New source",
        '  <ellipse cx="54" cy="28" rx="38" ry="14"/>\n'
        '  <rect x="16" y="28" width="76" height="66"/>\n'
        '  <ellipse cx="54" cy="94" rx="38" ry="14"/>\n'
        '  <rect x="94" y="78" width="12" height="40" rx="6"/>\n'
        '  <rect x="80" y="92" width="40" height="12" rx="6"/>\n',
    )


def phase_reconcile() -> str:  # two figures meet: a check
    return icon_doc(
        "Reconcile",
        '  <polygon points="14,66 30,50 54,74 98,30 114,46 54,106"/>\n',
    )


SVGS = {
    "assets/brand/cover.svg": cover_svg,
    "assets/brand/social.svg": social_svg,
    "assets/illustrations/empty-investigation.svg": empty_state_svg,
    "assets/illustrations/why-tree.svg": why_tree_svg,
    "assets/icons/phase-understand.svg": phase_understand,
    "assets/icons/phase-recall.svg": phase_recall,
    "assets/icons/phase-drill.svg": phase_drill,
    "assets/icons/phase-source.svg": phase_source,
    "assets/icons/phase-reconcile.svg": phase_reconcile,
}

PNGS = {  # svg -> (png, width, height)
    "assets/brand/cover.svg": ("assets/brand/cover.png", 1920, 1080),
    "assets/brand/social.svg": ("assets/brand/social.png", 1280, 640),
}


def write_svgs() -> None:
    for rel, fn in SVGS.items():
        path = ROOT / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(fn())


def write_pngs() -> None:
    if not Path(CHROME).exists():
        raise SystemExit(f"Chrome not found at {CHROME}; set CHROME=/path/to/chrome")
    for src, (dest, w, h) in PNGS.items():
        with tempfile.TemporaryDirectory() as tmp:
            page = Path(tmp) / "page.html"
            page.write_text(
                "<!doctype html><meta charset=utf-8><style>html,body{margin:0;background:"
                f"{INK}}}svg{{display:block}}</style>{(ROOT / src).read_text().split('?>', 1)[1]}"
            )
            subprocess.run(
                [
                    CHROME,
                    "--headless",
                    "--disable-gpu",
                    "--hide-scrollbars",
                    f"--window-size={w},{h}",
                    f"--screenshot={ROOT / dest}",
                    page.as_uri(),
                ],
                check=True,
                capture_output=True,
                timeout=60,
            )


def main(argv: list[str]) -> int:
    write_svgs()
    done = list(SVGS)
    if "--png" in argv:
        write_pngs()
        done += [p for p, _, _ in PNGS.values()]
    print("wrote", ", ".join(done))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
