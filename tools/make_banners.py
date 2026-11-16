"""Builds the Discord banners (v2, 2026-11-16) -> assets/banners/<name>.png

Look: a full-bleed aircraft hero shot (hand-built Blender renders, AS2 exterior set), a giant title set
BEHIND the jet (the jet is cut out of the sky, so the tail/nose pass in front of the letters), small tracked
captions on the bottom corners, then a post pass (sun + rays, temperature zoning, local contrast on the jet,
bloom, grade, vignette, lens fringe, grain). No colour panel, no AI.
Run (PC only, the PNGs are committed):  python -I tools/make_banners.py [name ...]
Sources: BANNER_SRC env var or the GFX folder below.
"""
import os
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont
from scipy.ndimage import binary_erosion, binary_fill_holes, gaussian_filter, map_coordinates

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "assets" / "banners"
SRC = Path(os.environ.get("BANNER_SRC", r"C:\Users\super\OneDrive\Desktop\Claude Coding\GFX - AS2 Role Renders\Exterior_Aircraft_Shots"))
FONT = "C:/Windows/Fonts/bahnschrift.ttf"      # DIN-style, same family as airport signage
OUT_W = 1500                                     # 3:1; Discord shows cards ~550 px wide, so 2.7x is plenty
V, HERO = "Final_9_Aircraft_Shots_v2/No_Logo/", "Heroes_ads_loading/Final_post/"

# name: shot, crop top (px of the 1920x1080 shot), title, right caption, title align, title top, title size,
#       sun (x, y as 0-1 of the banner; can sit outside the frame)
BANNERS = {
    "welcome":  dict(shot=V + "06_Ryanair_Touchdown.png", y0=290, title="WELCOME", sub2="START HERE", ty=34, size=216),
    "rules":    dict(shot=V + "08_KLM_Runway.png", y0=250, title="RULES", sub2="READ BEFORE YOU VERIFY", ty=40, size=250),
    "support":  dict(shot=V + "07_Southwest_Touchdown.png", y0=270, title="SUPPORT", sub2="WE'RE HERE TO HELP", align="left", ty=40, size=210),
    "help":     dict(shot=V + "03_United_Climb.png", y0=150, title="HELP", sub2="EVERYTHING IN ONE PLACE", align="left", ty=60, size=250, sunx=0.02),
    "stats":    dict(shot=V + "09_Delta_Touchdown.png", y0=280, title="STATS", sub2="LIVE NUMBERS AND DAILY REPORTS", ty=18, size=230),
    "liveries": dict(shot=V + "04_American_Climb.png", y0=120, title="LIVERIES", sub2="TEMPLATES AND SHOWCASE", align="left", ty=50, size=210, sunx=0.02),
    "contest":  dict(shot=V + "02_Turkish_Airlines_Aerial.png", y0=150, title="CONTEST", sub2="LIVERY OF THE MONTH", align="left", ty=60, size=210, sunx=0.02),
    "inbox":    dict(shot=V + "01_Delta_Aerial.png", y0=100, title="INBOX", sub2="EVERY OPEN TICKET", align="right", ty=150, size=230),
}
DEFAULTS = dict(align="center", track=14, sub="AIRPORT SIMULATOR", subsize=40, sunx=0.99, suny=-0.10,
                rays=0.55, g1=0.38, g2=0.10, t0=0.07, t1=0.16, shadow=0.42, margin=90)


def blur(a, r):
    return gaussian_filter(a, (r, r) + (0,) * (a.ndim - 2))


def lum(a):
    return a[..., 0] * 0.2126 + a[..., 1] * 0.7152 + a[..., 2] * 0.0722


def font(size, style):
    f = ImageFont.truetype(FONT, size)
    f.set_variation_by_name(style)
    return f


def tracked_width(d, text, f, track):
    return sum(d.textlength(c, font=f) for c in text) + track * (len(text) - 1)


def tracked(d, xy, text, f, track, fill):
    x, y = xy
    for c in text:
        d.text((x, y), c, font=f, fill=fill)
        x += d.textlength(c, font=f) + track


def jet_matte(img):
    """1 = jet (and land), 0 = open sky: distance from a smooth sky model built from sure-sky pixels"""
    r, g, b = img[..., 0], img[..., 1], img[..., 2]
    sure = (((b - r) > 0.28) & (b > 0.55) & (np.abs(g - (r + b) / 2) < 0.12)).astype(np.float32)
    num, den = blur(img * sure[..., None], 40), blur(sure, 40)[..., None]
    sky = num / np.maximum(den, 1e-4)
    for rr in (120, 300):                                   # far from any sky: wider estimates
        n2, d2 = blur(img * sure[..., None], rr), blur(sure, rr)[..., None]
        sky = np.where(den < 0.05, n2 / np.maximum(d2, 1e-4), sky)
        den = np.maximum(den, d2)
    dist = np.sqrt(((img - sky) ** 2).sum(-1))
    return dist, sky


def make(name, cfg):
    K = {**DEFAULTS, **cfg}
    im = Image.open(SRC / K["shot"]).convert("RGB")
    im = im.crop((0, K["y0"], im.width, K["y0"] + im.width // 3))
    img = np.asarray(im).astype(np.float32) / 255
    H, W = img.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W] / np.array([H, W]).reshape(2, 1, 1)

    # 1 cut the jet out of the sky (livery titles/windows inside the jet stay solid)
    dist, sky = jet_matte(img)
    matte = np.clip((dist - K["t0"]) / (K["t1"] - K["t0"]), 0, 1)
    solid = binary_erosion(binary_fill_holes(matte > 0.5), iterations=2)
    matte = blur(np.maximum(matte, solid.astype(np.float32)), 0.7)

    # 2 the title, behind the jet
    tf = font(K["size"], "Bold")
    layer = Image.new("L", (W, H), 0)
    d = ImageDraw.Draw(layer)
    tw = tracked_width(d, K["title"], tf, K["track"])
    x = {"center": (W - tw) / 2, "left": K["margin"], "right": W - K["margin"] - tw}[K["align"]]
    tracked(d, (x, K["ty"]), K["title"], tf, K["track"], 255)
    txt = np.asarray(layer).astype(np.float32) / 255
    rows = np.nonzero(txt.max(1) > 0.5)[0]
    tgrad = np.clip((np.arange(H) - rows.min()) / max(rows.max() - rows.min(), 1), 0, 1)[:, None, None]
    tcol = np.array([1.0, 1.0, 1.0]) * (1 - tgrad) + np.array([0.93, 0.96, 1.0]) * tgrad
    tcol = tcol * 0.97 + sky * 0.03                                         # a breath of air
    base = img * (1 - blur(np.roll(txt, (6, 3), (0, 1)), 9)[..., None] * 0.28)   # soft drop shadow
    jet_sh = blur(np.roll(matte, (10, 6), (0, 1)), 12) * (1 - matte)        # the jet's shadow on the letters
    tcol = tcol * (1 - jet_sh[..., None] * K["shadow"])
    comp = base * (1 - txt[..., None]) + tcol * txt[..., None]
    comp = comp * (1 - matte[..., None]) + img * matte[..., None]

    # 3 post
    top = np.clip(1 - yy / 0.55, 0, 1) ** 1.6                               # deeper sky up top holds the white title
    comp = comp * (1 - (top * 0.16)[..., None] * np.array([1.0, 0.8, 0.45]))
    sx, sy = K["sunx"], K["suny"]                                           # sun just off frame: rays past the jet
    occ = np.clip(1 - matte - txt * 0.5, 0, 1) * np.clip((lum(img) - 0.35) / 0.4, 0, 1)
    cy_, cx_ = sy * H, sx * W
    Y, X = np.mgrid[0:H, 0:W].astype(np.float32)
    rays = np.zeros((H, W), np.float32)
    for k in range(28):
        s_ = 1 - k * 0.022
        rays += map_coordinates(occ, [cy_ + (Y - cy_) * s_, cx_ + (X - cx_) * s_], order=1, mode="nearest")
    rays = blur(rays / 28, 2) * K["rays"]
    dd = np.sqrt(((xx - sx) * 3) ** 2 + (yy - sy) ** 2)
    glow = (np.exp(-(dd / 0.30) ** 2) * K["g1"] + np.exp(-(dd / 1.0) ** 2) * K["g2"])[..., None] * np.array([1.0, 0.95, 0.85])
    streak = (np.exp(-((yy - sy - 0.06) / 0.018) ** 2) * np.exp(-((xx - sx) / 0.55) ** 2) * 0.10)[..., None] * np.array([0.85, 0.93, 1.0])
    light = glow + streak + rays[..., None] * np.array([1.0, 0.95, 0.86])
    comp = 1 - (1 - comp) * (1 - np.clip(light * (1 - matte[..., None] * 0.55), 0, 1))
    wz = np.clip(1 - np.abs(xx - sx) / 0.55, 0, 1)[..., None]               # warm near the sun, cool away from it
    comp = comp * (1 + wz * (np.array([1.03, 1.0, 0.95]) - 1) + (1 - wz) * (np.array([0.98, 0.995, 1.03]) - 1) * 0.6)
    hz = np.exp(-((yy - 0.97) / 0.10) ** 2)[..., None] * 0.18              # horizon haze
    comp = comp * (1 - hz) + np.array([0.80, 0.86, 0.92]) * hz
    comp = comp + (comp - blur(comp, 24)) * 0.35 * matte[..., None]         # local contrast on the jet only
    L = lum(comp)
    hot = np.clip((L - 0.82) / 0.18, 0, 1)[..., None] * comp                # bloom on the hottest highlights
    comp = 1 - (1 - comp) * (1 - np.clip((blur(hot, 4) * 0.5 + blur(hot, 16) * 0.4 + blur(hot, 50) * 0.3) * 0.6, 0, 1))
    x_ = np.clip(comp, 0, 1)                                                # grade: soft S, saturation, split tone
    comp = x_ + 0.18 * (3 * x_ ** 2 - 2 * x_ ** 3 - x_)
    L = lum(comp)[..., None]
    comp = L + (comp - L) * 1.12
    lo, hi = np.clip((0.4 - L) / 0.4, 0, 1), np.clip((L - 0.6) / 0.4, 0, 1)
    comp = comp * (1 + lo * (np.array([0.96, 0.99, 1.05]) - 1) + hi * (np.array([1.02, 1.0, 0.97]) - 1))
    bot = np.clip((yy - 0.70) / 0.30, 0, 1) ** 1.3                          # bottom shade for the captions
    comp = comp * (1 - (bot * 0.62)[..., None])
    v = 1 - 0.30 * np.clip(((xx - 0.5) ** 2 * 0.8 + (yy - 0.45) ** 2 * 1.6) * 2.2, 0, 1) ** 1.4
    comp = comp * v[..., None]
    ca = np.clip((np.abs(xx - 0.5) - 0.3) / 0.2, 0, 1)                      # faint lens fringe at the edges
    comp[..., 0] = comp[..., 0] * (1 - ca * 0.5) + np.roll(comp[..., 0], 2, 1) * ca * 0.5
    comp[..., 2] = comp[..., 2] * (1 - ca * 0.5) + np.roll(comp[..., 2], -2, 1) * ca * 0.5
    comp = comp + np.random.default_rng(5).normal(0, 0.010, comp.shape[:2])[..., None]
    out = Image.fromarray((np.clip(comp, 0, 1) * 255).astype(np.uint8))

    # 4 captions on top, crisp
    d = ImageDraw.Draw(out)
    cf = font(K["subsize"], "SemiBold")
    cy = H - K["subsize"] - 26
    tracked(d, (56, cy), K["sub"], cf, 9, (240, 244, 250))
    tracked(d, (W - 56 - tracked_width(d, K["sub2"], cf, 9), cy), K["sub2"], cf, 9, (240, 244, 250))
    out = out.resize((OUT_W, OUT_W // 3), Image.LANCZOS).filter(ImageFilter.UnsharpMask(1.0, 60, 2))
    OUT.mkdir(parents=True, exist_ok=True)
    out.save(OUT / f"{name}.png", optimize=True)
    return OUT / f"{name}.png"


def server_art(shot=HERO + "h05_ryanair_aerial.png"):
    """Server banner (960x540, needs boost level 2) + invite splash (1920x1080, level 1): the hero shot, no text
    (Discord prints the server name over both). Uploaded by hand in Server Settings."""
    im = Image.open(SRC / shot).convert("RGB")
    a = np.asarray(im).astype(np.float32) / 255
    H, W = a.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W] / np.array([H, W]).reshape(2, 1, 1)
    a = a * (1 - 0.35 * np.clip(1 - yy / 0.35, 0, 1)[..., None] ** 2)    # darker top: the server name sits there
    a = a * (1 - 0.25 * np.clip(((xx - 0.5) ** 2 + (yy - 0.5) ** 2) * 2.2, 0, 1) ** 1.4)[..., None]
    out = Image.fromarray((np.clip(a, 0, 1) * 255).astype(np.uint8))
    d = OUT.parent / "server"
    d.mkdir(parents=True, exist_ok=True)
    out.resize((960, 540), Image.LANCZOS).save(d / "server_banner.png", optimize=True)
    out.save(d / "invite_splash.png", optimize=True)
    return d


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1].startswith("--out="):
        OUT = Path(sys.argv.pop(1)[6:])
    names = sys.argv[1:] or [*BANNERS, "server"]
    for n in names:
        print("made", server_art() if n == "server" else make(n, BANNERS[n]))
