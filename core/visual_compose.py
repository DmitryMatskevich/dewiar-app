"""Core export: original + marker.js overlay -> clean PNG.

Used by visual_assets.py. The original pixels come from the file on disk, never
from the browser; covers are re-applied as opaque polygons; crop and metadata are
handled here so every outgoing path shares one function.
"""
import io
import math
import struct

from PIL import Image, ImageDraw, ImageOps

COVER_TYPES = {'CoverMarker'}


def _corners(marker, margin):
    """Rectangle corners in source pixels, honouring marker.js rotationAngle (degrees, around centre)."""
    left, top = float(marker['left']) - margin, float(marker['top']) - margin
    width, height = float(marker['width']) + 2 * margin, float(marker['height']) + 2 * margin
    cx, cy = left + width / 2, top + height / 2
    angle = math.radians(float(marker.get('rotationAngle') or 0))
    cos, sin = math.cos(angle), math.sin(angle)
    points = []
    for x, y in ((left, top), (left + width, top), (left + width, top + height), (left, top + height)):
        dx, dy = x - cx, y - cy
        points.append((cx + dx * cos - dy * sin, cy + dx * sin + dy * cos))
    return points


def compose(source_bytes, overlay_bytes, state, crop=None, margin=2):
    with Image.open(io.BytesIO(source_bytes)) as src:
        src.load()
        has_alpha = src.mode in ('RGBA', 'LA') or (src.mode == 'P' and 'transparency' in src.info)
        base = ImageOps.exif_transpose(src).convert('RGBA')
    if (state.get('width'), state.get('height')) != base.size:
        raise ValueError('Разметка относится к изображению другого размера')
    with Image.open(io.BytesIO(overlay_bytes)) as ov:
        ov.load()
        if ov.size != base.size:
            raise ValueError('Слой пометок не совпадает по размеру с оригиналом')
        overlay = ov.convert('RGBA')
    out = Image.alpha_composite(base, overlay)
    draw = ImageDraw.Draw(out)
    covers = 0
    for marker in state.get('markers', []):
        if marker.get('typeName') in COVER_TYPES:
            draw.polygon(_corners(marker, margin), fill=(0, 0, 0, 255))
            covers += 1
    if crop:
        x, y, w, h = (int(crop[k]) for k in ('x', 'y', 'width', 'height'))
        if x < 0 or y < 0 or w < 2 or h < 2 or x + w > out.width or y + h > out.height:
            raise ValueError('Область обрезки выходит за границы изображения')
        out = out.crop((x, y, x + w, y + h))
    if not has_alpha:
        out = out.convert('RGB')
    buffer = io.BytesIO()
    out.info.clear()
    out.save(buffer, 'PNG', compress_level=6)          # no pnginfo/exif/icc: clean file
    return buffer.getvalue(), {'width': out.width, 'height': out.height, 'covers': covers}


def png_chunks(data):
    pos, names = 8, []
    while pos < len(data):
        length, name = struct.unpack('>I4s', data[pos:pos + 8])
        names.append(name.decode())
        pos += 12 + length
    return names
