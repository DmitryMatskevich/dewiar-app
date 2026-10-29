#!/usr/bin/env python3
"""Зерно «матового стекла» для тем оформления (ui/assets/skins/grain.webp): плитка 160×160 из случайного шума с
прозрачностью — поверх размытых цветных полей даёт ощущение запотевшего стекла (темы frost и fog в skins.css).
Картинка, а не живой фильтр SVG: фон окна тогда ничего не стоит процессору. Запуск:

    python3 tools/skin_textures.py ui/assets/skins
"""
import random
import sys
from pathlib import Path

from PIL import Image

out = Path(sys.argv[1] if len(sys.argv) > 1 else "ui/assets/skins")
out.mkdir(parents=True, exist_ok=True)
random.seed(7)
im = Image.new("RGBA", (160, 160))
px = im.load()
for y in range(160):
    for x in range(160):
        v = random.randint(0, 255)
        px[x, y] = (v, v, v, random.randint(0, 22))
im.save(out / "grain.webp", lossless=True)
print(out / "grain.webp", (out / "grain.webp").stat().st_size)
