#!/usr/bin/env python3
"""Generate the MurOS installer boot splash images.

Flat, on-brand splash reproducing the MurOS logo (three descending amber
bars + "MurOS" wordmark) on the brand navy. Two sizes are produced:
  - splash-640x480.png  for the BIOS isolinux menu
  - splash-800x600.png  for the UEFI grub menu
The navy background keeps the white menu text readable. Run with Pillow:
  python3 make-splash.py
"""
from PIL import Image, ImageDraw, ImageFont
import os

NAVY = (15, 23, 42)      # #0f172a brand dark
AMBER = (245, 197, 24)   # #F5C518 logo bars / accent
WHITE = (255, 255, 255)
MUTED = (148, 163, 184)  # #94a3b8 tagline
FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"
HERE = os.path.dirname(os.path.abspath(__file__))


def rrect(d, box, r, fill):
    try:
        d.rounded_rectangle(box, radius=r, fill=fill)
    except Exception:
        d.rectangle(box, fill=fill)


def render(W, H):
    img = Image.new("RGB", (W, H), NAVY)
    d = ImageDraw.Draw(img)
    word_px = int(H * 0.12)
    f_word = ImageFont.truetype(FONT, word_px)
    f_tag = ImageFont.truetype(FONT, int(H * 0.038))

    # Measure wordmark parts ("Mur" white, "OS" amber).
    def tw(txt, f):
        b = d.textbbox((0, 0), txt, font=f)
        return b[2] - b[0], b[3] - b[1]
    w_mur, h_word = tw("Mur", f_word)
    w_os, _ = tw("OS", f_word)

    # Logo bars block, scaled to the wordmark cap height.
    bar_h = max(4, int(h_word * 0.20))
    bar_gap = max(2, int(bar_h * 0.55))
    bar_w = [int(h_word * 0.95), int(h_word * 0.74), int(h_word * 0.52)]
    bars_w = max(bar_w)
    bars_h = bar_h * 3 + bar_gap * 2
    gap = int(H * 0.03)
    total_w = bars_w + gap + w_mur + w_os
    x0 = (W - total_w) // 2
    y_top = int(H * 0.30)

    by = y_top + (h_word - bars_h) // 2
    for i, bw in enumerate(bar_w):
        y = by + i * (bar_h + bar_gap)
        rrect(d, [x0, y, x0 + bw, y + bar_h], bar_h // 3, AMBER)

    tx = x0 + bars_w + gap
    d.text((tx, y_top), "Mur", font=f_word, fill=WHITE)
    d.text((tx + w_mur, y_top), "OS", font=f_word, fill=AMBER)

    # Tagline + thin amber rule, centered under the wordmark.
    tag = "Open-source firewall"
    wt, ht = tw(tag, f_tag)
    ty = y_top + h_word + int(H * 0.06)
    d.text(((W - wt) // 2, ty), tag, font=f_tag, fill=MUTED)
    rule_w = int(total_w * 0.9)
    rule_y = ty - int(H * 0.025)
    d.rectangle([(W - rule_w) // 2, rule_y, (W + rule_w) // 2, rule_y + max(2, H // 300)], fill=AMBER)
    return img


for w, h in [(640, 480), (800, 600)]:
    out = os.path.join(HERE, f"splash-{w}x{h}.png")
    render(w, h).save(out)
    print("wrote", out)
