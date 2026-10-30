"""Decompose the visible objects into 8x8 cells of the $C400 pattern bank.

  python3 tools/decompose.py <dumpdir> <outdir>

For every hand-picked object box this prints, cell by cell, the pattern code
that reproduces that 8x8 block of the screen (all 8 vertical rotations are
tried, because the blitter may start in the middle of a tile) and writes

  <outdir>/05_object_cells.png   crop / rebuilt-from-codes / code grid
"""
import sys

from PIL import Image, ImageDraw

BASE = 0x4000
OBJECTS = [
    ('crystal_small', 63, 83, 85, 98),
    ('crystal_left', 53, 86, 67, 100),
    ('plate', 83, 99, 101, 114),
    ('block_near', 62, 145, 87, 164),
    ('block_edge', 0, 128, 13, 147),
]


def main(dumpdir, outdir):
    d = open(dumpdir + '/all.bin', 'rb').read()

    def b(a):
        return d[a - BASE]

    def cell_bytes(x0, y0):
        """8 bytes of the 8x8 block at pixel (x0, y0), top row first."""
        cx = x0 >> 3
        return [b(0x4000 + ((y0 + r) & 0xC0) * 32 + ((y0 + r) & 7) * 256
                  + ((y0 + r) & 0x38) * 4 + cx) for r in range(8)]

    bank = {}
    for code in range(64):
        bank[tuple(b(0xC400 + code * 8 + r) for r in range(8))] = code

    def match(rows):
        """-> (code, rot, err) with rows rotated so that rot=0 is exact."""
        best = None
        for rot in range(8):
            probe = tuple(rows[(r - rot) % 8] for r in range(8))
            code = bank.get(probe)
            err = 0 if code is not None else 99
            if code is None:          # nearest, for diagnostics only
                bc, be = None, 1e9
                for c in range(64):
                    e = sum(bin(b(0xC400 + c * 8 + k) ^ probe[k]).count('1')
                            for k in range(8))
                    if e < be:
                        bc, be = c, e
                code, err = bc, be
            if best is None or err < best[2]:
                best = (code, rot, err)
        return best

    zoom = 6
    tiles = []
    for name, x0, y0, x1, y1 in OBJECTS:
        x0 -= x0 % 8
        y0 -= y0 % 8
        x1 += (8 - x1 % 8) % 8
        y1 += (8 - y1 % 8) % 8
        cw, ch = (x1 - x0) // 8, (y1 - y0) // 8
        grid, rebuilt = [], []
        print('--- %s  %dx%d cells at (%d,%d)' % (name, cw, ch, x0, y0))
        for cy in range(ch):
            line, rline = [], []
            for cx in range(cw):
                rows = cell_bytes(x0 + cx * 8, y0 + cy * 8)
                code, rot, err = match(rows)
                grid.append((code, rot, err))
                line.append('%02X%s/%d' % (code, '!' if err else ' ', rot))
                rline.append(code)
            print('   ' + ' '.join(line))
            rebuilt.append(rline)
        tiles.append((name, (x0, y0, x1, y1), grid, cw, ch, rebuilt))

    W = sum(t[3] * 8 * zoom + 20 for t in tiles)
    H = max(t[4] for t in tiles) * 8 * zoom + 90
    img = Image.new('RGB', (W, H), (0, 0, 0))
    dr = ImageDraw.Draw(img)
    px = 4
    for name, (x0, y0, x1, y1), grid, cw, ch, rebuilt in tiles:
        crop = Image.new('RGB', (x1 - x0, y1 - y0))
        for y in range(y0, y1):
            for x in range(x0, x1):
                a = b(0x5800 + (y >> 3) * 32 + (x >> 3))
                ink = (a & 7)
                pap = (a >> 3) & 7
                pal = [(0, 0, 0), (0, 0, 205), (205, 0, 0), (205, 0, 205),
                       (0, 205, 0), (0, 205, 205), (205, 205, 0), (205, 205, 205)]
                bit = (b(0x4000 + (y & 0xC0) * 32 + (y & 7) * 256
                         + (y & 0x38) * 4 + (x >> 3)) >> (7 - (x & 7))) & 1
                c = pal[ink if bit else pap]
                if (a >> 6) & 1:
                    c = tuple(min(255, v + 50) for v in c)
                crop.putpixel((x - x0, y - y0), c)
        big = crop.resize((crop.width * zoom, crop.height * zoom),
                          Image.NEAREST)
        img.paste(big, (px, 20))
        dr.text((px, 4), '%s  (%d,%d)' % (name, x0, y0), fill=(255, 255, 255))
        # code labels under each cell
        for cy in range(ch):
            for cx in range(cw):
                code, rot, err = grid[cy * cw + cx]
                col = (255, 255, 255) if not err else (255, 120, 120)
                dr.text((px + cx * 8 * zoom + 2,
                         24 + crop.height * zoom + cy * 9),
                        '%02X/%d' % (code, rot), fill=col)
        px += crop.width * zoom + 20
    img.save(outdir + '/05_object_cells.png')
    print('wrote', outdir + '/05_object_cells.png')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
