#!/usr/bin/env python3
import os
import json
import subprocess
from PIL import Image, ImageDraw, ImageFont

OUTPUT_DIR = "/root/agy-icons-20260803"
SOURCE_BASE = "/srv/jellyfin-npvr/nextpvr/config/media/channels"
FONT_PATH = "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf"

def hex_to_rgb(h):
    h = h.lstrip('#')
    return tuple(int(h[i:i+2], 16) for i in (0, 2, 4))

def fit_text(draw, text, max_w, max_h, font_path):
    for size in range(50, 10, -1):
        font = ImageFont.truetype(font_path, size)
        bbox = draw.textbbox((0, 0), text, font=font)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        if w <= max_w and h <= max_h:
            return font, bbox
    font = ImageFont.truetype(font_path, 10)
    return font, draw.textbbox((0, 0), text, font=font)

CHANNELS_CONFIG = [
    {
        "channel": "US: MUSIC CHOICE POP HITS",
        "identifier": "POP HITS",
        "band_colour": "#E31B23",
        "backdrop": "#141414",
        "group": "standard",
        "source_filename": "US MUSIC CHOICE POP HITS.png",
    },
    {
        "channel": "US: MUSIC CHOICE CLASSIC COUNTRY HD",
        "identifier": "CLASSIC COUNTRY",
        "band_colour": "#E31B23",
        "backdrop": "#141414",
        "group": "standard",
        "source_filename": "US MUSIC CHOICE CLASSIC COUNTRY HD.png",
    },
    {
        "channel": "US: MUSIC CHOICE HIP-HOP AND R&B",
        "identifier": "HIP-HOP & R&B",
        "band_colour": "#E31B23",
        "backdrop": "#141414",
        "group": "standard",
        "source_filename": "US MUSIC CHOICE HIP-HOP AND R&B.png",
    },
    {
        "channel": "US: MUSIC CHOICE TROPICALES HD",
        "identifier": "TROPICALES",
        "band_colour": "#E31B23",
        "backdrop": "#141414",
        "group": "standard",
        "source_filename": "US MUSIC CHOICE TROPICALES HD.png",
    },
    {
        "channel": "US: MUSIC CHOICE: HIP-HOP CLASSICS",
        "identifier": "HIP-HOP CLASSICS",
        "band_colour": "#E31B23",
        "backdrop": "#141414",
        "group": "standard",
        "source_filename": "US MUSIC CHOICE HIP-HOP CLASSICS.png",
    },
    {
        "channel": "Green Bay: PBS 38 (WPNE)",
        "identifier": "WPNE 38",
        "band_colour": "#2638C4",
        "backdrop": "#141414",
        "group": "standard",
        "source_filename": "Green Bay PBS 38 (WPNE).png",
    },
    {
        "channel": "New York: PBS (WNJN)",
        "identifier": "WNJN",
        "band_colour": "#2638C4",
        "backdrop": "#141414",
        "group": "standard",
        "source_filename": "New York PBS (WNJN).png",
    },
    {
        "channel": "Los Angeles: PBS (KQIN)",
        "identifier": "KQIN",
        "band_colour": "#2638C4",
        "backdrop": "#141414",
        "group": "standard",
        "source_filename": "Los Angeles PBS (KQIN).png",
    },
    {
        "channel": "US: BALLY SPORTS PRIME TICKET HD",
        "identifier": "PRIME TICKET",
        "band_colour": "#E4022D",
        "backdrop": "#f2f2f2",
        "group": "bally",
        "source_filename": "US BALLY SPORTS PRIME TICKET HD.png",
    },
    {
        "channel": "US: BALLY SPORTS CINCINNATI HD",
        "identifier": "CINCINNATI",
        "band_colour": "#E4022D",
        "backdrop": "#f2f2f2",
        "group": "bally",
        "source_filename": "US BALLY SPORTS CINCINNATI HD.png",
    },
    {
        "channel": "US: BALLY SPORTS CINCINNATI PLUS HD",
        "identifier": "CINCINNATI / PLUS",
        "band_colour": "#E4022D",
        "backdrop": "#f2f2f2",
        "group": "bally_twobands",
        "source_filename": "US BALLY SPORTS CINCINNATI PLUS HD.png",
    },
    {
        "channel": "DE: BR FERNSEHEN HD NORD",
        "identifier": "NORD",
        "band_colour": "#2A65A9",
        "backdrop": "#f2f2f2",
        "group": "standard",
        "source_filename": "DE BR FERNSEHEN HD NORD.png",
    },
    {
        "channel": "DE: BR FERNSEHEN HD SÜD",
        "identifier": "SÜD",
        "band_colour": "#2A65A9",
        "backdrop": "#f2f2f2",
        "group": "standard",
        "source_filename": "DE BR FERNSEHEN HD SÜD.png",
    },
    {
        "channel": "DE: MTV HD",
        "identifier": "DEUTSCHLAND",
        "band_colour": "#00A9CF",
        "backdrop": "#f2f2f2",
        "group": "standard",
        "source_filename": "DE MTV HD.png",
    },
    {
        "channel": "US: CSN PHILADELPHIA PLUS HD",
        "identifier": "PLUS",
        "band_colour": "#008CC3",
        "backdrop": "#141414",
        "group": "standard",
        "source_filename": "US CSN PHILADELPHIA PLUS HD.png",
    },
    {
        "channel": "US: HALLMARK MOVIES/MYSTERIES 4K",
        "identifier": "4K",
        "band_colour": "#CDE950",
        "backdrop": "#141414",
        "group": "standard",
        "source_filename": "US HALLMARK MOVIESMYSTERIES 4K.png",
    },
    {
        "channel": "Green Bay: CBS 5 (WFRV) Alt",
        "identifier": "ALT",
        "band_colour": "#0055A5",
        "backdrop": "#f2f2f2",
        "group": "cbs",
        "source_filename": "Green Bay CBS 5 (WFRV) Alt.png",
    },
]

def main():
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    manifest = []

    for cfg in CHANNELS_CONFIG:
        ch_name = cfg["channel"]
        clean_name = ch_name.replace(":", "").replace("/", "").replace("|", "")
        out_filename = f"{clean_name}.png"
        out_path = os.path.join(OUTPUT_DIR, out_filename)
        src_path_container = f"{SOURCE_BASE}/{cfg['source_filename']}"
        local_src = f"/tmp/channel_sources/{cfg['source_filename']}"

        # Ensure local source file exists (pulled from CT 112 if needed)
        if not os.path.exists(local_src):
            subprocess.run(["pct", "pull", "112", src_path_container, local_src], check=True)

        src_img = Image.open(local_src)
        bg_rgb = hex_to_rgb(cfg["backdrop"])
        band_rgb = hex_to_rgb(cfg["band_colour"])
        text_rgb = (20, 20, 20) if cfg["band_colour"].upper() == "#CDE950" else (255, 255, 255)
        text = cfg["identifier"]
        group = cfg["group"]

        if group == "bally_twobands":
            canvas_w, canvas_h = 400, 286
            canvas = Image.new("RGB", (canvas_w, canvas_h), bg_rgb)
            logo = src_img.copy().convert("RGBA")
            logo.thumbnail((260, 150), Image.Resampling.LANCZOS)
            canvas.paste(logo, ((canvas_w - logo.width) // 2, 15), logo if "A" in logo.mode else None)

            draw = ImageDraw.Draw(canvas)
            slant = 12

            # Band 1: CINCINNATI
            b1_y1, b1_y2 = 180, 222
            b1_w = 260
            b1_x1 = (canvas_w - b1_w) // 2
            b1_x2 = b1_x1 + b1_w
            poly1 = [(b1_x1, b1_y1), (b1_x2, b1_y1), (b1_x2 - slant, b1_y2), (b1_x1 - slant, b1_y2)]
            draw.polygon(poly1, fill=band_rgb)

            font1, bbox1 = fit_text(draw, "CINCINNATI", b1_w - 30, (b1_y2 - b1_y1) - 10, FONT_PATH)
            tw1, th1 = bbox1[2] - bbox1[0], bbox1[3] - bbox1[1]
            tx1 = (b1_x1 + b1_x2 - slant) // 2 - tw1 // 2 - bbox1[0]
            ty1 = (b1_y1 + b1_y2) // 2 - th1 // 2 - bbox1[1]
            draw.text((tx1, ty1), "CINCINNATI", fill=text_rgb, font=font1)

            # Band 2: PLUS
            b2_y1, b2_y2 = 230, 272
            b2_w = 140
            b2_x1 = (canvas_w - b2_w) // 2
            b2_x2 = b2_x1 + b2_w
            poly2 = [(b2_x1, b2_y1), (b2_x2, b2_y1), (b2_x2 - slant, b2_y2), (b2_x1 - slant, b2_y2)]
            draw.polygon(poly2, fill=band_rgb)

            font2, bbox2 = fit_text(draw, "PLUS", b2_w - 20, (b2_y2 - b2_y1) - 10, FONT_PATH)
            tw2, th2 = bbox2[2] - bbox2[0], bbox2[3] - bbox2[1]
            tx2 = (b2_x1 + b2_x2 - slant) // 2 - tw2 // 2 - bbox2[0]
            ty2 = (b2_y1 + b2_y2) // 2 - th2 // 2 - bbox2[1]
            draw.text((tx2, ty2), "PLUS", fill=text_rgb, font=font2)

        elif group == "bally":
            canvas_w, canvas_h = 400, 286
            canvas = Image.new("RGB", (canvas_w, canvas_h), bg_rgb)
            logo = src_img.copy().convert("RGBA")
            logo.thumbnail((280, 170), Image.Resampling.LANCZOS)
            canvas.paste(logo, ((canvas_w - logo.width) // 2, 20), logo if "A" in logo.mode else None)

            draw = ImageDraw.Draw(canvas)
            b_y1, b_y2 = 215, 265
            b_w = 280
            b_x1 = (canvas_w - b_w) // 2
            b_x2 = b_x1 + b_w
            slant = 14
            poly = [(b_x1, b_y1), (b_x2, b_y1), (b_x2 - slant, b_y2), (b_x1 - slant, b_y2)]
            draw.polygon(poly, fill=band_rgb)

            font, bbox = fit_text(draw, text, b_w - 30, (b_y2 - b_y1) - 10, FONT_PATH)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            tx = (b_x1 + b_x2 - slant) // 2 - tw // 2 - bbox[0]
            ty = (b_y1 + b_y2) // 2 - th // 2 - bbox[1]
            draw.text((tx, ty), text, fill=text_rgb, font=font)

        elif group == "cbs":
            canvas_w, canvas_h = 400, 400
            canvas = Image.new("RGB", (canvas_w, canvas_h), bg_rgb)
            logo = src_img.copy().convert("RGBA")
            logo.thumbnail((340, 310), Image.Resampling.LANCZOS)
            canvas.paste(logo, ((canvas_w - logo.width) // 2, 15), logo if "A" in logo.mode else None)

            draw = ImageDraw.Draw(canvas)
            b_y1, b_y2 = 335, 385
            b_w = 340
            b_x1 = (canvas_w - b_w) // 2
            b_x2 = b_x1 + b_w
            draw.rectangle([b_x1, b_y1, b_x2, b_y2], fill=band_rgb)

            font, bbox = fit_text(draw, text, b_w - 30, (b_y2 - b_y1) - 10, FONT_PATH)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            tx = (b_x1 + b_x2) // 2 - tw // 2 - bbox[0]
            ty = (b_y1 + b_y2) // 2 - th // 2 - bbox[1]
            draw.text((tx, ty), text, fill=text_rgb, font=font)

        else:
            # Standard horizontal channels
            canvas_w = 400
            canvas_h = 280
            canvas = Image.new("RGB", (canvas_w, canvas_h), bg_rgb)

            logo = src_img.copy().convert("RGBA")
            logo.thumbnail((360, 190), Image.Resampling.LANCZOS)

            logo_x = (canvas_w - logo.width) // 2
            logo_y = 10 + (200 - logo.height) // 2
            canvas.paste(logo, (logo_x, logo_y), logo if "A" in logo.mode else None)

            draw = ImageDraw.Draw(canvas)
            b_y1, b_y2 = 220, 266
            b_w = 360
            b_x1 = (canvas_w - b_w) // 2
            b_x2 = b_x1 + b_w

            draw.rectangle([b_x1, b_y1, b_x2, b_y2], fill=band_rgb)

            font, bbox = fit_text(draw, text, b_w - 30, (b_y2 - b_y1) - 10, FONT_PATH)
            tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
            tx = (b_x1 + b_x2) // 2 - tw // 2 - bbox[0]
            ty = (b_y1 + b_y2) // 2 - th // 2 - bbox[1]
            draw.text((tx, ty), text, fill=text_rgb, font=font)

        canvas.save(out_path, "PNG")
        file_bytes = os.path.getsize(out_path)

        manifest.append({
            "channel": ch_name,
            "file": out_filename,
            "identifier": text,
            "band_colour": cfg["band_colour"],
            "source_file": src_path_container,
            "bytes": file_bytes,
        })
        print(f"Generated {out_filename}: {file_bytes} bytes")

    manifest_path = os.path.join(OUTPUT_DIR, "manifest.json")
    with open(manifest_path, "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    print(f"\nManifest successfully written to {manifest_path} with {len(manifest)} entries.")

if __name__ == "__main__":
    main()
