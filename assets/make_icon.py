#!/usr/bin/env python3
"""Generate the app icon in every format the installers need, from one design.

Outputs (into this assets/ folder):
  icon.png        1024x1024 master (also used by the GUI window)
  icon.icns       macOS app icon
  icon.ico        Windows app icon (multi-size)
  icon_256.png    Linux hicolor theme / .desktop
  icon_512.png    Linux hicolor theme

Run:  python3 assets/make_icon.py
Requires Pillow. On macOS the crisp .icns is built with sips+iconutil when
available, falling back to Pillow if not.
"""

import shutil
import subprocess
import tempfile
from pathlib import Path

from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
MASTER = 1024

# Brand colours (indigo -> violet gradient, white play mark).
TOP = (90, 79, 230)
BOTTOM = (150, 92, 246)
MARK = (255, 255, 255, 245)


def draw_master(size=MASTER):
    """Return the master RGBA icon: a rounded gradient tile with a play mark."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    margin = int(size * 0.08)                 # leave padding so the tile isn't full-bleed
    span = size - 2 * margin

    # Vertical gradient, built as a 1px column and stretched (fast + smooth).
    column = Image.new("RGB", (1, span))
    for y in range(span):
        t = y / (span - 1)
        column.putpixel((0, y), tuple(int(a + (b - a) * t) for a, b in zip(TOP, BOTTOM)))
    gradient = column.resize((span, span)).convert("RGBA")

    # Rounded-rectangle mask so the tile has soft corners.
    mask = Image.new("L", (span, span), 0)
    ImageDraw.Draw(mask).rounded_rectangle([0, 0, span - 1, span - 1],
                                           radius=int(span * 0.22), fill=255)
    img.paste(gradient, (margin, margin), mask)

    # Centred play triangle.
    draw = ImageDraw.Draw(img)
    cx, cy = size / 2, size / 2
    r = span * 0.26
    draw.polygon([(cx - r * 0.5, cy - r), (cx - r * 0.5, cy + r), (cx + r, cy)], fill=MARK)
    return img


def save_png(img, path, size):
    img.resize((size, size), Image.LANCZOS).save(path)
    print(f"  wrote {path.name} ({size}x{size})")


def make_ico(img, path):
    sizes = [16, 24, 32, 48, 64, 128, 256]
    img.save(path, sizes=[(s, s) for s in sizes])
    print(f"  wrote {path.name} ({', '.join(str(s) for s in sizes)})")


def make_icns(img, path):
    """Build a macOS .icns. Prefer sips+iconutil for crispness; else Pillow."""
    if shutil.which("iconutil"):
        with tempfile.TemporaryDirectory() as tmp:
            iconset = Path(tmp) / "icon.iconset"
            iconset.mkdir()
            specs = [(16, "16x16"), (32, "16x16@2x"), (32, "32x32"), (64, "32x32@2x"),
                     (128, "128x128"), (256, "128x128@2x"), (256, "256x256"),
                     (512, "256x256@2x"), (512, "512x512"), (1024, "512x512@2x")]
            for px, label in specs:
                img.resize((px, px), Image.LANCZOS).save(iconset / f"icon_{label}.png")
            subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(path)], check=True)
        print(f"  wrote {path.name} (via iconutil)")
    else:
        img.save(path)  # Pillow can write .icns directly (fine on non-mac build hosts)
        print(f"  wrote {path.name} (via Pillow)")


def main():
    master = draw_master()
    save_png(master, HERE / "icon.png", MASTER)
    save_png(master, HERE / "icon_512.png", 512)
    save_png(master, HERE / "icon_256.png", 256)
    make_ico(master, HERE / "icon.ico")
    make_icns(master, HERE / "icon.icns")
    print("Done.")


if __name__ == "__main__":
    main()
