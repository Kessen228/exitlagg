"""Рисует иконку программы: assets/icon.png и assets/icon.ico (нужен Pillow)."""

from pathlib import Path

from PIL import Image, ImageDraw

SIZE = 256
ASSETS = Path(__file__).resolve().parent.parent / "assets"


def draw() -> Image.Image:
    scale = 4  # рисуем крупнее и уменьшаем — так края получаются сглаженными
    s = SIZE * scale
    image = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    d = ImageDraw.Draw(image)
    d.rounded_rectangle((8 * scale, 8 * scale, s - 8 * scale, s - 8 * scale), radius=56 * scale,
                        fill=(37, 99, 235, 255))
    # циферблат
    c, r = s // 2, 84 * scale
    d.ellipse((c - r, c - r, c + r, c + r), fill=(255, 255, 255, 255))
    d.ellipse((c - r + 14 * scale, c - r + 14 * scale, c + r - 14 * scale, c + r - 14 * scale),
              fill=(37, 99, 235, 255))
    # стрелки
    w = 14 * scale
    d.rounded_rectangle((c - w // 2, c - 62 * scale, c + w // 2, c + w // 2), radius=w // 2,
                        fill=(255, 255, 255, 255))
    d.rounded_rectangle((c - w // 2, c - w // 2, c + 48 * scale, c + w // 2), radius=w // 2,
                        fill=(255, 255, 255, 255))
    d.ellipse((c - 12 * scale, c - 12 * scale, c + 12 * scale, c + 12 * scale), fill=(255, 255, 255, 255))
    return image.resize((SIZE, SIZE), Image.LANCZOS)


def main() -> None:
    ASSETS.mkdir(exist_ok=True)
    image = draw()
    image.resize((64, 64), Image.LANCZOS).save(ASSETS / "icon.png")
    image.save(ASSETS / "icon.ico", sizes=[(16, 16), (24, 24), (32, 32), (48, 48), (64, 64), (128, 128), (256, 256)])


if __name__ == "__main__":
    main()
