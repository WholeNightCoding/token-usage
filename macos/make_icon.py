#!/usr/bin/env python3
"""Generate a macOS app icon (.iconset) for the Token Usage dashboard launcher.

Pillow-only. Draws a rounded-rect dark icon with an electric bar-chart glyph and
a trending-up spark, then emits every size macOS needs into an .iconset folder.
build_app.sh runs `iconutil -c icns` on that folder.

Usage:
    python3 make_icon.py <output.iconset dir>
"""
import sys
import os
from PIL import Image, ImageDraw

MASTER = 1024


def lerp(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(len(a)))


def vertical_gradient(size, top, bottom):
    """Opaque RGB gradient, top->bottom, drawn one row at a time."""
    img = Image.new("RGB", (size, size))
    px = img.load()
    for y in range(size):
        t = y / (size - 1)
        c = lerp(top, bottom, t)
        for x in range(size):
            px[x, y] = c
    return img


def rounded_mask(size, radius):
    m = Image.new("L", (size, size), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=radius, fill=255)
    return m


def draw_icon():
    S = MASTER
    # macOS app-icon convention: rounded-rect content inset a touch from the edge.
    inset = int(S * 0.085)
    content = S - inset * 2
    radius = int(content * 0.225)  # squircle-ish corner

    # --- background: deep charcoal-navy vertical gradient, rounded ---
    grad = vertical_gradient(content, (0x27, 0x35, 0x4d), (0x0b, 0x11, 0x1f))
    mask = rounded_mask(content, radius)
    icon = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    icon.paste(grad, (inset, inset), mask)

    draw = ImageDraw.Draw(icon)

    # subtle inner top-left sheen
    sheen = Image.new("RGBA", (S, S), (0, 0, 0, 0))
    sd = ImageDraw.Draw(sheen)
    sd.rounded_rectangle(
        [inset, inset, inset + content - 1, inset + content - 1],
        radius=radius,
        fill=(255, 255, 255, 22),
    )
    # keep sheen only on the upper portion
    clip = Image.new("L", (S, S), 0)
    cd = ImageDraw.Draw(clip)
    cd.rectangle([0, 0, S, inset + int(content * 0.42)], fill=255)
    icon = Image.alpha_composite(icon, Image.composite(sheen, Image.new("RGBA", (S, S), (0, 0, 0, 0)), clip))
    draw = ImageDraw.Draw(icon)

    # --- bar chart ---
    # plotting area within the rounded rect
    pad = inset + int(content * 0.20)
    base_y = inset + content - int(content * 0.22)   # baseline
    top_y = inset + int(content * 0.24)              # tallest bar top
    area_w = (inset + content - int(content * 0.20)) - pad

    n = 4
    gap_ratio = 0.42
    bar_w = area_w / (n + (n - 1) * gap_ratio)
    gap = bar_w * gap_ratio
    heights = [0.42, 0.62, 0.80, 1.0]  # ascending
    # electric palette bottom->top per bar (teal -> sky)
    col_lo = (0x0e, 0xa5, 0xb0)
    col_hi = (0x53, 0xd7, 0xf5)

    tops = []
    for i in range(n):
        x0 = pad + i * (bar_w + gap)
        x1 = x0 + bar_w
        h = heights[i]
        y0 = base_y - (base_y - top_y) * h
        tops.append((x0 + bar_w / 2, y0))
        # per-bar vertical gradient fill via clipped paste
        bar_h = int(base_y - y0)
        if bar_h < 1:
            bar_h = 1
        bar_grad = vertical_gradient(max(int(bar_w), 1), lerp(col_lo, col_hi, i / (n - 1)),
                                     lerp(col_lo, col_hi, i / (n - 1)))
        # simple: solid-ish with slight top highlight — draw rounded bar
        r = int(bar_w * 0.28)
        col = lerp(col_lo, col_hi, i / (n - 1))
        draw.rounded_rectangle([x0, y0, x1, base_y], radius=r, fill=col + (255,))
        # glossy top cap
        draw.rounded_rectangle([x0, y0, x1, y0 + bar_w * 0.55], radius=r,
                               fill=lerp(col, (255, 255, 255), 0.28) + (255,))

    # --- trending-up spark line across the bar tops ---
    spark = (0xff, 0xe1, 0x66)
    line_pts = [(pad - bar_w * 0.15, base_y - (base_y - top_y) * 0.20)] + tops
    draw.line(line_pts, fill=spark + (255,), width=int(S * 0.018), joint="curve")
    for (cx, cy) in tops:
        rr = S * 0.017
        draw.ellipse([cx - rr, cy - rr, cx + rr, cy + rr], fill=spark + (255,),
                     outline=(0x0b, 0x11, 0x1f, 255), width=int(S * 0.006))

    # arrow head at the last point
    ax, ay = tops[-1]
    ah = S * 0.052
    draw.polygon([(ax + ah * 0.1, ay - ah), (ax + ah * 0.95, ay - ah * 0.55),
                  (ax + ah * 0.2, ay - ah * 0.2)], fill=spark + (255,))

    return icon


ICON_SIZES = [16, 32, 64, 128, 256, 512, 1024]
NAME_MAP = {
    16: [("icon_16x16.png", 16)],
    32: [("icon_16x16@2x.png", 32), ("icon_32x32.png", 32)],
    64: [("icon_32x32@2x.png", 64)],
    128: [("icon_128x128.png", 128)],
    256: [("icon_128x128@2x.png", 256), ("icon_256x256.png", 256)],
    512: [("icon_256x256@2x.png", 512), ("icon_512x512.png", 512)],
    1024: [("icon_512x512@2x.png", 1024)],
}


def main():
    if len(sys.argv) != 2:
        print("usage: make_icon.py <output.iconset dir>", file=sys.stderr)
        sys.exit(2)
    out = sys.argv[1]
    os.makedirs(out, exist_ok=True)
    master = draw_icon()
    for size in ICON_SIZES:
        resized = master.resize((size, size), Image.LANCZOS)
        for fname, _ in NAME_MAP[size]:
            resized.save(os.path.join(out, fname))
    print(f"wrote iconset -> {out}")


if __name__ == "__main__":
    main()
