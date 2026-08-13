#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
make_icons.py
Genere des icônes PWA (192x192 et 512x512) sans dependance externe.
Dessine un motif lattice (noeuds + liens) sur fond sombre, accent cyan.
Usage : python3 make_icons.py
"""
import math
import struct
import zlib
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "icons"


def write_png(path: Path, width: int, height: int, pixels: list) -> None:
    """pixels : liste de lignes, chaque ligne = liste de (r,g,b,a)."""
    raw = bytearray()
    for row in pixels:
        raw.append(0)  # filtre : aucun
        for (r, g, b, a) in row:
            raw.extend((r, g, b, a))
    compressed = zlib.compress(bytes(raw), 9)

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data +
                struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    sig = b"\x89PNG\r\n\x1a\n"
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)  # 8-bit RGBA
    png = sig + chunk(b"IHDR", ihdr) + chunk(b"IDAT", compressed) + chunk(b"IEND", b"")
    OUT.mkdir(parents=True, exist_ok=True)
    with open(path, "wb") as fh:
        fh.write(png)
    print(f"ecrit : {path} ({len(png)} octets)")


def lerp(a, b, t):
    return int(a + (b - a) * t)


def blend(bg, fg, alpha):
    return tuple(lerp(b, f, alpha) for b, f in zip(bg, fg))


def draw_icon(size: int, path: Path) -> None:
    # couleurs
    BG = (15, 17, 21)        # #0f1115
    NODE = (124, 196, 255)   # #7cc4ff accent
    LINK = (60, 90, 130)     # lien plus sombre
    BORDER = (38, 43, 54)    # cadre discret

    # grille de noeuds (ex: 5x5)
    n = 5
    margin = size * 0.18
    step = (size - 2 * margin) / (n - 1)
    nodes = []
    for j in range(n):
        for i in range(n):
            x = margin + i * step
            y = margin + j * step
            nodes.append((x, y))

    # precompute distance field pour les liens (anti-aliasing simple)
    pixels = []
    for py in range(size):
        row = []
        for px in range(size):
            r, g, b, a = BG[0], BG[1], BG[2], 255
            # bordure
            edge = min(px, py, size - 1 - px, size - 1 - py)
            if edge < 2:
                t = 1.0 - edge / 2.0
                r, g, b = blend((r, g, b), BORDER, t)
            # liens horizontaux et verticaux entre noeuds adjacents
            # pour chaque lien, distance du point au segment
            min_link_dist = 1e9
            for j in range(n):
                for i in range(n):
                    idx = j * n + i
                    if i < n - 1:  # lien droit
                        x0, y0 = nodes[idx]
                        x1, y1 = nodes[idx + 1]
                        d = _dist_segment(px, py, x0, y0, x1, y1)
                        min_link_dist = min(min_link_dist, d)
                    if j < n - 1:  # lien bas
                        x0, y0 = nodes[idx]
                        x1, y1 = nodes[idx + n]
                        d = _dist_segment(px, py, x0, y0, x1, y1)
                        min_link_dist = min(min_link_dist, d)
                    if i < n - 1 and j < n - 1:  # diagonale
                        x0, y0 = nodes[idx]
                        x1, y1 = nodes[idx + n + 1]
                        d = _dist_segment(px, py, x0, y0, x1, y1)
                        min_link_dist = min(min_link_dist, d)
            link_w = max(1.0, size / 192.0 * 1.6)
            if min_link_dist < link_w:
                t = 1.0 - min_link_dist / link_w
                r, g, b = blend((r, g, b), LINK, t * 0.85)
            # noeuds (disques)
            node_r = max(2.0, size / 192.0 * 4.5)
            for (nx, ny) in nodes:
                d = math.hypot(px - nx, py - ny)
                if d < node_r + 1.5:
                    t = 1.0 - min(d / (node_r + 1.5), 1.0)
                    r, g, b = blend((r, g, b), NODE, t)
            row.append((r, g, b, a))
        pixels.append(row)
    write_png(path, size, size, pixels)


def _dist_segment(px, py, x0, y0, x1, y1):
    dx, dy = x1 - x0, y1 - y0
    if dx == 0 and dy == 0:
        return math.hypot(px - x0, py - y0)
    t = max(0.0, min(1.0, ((px - x0) * dx + (py - y0) * dy) / (dx * dx + dy * dy)))
    proj_x = x0 + t * dx
    proj_y = y0 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


if __name__ == "__main__":
    draw_icon(192, OUT / "icon-192.png")
    draw_icon(512, OUT / "icon-512.png")
    print("OK")
