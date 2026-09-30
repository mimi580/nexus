"""Ad images for Meta: the operator's product photos first, a clean generated card otherwise.

Real product photos convert far better than any generated graphic, so upload
them (dashboard → Ads → Photos). Until you do, NEXUS draws a simple branded
card with the headline and the call to action, in the 1:1 (1080×1080) format
both feed and stories placements accept. Text on the card is short on
purpose: Meta delivers ads with little text in the image more widely.
"""

from __future__ import annotations

import io
import textwrap
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

SIZE = 1080
PALETTES = {
    "refurbished_laptop": ((18, 52, 86), (255, 196, 0)),
    "used_iphone": ((24, 24, 27), (56, 189, 248)),
    "medical_equipment": ((8, 80, 90), (255, 255, 255)),
    "server_it": ((30, 41, 59), (74, 222, 128)),
}
FONT_DIRS = ("/usr/share/fonts/truetype/dejavu", "/usr/share/fonts/dejavu", "C:/Windows/Fonts")


def _font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    names = ["DejaVuSans-Bold.ttf", "arialbd.ttf"] if bold else ["DejaVuSans.ttf", "arial.ttf"]
    for folder in FONT_DIRS:
        for name in names:
            path = Path(folder) / name
            if path.exists():
                return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size=size)


def _wrap(draw: ImageDraw.ImageDraw, text: str, font, width_px: int) -> list[str]:
    for chars in range(40, 8, -1):
        lines = textwrap.wrap(text, chars)
        if all(draw.textlength(line, font=font) <= width_px for line in lines):
            return lines
    return textwrap.wrap(text, 10)


def generated_card(category: str, headline: str, subline: str, cta: str, business: str) -> bytes:
    background, accent = PALETTES.get(category, ((30, 41, 59), (250, 204, 21)))
    image = Image.new("RGB", (SIZE, SIZE), background)
    draw = ImageDraw.Draw(image)
    margin = 90
    draw.rectangle([0, 0, SIZE, 18], fill=accent)
    title_font = _font(84, bold=True)
    y = 230
    for line in _wrap(draw, headline, title_font, SIZE - 2 * margin)[:4]:
        draw.text((margin, y), line, font=title_font, fill=(255, 255, 255))
        y += 102
    body_font = _font(44)
    y += 30
    for line in _wrap(draw, subline, body_font, SIZE - 2 * margin)[:3]:
        draw.text((margin, y), line, font=body_font, fill=(226, 232, 240))
        y += 58
    button_font = _font(46, bold=True)
    label = cta
    width = draw.textlength(label, font=button_font) + 80
    top = SIZE - 260
    draw.rounded_rectangle([margin, top, margin + width, top + 96], radius=18, fill=accent)
    draw.text((margin + 40, top + 22), label, font=button_font, fill=background)
    draw.text((margin, SIZE - 110), business, font=_font(34), fill=(203, 213, 225))
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    return buffer.getvalue()


def normalise_photo(data: bytes) -> tuple[bytes, str]:
    """Validate an uploaded photo and fit it to 1080×1080 (letterboxed, no distortion)."""
    try:
        source = Image.open(io.BytesIO(data))
        source.verify()
        source = Image.open(io.BytesIO(data)).convert("RGB")
    except Exception as exc:  # noqa: BLE001 - any decode failure means "not an image"
        raise ValueError("not a readable image (use JPG or PNG)") from exc
    if min(source.size) < 500:
        raise ValueError("image is too small: use at least 500×500 pixels")
    source.thumbnail((SIZE, SIZE))
    canvas = Image.new("RGB", (SIZE, SIZE), (255, 255, 255))
    canvas.paste(source, ((SIZE - source.width) // 2, (SIZE - source.height) // 2))
    buffer = io.BytesIO()
    canvas.save(buffer, format="JPEG", quality=90)
    return buffer.getvalue(), "image/jpeg"
