#!/usr/bin/env python3
"""Значки Dewiar из брендового логотипа (brand/dewiar-logo-original.png: чёрный знак на белом). Нужен Pillow.

    python3 tools/make_icons.py

Получается (файлы перезаписываются):
  icons/dewiar-{48,64,128,256,512}.png, icons/dewiar.ico, packaging/windows/dewiar.ico, ui/assets/logo.png —
      знак как в оригинале: чёрный корпус, белые «окна»; снаружи прозрачно (виден и на тёмной, и на светлой панели)
  icons/dewiar-tray.png   — белый знак 64 px (трей на тёмной панели Ubuntu), «окна» прозрачные
  icons/dewiar-mark.png, ui/assets/logo-white.png — белый знак 128 px для интерфейса (полоса заголовка студии)
Картинки установщика Windows (правит welcome.bmp и header.bmp на месте, берёт уже готовые icons/):
    python3 tools/make_icons.py --installer"""
from collections import deque
from pathlib import Path

from PIL import Image, ImageOps

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "brand" / "dewiar-logo-original.png"


def masks():
    """-> (ink, shape), обрезанные по знаку: ink — насколько пиксель чёрный (белые «окна» = 0),
    shape — знак целиком вместе с «окнами». «Окна» отличаются от фона тем, что не связаны с краем картинки."""
    g = ImageOps.grayscale(Image.open(SRC).convert("RGB"))
    w, h = g.size
    px = g.load()
    # снаружи — всё светлое, что связано с краем картинки (заливка от краёв)
    outside = bytearray(w * h)
    q = deque((x, y) for x in range(w) for y in (0, h - 1))
    q.extend((x, y) for y in range(h) for x in (0, w - 1))
    while q:
        x, y = q.popleft()
        i = y * w + x
        if outside[i] or px[x, y] < 100:
            continue
        outside[i] = 1
        if x > 0: q.append((x - 1, y))
        if x < w - 1: q.append((x + 1, y))
        if y > 0: q.append((x, y - 1))
        if y < h - 1: q.append((x, y + 1))
    ink = Image.new("L", (w, h))            # насколько пиксель «чёрный» (со сглаживанием)
    shape = Image.new("L", (w, h))          # знак целиком (корпус + окна), снаружи — 0
    ik, sh = ink.load(), shape.load()
    for y in range(h):
        for x in range(w):
            v = 255 - px[x, y]
            ik[x, y] = v
            sh[x, y] = v if outside[y * w + x] else 255
    box = ImageOps.invert(g).point(lambda v: 255 if v > 40 else 0).getbbox()
    return ink.crop(box), shape.crop(box)


def square(img, pad):
    w, h = img.size
    s = int(max(w, h) * (1 + 2 * pad))
    out = Image.new(img.mode, (s, s), 0)
    out.paste(img, ((s - w) // 2, (s - h) // 2))
    return out


def main():
    ink, shape = masks()
    ink, shape = square(ink, 0.04), square(shape, 0.04)
    # оригинал: чёрный корпус, белые окна, снаружи прозрачно
    body = Image.merge("RGBA", (ImageOps.invert(ink),) * 3 + (shape,))
    # белый знак: корпус белый, окна и снаружи прозрачно
    white = Image.merge("RGBA", (Image.new("L", ink.size, 255),) * 3 + (ink,))
    icons = ROOT / "icons"
    for s in (48, 64, 128, 256, 512):
        body.resize((s, s), Image.LANCZOS).save(icons / f"dewiar-{s}.png", optimize=True)
    ico = body.resize((256, 256), Image.LANCZOS)
    for p in (icons / "dewiar.ico", ROOT / "packaging" / "windows" / "dewiar.ico"):
        ico.save(p, sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])
    white.resize((64, 64), Image.LANCZOS).save(icons / "dewiar-tray.png", optimize=True)
    white.resize((128, 128), Image.LANCZOS).save(icons / "dewiar-mark.png", optimize=True)
    white.resize((128, 128), Image.LANCZOS).save(ROOT / "ui" / "assets" / "logo-white.png", optimize=True)
    body.resize((128, 128), Image.LANCZOS).save(ROOT / "ui" / "assets" / "logo.png", optimize=True)
    print("ok")




def installer_art():
    """Картинки установщика Windows: в welcome.bmp круг (центр 82,110, радиус 52) закрашивается градиентом фона
    и на его место ставится белый знак; в header.bmp справа — белая полоса с чёрным знаком. Подписи не трогаются.
    Координаты подобраны под эти .bmp."""
    win = ROOT / "packaging" / "windows"

    def erase_disc(img, cx, cy, r):
        px = img.load()
        for y in range(max(0, cy - r), min(img.height, cy + r + 1)):
            dx = int((r * r - (y - cy) ** 2) ** 0.5) if abs(y - cy) <= r else 0
            x0, x1 = max(0, cx - dx - 2), min(img.width - 1, cx + dx + 2)
            a, b = px[x0, y], px[x1, y]
            for x in range(x0, x1 + 1):
                t = (x - x0) / max(1, x1 - x0)
                px[x, y] = tuple(int(a[i] + (b[i] - a[i]) * t) for i in range(3))
        return img
    w = Image.open(win / "welcome.bmp").convert("RGB")
    erase_disc(w, 82, 110, 52)
    big = Image.open(ROOT / "icons" / "dewiar-tray.png").resize((78, 78), Image.LANCZOS)
    w.paste(big, (82 - 39, 110 - 39), big)
    w.save(win / "welcome.bmp")
    h = Image.open(win / "header.bmp").convert("RGB")
    h.paste((255, 255, 255), (h.width - 60, 0, h.width, h.height))
    blk = Image.open(ROOT / "icons" / "dewiar-128.png").resize((44, 44), Image.LANCZOS)
    h.paste(blk, (h.width - 52, (h.height - 44) // 2), blk)
    h.save(win / "header.bmp")


if __name__ == "__main__":
    import sys
    installer_art() if "--installer" in sys.argv else main()
