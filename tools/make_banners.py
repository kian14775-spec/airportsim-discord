"""Builds the Discord banners from real in-game screenshots (assets/shots) -> assets/banners.

Look: a calm colour panel on the left with a big bold title, the screenshot on the right,
blended with a soft gradient. No AI images: shots + type only.
Run: .venv\\Scripts\\python tools\\make_banners.py   (PC only; the PNGs are committed)
"""
from pathlib import Path

from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parents[1]
SHOTS, OUT = ROOT / "assets" / "shots", ROOT / "assets" / "banners"
W, H = 1200, 400
TITLE_FONT = "C:/Windows/Fonts/seguibl.ttf"   # Segoe UI Black
SUB_FONT = "C:/Windows/Fonts/segoeuib.ttf"

# name: (title, subtitle, panel colour, shot, focus x 0-1)
BANNERS = {
    "welcome":  ("Welcome", "Airport Simulator", (52, 86, 158), "shot2.jpg", 0.45),
    "rules":    ("Rules", "Read before you verify", (150, 58, 64), "shot4.jpg", 0.40),
    "support":  ("Support", "We're here to help", (38, 122, 112), "shot6.jpg", 0.55),
    "help":     ("Help", "Everything in one place", (52, 86, 158), "shot5.jpg", 0.35),
    "stats":    ("Stats", "Live numbers and daily reports", (58, 66, 84), "shot1.jpg", 0.30),
    "liveries": ("Liveries", "Templates and showcase", (168, 118, 46), "shot4.jpg", 0.70),
    "contest":  ("Contest", "Livery of the Month", (168, 118, 46), "shot5.jpg", 0.55),
    "inbox":    ("Inbox", "Every open ticket", (58, 66, 84), "shot6.jpg", 0.40),
}


def cover(img: Image.Image, w: int, h: int, focus: float) -> Image.Image:
    scale = max(w / img.width, h / img.height)
    img = img.resize((int(img.width * scale) + 1, int(img.height * scale) + 1), Image.LANCZOS)
    x = int((img.width - w) * focus)
    y = (img.height - h) // 2
    return img.crop((x, y, x + w, y + h))


def make(name, title, sub, colour, shot, focus):
    photo = Image.open(SHOTS / shot).convert("RGB")
    bg = cover(photo, W, H, focus)
    bg = ImageEnhance.Contrast(ImageEnhance.Color(bg).enhance(1.08)).enhance(1.05)
    canvas = bg.copy()

    # measure the title first so the colour panel always covers it
    probe = ImageDraw.Draw(canvas)
    size = 118
    font = ImageFont.truetype(TITLE_FONT, size)
    while probe.textlength(title, font=font) > W * 0.60 and size > 50:
        size -= 4
        font = ImageFont.truetype(TITLE_FONT, size)
    title_w = probe.textlength(title, font=font)

    # colour panel with a soft feathered edge
    panel = Image.new("RGB", (W, H), colour)
    mask = Image.new("L", (W, H), 0)
    md = ImageDraw.Draw(mask)
    edge, fade = int(max(W * 0.38, 64 + title_w + 30)), int(W * 0.24)
    md.rectangle([0, 0, edge, H], fill=240)
    for i in range(fade):
        md.line([(edge + i, 0), (edge + i, H)], fill=int(240 * (1 - i / fade) ** 1.7))
    mask = mask.filter(ImageFilter.GaussianBlur(8))
    canvas = Image.composite(panel, canvas, mask)

    # gentle bottom shade so the photo side isn't flat
    shade = Image.new("L", (W, H), 0)
    sd = ImageDraw.Draw(shade)
    for y in range(H):
        sd.line([(0, y), (W, y)], fill=int(60 * (y / H) ** 2))
    canvas = Image.composite(Image.new("RGB", (W, H), (10, 14, 22)), canvas, shade)

    sub_font = ImageFont.truetype(SUB_FONT, 28)
    tx = 64
    box = probe.textbbox((0, 0), title, font=font)          # real glyph box (Segoe has big side bearings)
    title_h, gap, sub_h = box[3] - box[1], 22, 28
    block = title_h + gap + sub_h + 22
    ty = (H - block) // 2 - box[1]

    soft = Image.new("RGBA", (W, H), (0, 0, 0, 0))           # soft shadow, not a hard black copy
    ImageDraw.Draw(soft).text((tx + 2, ty + 5), title, font=font, fill=(0, 0, 0, 120))
    soft = soft.filter(ImageFilter.GaussianBlur(7))
    canvas = Image.alpha_composite(canvas.convert("RGBA"), soft)
    d = ImageDraw.Draw(canvas)
    d.text((tx, ty), title, font=font, fill=(255, 255, 255))
    sub_y = ty + box[3] + gap
    d.text((tx + 3, sub_y), sub.upper(), font=sub_font, fill=(232, 238, 247))

    # rounded corners like Discord shows them
    radius = 22
    rmask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(rmask).rounded_rectangle([0, 0, W - 1, H - 1], radius, fill=255)
    out = Image.new("RGBA", (W, H))
    out.paste(canvas.convert("RGB"), (0, 0), rmask)
    OUT.mkdir(parents=True, exist_ok=True)
    out.save(OUT / f"{name}.png", optimize=True)
    return OUT / f"{name}.png"


if __name__ == "__main__":
    for n, args in BANNERS.items():
        print("made", make(n, *args).name)
