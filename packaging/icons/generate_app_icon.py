#!/usr/bin/env python3
"""Generate PDF2SEPA app icons (PNG, ICO, ICNS) from app_icon_source.png.

The visual design lives in ``app_icon_source.png`` (1024×1024 RGBA, squircle).
This script only resizes and exports platform formats — it does not redraw the logo.

Usage:
    python packaging/icons/generate_app_icon.py

Output:
    app_icon.png, app_icon.ico, app_icon.icns
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 1024
OUT_DIR = Path(__file__).resolve().parent
SOURCE = OUT_DIR / "app_icon_source.png"

ICO_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICNS_PAIRS = ((16, 32), (32, 64), (128, 256), (256, 512), (512, 1024))
SQUIRCLE_RADIUS_FRAC = 0.2237


def squircle_mask(size: int) -> Image.Image:
    radius = max(1, int(size * SQUIRCLE_RADIUS_FRAC))
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle(
        (0, 0, size - 1, size - 1), radius=radius, fill=255
    )
    return mask


def _is_baked_checkerboard(r: int, g: int, b: int) -> bool:
    """AI exports often bake a grey checkerboard instead of real alpha."""
    if abs(r - g) > 4 or abs(g - b) > 4:
        return False
    return (48 <= r <= 65) or (28 <= r <= 44)


def prepare_master(img: Image.Image) -> Image.Image:
    """Strip baked checkerboard and ensure squircle transparency."""
    img = img.convert("RGBA")
    if img.size != (SIZE, SIZE):
        img = img.resize((SIZE, SIZE), Image.Resampling.LANCZOS)

    px = img.load()
    for y in range(SIZE):
        for x in range(SIZE):
            r, g, b, a = px[x, y]
            if _is_baked_checkerboard(r, g, b):
                px[x, y] = (r, g, b, 0)

    # Clip to macOS squircle so Dock/taskbar looks native
    mask = squircle_mask(SIZE)
    img.putalpha(Image.composite(img.getchannel("A"), Image.new("L", (SIZE, SIZE), 0), mask))
    return img


def load_master() -> Image.Image:
    if not SOURCE.is_file():
        raise FileNotFoundError(
            f"Missing {SOURCE.name} — place the 1024×1024 master PNG there first."
        )
    return prepare_master(Image.open(SOURCE))


def write_png(path: Path, img: Image.Image) -> None:
    img.save(path, format="PNG", optimize=True)


def write_ico(path: Path, master: Image.Image) -> None:
    frames = [master.resize((s, s), Image.Resampling.LANCZOS) for s in ICO_SIZES]
    frames[-1].save(
        path,
        format="ICO",
        sizes=[(s, s) for s in ICO_SIZES],
        append_images=frames[:-1],
    )


def write_icns(path: Path, master: Image.Image) -> None:
    iconset = path.with_suffix(".iconset")
    iconset.mkdir(exist_ok=True)
    try:
        for base, retina in ICNS_PAIRS:
            master.resize((base, base), Image.Resampling.LANCZOS).save(
                iconset / f"icon_{base}x{base}.png"
            )
            master.resize((retina, retina), Image.Resampling.LANCZOS).save(
                iconset / f"icon_{base}x{base}@2x.png"
            )
        result = subprocess.run(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            raise RuntimeError(result.stderr.strip() or "iconutil failed (macOS only)")
    finally:
        for f in iconset.glob("*.png"):
            f.unlink()
        if iconset.exists():
            iconset.rmdir()


def main() -> None:
    master = load_master()
    png_path = OUT_DIR / "app_icon.png"
    ico_path = OUT_DIR / "app_icon.ico"
    icns_path = OUT_DIR / "app_icon.icns"

    write_png(png_path, master)
    write_ico(ico_path, master)
    write_icns(icns_path, master)

    px = master.load()
    corners = [px[0, 0][3], px[-1, 0][3], px[0, -1][3], px[-1, -1][3]]
    print(f"Source: {SOURCE.name}")
    print(f"Size: {master.size} RGBA")
    print(f"Corner alpha: {corners}")
    print(f"ICO sizes: {ICO_SIZES}")
    print(f"ICNS: {icns_path.stat().st_size} bytes")
    print("Written:", png_path.name, ico_path.name, icns_path.name)


if __name__ == "__main__":
    main()
