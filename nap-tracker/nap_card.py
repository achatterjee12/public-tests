#!/usr/bin/env python3
"""
nap_card.py — turn a nap into a pretty pastel poster.

Usage:
    python nap_card.py 12:45 2:10
    python nap_card.py 12:45pm 2:10pm --name Leo --date 2026-11-24 -o today.jpg

Times without am/pm are read as afternoon times (12:xx stays 12, 1-11 become PM).
The clock arc spans 12 PM to 5 PM and stretches automatically if a nap falls outside it.
Needs Pillow and numpy:  pip install pillow numpy
"""
import argparse
import math
import random
import re
from datetime import date, datetime
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageFont

# ---------------------------------------------------------------- config ---
W, H = 1080, 1350          # final size (Instagram portrait)
SS = 2                     # supersampling factor for crisp anti-aliasing
FONT_DIR = Path(__file__).parent / "fonts"

INK = (74, 63, 107)        # deep lavender-plum text
INK_SOFT = (138, 126, 168)
TRACK = (255, 255, 255)
# gradient along the sleep segment: peach -> pink -> lilac -> sky
SLEEP_STOPS = [(255, 176, 140), (255, 138, 178), (180, 140, 245), (118, 182, 245)]
ICON_MOON = (240, 128, 150)
ICON_SUN = (98, 156, 230)
BG_TOP = (255, 244, 236)
BG_BOTTOM = (236, 230, 255)
BLOBS = [  # (x, y, radius, colour, alpha) in final-size units
    (140, 220, 380, (255, 205, 215), 170),
    (960, 360, 420, (200, 225, 255), 170),
    (880, 1180, 420, (200, 242, 220), 200),
    (120, 1150, 360, (228, 210, 255), 190),
    (540, 680, 300, (255, 255, 255), 120),
]


def s(v):
    """scale a final-size measurement to the supersampled canvas"""
    return int(round(v * SS))


def font(weight, size):
    path = FONT_DIR / f"Fredoka-{weight}.woff"
    try:
        return ImageFont.truetype(str(path), s(size))
    except OSError:
        for fallback in ("Inter-SemiBold.ttf", "DejaVuSans-Bold.ttf", "DejaVuSans.ttf"):
            try:
                return ImageFont.truetype(fallback, s(size))
            except OSError:
                continue
        return ImageFont.load_default()


# ------------------------------------------------------------ time utils ---
def parse_time(text):
    m = re.fullmatch(r"\s*(\d{1,2})(?::(\d{2}))?\s*([ap]\.?m\.?)?\s*", text.lower())
    if not m:
        raise argparse.ArgumentTypeError(f"can't read time '{text}' (try 12:45 or 2:10pm)")
    h, mnt, ampm = int(m.group(1)), int(m.group(2) or 0), m.group(3)
    if ampm:
        h = h % 12 + (12 if ampm.startswith("p") else 0)
    elif 1 <= h <= 11:
        h += 12  # naps happen in the afternoon
    if not (0 <= h <= 23 and 0 <= mnt <= 59):
        raise argparse.ArgumentTypeError(f"invalid time '{text}'")
    return h * 60 + mnt  # minutes since midnight


def fmt_clock(mins):
    h, m = divmod(mins, 60)
    return f"{(h % 12) or 12}:{m:02d}", "PM" if h >= 12 else "AM"


def fmt_hour(h):
    return f"{(h % 12) or 12}"


def nap_message(total):
    if total < 45:
        return "A sweet little power nap"
    if total < 90:
        return "A cozy little recharge"
    if total < 150:
        return "A dreamy, deep snooze"
    return "Champion napper!"


# ------------------------------------------------------------ drawing ------
def lerp(a, b, t):
    return a + (b - a) * t


def gradient_lut(stops, n=256):
    stops = np.array(stops, dtype=float)
    xs = np.linspace(0, 1, len(stops))
    t = np.linspace(0, 1, n)
    return np.stack([np.interp(t, xs, stops[:, c]) for c in range(3)], axis=1)


def background():
    w, h = s(W), s(H)
    t = np.linspace(0, 1, h)[:, None, None]
    top, bot = np.array(BG_TOP, float), np.array(BG_BOTTOM, float)
    arr = np.broadcast_to(top + (bot - top) * t, (h, w, 3)).astype(np.uint8)
    img = Image.fromarray(arr).convert("RGBA")

    for x, y, r, col, a in BLOBS:
        mask = Image.new("L", img.size, 0)
        ImageDraw.Draw(mask).ellipse([s(x - r), s(y - r), s(x + r), s(y + r)], fill=a)
        layer = Image.new("RGBA", img.size, col + (0,))
        layer.putalpha(mask.filter(ImageFilter.GaussianBlur(s(150))))
        img = Image.alpha_composite(img, layer)
    return img


def add_grain(img, amount=6):
    arr = np.asarray(img.convert("RGB")).astype(np.int16)
    rng = np.random.default_rng(7)
    noise = rng.normal(0, amount, arr.shape[:2])[..., None]
    return Image.fromarray(np.clip(arr + noise, 0, 255).astype(np.uint8))


def sparkle(d, cx, cy, r, col):
    """four-point twinkle star"""
    k = r * 0.28
    pts = [(cx, cy - r), (cx + k, cy - k), (cx + r, cy), (cx + k, cy + k),
           (cx, cy + r), (cx - k, cy + k), (cx - r, cy), (cx - k, cy - k)]
    d.polygon(pts, fill=col)


def cloud(layer, cx, cy, scale, alpha=235):
    d = ImageDraw.Draw(layer)
    col = (255, 255, 255, alpha)
    for dx, dy, r in [(-60, 10, 42), (-15, -18, 58), (45, 0, 48), (85, 18, 32)]:
        x, y, rr = s(cx + dx * scale), s(cy + dy * scale), s(r * scale)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=col)
    d.rounded_rectangle([s(cx - 100 * scale), s(cy + 5 * scale),
                         s(cx + 115 * scale), s(cy + 50 * scale)],
                        radius=s(22 * scale), fill=col)


def soft_shadow(size, box, radius, blur, color, offset=(0, 10)):
    layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(layer).rounded_rectangle(
        [box[0] + s(offset[0]), box[1] + s(offset[1]), box[2] + s(offset[0]), box[3] + s(offset[1])],
        radius=radius, fill=color)
    return layer.filter(ImageFilter.GaussianBlur(blur))


def arc_mask(size, c, r, width, a0, a1):
    """anti-aliased arc band with round caps (angles: PIL style, clockwise from 3 o'clock)"""
    m = Image.new("L", size, 0)
    d = ImageDraw.Draw(m)
    cx, cy = c
    d.arc([cx - r, cy - r, cx + r, cy + r], a0, a1, fill=255, width=width)
    # PIL's arc width grows inward, so centre-line radius is r - width/2
    rc = r - width / 2
    for a in (a0, a1):
        x, y = cx + rc * math.cos(math.radians(a)), cy + rc * math.sin(math.radians(a))
        d.ellipse([x - width / 2, y - width / 2, x + width / 2, y + width / 2], fill=255)
    return m


def conic_fill(size, c, a0, a1, stops):
    w, h = size
    yy, xx = np.mgrid[0:h, 0:w]
    ang = np.degrees(np.arctan2(yy - c[1], xx - c[0])) % 360
    span = (a1 - a0) % 360 or 360
    t = np.clip(((ang - a0) % 360) / span, 0, 1)
    # pixels just before a0 (round cap) wrap to ~1; pin them to 0
    t = np.where(((ang - a0) % 360) > span + (360 - span) / 2, 0, t)
    lut = gradient_lut(stops)
    rgb = lut[(t * 255).astype(int)].astype(np.uint8)
    return Image.fromarray(rgb).convert("RGBA")


def draw_moon(d, cx, cy, r, col, bg):
    d.ellipse([cx - r, cy - r, cx + r, cy + r], fill=col)
    o = r * 0.55
    d.ellipse([cx - r + o, cy - r - o * 0.35, cx + r + o, cy + r - o * 0.35], fill=bg)


def draw_sun(d, cx, cy, r, col):
    core = r * 0.48
    d.ellipse([cx - core, cy - core, cx + core, cy + core], fill=col)
    for i in range(8):
        a = math.radians(i * 45)
        r1, r2 = r * 0.7, r * 0.98
        d.line([cx + r1 * math.cos(a), cy + r1 * math.sin(a),
                cx + r2 * math.cos(a), cy + r2 * math.sin(a)], fill=col, width=max(2, int(r * 0.16)))


def text_center(d, xy, txt, fnt, fill, anchor="mm"):
    d.text(xy, txt, font=fnt, fill=fill, anchor=anchor)


def spaced(d, xy, txt, fnt, fill, tracking, anchor="m"):
    """letter-spaced text, centred on xy"""
    widths = [d.textlength(ch, font=fnt) for ch in txt]
    total = sum(widths) + tracking * (len(txt) - 1)
    x = xy[0] - total / 2 if anchor == "m" else xy[0]
    for ch, w in zip(txt, widths):
        d.text((x, xy[1]), ch, font=fnt, fill=fill, anchor="lm")
        x += w + tracking


# --------------------------------------------------------------- poster ----
def render(start, end, out, name=None, day=None, win_start=12, win_end=17):
    if end <= start:
        raise SystemExit("Wake-up time must be after sleep time.")
    total = end - start
    # window in minutes; stretch if the nap spills over
    w0 = min(win_start * 60, (start // 60) * 60)
    w1 = max(win_end * 60, math.ceil(end / 60) * 60)

    img = background()
    size = img.size

    # ----- decorative sky: sparkles + clouds
    deco = Image.new("RGBA", size, (0, 0, 0, 0))
    dd = ImageDraw.Draw(deco)
    rnd = random.Random(3)
    for _ in range(60):
        x, y = rnd.uniform(60, W - 60), rnd.uniform(60, H - 60)
        if (230 < y < 1180 and 40 < x < 1040) or (y < 240 and 300 < x < 780) \
                or (1220 < y < 1280 and 230 < x < 850) or (250 < y < 360 and 80 < x < 260):
            continue  # keep text, clock and cards calm
        col = rnd.choice([(255, 214, 120), (255, 180, 200), (190, 170, 250), (255, 255, 255)])
        sparkle(dd, s(x), s(y), s(rnd.uniform(5, 13)), col + (rnd.randint(150, 230),))
    cloud(deco, 905, 205, 0.75, 210)
    cloud(deco, 165, 300, 0.5, 200)
    img = Image.alpha_composite(img, deco)

    # ----- header
    d = ImageDraw.Draw(img)
    day = day or date.today()
    spaced(d, (s(W / 2), s(92)), "NAP  REPORT", font(600, 22), INK_SOFT, s(7))
    title = f"{name}'s afternoon nap" if name else "Afternoon nap"
    text_center(d, (s(W / 2), s(150)), title, font(600, 50), INK)
    text_center(d, (s(W / 2), s(205)), day.strftime("%A, %d %B %Y").replace(" 0", " "),
                font(400, 26), INK_SOFT)

    # ----- clock arch geometry
    C = (s(W / 2), s(690))
    R = s(390)                # outer radius
    BAND = s(62)
    SWEEP = 220               # degrees used by the window
    A_START = 270 - SWEEP / 2  # PIL angles: 270 = 12 o'clock (top)

    def ang(mins):
        return A_START + (mins - w0) / (w1 - w0) * SWEEP

    def polar(a, r):
        return C[0] + r * math.cos(math.radians(a)), C[1] + r * math.sin(math.radians(a))

    a_end = A_START + SWEEP
    a0, a1 = ang(start), ang(end)
    rc = R - BAND / 2

    # track (frosted)
    track_shadow = arc_mask(size, (C[0], C[1] + s(12)), R, BAND, A_START, a_end)
    sh = Image.new("RGBA", size, (150, 130, 200, 0))
    sh.putalpha(track_shadow.filter(ImageFilter.GaussianBlur(s(26))).point(lambda v: v * 0.35))
    img = Image.alpha_composite(img, sh)
    track = Image.new("RGBA", size, TRACK + (0,))
    track.putalpha(arc_mask(size, C, R, BAND, A_START, a_end).point(lambda v: v * 0.72))
    img = Image.alpha_composite(img, track)

    # sleep segment: glow + gradient
    seg_mask = arc_mask(size, C, R - s(8), BAND - s(16), a0, a1)
    fill = conic_fill(size, C, a0, a1, SLEEP_STOPS)
    glow = fill.copy()
    glow.putalpha(seg_mask.filter(ImageFilter.GaussianBlur(s(30))).point(lambda v: v * 1.0))
    img = Image.alpha_composite(img, glow)
    img = Image.alpha_composite(img, glow)
    seg = fill.copy()
    seg.putalpha(seg_mask)
    img = Image.alpha_composite(img, seg)

    d = ImageDraw.Draw(img)

    # quarter-hour dots inside, hour labels outside
    for mins in range(w0, w1 + 1, 15):
        a = ang(mins)
        on_hour = mins % 60 == 0
        x, y = polar(a, R - BAND - s(26))
        r = s(4.5) if on_hour else s(2.5)
        d.ellipse([x - r, y - r, x + r, y + r], fill=INK_SOFT + ((220,) if on_hour else (130,)))
        if on_hour:
            h = mins // 60
            lx, ly = polar(a, R + s(46))
            label = fmt_hour(h)
            text_center(d, (lx, ly), label, font(600, 32), INK)
            if mins in (w0, w1):
                text_center(d, (lx, ly + s(32)), "PM" if h >= 12 else "AM", font(500, 17), INK_SOFT)

    # end markers: moon (fell asleep) and sun (woke up)
    for a, kind, icon_col in ((a0, "moon", ICON_MOON), (a1, "sun", ICON_SUN)):
        x, y = polar(a, rc)
        rr = s(40)
        shadow = Image.new("RGBA", size, (0, 0, 0, 0))
        ImageDraw.Draw(shadow).ellipse([x - rr, y - rr + s(8), x + rr, y + rr + s(8)],
                                       fill=(120, 100, 180, 90))
        img = Image.alpha_composite(img, shadow.filter(ImageFilter.GaussianBlur(s(10))))
        d = ImageDraw.Draw(img)
        d.ellipse([x - rr, y - rr, x + rr, y + rr], fill=(255, 255, 255))
        if kind == "moon":
            draw_moon(d, x - s(2), y, s(18), icon_col, (255, 255, 255))
        else:
            draw_sun(d, x, y, s(25), icon_col)

    # ----- centre read-out
    hrs, mins = divmod(total, 60)
    cy = C[1] - s(60)
    big, unit = font(600, 160), font(500, 60)
    parts = []
    if hrs:
        parts += [(str(hrs), big), ("h", unit), ("  ", unit)]
    parts += [(f"{mins:02d}" if hrs else str(mins), big), ("m", unit)]
    widths = [d.textlength(t, font=f) for t, f in parts]
    x = C[0] - sum(widths) / 2
    for (t, f), w in zip(parts, widths):
        d.text((x, cy + s(48)), t, font=f, fill=INK, anchor="ls")
        x += w
    spaced(d, (C[0], cy + s(102)), "TOTAL  SLEEP", font(600, 20), INK_SOFT, s(6))

    # sleepy "z z z" above the number
    for i, (dx, dy, fs) in enumerate([(-40, -120, 30), (0, -165, 40), (50, -220, 54)]):
        text_center(d, (C[0] + s(dx), cy + s(dy)), "z", font(600, fs),
                    (180, 140, 245) if i % 2 else (255, 138, 178))

    # ----- glass cards
    cards_y = s(975)
    cw, ch, gap = s(445), s(180), s(30)
    boxes = [(C[0] - gap / 2 - cw, cards_y, C[0] - gap / 2, cards_y + ch),
             (C[0] + gap / 2, cards_y, C[0] + gap / 2 + cw, cards_y + ch)]
    for b in boxes:
        img = Image.alpha_composite(img, soft_shadow(size, b, s(36), s(24), (140, 120, 200, 60)))
    glass = Image.new("RGBA", size, (0, 0, 0, 0))
    gd = ImageDraw.Draw(glass)
    for b in boxes:
        gd.rounded_rectangle(b, radius=s(36), fill=(255, 255, 255, 165),
                             outline=(255, 255, 255, 240), width=s(2))
    img = Image.alpha_composite(img, glass)
    d = ImageDraw.Draw(img)

    for b, label, mins_v, col, kind in (
        (boxes[0], "FELL ASLEEP", start, (255, 176, 160), "moon"),
        (boxes[1], "WOKE UP", end, (140, 190, 245), "sun"),
    ):
        x0, y0 = b[0] + s(40), b[1]
        # icon chip
        chip = s(30)
        cx_, cy_ = x0 + chip, y0 + s(90)
        d.ellipse([cx_ - chip, cy_ - chip, cx_ + chip, cy_ + chip], fill=col)
        if kind == "moon":
            draw_moon(d, cx_ - s(1), cy_, s(14), (255, 255, 255), col)
        else:
            draw_sun(d, cx_, cy_, s(19), (255, 255, 255))
        tx = x0 + chip * 2 + s(24)
        spaced(d, (tx, y0 + s(56)), label, font(600, 18), INK_SOFT, s(4), anchor="l")
        clock, ampm = fmt_clock(mins_v)
        f_clock = font(600, 62)
        d.text((tx, y0 + s(140)), clock, font=f_clock, fill=INK, anchor="ls")
        d.text((tx + d.textlength(clock, font=f_clock) + s(10), y0 + s(140)), ampm,
               font=font(500, 26), fill=INK_SOFT, anchor="ls")

    # ----- footer pill with a little message
    msg = nap_message(total)
    f_msg = font(500, 28)
    tw = d.textlength(msg, font=f_msg)
    pill = (C[0] - tw / 2 - s(36), C[1] + s(95), C[0] + tw / 2 + s(36), C[1] + s(151))
    pill_layer = Image.new("RGBA", size, (0, 0, 0, 0))
    ImageDraw.Draw(pill_layer).rounded_rectangle(pill, radius=s(29), fill=(255, 255, 255, 200))
    text_center(ImageDraw.Draw(pill_layer), (C[0], (pill[1] + pill[3]) / 2), msg, f_msg, INK + (255,))
    img = Image.alpha_composite(img, pill_layer)

    d = ImageDraw.Draw(img)
    spaced(d, (C[0], s(1250)), "SWEET  DREAMS,  LITTLE  ONE", font(500, 18), INK_SOFT, s(5))
    for dx in (-250, 250):
        sparkle(d, C[0] + s(dx), s(1250), s(9), (255, 214, 120))

    final = add_grain(img.resize((W, H), Image.LANCZOS), amount=3.5)
    final.save(out, "JPEG", quality=95, subsampling=0)
    print(f"Saved {out}  ({fmt_clock(start)[0]} → {fmt_clock(end)[0]}, {total} min)")


def main():
    p = argparse.ArgumentParser(description="Make a pastel nap poster.")
    p.add_argument("start", type=parse_time, help="when the nap started, e.g. 12:45")
    p.add_argument("end", type=parse_time, help="when the nap ended, e.g. 2:10")
    p.add_argument("-o", "--out", default="nap.jpg", help="output JPEG path")
    p.add_argument("--name", help="baby's name for the title (optional)")
    p.add_argument("--date", type=lambda v: datetime.strptime(v, "%Y-%m-%d").date(),
                   help="date as YYYY-MM-DD (default: today)")
    a = p.parse_args()
    render(a.start, a.end, a.out, name=a.name, day=a.date)


if __name__ == "__main__":
    main()
