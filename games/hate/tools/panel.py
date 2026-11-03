"""Панели HUD: статичный образ экрана в $A000.

  python3 tools/panel.py dump  <script.txt> <img_frame> <play_frame> <out.scr>
  python3 tools/panel.py when  <script.txt> <from_frame> <to_frame> [limit]
  python3 tools/panel.py shots <script.txt> <to_frame> <frame...>
  python3 tools/panel.py who   <script.txt> <from_frame> <to_frame>

Итог исследования (всё измерено):

1. tools/hud3.py доказал, что блиттер пейзажа ($D35E) принципиально не трогает
   два треугольника экрана - левый верхний (логотип + шкала) и правый нижний
   (SCORE/MAX/HIGH).  Значит их содержимое живёт в экранной памяти постоянно.
2. `when` на всём прогоне 0..2100 даёт ровно один большой всплеск изменений в
   этих треугольниках: кадры 1235-1237, переход заставка -> меню.  В геймплее
   меняются только 9 клеток мигающей диагональной рамки панели.
3. `who` внутри этого перехода показывает виновника - самомодифицируемый став
   в кольце, который пишется на лету:

     $9F1C DI
     $9F1D LD HL,$A000        ; bitmap образа
     $9F20 LD DE,$4000        ; экран
     $9F23 LD BC,$2000        ; 8192 байта = картинка + атрибуты с хвостом
     $9F26 LDIR               ; <<< вот этот PC дают все события
     $9F28 XOR A / LD ($C2F8),A / JP $C2B1

   Перед ним тот же став делает $7C00 -> $6300 ($2400 байт, шрифт/растр),
   CALL $EE17 (строка $9F2F = «HOSTILE-ALL-TERRAIN ENCOUNTER»), CALL $9F4B и
   CALL $8000.
4. $A000..$B7FF = bitmap, $B800..$BFFF = атрибуты: это ГОТОВЫЙ ЭКРАН, а не
   тайл-таблица.  Панели нарисованы в нём пиксель в пиксель; текст меню, цифры
   SCORE и сегменты шкалы поверх него кладёт оконный движок ($EE17/$D9D6).
5. Образ в $A000 кладёт RLE-разворачиватель $5B8C (escape $97, счётчик с
   битом 7 = N нулей, иначе N повторов; HL $E9F6 вниз до $5FB3, DE $FFFF
   вниз) - то есть панель приходит с диска в куче INXS+RLE, а не генерится.

`dump` сохраняет образ в .scr и сверяет его клетка с клеткой с игровым экраном.

Хук `draw` C-симулятор вызывает ровно раз на кадр, но только если он возвращает
истинное значение (`draw_screen`: falsy = прервать трассировку).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hate_z80  # noqa: F401,E402

HATE = os.path.dirname(HERE)
sys.path.insert(0, HATE)

from skoolkit import CSimulator                          # noqa: E402
from skoolkit.simutils import from_snapshot              # noqa: E402
from skoolkit.snapshot import Snapshot                    # noqa: E402

from coverage import parse_script, FRAME_DURATION, ScriptedTracer  # noqa: E402

PC, T = 24, 25
A, BC, DE, HL = 0, 2, 4, 6
IX, IY = 8, 10


def w16(r, k):
    """Регистры C-симулятора лежат побайтно: чётный индекс - младший байт."""
    return r[k] | (r[k + 1] << 8)


def off(col, row, pix):
    """Экранный адрес (col,row) знака, строка pix -> смещение от $4000."""
    y = row * 8 + pix
    return ((y >> 6) & 3) * 2048 + (y & 7) * 256 + ((y >> 3) & 7) * 32 + col


def triangles():
    """Клетки, до которых блиттер пейзажа не дотягивается (замер hud3.py).

    Верхний левый треугольник - ряды 0..12, левый край диагонали 25-2*row;
    нижний правый - ряды 13..23, правый край.  Ряд 6 прорезан окнишком субтитров.
    """
    cells = set()
    for row in range(13):
        for col in range(0, max(0, 25 - 2 * row)):
            if row == 6 and 2 <= col < 8:
                continue          # здесь пишет оконный движок
            cells.add((col, row))
    edge = {13: 30, 14: 28, 15: 26, 16: 24, 17: 19, 18: 19, 19: 18, 20: 16,
            21: 14, 22: 12, 23: 10}
    for row, lo in edge.items():
        for col in range(lo, 32):
            cells.add((col, row))
    return cells


def byte_offsets(cells):
    out = []
    for col, row in sorted(cells):
        out.extend(off(col, row, p) for p in range(8))
    return out


def cmd_when(script_path, first, last, limit):
    cells = triangles()
    offs = byte_offsets(cells)
    state = {'delta': -1}

    class Watch(ScriptedTracer):
        def draw(self, scr, frame, border, keyboard=None):
            self._apply_frame(frame)
            if frame < first:
                return True
            sig = tuple(scr[o] for o in offs)
            filled = sum(1 for v in sig if v)
            if sig != state.get('sig'):
                delta = sum(1 for a, b in zip(sig, state.get('sig') or ())
                            if a != b)
                state['sig'] = sig
                state['delta'] = delta
                if delta >= limit or 'base' not in state:
                    state['base'] = 1
                    print('кадр %5d: заполнено %4d/%d байт, изменено %4d'
                          % (frame, filled, len(sig), delta), flush=True)
            return True

    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tr = Watch(sim, script, [], 0, '/tmp/panel', ovc=False)
    sim.set_tracer(tr)
    sim.trace(snapshot.pc, 0x10000, 0, last * FRAME_DURATION, 1,
              tr.draw, None, tr.keyboard, None, None)
    print('кадр %d' % tr.frame)


def cmd_shots(script_path, last, frames):
    """Снимки экрана на заданных кадрах - чем рисовался кадр 1235 и т.д."""
    from PIL import Image
    import frame_png
    want = set(frames)
    keep = {}

    class Watch(ScriptedTracer):
        def draw(self, scr, frame, border, keyboard=None):
            self._apply_frame(frame)
            if frame in want:
                keep[frame] = ([mem[0x4000 + i] for i in range(0x1800)],
                               [mem[0x5800 + i] for i in range(0x300)])
            return True

    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    mem = sim.memory
    tr = Watch(sim, script, [], 0, '/tmp/panel', ovc=False)
    sim.set_tracer(tr)
    sim.trace(snapshot.pc, 0x10000, 0, last * FRAME_DURATION, 1,
              tr.draw, None, tr.keyboard, None, None)

    tiles = []
    for f in sorted(keep):
        scr, attr = keep[f]
        im = frame_png.frame({'screen': scr, 'attr': attr}, 2)
        path = os.path.join(HATE, 'tmp', 'panel_%05d.png' % f)
        im.save(path)
        print('%s  %dx%d' % (path, im.width, im.height))
        tiles.append((f, im))
    if len(tiles) > 1:
        w, h = tiles[0][1].size
        strip = Image.new('RGB', (w * len(tiles) + 8 * (len(tiles) - 1), h),
                          (30, 30, 30))
        for k, (_f, im) in enumerate(tiles):
            strip.paste(im, (k * (w + 8), 0))
        strip.save(os.path.join(HATE, 'tmp', 'panel_strip.png'))
        print('-> tmp/panel_strip.png')


WATCH = [(2, 1), (4, 4), (1, 9), (20, 17), (22, 18), (28, 20), (24, 22),
         (26, 22), (30, 23), (12, 22)]
PIXELS = (0, 4)


def cmd_who(script_path, f0, f1):
    """Кто пишет в панели: пошаговая трассировка кадров f0..f1 с опросом
    контрольных байт на каждой инструкции (память в callback'е - уже после неё).
    """
    offs = [off(c, r, p) for c, r in WATCH for p in PIXELS]
    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    mem = sim.memory
    tr = ScriptedTracer(sim, script, [], 0, '/tmp/panel', ovc=False)
    sim.set_tracer(tr)
    sim.trace(snapshot.pc, 0x10000, 0, f0 * FRAME_DURATION, 1,
              tr.draw, None, tr.keyboard, None, None)
    print('стоп на кадре %d, PC=%04X' % (tr.frame, sim.registers[PC]), flush=True)

    last = [mem[0x4000 + o] for o in offs]
    events = []
    regs = sim.registers

    def cb(pc, dis, t0):
        for k, o in enumerate(offs):
            v = mem[0x4000 + o]
            if v != last[k]:
                events.append((t0 // FRAME_DURATION, pc, o, last[k], v,
                               tuple(regs),
                               (mem[pc], mem[pc + 1], mem[pc + 2])))
                last[k] = v

    sim.trace(sim.registers[PC], 0x10000, 0, f1 * FRAME_DURATION, 1,
              tr.draw, None, tr.keyboard, lambda pc: None, cb)
    print('событий: %d' % len(events))
    for frame, pc, o, a, b, r, op in events:
        print('  кадр %d PC=%04X %02X %02X %02X  $4000+%04X: %02X->%02X  '
              'HL=%04X DE=%04X BC=%04X IX=%04X A=%02X'
              % (frame, pc, op[0], op[1], op[2], o, a, b,
                 w16(r, HL), w16(r, DE), w16(r, BC), w16(r, IX), r[A]))


def cmd_dump(script_path, f_img, f_play, out_path):
    """Снять образ панелей с $A000 и сверить его с игровым экраном."""
    from PIL import Image
    import frame_png
    grab = {}

    class Watch(ScriptedTracer):
        def draw(self, scr, frame, border, keyboard=None):
            self._apply_frame(frame)
            if frame in (f_img, f_play):
                g = lambda a, n: [mem[a + k] for k in range(n)]
                grab[frame] = {'img': g(0xA000, 0x1800) + g(0xB800, 0x300),
                               'scr': g(0x4000, 0x1800) + g(0x5800, 0x300)}
            return True

    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    mem = sim.memory
    tr = Watch(sim, script, [], 0, '/tmp/panel', ovc=False)
    sim.set_tracer(tr)
    sim.trace(snapshot.pc, 0x10000, 0, (f_play + 1) * FRAME_DURATION, 1,
              tr.draw, None, tr.keyboard, None, None)

    img = grab[f_img]['img']
    with open(out_path, 'wb') as f:
        f.write(bytes(img))
    print('-> %s (%d байт, образ $A000 с кадра %d)' % (out_path, len(img), f_img))

    scr = grab[f_play]['scr']
    cells = sorted(triangles())
    same = [c for c in cells if byte_of(img, c) == byte_of(scr, c)]
    print('клеток панели: %d, совпадает с игровым экраном (%d): %d, '
          'отличается: %d' % (len(cells), f_play, len(same),
                              len(cells) - len(same)))
    print('отличаются (это текст/цифры/шкала, поверх образа):')
    for c in cells:
        if byte_of(img, c) != byte_of(scr, c):
            print('   (%2d,%2d) образ %02X -> экран %02X'
                  % (c[0], c[1], byte_of(img, c)[0], byte_of(scr, c)[0]))

    out = os.path.splitext(out_path)[0] + '.png'
    ia = frame_png.frame({'screen': img[:0x1800], 'attr': img[0x1800:]}, 2)
    ib = frame_png.frame({'screen': scr[:0x1800], 'attr': scr[0x1800:]}, 2)
    w, h = ia.size
    both = Image.new('RGB', (w * 2 + 12, h), (30, 30, 30))
    both.paste(ia, (0, 0))
    both.paste(ib, (w + 12, 0))
    both.save(out)
    print('-> %s (слева образ $A000, справа игровой экран)' % out)


def byte_of(buf, cell):
    col, row = cell
    return bytes(buf[off(col, row, p)] for p in range(8))


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    what = sys.argv[1]
    if what == 'when':
        cmd_when(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]),
                 int(sys.argv[5]) if len(sys.argv) > 5 else 50)
    elif what == 'shots':
        cmd_shots(sys.argv[2], int(sys.argv[3]),
                 [int(a) for a in sys.argv[4:]])
    elif what == 'who':
        cmd_who(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]))
    elif what == 'dump':
        cmd_dump(sys.argv[2], int(sys.argv[3]), int(sys.argv[4]), sys.argv[5])
    else:
        sys.exit('не знаю фазу %r' % what)


if __name__ == '__main__':
    main()
