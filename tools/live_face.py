"""Живое лицо персонажа из портрета (ui/assets/entities.js → liveFace): слои для Патрика.

Из data/avatars/patrik.webp (512×512, чёрный фон) собирает в ui/assets/live:
  patrik.webp         — портрет 360×360 с прозрачным фоном и пустыми глазами (радужку рисует entities.js);
  patrik-eyes.png     — маска разреза глаз 360×360: зрачок рисуется только внутри неё (веки его прикрывают);
  patrik-mouth-0..3   — рот: 0 — как на портрете (закрыт), 1–3 — приоткрыт сильнее (нижняя губа опускается,
                        между губами — тёмная глубина, на самом большом — край зубов);
  patrik-smile.webp   — лёгкая улыбка (уголки губ вверх).
Рот — 3× разрешения портрета, с мягким краем: на портрете он лежит в рамке GEO["mouth"].
Геометрия (GEO) — та же, что в LIVE_CONF.patrik в entities.js; поменяли здесь — поменяйте и там.
Запуск: python3 tools/live_face.py   (нужны Pillow и numpy)."""
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data/avatars/patrik.webp"
OUT = ROOT / "ui/assets/live"
K = 3                                     # рот — в 3× разрешения портрета
# глаза: центр радужки (найден по тёмным синеватым пикселям), разрез — центр, полуширина, полувысота вверх и вниз
# Радужка — НАСТОЯЩАЯ, вырезанная с портрета кругом (patrik-iris-l/r.png): в покое лицо в точности как на фото,
# глаза не «нарисованные кружочки». Центр — где радужка на портрете, IRIS_D — диаметр выреза.
IRIS = [(146.8, 148.9), (212.5, 149.3)]
IRIS_D = 12.0
LID_TOP, LID_BOT = 0.85, 0.35             # насколько разрез уже края века сверху и снизу: зрачок не заходит на ресницы
OPEN = [(144.8, 150.6, 13.8, 3.6, 4.0), (209.8, 150.8, 14.4, 3.6, 4.0)]
LIP_Y = 221.4                             # линия смыкания губ
MOUTH = (143.0, 201.0, 74.0, 44.0)        # рамка рта на портрете: x, y, ширина, высота
MOUTH_CX, MOUTH_HALF = 180.0, 28.5        # середина губ и полуширина (уголки)


def portrait():
    src = Image.open(SRC).convert("RGB")
    big = np.asarray(src.resize((1080, 1080), Image.LANCZOS)).astype(np.float32)
    # фон: тёмное, связанное с краем — прозрачное (заливка от углов по маске «почти чёрное»)
    dark = (big.max(2) < 26).astype(np.uint8) * 255
    m = Image.fromarray(dark, "L").copy()       # копия: картинка поверх массива numpy только для чтения — floodfill молча не красил (Pillow 12)
    for xy in [(0, 0), (1079, 0), (0, 1079), (1079, 1079), (540, 0), (0, 540), (1079, 540)]:
        if m.getpixel(xy) == 255:                                  # только с тёмной точки фона (не с макушки)
            ImageDraw.floodfill(m, xy, 128)
    bg = (np.asarray(m) == 128)
    alpha = Image.fromarray(np.where(bg, 0, 255).astype(np.uint8), "L").filter(ImageFilter.GaussianBlur(1.6))
    rgba = Image.fromarray(big.clip(0, 255).astype(np.uint8), "RGB").convert("RGBA")
    rgba.putalpha(alpha)
    return rgba                                                     # 1080×1080 = 3× портрета


def almond(draw, cx, cy, hw, up, dn, k=K, inset=0.0):
    """Миндалевидный разрез глаза многоугольником в координатах k×."""
    pts = []
    for i in range(41):
        t = -1 + 2 * i / 40
        pts.append(((cx + t * (hw - inset)) * k, (cy - (up - inset) * (1 - t * t) ** 0.75) * k))
    for i in range(40, -1, -1):
        t = -1 + 2 * i / 40
        pts.append(((cx + t * (hw - inset)) * k, (cy + (dn - inset) * (1 - t * t) ** 0.85) * k))
    draw.polygon(pts, fill=255)


def almond2(draw, cx, cy, hw, up, dn, k=K):
    """Разрез без ресниц: сверху уже на LID_TOP, снизу на LID_BOT, по бокам на 0.5."""
    almond(draw, cx, cy, hw - 0.5, up - LID_TOP + 0.5, dn - LID_BOT + 0.5, k=k, inset=0.5)


def sclera_color(arr):
    """Цвет белка с самого портрета: светлые пиксели разреза вне радужки (медиана) — белок не светится, а как на фото."""
    vals = []
    for (cx, cy, hw, up, dn), (ix, iy) in zip(OPEN, IRIS):
        for yy in range(int((cy - 1) * K), int((cy + 2) * K)):
            for xx in range(int((cx - hw + 2) * K), int((cx + hw - 2) * K)):
                if ((xx / K - ix) ** 2 + (yy / K - iy) ** 2) ** 0.5 > IRIS_D / 2 + 0.8:
                    vals.append(arr[yy, xx, :3])
    v = np.array(vals)
    lum = v.mean(1)
    return np.median(v[lum > np.percentile(lum, 60)], axis=0)


def blank_eyes(big):
    """Радужку и зрачок — под белок цвета самого портрета, с тенью от верхнего века; поверх entities.js кладёт
    вырезанную настоящую радужку (в покое — ровно как на фото)."""
    arr = np.asarray(big).astype(np.float32)
    mask = Image.new("L", big.size, 0)
    d = ImageDraw.Draw(mask)
    for cx, cy, hw, up, dn in OPEN:
        almond2(d, cx, cy, hw, up, dn)
    mask = np.asarray(mask.filter(ImageFilter.GaussianBlur(1.1))).astype(np.float32) / 255
    scl = sclera_color(arr)
    ys = np.arange(big.size[1], dtype=np.float32)[:, None] / K
    out = arr.copy()
    for cx, cy, hw, up, dn in OPEN:
        # белок: светлее к середине, темнее под верхним веком и к уголкам
        v = np.clip((ys - (cy - up)) / (up + dn), 0, 1)
        shade = 0.62 + 0.38 * np.maximum(np.sin(np.pi * np.clip(v * 1.15, 0, 1)), 0) ** 0.7
        xs = np.arange(big.size[0], dtype=np.float32)[None, :] / K
        corner = np.clip(1 - (np.abs(xs - cx) / hw) ** 2.5, 0, 1) ** 0.35
        col = scl[None, None, :] * (shade * (0.72 + 0.28 * corner))[..., None]
        sel = np.zeros(mask.shape, bool)
        y0, y1 = int((cy - up - 2) * K), int((cy + dn + 2) * K)
        x0, x1 = int((cx - hw - 2) * K), int((cx + hw + 2) * K)
        sel[y0:y1, x0:x1] = True
        a = (mask * sel)[..., None]
        out[..., :3] = out[..., :3] * (1 - a) + col * a
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGBA")


def eye_mask():
    """Где виден зрачок: тот же разрез, что закрашен под белок (без ресниц), с мягким краем."""
    m = Image.new("L", (360 * K, 360 * K), 0)
    d = ImageDraw.Draw(m)
    for cx, cy, hw, up, dn in OPEN:
        almond2(d, cx, cy, hw, up, dn)
    return m.filter(ImageFilter.GaussianBlur(0.9)).resize((360, 360), Image.LANCZOS)


def iris_crops(big):
    """Настоящая радужка с портрета: круг IRIS_D, края мягкие — patrik-iris-l.png и patrik-iris-r.png (в 3×)."""
    arr = np.asarray(big).astype(np.float32)
    D = int(round(IRIS_D * K))
    yy, xx = np.mgrid[0:D, 0:D].astype(np.float32)
    r = np.hypot(xx - (D - 1) / 2, yy - (D - 1) / 2) / (D / 2)
    alpha = np.clip((1 - r) / 0.18, 0, 1) * 255
    out = []
    for ix, iy in IRIS:
        x0, y0 = int(round((ix - IRIS_D / 2) * K)), int(round((iy - IRIS_D / 2) * K))
        crop = arr[y0:y0 + D, x0:x0 + D].copy()
        crop[..., 3] = alpha
        out.append(Image.fromarray(crop.clip(0, 255).astype(np.uint8), "RGBA"))
    return out


def mouth_layer(big, open_px=0.0, smile_px=0.0):
    """Рот в рамке MOUTH (в 3×): open_px — насколько опускается нижняя губа (пиксели портрета), smile_px — уголки вверх."""
    x, y, w, h = MOUTH
    X0, Y0, W, H = int(x * K), int(y * K), int(w * K), int(h * K)
    src = np.asarray(big).astype(np.float32)[Y0:Y0 + H, X0:X0 + W]
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32)
    px, py = xx / K + x, yy / K + y                                 # координаты портрета
    lip = LIP_Y
    t = np.clip(np.abs(px - MOUTH_CX) / MOUTH_HALF, 0, 1)
    prof = np.maximum(np.cos(t * np.pi / 2), 0) ** 0.9                             # 1 в середине, 0 в уголках
    bottom, top = y + h, y
    # смещение вниз для нижней губы и подбородка (затухает к низу рамки), вверх — чуть-чуть для верхней
    dn_fade = np.clip(1 - (py - lip) / (bottom - lip), 0, 1)
    up_fade = np.clip(1 - (lip - py) / (lip - top), 0, 1)
    D = np.where(py >= lip, open_px * prof * dn_fade, -0.22 * open_px * prof * up_fade)
    # улыбка: уголки вверх (сильнее у краёв), слегка растянуть в стороны
    corner = np.clip(np.abs(px - MOUTH_CX) / MOUTH_HALF, 0, 1.3) ** 2
    near = np.exp(-((py - lip) / 7.0) ** 2)
    S = -smile_px * corner * near
    sx = px - (px - MOUTH_CX) * 0.035 * (smile_px > 0) * near       # растяжение к краям
    # обратное отображение: где взять пиксель (итерациями)
    sy = py.copy()
    for _ in range(4):
        dn_f = np.clip(1 - (sy - lip) / (bottom - lip), 0, 1)
        up_f = np.clip(1 - (lip - sy) / (lip - top), 0, 1)
        Dd = np.where(sy >= lip, open_px * prof * dn_f, -0.22 * open_px * prof * up_f)
        sy = py - Dd - S
    gap = np.zeros_like(py, bool)
    if open_px > 0:
        lower = lip + open_px * prof                                # куда опустился край нижней губы
        upper = lip - 0.22 * open_px * prof
        gap = (py > upper) & (py < lower) & (prof > 0.04)
    sxi = np.clip((sx - x) * K, 0, W - 1.001)
    syi = np.clip((sy - y) * K, 0, H - 1.001)
    x0, y0 = np.floor(sxi).astype(int), np.floor(syi).astype(int)
    fx, fy = (sxi - x0)[..., None], (syi - y0)[..., None]
    s00, s10 = src[y0, x0], src[y0, x0 + 1]
    s01, s11 = src[y0 + 1, x0], src[y0 + 1, x0 + 1]
    out = (s00 * (1 - fx) + s10 * fx) * (1 - fy) + (s01 * (1 - fx) + s11 * fx) * fy
    if open_px > 0:
        lower = lip + open_px * prof
        upper = lip - 0.22 * open_px * prof
        v = np.clip((py - upper) / np.maximum(lower - upper, 1e-3), 0, 1)
        inner = np.array([46, 30, 36], np.float32)
        col = inner[None, None, :] * (0.55 + 0.9 * np.abs(v - 0.5))[..., None]       # глубже в середине
        if open_px >= 4.0:                                         # край верхних зубов — мягкой полосой
            tw = np.clip(1 - v / 0.3, 0, 1) * np.clip((prof - 0.3) / 0.3, 0, 1)
            col = col * (1 - tw[..., None] * 0.85) + np.array([176, 172, 172], np.float32) * (tw[..., None] * 0.85)
        edge = np.clip(np.minimum(py - upper, lower - py) * K / 3.0, 0, 1)[..., None]   # мягкий край щели
        a = (gap[..., None] * edge)
        out[..., :3] = out[..., :3] * (1 - a) + col * a
    out[..., 3] = 255
    # мягкая овальная рамка: края слоя растворяются в портрете
    e = Image.new("L", (W, H), 0)
    ImageDraw.Draw(e).ellipse([W * 0.06, H * 0.08, W * 0.94, H * 0.94], fill=255)
    e = np.asarray(e.filter(ImageFilter.GaussianBlur(W * 0.06))).astype(np.float32)
    out[..., 3] = e
    return Image.fromarray(out.clip(0, 255).astype(np.uint8), "RGBA")


def main():
    big = portrait()
    blank = blank_eyes(big)
    blank.resize((360, 360), Image.LANCZOS).save(OUT / "patrik.webp", quality=92, method=6)
    eye_mask().save(OUT / "patrik-eyes.png", optimize=True)
    for side, im in zip("lr", iris_crops(big)):
        im.save(OUT / f"patrik-iris-{side}.png", optimize=True)
    for i, op in enumerate((0.0, 2.4, 4.2, 6.2)):
        mouth_layer(big, open_px=op).save(OUT / f"patrik-mouth-{i}.webp", quality=92, method=6)
    mouth_layer(big, smile_px=2.4).save(OUT / "patrik-smile.webp", quality=92, method=6)
    cx = [(x - IRIS_D / 2, y - IRIS_D / 2) for x, y in IRIS]
    print("eyes (левый верхний угол радужки):", [[round(a, 1), round(b, 1)] for a, b in cx], "iris:", IRIS_D, "mouth:", MOUTH)


if __name__ == "__main__":
    main()
