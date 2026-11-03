"""Film strip of the unit sprites a capture actually drew.

  python3 tools/sprite_strip.py <capture.json> <out.png> [--ptr 7C10,7CB8]
                                              [--lo 7C00] [--hi 8000]
                                              [--scale N] [--bg RRGGGB]

`tools/units.py … --bank` writes a page dump `<capture>_f<frame>.bin` next to the
JSON for every captured object pass, so the shape pointer of each element can be
resolved without guessing which 16K page was live.  This tool walks all passes of
a capture, pulls the elements whose shape pointer matches the filter, renders each
one the way `unit_shot.py` does - the art alone, with the ink/paper of the
attribute cell it really landed on, over a flat background - and stacks the result
as one row per pass.  It is the "which sprite is which unit" view: the sequence of
pointers down a row is the animation the engine played during those frames.
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import patchpass                                    # noqa: E402
import unit_shot as us                              # noqa: E402
from PIL import Image, ImageDraw                    # noqa: E402

LABEL_H = 12
GAP = 4


def elements(pre):
    """All $FF00 draw elements of a pass (the $FE64 list only restores background)."""
    out = []
    for p in patchpass.replay(pre)[1]:
        if p['list'] == 'ff00' and p.get('ptr'):
            out.append(p)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('capture')
    ap.add_argument('out')
    ap.add_argument('--ptr', help='comma separated shape pointers to keep')
    ap.add_argument('--lo', type=lambda s: int(s, 16), help='lowest pointer')
    ap.add_argument('--hi', type=lambda s: int(s, 16), help='highest pointer')
    ap.add_argument('--scale', type=int, default=4)
    ap.add_argument('--bg', default='909090', help='flat background, RRGGBB hex')
    a = ap.parse_args()

    keep = ({int(x, 16) for x in a.ptr.split(',')} if a.ptr else None)
    d = json.load(open(a.capture))
    stem = a.capture.rsplit('.', 1)[0]
    walk = d['blit']['walk']
    bg = tuple(int(a.bg[i:i + 2], 16) for i in (0, 2, 4))

    rows = []
    for fr in d['frames']:
        pre = fr['pre']
        page = '%s_f%d.bin' % (stem, fr['frame'])
        if not os.path.exists(page):
            continue
        bank = us.load_bank(page)
        s2 = us.ring_to_screen(walk, pre['c32e'], pre['c32f'])
        tiles = []
        for p in elements(pre):
            if keep is not None:
                if p['ptr'] not in keep:
                    continue
            elif a.lo is not None or a.hi is not None:
                lo, hi = a.lo or 0, a.hi or 0x10000
                if not lo <= p['ptr'] < hi:
                    continue
            img = us.render_art_flat(bank, p, s2, pre['attr'], a.scale, bg)
            tiles.append(('%04X' % p['ptr'], img))
        if tiles:
            rows.append((fr['frame'], tiles))

    if not rows:
        print('нет проходов с таким фильтром')
        return
    sc = a.scale
    widest = max(sum(t[1].width for t in r[1])
                 + GAP * (len(r[1]) - 1) for r in rows)
    tall = max(max(t[1].height for t in r[1]) for r in rows)
    sheet = Image.new('RGB', (widest + 130, len(rows) * (tall + LABEL_H + GAP)
                              + GAP), (30, 30, 30))
    dr = ImageDraw.Draw(sheet)
    y = GAP
    for frame, tiles in rows:
        x = 130
        dr.text((4, y + 2), 'frame %d' % frame, fill=(255, 255, 0))
        dr.text((4, y + 12), '->'.join(t[0] for t in tiles), fill=(160, 255, 160))
        for name, img in tiles:
            sheet.paste(img, (x, y + LABEL_H))
            dr.text((x + 1, y + 1), name, fill=(255, 255, 255))
            x += img.width + GAP
        y += tall + LABEL_H + GAP + 6
    sheet.save(a.out)
    print('%d проходов -> %s' % (len(rows), a.out))


if __name__ == '__main__':
    main()
