"""Tegner appikonet og lager packaging/icon.icns. Krever pymupdf og macOS (sips, iconutil)."""

import subprocess
import tempfile
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
S = 1024


def draw() -> pymupdf.Pixmap:
    doc = pymupdf.open()
    page = doc.new_page(width=S, height=S)
    blue, dark, white = (0.18, 0.36, 0.54), (0.11, 0.24, 0.38), (1, 1, 1)

    # Bakgrunn: avrundet kvadrat med macOS-marger (824 av 1024)
    m = 100
    page.draw_rect(pymupdf.Rect(m, m, S - m, S - m), color=None, fill=blue, radius=0.22)

    # Ark med brettet hjørne
    x0, y0, x1, y1, fold = 300, 230, 724, 794, 110
    sheet = [(x0, y0), (x1 - fold, y0), (x1, y0 + fold), (x1, y1), (x0, y1)]
    page.draw_polyline(sheet + [sheet[0]], color=None, fill=white, closePath=True)
    page.draw_polyline([(x1 - fold, y0), (x1 - fold, y0 + fold), (x1, y0 + fold)],
                       color=None, fill=(0.82, 0.87, 0.92), closePath=True)

    # Markdown-merket: M og pil ned
    lw = 34
    my0, my1 = 470, 650
    page.draw_polyline([(372, my1), (372, my0), (437, my0 + 75), (502, my0), (502, my1)],
                       color=dark, width=lw, lineJoin=1, lineCap=1)
    ax = 600
    page.draw_line((ax, my0), (ax, my1 - 40), color=dark, width=lw, lineCap=1)
    page.draw_polyline([(ax - 56, my1 - 70), (ax, my1), (ax + 56, my1 - 70)],
                       color=dark, width=lw, lineJoin=1, lineCap=1)

    return page.get_pixmap(alpha=True, dpi=72)


def main():
    pix = draw()
    with tempfile.TemporaryDirectory() as tmp:
        master = Path(tmp) / "icon.png"
        pix.save(master)
        iconset = Path(tmp) / "icon.iconset"
        iconset.mkdir()
        for size in (16, 32, 128, 256, 512):
            for scale in (1, 2):
                px = size * scale
                name = f"icon_{size}x{size}{'@2x' if scale == 2 else ''}.png"
                subprocess.run(["sips", "-z", str(px), str(px), str(master), "--out", str(iconset / name)],
                               check=True, capture_output=True)
        subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(HERE / "icon.icns")], check=True)
        pix.save(HERE / "icon.png")
    print("Lagret", HERE / "icon.icns")


if __name__ == "__main__":
    main()
