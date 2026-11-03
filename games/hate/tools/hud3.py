"""Кто рисует пиксели панелей?

  python3 tools/hud3.py <script.txt> <frame> <out_prefix>

В §6 раньше стояла догадка «панели - статичные картинки, затирающие правый
низ».  Проверка прямая: залить весь битмап маркерным значением на верхушку
кадра, а затем останавливаться на границе каждого прохода и смотреть, какой
проход что-то вернул.

  $D3F2 после блита   <- здесь происходит заливка
  $D9D6 список+окна
  $DDED голова обхода $FE64
  $EAE3 композит юнитов
  $D35E блит ландшафта
  $D3F2 следующий проход

Между двумя остановками движок либо восстановил байт, либо нет - значит набор
изменившихся байтов и есть вклад прохода в экран.  Маркер переживает там, где
не пишет никто: так разовая отрисовка отличается от перерисовки каждый кадр.

Итог замера (кадр 2000): ни один проход не возвращает 2304 байта = 299 клеток
в левом верхнем и правом нижнем треугольнике.  Панели живут в экранной памяти
постоянно; см. tools/panel.py и ENGINE.MD §6.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hate_z80  # noqa: F401,E402

HATE = os.path.dirname(HERE)
sys.path.insert(0, HATE)

from skoolkit import CSimulator                         # noqa: E402
from skoolkit.simutils import from_snapshot             # noqa: E402
from skoolkit.snapshot import Snapshot                   # noqa: E402

from coverage import parse_script, FRAME_DURATION, ScriptedTracer  # noqa: E402
import frame_png                                        # noqa: E402

PC, T = 24, 25
MARK = 0x5A
# the display pipeline, in the order the trace shows it; the blit pass runs once
# every four frames, so a cycle here is a whole render, not one TV frame
STOPS = [(0xD9D6, 'list builder'), (0xDDED, 'hud walk'),
         (0xEAE3, 'object composite'), (0xD35E, 'landscape blit'),
         (0xD3F2, 'after blit')]


def main():
    script_path = sys.argv[1]
    frame_target = int(sys.argv[2])
    prefix = sys.argv[3]

    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tracer = ScriptedTracer(sim, script, [], 0, '/tmp/hud3', ovc=False)
    sim.set_tracer(tracer)
    mem, regs = sim.memory, sim.registers

    def run_to(addr, budget=6):
        for _ in range(4):
            if regs[PC] == addr:
                return True
            sim.trace(regs[PC], addr, 0, regs[T] + budget * FRAME_DURATION, 1,
                      None, None, tracer.keyboard, None, None)
        return regs[PC] == addr

    def snap(a, n):
        return [mem[a + k] for k in range(n)]

    sim.trace(snapshot.pc, 0x10000, 0, frame_target * FRAME_DURATION, 1,
              tracer.draw, None, tracer.keyboard, None, None)
    if not run_to(0xD3F2):
        sys.exit('нет $D3F2 (PC=%04X)' % regs[PC])
    real = snap(0x4000, 0x1A00)
    attr_real = snap(0x5800, 0x300)
    for k in range(0x1800):
        mem[0x4000 + k] = MARK
    for k in range(0x300):
        mem[0x5800 + k] = MARK
    print('экран затёрт маркером %02X' % MARK)

    def cell(i):
        """Screen byte offset -> (text column, text row) of its 8x8 cell.

        A screen line is 32 bytes = 32 character cells, so the byte's position
        inside the line *is* the text column; only the row needs unshuffling.
        """
        third, w = divmod(i, 2048)
        _eighth, rest = divmod(w, 256)
        return rest % 32, third * 8 + rest // 32

    def show(hit, title):
        print('  %s: %d знаков' % (title, len(hit)))
        for row in range(24):
            print('    ' + ''.join('#' if (col, row) in hit else '.'
                                  for col in range(32)))

    prev = snap(0x4000, 0x1A00)
    out = {'frame': frame_target, 'real': real, 'attr': attr_real,
           'passes': []}
    for cycle in range(3):
        for addr, name in STOPS:
            if not run_to(addr):
                print('  %-18s: пропуск, PC=%04X' % ('f%d %s' % (cycle, name),
                                                     regs[PC]))
                continue
            now = snap(0x4000, 0x1A00)
            wrote = [i for i in range(0x1A00) if now[i] != prev[i]]
            left = sum(1 for i in range(0x1800) if now[i] == MARK)
            print('  f%d %-16s: записал %5d байт, маркера осталось %5d'
                  % (cycle, name, len(wrote), left))
            if cycle == 0:
                show({cell(i) for i in wrote if i < 0x1800}, name)
            out['passes'].append({'name': name, 'cycle': cycle, 'pc': addr,
                                  'wrote': wrote, 'left': left})
            prev = now
        if not left:
            break

    # which bytes never came back?
    never = [i for i in range(0x1800) if prev[i] == MARK]
    print('никоим проходом не восстановлено: %d байт' % len(never))
    with open(prefix + '.json', 'w') as f:
        json.dump(out, f)

    # render the last stop and the real frame side by side
    ia = frame_png.frame({'screen': prev, 'attr': snap(0x5800, 0x300)}, 3)
    ib = frame_png.frame({'screen': real, 'attr': attr_real}, 3)
    w, h = ia.size
    both = Image.new('RGB', (w * 2 + 12, h), (30, 30, 30))
    both.paste(ia, (0, 0))
    both.paste(ib, (w + 12, 0))
    both.save(prefix + '.png')
    print('-> %s.png / %s.json' % (prefix, prefix))


if __name__ == '__main__':
    from PIL import Image
    main()
