"""Un-shear the ring into the screen using the *measured* blitter walk.

  python3 tools/ringscreen.py <capture.json> [frame-index] [-png out.png]

The blitter at $D35E is a straight-line copier with a fixed walk:

    LD SP,$D5B2                  } the POP list is engine code, never changes
    D = $80 + C32E + (C32F & 7F) } ring page cursor
    E = C32F                     } ring offset cursor, +1 per byte
    16 x ( POP HL                } screen cursor, from the fixed list
             4 or 8 bytes at HL, HL-100, HL-200, ...  (DEC H between bytes!)
           )

DEC H steps the address back by 256, which is one pixel line *up* inside the
same character row, so a run of n bytes lands on n consecutive scanlines of one
byte column - the ring stores each screen column vertically.  Because the walk is
fixed, one pass always repaints the same 3792 screen bytes and never touches the
other 2352 (the HUD).  The ring is a sheared panorama: each byte column is read
from a ring offset 4 lower than the previous one, which is why C32F steps by 4
and why patches use a stride of $1FC = +$200-4 - the two shears cancel, so an
object lands on the screen upright.

The walk itself is measured once (tools/units.py -> capture['blit']) and re-based
per frame, because only C32E/C32F move between frames:

    page   = (C32E + (C32F & 7F) + 2*block) mod 64
    offset = (off + C32F) mod 256

With -png the reconstruction is written as a 256x192 image, covered pixels in
white and the untouched ones in black.
"""
import json
import sys

SCR_LO, SCR_SIZE, RING_LO = 0x4000, 0x1800, 0x8000
DISCARD = 0x0800


def screen_xy(addr):
    """(x, y) of a screen byte.  Bits 5-7 are the character row inside the
    third, bits 8-10 the pixel line inside the character row (see
    skoolkit.graphics.scr_udgs)."""
    o = addr - SCR_LO
    y = ((o >> 11) & 3) * 64 + ((o >> 5) & 7) * 8 + ((o >> 8) & 7)
    return o & 0x1F, y


def blit_map(walk, c32e, c32f):
    """[(screen_addr, ring_addr)] in copy order; DEC H runs expanded."""
    out = []
    for scr, block, off, length in walk:
        page = (c32e + (c32f & 0x7F) + 2 * block) % 64
        src = RING_LO + page * 256 + ((off + c32f) & 0xFF)
        for k in range(length):
            out.append((scr - 0x100 * k, src + k))
    return out


def rebuild(ring, walk, c32e, c32f, base=None):
    """Return (screen bytearray, covered set).  base = previous screen."""
    scr = bytearray(base if base is not None else bytes(SCR_SIZE))
    covered = set()
    for addr, src in blit_map(walk, c32e, c32f):
        if addr == DISCARD:
            continue
        if not SCR_LO <= addr < SCR_LO + SCR_SIZE:
            continue
        scr[addr - SCR_LO] = ring[(src - RING_LO) & 0x3FFF]
        covered.add(addr - SCR_LO)
    return scr, covered


def main():
    d = json.load(open(sys.argv[1]))
    walk = d['blit']['walk']
    idx = int(sys.argv[2]) if len(sys.argv) > 2 and sys.argv[2].isdigit() else 0
    fr = d['frames'][idx]
    pre = fr['pre']
    scr, covered = rebuild(pre['ring'], walk, pre['c32e'], pre['c32f'])
    real = bytes(pre['screen'])
    same = sum(1 for i in covered if scr[i] == real[i])
    print('кадр %d: C32E=%02X C32F=%02X, перекрывается %d/%d байт экрана, '
          'совпадений в перекрытии %d/%d'
          % (fr['frame'], pre['c32e'], pre['c32f'], len(covered), SCR_SIZE,
             same, len(covered)))
    bad = [i for i in sorted(covered) if scr[i] != real[i]]
    for i in bad[:12]:
        x, y = screen_xy(SCR_LO + i)
        print('   %04X (x=%3d y=%3d) ждали %02X получили %02X'
              % (SCR_LO + i, x, y, scr[i], real[i]))
    if '-png' in sys.argv:
        from PIL import Image
        p = sys.argv[sys.argv.index('-png') + 1]
        img = Image.new('L', (256, 192))
        px = img.load()
        for i in range(SCR_SIZE):
            x, y = screen_xy(SCR_LO + i)
            for b in range(8):
                px[x * 8 + b, y] = 255 if (scr[i] >> (7 - b)) & 1 else 0
        for i in range(SCR_SIZE):
            if i not in covered:
                x, y = screen_xy(SCR_LO + i)
                for b in range(8):
                    px[x * 8 + b, y] = 60
        img.save(p)
        print('записано', p)


if __name__ == '__main__':
    main()
