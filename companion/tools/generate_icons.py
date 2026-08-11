#!/usr/bin/env python3
"""Generate the PWA icons (pure Python, no Pillow): dark rounded square
with a teal waveform glyph, at 192x192 and 512x512."""

import struct
import zlib
from pathlib import Path

BG = (15, 17, 21, 255)  # --bg
PANEL = (25, 29, 36, 255)  # --panel
ACCENT = (45, 212, 191, 255)  # --accent teal

# Symmetric bar heights (fraction of half-height) forming a waveform glyph
BARS = [0.18, 0.42, 0.30, 0.72, 0.95, 0.55, 0.80, 0.38, 0.60, 0.25, 0.45, 0.15]


def make_icon(size: int) -> bytes:
    px = [[BG] * size for _ in range(size)]

    # Rounded-rect panel inset
    inset = size // 16
    radius = size // 6
    lo, hi = inset, size - inset
    for y in range(lo, hi):
        for x in range(lo, hi):
            # rounded corner check
            cx = max(lo + radius - x, x - (hi - 1 - radius), 0)
            cy = max(lo + radius - y, y - (hi - 1 - radius), 0)
            if cx * cx + cy * cy <= radius * radius:
                px[y][x] = PANEL

    # Waveform bars
    n = len(BARS)
    span = hi - lo
    bar_w = max(2, int(span * 0.045))
    gap = (span - n * bar_w) // (n + 1)
    mid = size // 2
    half = int(span * 0.36)
    x = lo + gap
    for h in BARS:
        bh = max(2, int(half * h))
        for yy in range(mid - bh, mid + bh):
            for xx in range(x, min(x + bar_w, hi)):
                px[yy][xx] = ACCENT
        x += bar_w + gap

    # Encode PNG
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
