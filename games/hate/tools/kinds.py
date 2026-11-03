"""Render every unit kind the engine knows about, straight out of an art page.

  python3 tools/kinds.py <page.bin> <out.png> [--only 0,1,2] [--scale N]

`tools/units.py … --bank` dumps the 16K page that is live at $4000-$7FFF while a
unit pass runs; that page holds the shape data of *all* the kinds below, so one
dump is enough to draw the whole menagerie.

The engine indexes this table twice over with one number.  For the player $D21B..$D246
picks the art from the scene descriptor:

    A  = (scene descriptor)          ; the kind byte
    ($C000) = A                      ; record 0 = the player
    ($C008) = (A & $0F) * 2          ; its shape-table offset

and for every other record $E2A1..$E2A4 keeps [8] = 2 * (type & $0F), while the class
byte that reaches the $FF00 draw list is that same low nibble ($DBC0 -> $DBD6 ->
$DBD8/$DBDC).  So class, art index and geometry are one number:

    class < $04        21 rows x 4 columns      phase stride $A8
    class < $0B        17 rows x 3 columns      phase stride $66
    otherwise          13 rows x 2 columns      phase stride $34

The regions tile exactly: the gap between consecutive bases equals the largest
pointer the engine can form, 3 * stride + (cols - 1) * 2 * width = 630 / 374 / 182
bytes.  Indices 16..22 are the exception - they are the death animation, drawn with
the class forced to $03 by $DBC5..$DBD4, hence --all renders them 4x21 too.  Classes
$0E and $0F take a separate $DBE1 branch and were never seen on the draw list, so
the geometry assumed for kinds 14 and 15 is only a guess.

Each kind owns the space up to the next base in address order, which is why some
kinds hold more than the four viewing phases (the animated ones).  Tiles are the
bare art: white = painted 1, grey = painted 0, black = transparent.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patchpass                                    # noqa: E402
import unit_shot as us                              # noqa: E402
from PIL import Image, ImageDraw                    # noqa: E402

TABLE = 0xC300
NENT = 23


def geom(cls):
    if cls < 4:
        return 21, 4
    if cls < 0x0B:
        return 17, 3
    return 13, 2


def geom_of(index):
    """Geometry a table index is drawn with.

    Indices 16..22 are the death animation: $DBC5..$DBD4 forces the class to $03
    while bit 5 of the type is set, so they all use the widest 4x21 frame.
    """
    return (21, 4) if index >= 16 else geom(index)


def tile(page, ptr, width, cols, scale, off=0x4000):
    img = Image.new('RGB', (cols * 8 * scale, width * scale), (0, 0, 0))
    px = img.load()
    for j in range(cols):
        for i in range(width):
            o = ptr - off + j * 2 * width + 2 * i
            if o + 1 >= len(page):
                break
            m, v = page[o], page[o + 1]
            y = width - 1 - i
            for k in range(8):
                if (m >> (7 - k)) & 1:
                    continue
                col = (255, 255, 255) if (v >> (7 - k)) & 1 else (110, 110, 110)
                for sy in range(scale):
                    for sx in range(scale):
                        px[j * 8 * scale + k * scale + sx,
                           y * scale + sy] = col
    return img


def colour_page(path, pno, eno):
    """(bank, s2, attr, dest) borrowed from one element of a units.py capture.

    The attribute cells under a unit depend on where it stands, so a catalogue
    has to borrow them from somewhere; one real element gives every tile the
    same ink/paper the game used for the object we captured.
    """
    d = json.load(open(path))
    fr = d['frames'][pno]
    pre = fr['pre']
    s2 = us.ring_to_screen(d['blit']['walk'], pre['c32e'], pre['c32f'])
    # ELEMENT is the index in the merged $FE64+$FF00 list, the same numbering
    # `unit_shot.py` prints
    el = patchpass.replay(pre)[1][eno]
    return us.load_bank(path.rsplit('.', 1)[0] + '_f%d.bin' % fr['frame']), \
        s2, pre['attr'], el['dest']


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('page')
    ap.add_argument('out')
    ap.add_argument('--sweep', help='comma separated unitsweep.py JSONs: mark '
                                    'the kinds that were really drawn, with '
                                    'their records')
    ap.add_argument('--only', help='comma separated kind numbers')
    ap.add_argument('--all', action='store_true',
                    help='also draw table indices 16..22 (death animation)')
    ap.add_argument('--colour', nargs=3, metavar=('CAPTURE', 'PASS', 'ELEMENT'),
                    help='draw with the real ink/paper of one captured element')
    ap.add_argument('--scale', type=int, default=3)
    a = ap.parse_args()

    full = open(a.page, 'rb').read()
    page, hi = full[:0x4000], full[0x4000:]
    if len(hi) < 0x1000:
        sys.exit('дамп должен содержать и страницу $C000-$FFFF')

    def peek(addr):
        return hi[addr - 0xC000]

    bases = [peek(TABLE + 2 * i) | (peek(TABLE + 2 * i + 1) << 8)
             for i in range(NENT)]
    order = sorted(set(bases))

    # which classes the engine really put on the $FF00 list, and which records
    # produced them; a class can be drawn without the record being attributed,
    # so "never drawn" and "record unknown" have to stay separate
    drawn, seen = {}, set()
    if a.sweep:
        for name in a.sweep.split(','):
            for e in json.load(open(name))['seen'].values():
                k = e['class'] & 0x0F
                seen.add(k)
                drawn.setdefault(k, set()).update(
                    r for hit in e['recs'] for r in hit)

    last = NENT if a.all else 16
    only = {int(x) for x in a.only.split(',')} if a.only \
        else set(range(last))
    sc = a.scale
    col = colour_page(a.colour[0], int(a.colour[1]), int(a.colour[2])) \
        if a.colour else None
    rows = []
    for kind in sorted(only):
        base = bases[kind] if kind < len(bases) else None
        if base is None or base < 0x4000 or base >= 0x8000:
            continue
        width, cols = geom_of(kind)
        stride = 2 * cols * width
        nxt = min([b for b in order if b > base], default=base + 4 * stride)
        # the last death step runs to the end of the page, not to a next base
        if kind >= 16:
            nxt = min([b for b in order if b > base] + [0x8000],
                      default=base + 4 * stride)
        # the region a kind owns is usually a few bytes short of four whole
        # phases (the last column of phase 3 is shared with the next kind), so
        # round up: the engine indexes exactly four viewing phases
        nph = max(1, min(4, -(-(nxt - base) // stride)))
        if col:
            bank, s2, attr, dest = col
            tiles = [us.render_art_flat(
                bank, {'class': kind, 'count': cols, 'width': width,
                        'dest': dest, 'ptr': base + p * stride},
                s2, attr, sc) for p in range(nph)]
        else:
            tiles = [tile(page, base + p * stride, width, cols, sc)
                     for p in range(nph)]
        w = sum(t.width for t in tiles) + 4 * (len(tiles) - 1)
        row = Image.new('RGB', (max(w, 150), max(t.height for t in tiles)),
                        (20, 20, 20))
        x = 0
        for t in tiles:
            row.paste(t, (x, 0))
            x += t.width + 4
        rows.append((kind, base, nph, nxt - base, row))

    if not rows:
        print('нет ни одного вида')
        return
    gap, lab = 4, 14
    sheet = Image.new('RGB', (max(r[4].width for r in rows) + 170 + gap,
                              len(rows) * (max(r[4].height for r in rows)
                                           + lab + gap) + gap), (40, 40, 40))
    dr = ImageDraw.Draw(sheet)
    y = gap
    hh = max(r[4].height for r in rows)
    for kind, base, nph, size, row in rows:
        who = sorted(drawn.get(kind, ())) if drawn else []
        if who:
            note = 'records ' + ','.join(str(r) for r in who)
            fill = (120, 255, 120)
        elif drawn and kind in seen:
            note = 'drawn, record not attributed'
            fill = (255, 200, 90)
        else:
            note = 'never drawn' if drawn else ''
            fill = (255, 255, 0)
        # PIL's built-in font is latin-1 only, so the labels stay ASCII
        dr.text((4, y + 2), 'kind %2d  $%04X  %d phases  %d bytes  %s'
                % (kind, base, nph, size, note), fill=fill)
        sheet.paste(row, (170, y + lab))
        y += hh + lab + gap
    sheet.save(a.out)
    print('%d видов -> %s' % (len(rows), a.out))


if __name__ == '__main__':
    main()
