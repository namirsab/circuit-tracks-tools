#!/usr/bin/env python3
"""Generate the PWA icons (pure Python, no Pillow), matching favicon.svg:
dark rounded square with a teal waveform glyph, at 192x192 and 512x512."""

import struct
import zlib
from pathlib import Path

BG = (20, 24, 31, 255)  # #14181f, same as favicon.svg

# (center offset fraction of width, half-height fraction, RGB) per bar,
# mirroring the favicon.svg bar layout and teal shades
BARS = [
    (12.5 / 64, 6 / 64, (0x14, 0x9E, 0x8F)),
    (19.5 / 64, 14 / 64, (0x14, 0xB8, 0xA6)),
    (26.5 / 64, 22 / 64, (0x1F, 0xCD, 0xB9)),
    (33.5 / 64, 27 / 64, (0x2D, 0xD4, 0xBF)),
    (40.5 / 64, 17 / 64, (0x56, 0xE0, 0xCF)),
    (47.5 / 64, 10 / 64, (0x7C, 0xE9, 0xDC)),
    (54.5 / 64, 5 / 64, (0xA5, 0xF0, 0xE6)),
]
BAR_WIDTH = 5 / 64


def make_icon(size: int) -> bytes:
    transparent = (0, 0, 0, 0)
    px = [[transparent] * size for _ in range(size)]

    # Rounded square background (full-bleed, like the favicon)
    radius = round(size * 14 / 64)
    hi = size - 1
    for y in range(size):
        for x in range(size):
            cx = max(radius - x, x - (hi - radius), 0)
            cy = max(radius - y, y - (hi - radius), 0)
            if cx * cx + cy * cy <= radius * radius:
                px[y][x] = BG

    # Waveform bars, vertically centered
    mid = size // 2
    bar_w = max(2, round(size * BAR_WIDTH))
    for center_frac, half_frac, rgb in BARS:
        x0 = round(size * center_frac) - bar_w // 2
        half = max(2, round(size * half_frac))
        for yy in range(mid - half, mid + half):
            for xx in range(x0, min(x0 + bar_w, size)):
                px[yy][xx] = (*rgb, 255)

    raw = b"".join(b"\x00" + b"".join(struct.pack("4B", *p) for p in row) for row in px)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)

    ihdr = struct.pack(">IIBBBBB", size, size, 8, 6, 0, 0, 0)
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")


def main() -> None:
    out_dir = Path(__file__).resolve().parent.parent / "icons"
    out_dir.mkdir(exist_ok=True)
    for size in (192, 512):
        path = out_dir / f"icon-{size}.png"
        path.write_bytes(make_icon(size))
        print(f"Wrote {path} ({path.stat().st_size} bytes)")


if __name__ == "__main__":
    main()
