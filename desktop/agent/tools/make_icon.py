"""Generates the Home Screen icon (app/web/icon-180.png) with the standard library only.

Run from desktop/agent:  python tools/make_icon.py
"""

from __future__ import annotations

import struct
import zlib
from pathlib import Path

SIZE = 180
TOP, BOTTOM = (76, 194, 255), (43, 108, 255)
TILE = (255, 255, 255)


def pixel(x: int, y: int) -> tuple[int, int, int]:
    # A 2x2 grid of rounded tiles on a diagonal gradient, matching the in-app logo.
    for cx in (62, 118):
        for cy in (62, 118):
            dx, dy = abs(x - cx), abs(y - cy)
            if dx <= 20 and dy <= 20 and not (dx > 13 and dy > 13 and (dx - 13) ** 2 + (dy - 13) ** 2 > 49):
                return TILE
    t = (x + y) / (2 * SIZE)
    return tuple(round(a + (b - a) * t) for a, b in zip(TOP, BOTTOM, strict=True))  # type: ignore[return-value]


def chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def main() -> None:
    rows = b"".join(b"\x00" + bytes(c for x in range(SIZE) for c in pixel(x, y)) for y in range(SIZE))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", SIZE, SIZE, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(rows, 9))
        + chunk(b"IEND", b"")
    )
    target = Path(__file__).resolve().parent.parent / "app" / "web" / "icon-180.png"
    target.write_bytes(png)
    print(f"wrote {target} ({len(png)} bytes)")


if __name__ == "__main__":
    main()
