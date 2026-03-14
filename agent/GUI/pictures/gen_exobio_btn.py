"""
Generates exobio_btn_32.png — a 32x32 exobiology button icon.

Design:
  - Dark space background with radial depth gradient and stars
  - Bioluminescent alien cell (teal/green) with cilia, membrane shimmer
  - Darker nucleus with inner highlight
  - Subtle DNA hint (paired dots, right side)
  - Soft Gaussian glow composited beneath the cell

Run:  python3 gen_exobio_btn.py
Requires: pip install Pillow
"""
import math
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter

W, H   = 32, 32
OUT    = Path(__file__).parent / "exobio_btn_32.png"


def generate() -> Image.Image:
    img  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)

    # ── Background: radial dark gradient ─────────────────────────────────
    for y in range(H):
        for x in range(W):
            dx, dy = x - W / 2, y - H / 2
            dist   = math.sqrt(dx * dx + dy * dy) / (W * 0.7)
            b      = int(12 + dist * 18)
            draw.point((x, y), (b, b, int(b * 1.6), 255))

    # ── Stars ─────────────────────────────────────────────────────────────
    for sx, sy in [(2, 3), (28, 4), (5, 27), (29, 24),
                   (14, 1), (1, 15), (30, 14), (26, 29)]:
        draw.point((sx, sy), (200, 210, 255, 180))

    # ── Glow (separate layer → Gaussian blur → composite) ─────────────────
    glow  = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(glow).ellipse([3, 3, 29, 29], fill=(20, 200, 130, 60))
    glow  = glow.filter(ImageFilter.GaussianBlur(radius=3))
    img   = Image.alpha_composite(img, glow)
    draw  = ImageDraw.Draw(img)

    cx, cy   = 16, 16
    cell_r   = 9

    # ── Cilia ─────────────────────────────────────────────────────────────
    for angle in [i * 40 for i in range(9)]:
        rad = math.radians(angle)
        draw.line(
            [(int(cx + cell_r * math.cos(rad)),
              int(cy + cell_r * math.sin(rad))),
             (int(cx + (cell_r + 4) * math.cos(rad)),
              int(cy + (cell_r + 4) * math.sin(rad)))],
            fill=(60, 230, 160, 210), width=1,
        )

    # ── Cell body ─────────────────────────────────────────────────────────
    draw.ellipse(
        [cx - cell_r, cy - cell_r, cx + cell_r, cy + cell_r],
        fill=(25, 160, 105, 215),
        outline=(70, 235, 165, 255),
    )

    # ── Membrane shimmer (top-left arc) ───────────────────────────────────
    draw.arc(
        [cx - cell_r, cy - cell_r, cx + cell_r, cy + cell_r],
        start=200, end=310,
        fill=(160, 255, 210, 130),
        width=1,
    )

    # ── Nucleus ───────────────────────────────────────────────────────────
    draw.ellipse(
        [cx - 4, cy - 4, cx + 4, cy + 4],
        fill=(15, 100, 65, 240),
        outline=(50, 200, 140, 255),
    )
    draw.ellipse([cx - 1, cy - 2, cx + 1, cy], fill=(140, 255, 200, 180))

    # ── DNA hint (3 paired dots, right side) ──────────────────────────────
    for hx, hy in [(22, 10), (23, 13), (22, 16)]:
        draw.point((hx,     hy), (80, 220, 160, 200))
        draw.point((hx + 2, hy), (80, 180, 140, 160))

    return img


if __name__ == "__main__":
    icon = generate()
    icon.save(OUT, "PNG")
    print(f"Saved {OUT}  ({icon.size[0]}x{icon.size[1]} px, RGBA)")
