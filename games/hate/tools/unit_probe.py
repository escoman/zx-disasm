"""Where does one display-list element actually land on the screen?

  python3 tools/unit_probe.py <capture.json> <frame-index> [--list ff00]

For every element of $FF00 / $FE64 we replay its patch (tools/patchpass), map
each written ring byte to its screen pixel through the measured blitter walk
(tools/ringscreen) and print an ASCII map of the footprint plus the bytes.
"""
import json
import sys

sys.path.insert(0, __file__.rsplit('/', 1)[0])
import patchpass
import ringscreen as rs


def ring_to_screen(walk, c32e, c32f):
    """{ring_addr: (x, y)} for the bytes this pass actually displays."""
    out = {}
    for scr, block, off, length in walk:
        page = (c32e + (c32f & 0x7F) + 2 * block) % 64
        src = 0x8000 + page * 256 + ((off + c32f) & 0xFF)
        for k in range(length):
            a = scr - 0x100 * k
            if 0x4000 <= a < 0x5800:
                out[src + k] = rs.screen_xy(a)
    return out


def main():
    d = json.load(open(sys.argv[1]))
    idx = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    fr = d['frames'][idx]
    pre = fr['pre']
    s2 = ring_to_screen(d['blit']['walk'], pre['c32e'], pre['c32f'])
    ring, patches, used = patchpass.replay(pre)
    print('кадр %d  C32E=%02X C32F=%02X  отображаемых байт кольца %d'
          % (fr['frame'], pre['c32e'], pre['c32f'], len(s2)))
    for n, p in enumerate(patches):
        if len(sys.argv) > 3 and sys.argv[3] != p['list']:
            continue
        pts = []
        for j in range(p['count']):
            base = (p['dest'] - 0x8000 + j * 0x1FC) & 0x3FFF
            for i in range(p['width']):
                a = 0x8000 + ((base + i) & 0x3FFF)
                pts.append((a, s2.get(a)))
        shown = [q for q in pts if q[1]]
        print('\n#%02d %s cls=%02X count=%d width=%d dest=%04X ptr=%s  '
              'байт %d, на экране %d'
              % (n, p['list'], p['class'], p['count'], p['width'], p['dest'],
                 '%04X' % p['ptr'] if 'ptr' in p else '-', len(pts),
                 len(shown)))
        if not shown:
            continue
        xs = [q[1][0] for q in shown]
        ys = [q[1][1] for q in shown]
        print('   x %d..%d  y %d..%d' % (min(xs), max(xs), min(ys), max(ys)))
        grid = {}
        for a, (x, y) in shown:
            grid[(x, y)] = ring[a - 0x8000]
        for y in range(min(ys), max(ys) + 1):
            row = ''.join('..' if grid.get((x, y)) is None else
                          '%02X' % grid[(x, y)] for x in range(min(xs),
                                                               max(xs) + 1))
            print('   %3d %s' % (y, row))


if __name__ == '__main__':
    main()
