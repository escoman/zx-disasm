"""Render one display-list element (a unit) exactly as the player sees it.

  python3 tools/unit_shot.py <capture.json> <pass> <element> <out.png> [--bank b.bin]
  python3 tools/unit_shot.py <capture.json> <pass>                    # list all

The frame order is: the engine composites every object into the ring ($EAE3:
`ring = (ring & mask) | value`), the blitter at $D35E paints the ring onto the
screen, and only *after* that $DD6C restores the backgrounds it had saved into
the $F800 stream ($DD44) - so between two blits the ring holds terrain only.
That means the ring captured at $D3F2 is precisely what the same pass puts on
the screen: no delay, no extra pass, and `tools/ringscreen.py` maps it byte for
byte (verified: 51/51, 84/84 ... every byte of every element equals the screen).

A $FF00 element is `[0] class, [1] count, [2..3] ring address, [4..5] shape`.
The shape is `count` columns of `width` (AND-mask, OR-value) byte pairs,
column-major, stride 2*width; consecutive columns of the sprite are two ring
pages apart and four ring offsets back ($1FC), which cancels the blitter's
shear - so the sprite stands upright on the screen, `width` pixel rows tall and
`count*8` pixels wide, bank row 0 being the bottom row.

Panels, magnified `--scale`, cropped to the footprint - a 2x2 grid when
--bank is given, otherwise just the two right-hand ones:

    sprite      the shape over a flat grey ground, real ink/paper
    screen      the real screen of that pass, with the real attributes
    mask/value  white = painted 1, grey = painted 0, black = transparent
    model       the same crop rebuilt purely from the model - the ring bytes
                the walk maps under the element's footprint
"""
import argparse
import json
import sys

sys.path.insert(0, __file__.rsplit('/', 1)[0])
import patchpass
import ringscreen as rs
from PIL import Image, ImageDraw

PAL = [(0, 0, 0), (0, 0, 215), (215, 0, 0), (215, 0, 215), (0, 215, 0),
       (0, 215, 215), (215, 215, 0), (215, 215, 215), (0, 0, 0), (0, 0, 255),
       (255, 0, 0), (255, 0, 255), (0, 255, 0), (0, 255, 255), (255, 255, 0),
       (255, 255, 255)]
STRIDE = 0x1FC


def scr_off(x, y):
    """Screen offset of pixel column x, row y.  Inside a character row the 8
    scanlines are 256 bytes apart and the 32 byte columns 32 bytes apart."""
    return ((y >> 6) & 3) * 2048 + (y & 7) * 256 + ((y >> 3) & 7) * 32 + x


def ring_to_screen(walk, c32e, c32f):
    """{ring_addr: (x, y)} for the bytes this pass displays."""
    out = {}
    for scr, block, off, length in walk:
        page = (c32e + (c32f & 0x7F) + 2 * block) % 64
        src = 0x8000 + page * 256 + ((off + c32f) & 0xFF)
        for k in range(length):
            a = scr - 0x100 * k
            if 0x4000 <= a < 0x5800:
                out[src + k] = rs.screen_xy(a)
    return out


def footprint(p, s2, ring):
    """{(x, y): byte} - the element as it really lands on the screen."""
    pts = {}
    for j in range(p['count']):
        base = (p['dest'] - 0x8000 + j * STRIDE) & 0x3FFF
        for i in range(p['width']):
            a = 0x8000 + ((base + i) & 0x3FFF)
            xy = s2.get(a)
            if xy is not None:
                pts[xy] = ring[(a - 0x8000) & 0x3FFF]
    return pts


def load_bank(path):
    """bankdump.py output -> addressable memory over the whole Z80 space.

    The file holds the $4000-$7FFF page followed by $C000-$FFFF; $0000-$3FFF
    and the ring are zero-filled (they are read from the capture instead).
    """
    d = open(path, 'rb').read()
    return ([0] * 0x4000 + list(d[:0x4000]) + [0] * 0x4000
            + list(d[0x4000:0x8000]))


def shape_of(bank, p):
    """{(col, row): (mask, value)}, row 0 = bottom row of the sprite."""
    return {(j, i): (bank[p['ptr'] + j * 2 * p['width'] + 2 * i],
                     bank[p['ptr'] + j * 2 * p['width'] + 2 * i + 1])
            for j in range(p['count']) for i in range(p['width'])}


def render(px, scr, attr, x0, x1, y0, y1, scale, force=None, offset=(0, 0)):
    """Blit byte columns [x0..x1), pixel rows [y0..y1) into `px`."""
    ox, oy = offset
    for y in range(y0, y1):
        for bx in range(x0, x1):
            o = scr_off(bx, y)
            if force is None:
                b = scr[o]
                att = attr[(y // 8) * 32 + bx]
                br = 8 if att & 0x80 else 0
                ink, paper = PAL[(att & 7) + br], PAL[(att >> 3) & 7]
            else:
                b = force.get((bx, y), 0)
                ink, paper = (255, 255, 255), (0, 0, 0)
            for k in range(8):
                col = ink if (b >> (7 - k)) & 1 else paper
                for sy in range(scale):
                    for sx in range(scale):
                        px[ox + (bx - x0) * 8 * scale + k * scale + sx,
                           oy + (y - y0) * scale + sy] = col


def render_shape_img(shape, p, scale):
    """The bare art: white = painted 1, grey = painted 0, black = transparent."""
    img = Image.new('RGB', (p['count'] * 8 * scale, p['width'] * scale),
                    (0, 0, 0))
    px = img.load()
    for (j, i), (m, v) in shape.items():
        y = p['width'] - 1 - i
        for k in range(8):
            if (m >> (7 - k)) & 1:
                col = (0, 0, 0)
            else:
                col = (255, 255, 255) if (v >> (7 - k)) & 1 else (120, 120, 120)
            for sy in range(scale):
                for sx in range(scale):
                    px[j * 8 * scale + k * scale + sx, y * scale + sy] = col
    return img


def render_art_flat(bank, p, s2, attr, scale, bg=(128, 128, 128)):
    """The sprite on its own, painted with the ink/paper it really uses.

    `mask = 1` leaves the flat background, so this is the unit stripped of the
    terrain - the panel that answers "what does this unit actually look like".
    """
    img = Image.new('RGB', (p['count'] * 8 * scale, p['width'] * scale), bg)
    px = img.load()
    for j in range(p['count']):
        base = (p['dest'] - 0x8000 + j * STRIDE) & 0x3FFF
        for i in range(p['width']):
            xy = s2.get(0x8000 + ((base + i) & 0x3FFF))
            if xy is None:
                continue
            att = attr[((xy[1] // 8) * 32) + xy[0]]
            br = 8 if att & 0x80 else 0
            ink = PAL[(att & 7) + br]
            paper = PAL[(att >> 3) & 7]
            m = bank[p['ptr'] + j * 2 * p['width'] + 2 * i]
            v = bank[p['ptr'] + j * 2 * p['width'] + 2 * i + 1]
            y = p['width'] - 1 - i
            for k in range(8):
                if (m >> (7 - k)) & 1:
                    continue
                col = ink if (v >> (7 - k)) & 1 else paper
                for sy in range(scale):
                    for sx in range(scale):
                        px[j * 8 * scale + k * scale + sx,
                           y * scale + sy] = col
    return img


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('capture')
    ap.add_argument('pass', type=int, help='index of the pass in the capture')
    ap.add_argument('element', type=int, nargs='?')
    ap.add_argument('out', nargs='?')
    ap.add_argument('--bank', help='bankdump.py output, adds the bare art panel')
    ap.add_argument('--scale', type=int, default=4)
    a = ap.parse_args()
    d = json.load(open(a.capture))
    n = getattr(a, 'pass')
    fr = d['frames'][n]
    pre = fr['pre']
    walk = d['blit']['walk']
    s2 = ring_to_screen(walk, pre['c32e'], pre['c32f'])
    ring, patches, used = patchpass.replay(pre)
    bank = load_bank(a.bank) if a.bank else None
    if a.element is None:
        for eno, p in enumerate(patches):
            pts = footprint(p, s2, pre['ring'])
            if not pts:
                continue
            xs = [q[0] for q in pts]
            ys = [q[1] for q in pts]
            bits = sum(v.bit_count() for v in pts.values())
            print('#%02d %s cls=%02X cnt=%d w=%02X dest=%04X ptr=%04X  '
                  'отображ. %d/%d  x %d..%d y %d..%d  бит %d'
                  % (eno, p['list'], p['class'], p['count'], p['width'],
                     p['dest'], p.get('ptr', 0), len(pts),
                     p['count'] * p['width'], min(xs), max(xs), min(ys),
                     max(ys), bits))
        return
    p = patches[a.element]
    print('#%02d %s cls=%02X count=%d width=%d dest=%04X ptr=%04X  кадр %d '
          '(C32E=%02X C32F=%02X)' % (a.element, p['list'], p['class'],
                                     p['count'], p['width'], p['dest'],
                                     p.get('ptr', 0), fr['frame'],
                                     pre['c32e'], pre['c32f']))
    pts = footprint(p, s2, pre['ring'])
    wrong = 0
    real = bytes(pre['screen'])
    for (x, y), v in pts.items():
        if real[scr_off(x, y)] != v:
            wrong += 1
    print('   байт %d, на экране %d, не совпало с реальным кадром %d'
          % (p['count'] * p['width'], len(pts), wrong))
    if bank is not None:
        shape = shape_of(bank, p)
        bad = 0
        for j in range(p['count']):
            base = (p['dest'] - 0x8000 + j * STRIDE) & 0x3FFF
            for i in range(p['width']):
                m, v = shape[(j, i)]
                if (p['data'][j * p['width'] + i] & m) | v != \
                        pre['ring'][(base + i) & 0x3FFF]:
                    bad += 1
        print('   форма %04X: (фон & маска) | значение != кадр в %d/%d байт'
              % (p['ptr'], bad, len(shape)))
    if not pts:
        return
    xs = [q[0] for q in pts]
    ys = [q[1] for q in pts]
    pad = 2
    x0, x1 = max(0, min(xs) - pad), min(32, max(xs) + 1 + pad)
    y0, y1 = max(0, min(ys) - 8 * pad), min(192, max(ys) + 1 + 8 * pad)
    sc = a.scale
    gap = 6
    w, h = (x1 - x0) * 8 * sc, (y1 - y0) * sc
    model = Image.new('RGB', (w, h))
    render(model.load(), None, None, x0, x1, y0, y1, sc, force=pts)
    img = Image.new('RGB', (w, h * 2 + gap), (40, 40, 40))
    render(img.load(), real, pre['attr'], x0, x1, y0, y1, sc)
    img.paste(model, (0, h + gap))
    if bank is not None:
        #   +-----------------+-----------------+
        #   | sprite, flat bg | real screen     |
        #   +-----------------+-----------------+
        #   | mask/value art  | rebuilt model   |
        #   +-----------------+-----------------+
        art = render_shape_img(shape, p, sc)
        flat = render_art_flat(bank, p, s2, pre['attr'], sc)
        col = max(flat.width, art.width) + gap
        full = Image.new('RGB', (col + w, h * 2 + gap), (40, 40, 40))
        full.paste(flat, (0, 0))
        full.paste(art, (0, h + gap))
        render(full.load(), real, pre['attr'], x0, x1, y0, y1, sc,
               offset=(col, 0))
        full.paste(model, (col, h + gap))
        dr = ImageDraw.Draw(full)
        dr.text((2, 2), 'sprite', fill=(255, 255, 0))
        dr.text((col + 2, 2), 'screen', fill=(255, 255, 0))
        dr.text((2, h + gap + 2), 'mask/value', fill=(255, 255, 0))
        dr.text((col + 2, h + gap + 2), 'model', fill=(255, 255, 0))
        img = full
    if a.out:
        img.save(a.out)
    print('   x %d..%d (px %d..%d)  y %d..%d -> %s'
          % (min(xs), max(xs), min(xs) * 8, max(xs) * 8 + 7, min(ys), max(ys),
             a.out))


if __name__ == '__main__':
    main()
