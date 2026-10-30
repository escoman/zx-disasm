"""Re-synthesise the H.A.T.E. renderer from a frame dump and export PNGs.

  python3 tools/render_objects.py <dumpdir> <outdir>

<dumpdir> must contain all.bin (a full 4000-FFFF RAM dump taken at a frame
boundary, e.g. written by hate_run.py with the 'all' region) and, optionally,
screen.png (the visible frame).  The script writes:

  00_reference_screen.png   the real frame, 2x                (reference)
  01_pattern_bank_C400.png  the 64 8x8 patterns of $C400      ($C400-$C5FF)
  02_ring_page.png          one ring page as the engine builds it: the 8-byte
                            special tile + 15 cells of one landscape column,
                            plus the 2-bit-shifted copy in the upper half
  03_terrain_silhouette.png the cells the blitter paints, decoded from the
                            pop-address list $D5B2, over the reference frame
  10_objects_montage.png    the game's objects, 8x8 cell grid drawn over them
  20_object_*.png           one file per object, 8x colour crops

Everything is rebuilt from RAM only (no hand-drawn artwork), so the images can
be checked against the engine source in asm/HATE_engine.asm.
"""
import os
import sys

from PIL import Image, ImageDraw

BASE = 0x4000


def load(path):
    with open(path, 'rb') as f:
        return f.read()


class Mem:
    def __init__(self, data):
        self.d = data

    def b(self, addr):
        return self.d[addr - BASE]

    def bytes(self, addr, n):
        return self.d[addr - BASE:addr - BASE + n]

    def w(self, addr):
        return self.b(addr) | (self.b(addr + 1) << 8)

    def scr(self, x, y):
        return self.b(0x4000 + (y & 0xC0) * 32 + (y & 7) * 256
                      + (y & 0x38) * 4 + x)


def addr_yx(addr):
    """ZX screen byte address -> (byte column x, pixel row y)."""
    o = addr - 0x4000
    if not 0 <= o < 0x1B00:
        return None
    y = ((o >> 11) & 0x03) * 64 | ((o >> 8) & 0x07) | ((o >> 5) & 0x07) * 8
    return o & 0x1F, y


# ---------------------------------------------------------------- pattern bank

def pattern(mem, code, row):
    """One 8-pixel line of pattern <code> (row 0 = bottom line of the cell:
    the blitter stores the first byte at the cell's bottom row and walks up
    with DEC H, see $D38A)."""
    return mem.b(0xC400 + code * 8 + row)


def paste_pattern(img, mem, code, x0, y0, bottom_up=True):
    for row in range(8):
        v = pattern(mem, code, row)
        y = y0 + (7 - row if bottom_up else row)
        for bit in range(8):
            if (v >> (7 - bit)) & 1:
                img.putpixel((x0 + bit, y), 255)


def draw_pattern_bank(mem, path):
    zoom = 4
    cell = 10
    img = Image.new('L', (8 * cell * zoom, 8 * cell * zoom), 70)
    for code in range(64):
        x0 = (code % 8) * cell * zoom
        y0 = (code // 8) * cell * zoom
        sub = Image.new('L', (8, 8), 0)
        paste_pattern(sub, mem, code, 0, 0)
        img.paste(sub.resize((8 * zoom, 8 * zoom), Image.NEAREST),
                  (x0 + zoom, y0 + zoom))
    d = ImageDraw.Draw(img)
    for code in range(64):
        x0 = (code % 8) * cell * zoom
        y0 = (code // 8) * cell * zoom
        d.text((x0 + zoom, y0 + cell * zoom - 8), '%02X' % code, fill=255)
    img.save(path)


# ------------------------------------------------------------- the ring page

def special_tile(mem):
    """$D51D-$D544: the 8 bytes copied in front of the 15 cells.  The routine
    reads $C4B8, but patches L to $C0 (i.e. $C4C0) when the phase index is 0."""
    phase = ((mem.b(0xC32E) >> 1) + 1) & 0x03
    return mem.bytes(0xC4B8 if phase else 0xC4C0, 8)


def shift2(src, count=127):
    """$D440: RRA / RR C / RRA / RR C - a 2-bit right shift of the byte stream."""
    out = bytearray(src)
    carry = 0
    for n in range(count):
        a = src[1 + n]
        c = src[n]
        c1 = ((c << 1) & 0xFF) | carry
        carry = (c >> 7) & 1
        a1 = ((a << 1) & 0xFF) | carry
        carry = (a >> 7) & 1
        c2 = ((c1 << 1) & 0xFF) | carry
        carry = (c1 >> 7) & 1
        a2 = ((a1 << 1) & 0xFF) | carry
        carry = (a1 >> 7) & 1
        out[n] = a2
    return bytes(out)


def bank_index(mem):
    idx = {}
    for code in range(64):
        idx.setdefault(bytes(mem.bytes(0xC400 + code * 8, 8)), code)
    return idx


def draw_ring_page(mem, path):
    """Show the page the engine composes at $8000+($C32E): slot 0 = the special
    tile, slots 1..15 = the 15 cell codes of one landscape column read from the
    column record ($CA43), and the shifted copy at offset $80."""
    idx = bank_index(mem)
    scores = {}
    for p in range(0x80, 0xC0):
        cand = mem.bytes(p * 0x100, 0x100)
        got = [idx.get(bytes(cand[s * 8:s * 8 + 8])) for s in range(16)]
        scores[p] = (sum(1 for c in got if c is not None), cand, got)
    page = 0x80 + (mem.b(0xC32E) & 0x3F)
    if scores[page][0] < max(v[0] for v in scores.values()):
        page = max(scores, key=lambda p: scores[p][0])
    _, raw, codes = scores[page]

    zoom = 3
    cell = 8 * zoom
    img = Image.new('RGB', (2 * (cell + 44), 16 * cell + 48), (0, 0, 0))
    d = ImageDraw.Draw(img)
    # the 16 tiles of the page are one landscape column, far cell on top
    for half in (0, 1):
        x0 = 6 + half * (cell + 44)
        for slot in range(16):
            tile = Image.new('L', (8, 8), 0)
            for row in range(8):
                v = raw[half * 0x80 + slot * 8 + row]
                for bit in range(8):
                    if (v >> (7 - bit)) & 1:
                        tile.putpixel((bit, 7 - row), 255)
            img.paste(tile.resize((cell, cell), Image.NEAREST).convert('RGB'),
                      (x0, 26 + slot * cell))
        label = 'half $00' if half == 0 else 'half $80 (+2px)'
        d.text((x0, 2), label, fill=(255, 255, 255))
        d.text((x0, 13), 'page %02X' % page, fill=(180, 180, 180))
    d.text((2, 16 * cell + 36),
           ' '.join('--' if c is None else '%02X' % c for c in codes),
           fill=(255, 220, 0))
    img.save(path)
    return page, codes


# ----------------------------------------------------- the blitter's cell list

def pop_list(mem, start=0xD5B2, end=0xE300):
    """$D5B2 is popped straight into HL by the blitter ($D365/$D378): every
    word is a ZX screen address ($0800 means 'write into ROM and forget')."""
    out = []
    for addr in range(start, end, 2):
        v = mem.w(addr)
        if v == 0xFFFF:
            break
        out.append((addr, v))
    return out


def cells_per_column(pops):
    """The list advances one screen byte column at a time; every run is the
    bottom (or top) address of an 8-line cell."""
    cols = {}
    for i, (taddr, v) in enumerate(pops):
        if v == 0x0800:
            continue
        yx = addr_yx(v)
        if not yx:
            continue
        cols.setdefault(yx[0], []).append(yx[1])
    return cols


def draw_silhouette(mem, path, refpath):
    zoom = 2
    if refpath and os.path.exists(refpath):
        ref = Image.open(refpath).convert('RGB').resize((256, 192),
                                                       Image.NEAREST)
        img = ref.point(lambda v: v // 3)
    else:
        img = Image.new('RGB', (256, 192), (0, 0, 0))
    img = img.resize((256 * zoom, 192 * zoom), Image.NEAREST)
    d = ImageDraw.Draw(img)
    pops = pop_list(mem)
    cols = cells_per_column(pops)
    for x, rows in cols.items():
        for y in rows:
            # x is the byte column of the screen, i.e. 8 pixels
            d.rectangle([x * 8 * zoom, (y - 7) * zoom,
                         (x + 1) * 8 * zoom - 1, (y + 1) * zoom - 1],
                        outline=(255, 0, 0))
    d.text((4, 4), '$D5B2: %d runs, %d columns, 1 cell = 8x8'
           % (len(pops), len(cols)), fill=(255, 255, 0))
    d.text((4, 16), 'the shape of the road comes from the cell codes, '
           'not from this list', fill=(255, 255, 0))
    img.save(path)


# -------------------------------------------------------------- the objects

# Hand-picked in the reference frame (ZX pixel coordinates).  The models are
# drawn straight into the band ring, so they cannot be separated automatically;
# the boxes below were read off the frame with an 8-pixel grid overlay.
OBJECTS = [
    ('crystal_small', 63, 83, 85, 98),
    ('crystal_left', 53, 86, 67, 100),
    ('plate', 83, 99, 101, 114),
    ('block_near', 62, 145, 87, 164),
    ('block_edge', 0, 128, 13, 147),
]


def cut_object(mem, box, pad=1):
    x0, y0, x1, y1 = box
    x0 = max(0, x0 - pad)
    y0 = max(0, y0 - pad)
    x1 = min(255, x1 + pad)
    y1 = min(191, y1 + pad)
    img = Image.new('L', (x1 - x0 + 1, y1 - y0 + 1), 0)
    for y in range(y0, y1 + 1):
        for x in range(x0, x1 + 1):
            if (mem.scr(x, y) >> (7 - (x & 7))) & 1:
                img.putpixel((x - x0, y - y0), 255)
    return img, (x0, y0, x1, y1)


def draw_objects(mem, outdir, refpath):
    zoom = 8
    ref = Image.open(refpath).convert('RGB').resize((256, 192),
                                                    Image.NEAREST) if refpath else None
    tiles = []
    for i, (name, x0, y0, x1, y1) in enumerate(OBJECTS):
        box = (max(0, x0 - 1), max(0, y0 - 1),
               min(255, x1 + 1), min(191, y1 + 1))
        mono, cropped = cut_object(mem, box, pad=0)
        if ref is not None:
            img = ref.crop(cropped)
            img = img.resize((img.width * zoom, img.height * zoom),
                             Image.NEAREST)
        else:
            img = mono.resize((mono.width * zoom, mono.height * zoom),
                              Image.NEAREST).convert('RGB')
        img.save(os.path.join(outdir, '2%d_%s.png' % (i, name)))
        tiles.append((name, img, cropped))

    # the montage: 8x colour crops with the 8x8 cell grid of the renderer
    gap = 10
    tw = sum(t[1].width for t in tiles) + gap * (len(tiles) + 1)
    th = max(t[1].height for t in tiles) + 34
    mont = Image.new('RGB', (tw, th), (0, 0, 0))
    d = ImageDraw.Draw(mont)
    x = gap
    for name, img, cropped in tiles:
        mont.paste(img, (x, 24))
        step = 8 * zoom
        # grid aligned to the screen's own cell origin, not to the crop
        gx = (-cropped[0] % 8) * zoom
        while gx < img.width:
            d.line([(x + gx, 24), (x + gx, 24 + img.height)], fill=(90, 0, 0))
            gx += step
        gy = (-cropped[1] % 8) * zoom
        while gy < img.height:
            d.line([(x, 24 + gy), (x + img.width, 24 + gy)], fill=(90, 0, 0))
            gy += step
        cm = (cropped[2] - cropped[0] + 8) // 8
        cr = (cropped[3] - cropped[1] + 8) // 8
        d.text((x, 2), '%s  x=%d y=%d' % (name, cropped[0], cropped[1]),
               fill=(255, 255, 255))
        d.text((x, 12), '%dx%d cells' % (cm, cr), fill=(180, 180, 180))
        x += img.width + gap
    mont.save(os.path.join(outdir, '10_objects_montage.png'))


def main():
    dumpdir, outdir = sys.argv[1], sys.argv[2]
    os.makedirs(outdir, exist_ok=True)
    mem = Mem(load(os.path.join(dumpdir, 'all.bin')))

    ref = os.path.join(dumpdir, 'screen.png')
    have_ref = os.path.exists(ref)
    if have_ref:
        Image.open(ref).convert('RGB').save(
            os.path.join(outdir, '00_reference_screen.png'))

    draw_pattern_bank(mem, os.path.join(outdir, '01_pattern_bank_C400.png'))
    page, codes = draw_ring_page(mem, os.path.join(outdir, '02_ring_page.png'))
    draw_silhouette(mem, os.path.join(outdir, '03_terrain_silhouette.png'),
                    ref if have_ref else None)
    draw_objects(mem, outdir, ref if have_ref else None)
    print('ring page %02X cells:' % page,
          ' '.join('--' if c is None else '%02X' % c for c in codes))
    print('wrote', outdir)


if __name__ == '__main__':
    main()
