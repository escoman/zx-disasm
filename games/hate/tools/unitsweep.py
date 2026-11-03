"""Collect every unit sprite the engine actually draws during a run.

  python3 tools/unitsweep.py <script.txt> <frame0> <frame1> <out.json> [--png g.png]

The engine composites objects into the ring at $EAE3 just before the blit, and
stops at $D3F2 immediately after that - with the art page still paged into
$4000-$7FFF.  So every time we hit $D3F2 we can read the whole $FF00 draw list
and, for each element, lift its shape straight out of the bank:

    element = [0] class, [1] columns, [2..3] ring address, [4..5] shape pointer
    width   = 21 (class < 4) / 17 (< $0B) / 13
    shape   = columns * width (AND-mask, OR-value) pairs, column stride 2*width

`columns` is trimmed at the near/far edge, so we snapshot the full column set of
the class instead, and key everything by (class, pointer) - one entry per sprite
variant actually seen.  The JSON also keeps the frame, the record table and the
screen rectangle of the first sighting, which is enough to tell player from
enemy: the player's sprite keeps the same footprint from pass to pass.

This is the survey step; `tools/unit_shot.py` renders a single element of a
`tools/units.py` capture against the real screen.
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hate_z80  # noqa: F401,E402

HATE = os.path.dirname(HERE)
sys.path.insert(0, HATE)

from PIL import Image, ImageDraw                     # noqa: E402
from skoolkit import CSimulator                      # noqa: E402
from skoolkit.simutils import from_snapshot          # noqa: E402
from skoolkit.snapshot import Snapshot               # noqa: E402

from coverage import parse_script, FRAME_DURATION, ScriptedTracer  # noqa: E402
import patchpass                                    # noqa: E402
import unit_shot as us                              # noqa: E402

PC, T = 24, 25
STOP = 0xD3F2
MAXCOL = lambda cls: 4 if cls < 4 else (3 if cls < 0x0B else 2)  # noqa: E731


def elements(ff00):
    out, i = [], 0
    while i + 6 <= len(ff00) and ff00[i] != 0xFF:
        el = ff00[i:i + 6]
        out.append({'class': el[0], 'count': el[1],
                    'dest': el[2] | (el[3] << 8), 'ptr': el[4] | (el[5] << 8),
                    'width': patchpass.w_draw(el[0])})
        i += 6
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('script')
    ap.add_argument('frame0', type=int)
    ap.add_argument('frame1', type=int)
    ap.add_argument('out')
    ap.add_argument('--png', help='montage of every distinct sprite seen')
    ap.add_argument('--walk', metavar='CAPTURE.json',
                    help='a units.py capture, to reuse its measured blitter walk '
                         'and report where on screen each sprite lands')
    ap.add_argument('--scale', type=int, default=3)
    a = ap.parse_args()

    script, _ = parse_script(a.script)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tracer = ScriptedTracer(sim, script, [], 0, '/tmp/sweep', ovc=False)
    sim.set_tracer(tracer)
    mem, regs = sim.memory, sim.registers

    def run_to(addr, budget=4):
        for _ in range(3):
            if regs[PC] == addr:
                return True
            sim.trace(regs[PC], addr, 0, regs[T] + budget * FRAME_DURATION, 1,
                      None, None, tracer.keyboard, None, None)
        return regs[PC] == addr

    sim.trace(snapshot.pc, 0x10000, 0, a.frame0 * FRAME_DURATION, 1,
              tracer.draw, None, tracer.keyboard, None, None)

    seen = {}
    passes = 0
    play = []
    walk = None
    if a.walk:
        walk = json.load(open(a.walk))['blit']['walk']
    while regs[T] // FRAME_DURATION < a.frame1:
        if not run_to(STOP):
            # a transition (mission change, death, menu) runs no object pass:
            # skip ahead instead of ending the survey
            sim.trace(regs[PC], 0x10000, 0, regs[T] + 8 * FRAME_DURATION, 1,
                      tracer.draw, None, tracer.keyboard, None, None)
            continue
        passes += 1
        frame = regs[T] // FRAME_DURATION
        ff00 = [mem[(0xFF00 + k) & 0xFFFF] for k in range(0x100)]
        recs = [mem[(0xC000 + k) & 0xFFFF] for k in range(0x100)]
        c300 = [mem[(0xC300 + k) & 0xFFFF] for k in range(0x40)]
        bank = mem[0x4000:0x8000]
        s2 = None
        if walk:
            c32e, c32f = mem[0xC32E], mem[0xC32F]
            s2 = us.ring_to_screen(walk, c32e, c32f)
        r0 = recs[0:16]
        if r0[8] + 1 < len(c300):
            play.append([frame, r0[0] & 0x0F, r0[8],
                         c300[r0[8]] | (c300[r0[8] + 1] << 8), r0[9]])
        for p in elements(ff00):
            n = MAXCOL(p['class']) * p['width'] * 2
            # snapshot the whole four-phase block, not just the phase we caught:
            # the trimmed/near-edge start column is inside it too
            block = [p['ptr'] - c * p['width'] * 2 for c in
                     range(MAXCOL(p['class']) - p['count'] + 1)]
            lo = min(block)
            key = '%02X:%04X' % (p['class'], p['ptr'])
            e = seen.setdefault(key, {
                'class': p['class'], 'ptr': p['ptr'], 'width': p['width'],
                'cols': MAXCOL(p['class']), 'phase': n,
                # `bank` starts at $4000, so the shape address needs rebasing
                'bytes': list(bank[lo - 0x4000:min(lo + 4 * n, 0x8000) - 0x4000]),
                'lo': lo,
                'frames': [], 'counts': [], 'dests': [], 'recs': [],
                'rects': [], 'types': []})
            if len(e['frames']) < 40:
                e['frames'].append(frame)
                e['counts'].append(p['count'])
                e['dests'].append(p['dest'])
                # which record produced it: [8] is a byte offset into $C300,
                # [9] is the viewing phase, one phase block is n bytes, and a
                # shape trimmed at the near/far edge starts at column c
                hit = []
                for i in range(16):
                    r = recs[i * 16:i * 16 + 16]
                    if r[0] & 0x80 or r[8] + 1 >= len(c300):
                        continue
                    base = c300[r[8]] | (c300[r[8] + 1] << 8)
                    for c in range(MAXCOL(p['class']) - p['count'] + 1):
                        if base + ((r[9] - 1) & 3) * n + c * p['width'] * 2 \
                                == p['ptr']:
                            hit.append(i)
                            break
                e['recs'].append(hit)
                if s2 is not None:
                    pts = [s2.get(0x8000 + ((p['dest'] - 0x8000 + j * 0x1FC
                                             + i) & 0x3FFF))
                           for j in range(p['count']) for i in range(p['width'])]
                    pts = [q for q in pts if q]
                    if pts:
                        e['rects'].append([min(q[0] for q in pts),
                                           min(q[1] for q in pts),
                                           max(q[0] for q in pts),
                                           max(q[1] for q in pts)])
                e['types'].append(sorted({recs[i * 16] & 0x0F for i in range(16)
                                          if i in hit}))
        # one instruction, so that the next run_to(STOP) does not stop here
        sim.trace(regs[PC], 0x10000, 1, regs[T] + 10 ** 6, 1, None, None,
                  tracer.keyboard, None, None)

    for k, e in sorted(seen.items(), key=lambda kv: kv[1]['frames'][0]):
        who = sorted({r for hit in e['recs'] for r in hit})
        typ = sorted({t for ts in e['types'] for t in ts})
        rc = sorted({tuple(r) for r in e['rects']})
        print('%s cls=%02X cols=%d w=%02X  %d прох., кадры %d..%d, '
              'столбцов %s, записи %s, типы %s, экран %s'
              % (k, e['class'], e['cols'], e['width'], len(e['frames']),
                 e['frames'][0], e['frames'][-1], sorted(set(e['counts'])),
                 who, typ, rc if len(rc) <= 3 else '%d позиц.' % len(rc)))
    print('проходов %d, разных спрайтов %d -> %s' % (passes, len(seen), a.out))
    forms = {}
    for f, t, s, b, ph in play:
        forms.setdefault((t, s, b), [0, []])
        forms[(t, s, b)][0] += 1
        forms[(t, s, b)][1].append((f, ph))
    print('запись 0 (техника игрока): тип/[8]/база -> сколько проходов, фазы')
    for (t, s, b), (n, where) in sorted(forms.items(), key=lambda kv: kv[1][0],
                                        reverse=True):
        print('  тип %02X  [8]=$%02X  база $%04X  %3d  фазы %s'
              % (t, s, b, n, sorted({p for _, p in where})))
    with open(a.out, 'w') as f:
        json.dump({'script': a.script, 'passes': passes, 'seen': seen,
                   'player': play}, f)

    if a.png:
        gallery(seen, a.png, a.scale)
        print('галерея -> %s' % a.png)


def gallery(seen, path, scale):
    """One tile per distinct sprite: white = painted 1, grey = 0, black = clear."""
    tiles = []
    for k, e in seen.items():
        w, cols = e['width'], e['cols']
        # a shape near the end of the paged bank was cut off by the 16K window;
        # $FF is "leave the background", so padding with it is harmless
        b = e['bytes'] + [0xFF] * (4 * e['phase'] - len(e['bytes']))
        off = e['ptr'] - e['lo']
        img = Image.new('RGB', (cols * 8 * scale, w * scale), (0, 0, 0))
        px = img.load()
        for j in range(cols):
            for i in range(w):
                m = b[off + j * 2 * w + 2 * i]
                v = b[off + j * 2 * w + 2 * i + 1]
                y = w - 1 - i
                for k2 in range(8):
                    if (m >> (7 - k2)) & 1:
                        continue
                    col = (255, 255, 255) if (v >> (7 - k2)) & 1 else (110, 110, 110)
                    for sy in range(scale):
                        for sx in range(scale):
                            px[j * 8 * scale + k2 * scale + sx,
                               y * scale + sy] = col
        tiles.append((k, img))
    if not tiles:
        Image.new('RGB', (16, 16)).save(path)
        return
    gap = 4
    cols = 4
    rows = (len(tiles) + cols - 1) // cols
    cw = max(t[1].width for t in tiles) + gap
    ch = max(t[1].height for t in tiles) + gap + 12
    sheet = Image.new('RGB', (cols * cw, rows * ch), (40, 40, 40))
    dr = ImageDraw.Draw(sheet)
    for n, (k, img) in enumerate(sorted(tiles, key=lambda t: t[0])):
        x, y = (n % cols) * cw, (n // cols) * ch
        sheet.paste(img, (x, y + 12))
        dr.text((x + 1, y + 2), k, fill=(255, 255, 0))
    sheet.save(path)


if __name__ == '__main__':
    main()
