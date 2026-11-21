"""Render the illustrated sample room images shipped in data/sample_photos/.

    python tools/render_sample_photos.py

Pure Pillow + NumPy: perspective rooms with lighting, furniture and texture. They are
ILLUSTRATIONS for the demo listings (labelled as such) – real listings show the
landlord's uploaded photos.
"""
from __future__ import annotations

import random
import sys
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

OUT = Path(__file__).resolve().parent.parent / "data" / "sample_photos"
W, H = 1200, 800
VP = (600, 350)                        # vanishing point
BACK = (330, 170, 870, 520)            # back wall rectangle


def shade(c, f):
    return tuple(max(0, min(255, int(v * f))) for v in c)


def mix(a, b, t):
    return tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))


def persp_y(y_back, t):
    """Point on the floor line between the back wall bottom (t=0) and the image bottom (t=1)."""
    return y_back + (H - y_back) * t


def gradient(img, box, top, bottom):
    x0, y0, x1, y1 = box
    arr = np.array(img).astype(np.float32)
    h = y1 - y0
    for i in range(h):
        c = np.array(mix(top, bottom, i / max(h - 1, 1)), dtype=np.float32)
        arr[y0 + i, x0:x1] = c
    return Image.fromarray(arr.astype(np.uint8))


def light(img, cx, cy, radius, strength):
    arr = np.array(img).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius
    glow = np.clip(1 - d, 0, 1) ** 2 * strength
    arr = arr * (1 + glow[..., None] * 0.35) + glow[..., None] * 40
    return Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8))


def finish(img, rng):
    arr = np.array(img).astype(np.float32)
    yy, xx = np.mgrid[0:H, 0:W]
    vig = 1 - 0.28 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)
    arr = arr * vig[..., None]
    arr += np.random.default_rng(rng.randint(0, 10**6)).normal(0, 3.2, arr.shape)
    img = Image.fromarray(np.clip(arr, 0, 255).astype(np.uint8)).filter(ImageFilter.GaussianBlur(0.7))
    d = ImageDraw.Draw(img)
    try:
        font = ImageFont.load_default(size=18)
    except TypeError:
        font = ImageFont.load_default()
    d.rounded_rectangle([W - 150, H - 42, W - 14, H - 12], 8, fill=(0, 0, 0))
    d.text((W - 138, H - 39), "Illustration", fill=(255, 255, 255), font=font)
    return img


# ------------------------------------------------------------------ room shell --
def room(rng, wall, floor_kind, floor_col, ceiling=(245, 243, 238)):
    img = Image.new("RGB", (W, H), wall)
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = BACK
    d.polygon([(0, 0), (W, 0), (x1, y0), (x0, y0)], fill=ceiling)                    # ceiling
    d.polygon([(0, 0), (x0, y0), (x0, y1), (0, H)], fill=shade(wall, 0.82))          # left wall
    d.polygon([(W, 0), (x1, y0), (x1, y1), (W, H)], fill=shade(wall, 0.93))          # right wall
    d.rectangle(BACK, fill=wall)
    # floor
    d.polygon([(0, H), (x0, y1), (x1, y1), (W, H)], fill=floor_col)
    if floor_kind == "wood":
        n = 14
        for i in range(n):
            tb0, tb1 = x0 + (x1 - x0) * i / n, x0 + (x1 - x0) * (i + 1) / n
            bb0, bb1 = -300 + (W + 600) * i / n, -300 + (W + 600) * (i + 1) / n
            d.polygon([(bb0, H), (tb0, y1), (tb1, y1), (bb1, H)],
                      fill=shade(floor_col, rng.uniform(0.88, 1.08)))
        for i in range(n + 1):
            d.line([(-300 + (W + 600) * i / n, H), (x0 + (x1 - x0) * i / n, y1)], fill=shade(floor_col, 0.7), width=2)
    elif floor_kind == "tile":
        n = 10
        for i in range(n + 1):
            d.line([(-300 + (W + 600) * i / n, H), (x0 + (x1 - x0) * i / n, y1)], fill=shade(floor_col, 0.75), width=2)
        for k in range(1, 8):
            t = (k / 8) ** 1.8
            y = persp_y(y1, t)
            xl = x0 + (0 - x0) * t
            xr = x1 + (W - x1) * t
            d.line([(xl, y), (xr, y)], fill=shade(floor_col, 0.75), width=2)
    # skirting + corners
    d.line([(x0, y1), (x1, y1)], fill=shade(wall, 0.6), width=4)
    d.line([(x0, y0), (x0, y1)], fill=shade(wall, 0.7), width=2)
    d.line([(x1, y0), (x1, y1)], fill=shade(wall, 0.75), width=2)
    return img


def window(img, box, rng, curtains=None):
    d = ImageDraw.Draw(img)
    x0, y0, x1, y1 = box
    img = gradient(img, box, (120, 175, 230), (215, 232, 245))
    d = ImageDraw.Draw(img)
    for _ in range(3):                                                               # clouds
        cx, cy = rng.randint(x0 + 20, x1 - 20), rng.randint(y0 + 15, y0 + (y1 - y0) // 2)
        for j in range(4):
            d.ellipse([cx - 30 + j * 14, cy - 10, cx + j * 14, cy + 12], fill=(250, 250, 252))
    d.rectangle([x0, y1 - (y1 - y0) // 4, x1, y1], fill=(110, 150, 100))             # trees outside
    d.rectangle(box, outline=(250, 250, 250), width=10)
    d.line([((x0 + x1) // 2, y0), ((x0 + x1) // 2, y1)], fill=(250, 250, 250), width=8)
    d.line([(x0, (y0 + y1) // 2), (x1, (y0 + y1) // 2)], fill=(250, 250, 250), width=6)
    d.rectangle([x0 - 12, y1, x1 + 12, y1 + 10], fill=(240, 240, 240))               # sill
    if curtains:
        d.polygon([(x0 - 40, y0 - 20), (x0 + 10, y0 - 20), (x0 - 5, y1 + 40), (x0 - 55, y1 + 40)], fill=curtains)
        d.polygon([(x1 - 10, y0 - 20), (x1 + 40, y0 - 20), (x1 + 55, y1 + 40), (x1 + 5, y1 + 40)], fill=curtains)
        d.rectangle([x0 - 60, y0 - 28, x1 + 60, y0 - 20], fill=(80, 80, 80))
    return light(img, (x0 + x1) // 2, y1 + 120, 650, 0.9)


def box3d(d, x0, y0, x1, y1, depth, col, top=None):
    """Front face + top face of a box, receding toward the vanishing point."""
    def recede(x, y, k):
        return x + (VP[0] - x) * k, y + (VP[1] - y) * k
    k = depth
    a, b = recede(x0, y0, k), recede(x1, y0, k)
    d.polygon([(x0, y0), (x1, y0), b, a], fill=top or shade(col, 1.15))
    side_x = x1 if x1 < VP[0] else x0
    if x1 < VP[0]:
        c, e = recede(x1, y0, k), recede(x1, y1, k)
        d.polygon([(x1, y0), c, e, (x1, y1)], fill=shade(col, 0.8))
    elif x0 > VP[0]:
        c, e = recede(x0, y0, k), recede(x0, y1, k)
        d.polygon([(x0, y0), c, e, (x0, y1)], fill=shade(col, 0.8))
    d.rectangle([x0, y0, x1, y1], fill=col)


def shadow(img, box, strength=0.35):
    sh = Image.new("L", (W, H), 0)
    ImageDraw.Draw(sh).ellipse(box, fill=int(255 * strength))
    sh = sh.filter(ImageFilter.GaussianBlur(18))
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(dark, img, sh)


def plant(d, x, y, rng, s=1.0):
    d.polygon([(x - 28 * s, y - 60 * s), (x + 28 * s, y - 60 * s), (x + 20 * s, y), (x - 20 * s, y)], fill=(170, 110, 80))
    for _ in range(14):
        a = rng.uniform(-1.4, 1.4)
        lx, ly = x + np.sin(a) * rng.uniform(30, 75) * s, y - 60 * s - np.cos(a) * rng.uniform(40, 120) * s
        d.ellipse([lx - 18 * s, ly - 9 * s, lx + 18 * s, ly + 9 * s], fill=(rng.randint(40, 70), rng.randint(110, 150), rng.randint(50, 80)))


def frame(d, box, rng):
    d.rectangle(box, fill=(60, 50, 45))
    x0, y0, x1, y1 = box
    inner = (x0 + 8, y0 + 8, x1 - 8, y1 - 8)
    d.rectangle(inner, fill=(235, 230, 220))
    for _ in range(3):
        cx, cy = rng.randint(inner[0] + 10, inner[2] - 10), rng.randint(inner[1] + 10, inner[3] - 10)
        r = rng.randint(8, 20)
        d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=(rng.randint(150, 230), rng.randint(90, 180), rng.randint(70, 160)))


def lamp_ceiling(d, x):
    d.line([(x, 0), (x, 70)], fill=(60, 60, 60), width=3)
    d.polygon([(x - 45, 110), (x + 45, 110), (x + 22, 70), (x - 22, 70)], fill=(245, 225, 170))


def rug(d, col, y=640):
    d.polygon([(330, y - 40), (870, y - 40), (980, y + 90), (220, y + 90)], fill=col)
    d.polygon([(350, y - 30), (850, y - 30), (950, y + 78), (250, y + 78)], outline=shade(col, 1.25), width=4)


# ------------------------------------------------------------------- scenes --
PALETTES = [
    {"wall": (236, 230, 218), "floor": (178, 132, 92), "accent": (72, 98, 128), "fabric": (205, 112, 84)},
    {"wall": (220, 229, 226), "floor": (196, 160, 118), "accent": (156, 84, 72), "fabric": (96, 128, 110)},
    {"wall": (240, 236, 228), "floor": (150, 110, 80), "accent": (64, 72, 84), "fabric": (220, 180, 90)},
    {"wall": (228, 222, 236), "floor": (186, 150, 112), "accent": (110, 90, 140), "fabric": (120, 160, 190)},
]


def living(rng, p):
    img = room(rng, p["wall"], "wood", p["floor"])
    img = window(img, (640, 215, 830, 400), rng, curtains=shade(p["fabric"], 1.1))
    img = shadow(img, (260, 600, 880, 720))
    d = ImageDraw.Draw(img)
    rug(d, shade(p["fabric"], 0.9))
    frame(d, (390, 230, 560, 340), rng)
    lamp_ceiling(d, 600)
    # sofa
    c = p["accent"]
    box3d(d, 290, 470, 800, 600, 0.10, shade(c, 0.9))                 # back
    box3d(d, 270, 540, 820, 640, 0.08, c)                              # seat
    box3d(d, 250, 500, 310, 645, 0.06, shade(c, 0.85))                 # arms
    box3d(d, 780, 500, 840, 645, 0.06, shade(c, 0.85))
    for x in (340, 470, 600, 700):
        d.rounded_rectangle([x, 505, x + 90, 565], 14, fill=shade(p["fabric"], rng.uniform(0.95, 1.2)))
    # coffee table
    box3d(d, 420, 660, 780, 690, 0.05, (120, 85, 60))
    for x in (440, 750):
        d.rectangle([x, 690, x + 12, 735], fill=(90, 65, 45))
    d.ellipse([520, 640, 580, 668], fill=(240, 240, 240))
    plant(d, 1000, 690, rng, 1.2)
    plant(d, 150, 640, rng, 0.9)
    # floor lamp
    d.line([(915, 640), (915, 380)], fill=(50, 50, 50), width=5)
    d.polygon([(870, 380), (960, 380), (940, 320), (890, 320)], fill=(245, 230, 190))
    return finish(light(img, 915, 360, 260, 0.5), rng)


def bedroom(rng, p):
    img = room(rng, p["wall"], "wood", p["floor"])
    img = window(img, (680, 210, 840, 380), rng, curtains=shade(p["fabric"], 1.05))
    img = shadow(img, (240, 610, 900, 760))
    d = ImageDraw.Draw(img)
    rug(d, shade(p["accent"], 1.3), 690)
    frame(d, (420, 220, 600, 330), rng)
    # headboard + bed
    box3d(d, 330, 400, 740, 560, 0.05, shade(p["floor"], 0.75))
    box3d(d, 300, 540, 770, 640, 0.10, (245, 245, 245))                  # mattress
    d.polygon([(290, 590), (780, 590), (830, 760), (240, 760)], fill=p["fabric"])   # duvet
    d.polygon([(290, 590), (780, 590), (790, 620), (280, 620)], fill=shade(p["fabric"], 1.2))
    for x in (360, 560):
        d.rounded_rectangle([x, 500, x + 160, 570], 22, fill=(250, 250, 248))
    # nightstands + lamps
    for x in (200, 800):
        box3d(d, x, 560, x + 110, 650, 0.05, (150, 110, 80))
        d.rectangle([x + 45, 515, x + 60, 560], fill=(80, 80, 80))
        d.polygon([(x + 20, 520), (x + 90, 520), (x + 75, 480), (x + 35, 480)], fill=(250, 235, 200))
    img = light(img, 255, 500, 180, 0.6)
    img = light(img, 855, 500, 180, 0.6)
    d = ImageDraw.Draw(img)
    # wardrobe on the left wall
    d.polygon([(40, 150), (210, 230), (210, 600), (40, 700)], fill=(200, 190, 175))
    d.line([(125, 190), (125, 650)], fill=(150, 140, 130), width=3)
    plant(d, 1080, 700, rng, 1.0)
    return finish(img, rng)


def kitchen(rng, p):
    img = room(rng, p["wall"], "tile", (215, 210, 200))
    d = ImageDraw.Draw(img)
    for y in range(330, 470, 22):                                         # backsplash tiles
        d.line([(330, y), (870, y)], fill=(255, 255, 255), width=2)
    for x in range(330, 870, 44):
        d.line([(x, 330), (x, 470)], fill=(255, 255, 255), width=2)
    img = window(img, (620, 200, 790, 320), rng)
    d = ImageDraw.Draw(img)
    cab = p["accent"]
    for x in range(340, 560, 82):                                         # upper cabinets
        box3d(d, x, 180, x + 76, 300, 0.03, shade(cab, 1.15))
        d.rectangle([x + 60, 270, x + 64, 290], fill=(220, 220, 220))
    box3d(d, 320, 470, 880, 600, 0.08, cab)                               # lower cabinets
    box3d(d, 310, 455, 890, 475, 0.08, (235, 235, 230))                   # counter
    for x in range(340, 860, 90):
        d.rectangle([x, 485, x + 80, 590], outline=shade(cab, 0.8), width=3)
        d.rectangle([x + 35, 495, x + 45, 500], fill=(220, 220, 220))
    d.rectangle([640, 440, 760, 458], fill=(60, 60, 60))                  # hob
    for x in (665, 735):
        d.ellipse([x - 18, 443, x + 18, 455], outline=(150, 150, 150), width=2)
    d.rectangle([450, 445, 540, 458], fill=(200, 205, 210))               # sink
    d.line([(495, 445), (495, 410), (515, 410)], fill=(190, 190, 195), width=5)
    # fridge on the right
    d.polygon([(900, 130), (1060, 60), (1060, 760), (900, 600)], fill=(232, 234, 236))
    d.line([(900, 330), (1060, 300)], fill=(200, 200, 205), width=3)
    # table + chairs
    img = shadow(img, (380, 690, 820, 780))
    d = ImageDraw.Draw(img)
    box3d(d, 400, 650, 800, 675, 0.04, (170, 125, 85))
    for x in (420, 770):
        d.rectangle([x, 675, x + 12, 770], fill=(130, 95, 65))
    for x in (330, 820):
        d.rectangle([x, 620, x + 60, 700], fill=p["fabric"])
        d.rectangle([x + 5, 700, x + 12, 770], fill=(80, 80, 80))
        d.rectangle([x + 48, 700, x + 55, 770], fill=(80, 80, 80))
    d.ellipse([560, 625, 640, 650], fill=(240, 240, 240))
    for i in range(5):
        d.ellipse([575 + i * 10, 612, 590 + i * 10, 628], fill=(230, 120 + i * 20, 40))
    lamp_ceiling(d, 600)
    return finish(light(img, 600, 120, 400, 0.5), rng)


def bathroom(rng, p):
    tile = shade(p["wall"], 1.02)
    img = room(rng, tile, "tile", (200, 205, 210), ceiling=(250, 250, 250))
    d = ImageDraw.Draw(img)
    for y in range(180, 520, 34):
        d.line([(330, y), (870, y)], fill=(205, 210, 215), width=2)
    for x in range(330, 870, 34):
        d.line([(x, 170), (x, 520)], fill=(205, 210, 215), width=2)
    # bathtub on the left
    box3d(d, 230, 520, 560, 640, 0.10, (250, 250, 250))
    d.ellipse([270, 515, 520, 545], fill=(220, 235, 245))
    # glass + shower head
    d.line([(560, 250), (560, 640)], fill=(180, 200, 210), width=4)
    d.line([(420, 210), (420, 250), (460, 250)], fill=(170, 170, 175), width=6)
    # sink + mirror
    d.rectangle([660, 230, 820, 380], fill=(200, 220, 230), outline=(240, 240, 240), width=6)
    box3d(d, 640, 430, 840, 470, 0.05, (250, 250, 250))
    box3d(d, 655, 470, 825, 560, 0.05, p["accent"])
    d.line([(740, 430), (740, 405), (760, 405)], fill=(190, 190, 195), width=5)
    # toilet
    box3d(d, 880, 540, 960, 620, 0.05, (250, 250, 250))
    d.ellipse([860, 600, 980, 650], fill=(245, 245, 245))
    # towel
    d.rectangle([900, 300, 980, 420], fill=p["fabric"])
    d.rectangle([890, 296, 990, 304], fill=(170, 170, 175))
    plant(d, 620, 430, rng, 0.45)
    return finish(light(img, 600, 100, 500, 0.6), rng)


def exterior(rng, p):
    img = Image.new("RGB", (W, H))
    img = gradient(img, (0, 0, W, 520), (110, 165, 225), (205, 226, 242))
    d = ImageDraw.Draw(img)
    for _ in range(5):
        cx, cy = rng.randint(50, W - 50), rng.randint(40, 200)
        for j in range(5):
            d.ellipse([cx - 50 + j * 22, cy - 18, cx + j * 22, cy + 18], fill=(250, 251, 253))
    d.rectangle([0, 520, W, H], fill=(120, 150, 95))                       # grass
    d.polygon([(0, 640), (W, 600), (W, 680), (0, 730)], fill=(190, 185, 175))   # path
    facade = mix(p["wall"], (230, 200, 160), 0.5)
    bx0, bx1 = 220, 980
    box3d(d, bx0, 150, bx1, 560, 0.06, facade, top=(150, 140, 130))
    rows, cols = 6, 9
    for r in range(rows):
        for c in range(cols):
            x = bx0 + 30 + c * (bx1 - bx0 - 60) / cols
            y = 175 + r * 62
            d.rectangle([x, y, x + 52, y + 40], fill=(150, 185, 215), outline=(245, 245, 245), width=4)
            if c % 3 == 1 and r > 0:
                d.rectangle([x - 12, y + 40, x + 64, y + 52], fill=shade(p["accent"], 1.2))   # balcony
    d.rectangle([570, 470, 630, 560], fill=(90, 70, 55))                    # door
    for x in (90, 1100, 160):
        d.rectangle([x - 8, 480, x + 8, 600], fill=(100, 75, 55))
        for k in range(18):
            lx, ly = x + rng.randint(-70, 70), 430 + rng.randint(-80, 60)
            d.ellipse([lx - 34, ly - 26, lx + 34, ly + 26], fill=(rng.randint(50, 80), rng.randint(110, 150), rng.randint(55, 85)))
    return finish(img, rng)


SCENES = {"Living room": living, "Bedroom": bedroom, "Kitchen": kitchen, "Bathroom": bathroom, "Exterior": exterior}


def main(variants: int = 4) -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    for name, fn in SCENES.items():
        for v in range(variants):
            rng = random.Random(f"{name}-{v}")
            img = fn(rng, PALETTES[v % len(PALETTES)])
            path = OUT / f"{name.lower().replace(' ', '_')}_{v}.jpg"
            img.save(path, "JPEG", quality=84, optimize=True, progressive=True)
            print("wrote", path.name)


if __name__ == "__main__":
    main(int(sys.argv[1]) if len(sys.argv) > 1 else 4)
