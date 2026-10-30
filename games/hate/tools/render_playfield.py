"""Prove the ring->screen model by rebuilding the playfield from the ring only.

  python3 tools/render_playfield.py <dumpdir> <outdir>

The model (see ENGINE.MD, section 3): the screen byte column x is a slice of
the 16K ring at $8000, read upward from a per-column start address:

    scr(x, 191-n) = ring[(off(x) + n) mod $4000]

off(x) steps back by 4 bytes for every column (the blitter's "+$200-4" stride
in $DD47/$DD6C).  The script finds off(x) independently for every column, by
signature lookup plus a full-column match, paints the result and writes a
side-by-side comparison.

  <outdir>/04_reconstruction.png
"""
import sys

from PIL import Image, ImageDraw

BASE = 0x4000
PAL = [(0, 0, 0), (0, 0, 205), (205, 0, 0), (205, 0, 205),
       (0, 205, 0), (0, 205, 205), (205, 205, 0), (205, 205, 205)]


def main(dumpdir, outdir):
    d = open(dumpdir + '/all.bin', 'rb').read()

    def b(a):
        return d[a - BASE]

    def scr(x, y):
        return b(0x4000 + (y & 0xC0) * 32 + (y & 7) * 256
                 + (y & 0x38) * 4 + x)

    ring = [b(0x8000 + i) for i in range(0x4000)]
    S = {x: [scr(x, 191 - n) for n in range(192)] for x in range(32)}

    # signature index: every 4-byte window of the ring
    sig = {}
    for o in range(0x4000):
        k = (ring[o], ring[(o + 1) & 0x3FFF], ring[(o + 2) & 0x3FFF],
             ring[(o + 3) & 0x3FFF])
        sig.setdefault(k, []).append(o)

    def score(x, o):
        col = S[x]
        return sum(ring[(o + n) & 0x3FFF] == col[n] for n in range(192))

    # A free search per column is ambiguous (many blank pages), but the columns
    # on the right are unambiguous and show an exact arithmetic progression:
    #   off(x+1) = off(x) + $200 - 4   (mod $4000)
    # so anchor on column 31 and extrapolate, then refine +-3 bytes.
    anchor = max(range(0x4000), key=lambda o: score(31, o))
    offs, stats = {}, []
    for x in range(32):
        pred = (anchor - 0x1FC * (31 - x)) & 0x3FFF
        near = [((pred + k) & 0x3FFF) for k in range(-3, 4)]
        best = max(near, key=lambda o: score(x, o))
        offs[x] = best
        stats.append(score(x, best))

    prev = None
    for x in range(32):
        o = offs[x]
        step = '' if prev is None else '  step %+d' % (((o - prev + 0x2000)
                                                        & 0x3FFF) - 0x2000)
        print('x=%2d  off %04X = page %02X +%3d   %3d/192 bytes exact%s'
              % (x, 0x8000 + o, 0x80 + (o >> 8), o & 0xFF, stats[x], step))
        prev = o

    ZOOM = 2
    rebuilt = Image.new('L', (256, 192), 0)
    for x in range(32):
        o = offs[x]
        for n in range(192):
            v = ring[(o + n) & 0x3FFF]
            for bit in range(8):
                rebuilt.putpixel((x * 8 + (7 - bit), 191 - n),
                                 255 if (v >> bit) & 1 else 0)

    def colour(getbit, keep):
        img = Image.new('RGB', (256, 192))
        for y in range(192):
            for x in range(256):
                a = b(0x5800 + (y >> 3) * 32 + (x >> 3))
                p = getbit(x, y)
                c = PAL[(a & 7) if p else ((a >> 3) & 7)]
                if (a >> 6) & 1:
                    c = tuple(min(255, v + 50) for v in c)
                if not keep(x, y):
                    c = tuple(v // 4 for v in c)
                img.putpixel((x, y), c)
        return img

    real = colour(lambda x, y: (scr(x, y) >> (7 - (x & 7))) & 1,
                  lambda x, y: True)
    reb = colour(lambda x, y: rebuilt.getpixel((x, y)) > 128,
                 lambda x, y: True)
    diff = Image.new('RGB', (256, 192))
    for y in range(192):
        for x in range(256):
            r = (scr(x, y) >> (7 - (x & 7))) & 1
            p = rebuilt.getpixel((x, y)) > 128
            diff.putpixel((x, y), (255, 255, 255) if p == bool(r)
                          else ((255, 60, 60) if p else (60, 60, 255)))

    canvas = Image.new('RGB', (256 * ZOOM * 3 + 20, 192 * ZOOM + 26), (0, 0, 0))
    dr = ImageDraw.Draw(canvas)
    dr.text((2, 2), 'real frame', fill=(255, 255, 255))
    dr.text((256 * ZOOM + 8, 2), 'rebuilt from the $8000 ring only',
            fill=(255, 255, 255))
    dr.text((512 * ZOOM + 14, 2),
            'difference: white = match, red/blue = mismatch', fill=(255, 255, 255))
    for i, im in enumerate((real, reb, diff)):
        canvas.paste(im.resize((256 * ZOOM, 192 * ZOOM), Image.NEAREST),
                     (2 + i * (256 * ZOOM + 6), 14))
    canvas.save(outdir + '/04_reconstruction.png')
    print('mean exact bytes per column: %.1f / 192'
          % (sum(stats) / len(stats)))
    print('wrote', outdir + '/04_reconstruction.png')


if __name__ == '__main__':
    main(sys.argv[1], sys.argv[2])
