"""Assemble labeled Blender review frames into a static motion inspection sheet."""

import argparse
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-dir", type=Path, required=True)
    parser.add_argument("--move", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    paths = sorted(args.input_dir.glob(f"{args.move}-front-*.png"))
    if not paths:
        raise FileNotFoundError("No rendered motion frames found")
    size, label, columns = 384, 24, 4
    sheet = Image.new(
        "RGB", (columns * size, math.ceil(len(paths) / columns) * (size + label)), "#222222"
    )
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default(size=16)
    for index, path in enumerate(paths):
        left, top = (index % columns) * size, (index // columns) * (size + label)
        with Image.open(path) as picture:
            picture.thumbnail((size, size))
            sheet.paste(picture, (left + (size - picture.width) // 2, top))
        draw.text((left + 8, top + size + 3), path.stem, font=font, fill="white")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output)


if __name__ == "__main__":
    main()
