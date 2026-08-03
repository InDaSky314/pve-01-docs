"""Rebuild the three Bally Sports channels that had no regional artwork.

agy composited these onto the small generic "pill" mark, because that is what
was on disk for them. The other 44 Bally regionals carry the full script
wordmark with a slanted red banner, so these three stood out. Same base, same
banner geometry, so all 47 read as one set.
"""
import io
from PIL import Image, ImageDraw, ImageFont

BASE = "bally_base.png"
RED = (228, 2, 45)
BG = (242, 242, 242)          # the wordmark is red on transparent -> light backdrop
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"

TARGETS = {
    "US BALLY SPORTS PRIME TICKET HD": ["PRIME TICKET"],
    "US BALLY SPORTS CINCINNATI HD": ["CINCINNATI"],
    "US BALLY SPORTS CINCINNATI PLUS HD": ["CINCINNATI", "PLUS"],
}

W = 400
SKEW = 14          # horizontal offset that gives the banner its lean


def banner(draw, y, h, text, font):
    """One slanted red parallelogram with centred white text."""
    tw = draw.textbbox((0, 0), text, font=font)[2]
    pad = 26
    bw = min(W - 8, tw + pad * 2)
    x0 = (W - bw) / 2
    draw.polygon([(x0 + SKEW, y), (x0 + bw + SKEW, y),
                  (x0 + bw, y + h), (x0, y + h)], fill=RED)
    bb = draw.textbbox((0, 0), text, font=font)
    draw.text(((W - bb[2]) / 2 + SKEW / 2, y + (h - bb[3] - bb[1]) / 2),
              text, font=font, fill=(255, 255, 255))


for name, labels in TARGETS.items():
    logo = Image.open(BASE).convert("RGBA")
    logo.thumbnail((W - 40, 260), Image.LANCZOS)

    band_h = 46
    gap = 6
    total = logo.height + 10 + len(labels) * band_h + (len(labels) - 1) * gap + 12
    canvas = Image.new("RGBA", (W, total), BG + (255,))
    canvas.alpha_composite(logo, ((W - logo.width) // 2, 4))

    d = ImageDraw.Draw(canvas)
    y = logo.height + 10
    for text in labels:
        size = 34
        while size > 12:
            f = ImageFont.truetype(FONT, size)
            if d.textbbox((0, 0), text, font=f)[2] <= W - 70:
                break
            size -= 2
        banner(d, y, band_h, text, ImageFont.truetype(FONT, size))
        y += band_h + gap

    out = "agyicons/%s.png" % name
    canvas.convert("RGB").save(out, "PNG", optimize=True)
    print("OK", out, canvas.size)
