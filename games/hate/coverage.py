#!/usr/bin/env python3
"""coverage.py — headless-трассировщик геймплея H.A.T.E. с инжекцией клавиш.

Цель: собрать ПОЛНОЕ покрытие кода (exec map) для чистого дизассемблирования.

  $ python3 coverage.py hate/data/scripts/script_v8.txt hate/tmp/run11 [--tstates N]

script-file — пофреймовый сценарий нажатий, по строке вида:
    <кадр:duration> <ключи через пробел>        # комментарий
ключи: 0-9 q..p a..g ENTER CS SS SPACE (как в skoolkit/kbtracer.py),
комбинации через '+': 'CS+6' (Cursor Down), 'CS+8' и т.п.
'-' duration = до конца прогона.

Выход:
  <out-prefix>_exec.map     — карта выполненных адресов ($XXXX, по возрастанию)
  <out-prefix>_NNN.z80/.png — чекпоинты на кадрах из директив CHECKPOINT
"""

import re
import sys

from skoolkit import CSimulator
from skoolkit.simulator import Simulator
from skoolkit.kbtracer import KEYS
from skoolkit.simutils import from_snapshot
from skoolkit.snapshot import Snapshot, write_snapshot
from skoolkit.trace import Tracer
from skoolkit.graphics import Frame, scr_udgs
from skoolkit.components import get_image_writer

FRAME_DURATION = 69888  # 48K: T-states per screen frame

# Kempston-биты для скриптовых клавиш JOY+L/R/U/D/F (порт $1F)
KEMPSTON = {'JOY+L': 1, 'JOY+R': 2, 'JOY+D': 4, 'JOY+U': 8, 'JOY+F': 16}

def key_to_rowbit(name):
    """'CS+6' -> [(row, mask), ...]; маска инвертирована (0 = клавиша нажата).

    Row-индексация как в Tracer.read_port: h = high(port)^0xFF, бит i -> keyboard[i].
    """
    out = []
    for part in name.split('+'):
        port, bits = KEYS[part]
        row = ((((port >> 8) ^ 0xFF)) - 1).bit_length()  # порт 0xF7FE -> h=0x08 -> row 3
        out.append((row, bits ^ 0xFF))  # маски kbtracer инвертированы (0=нажата)
    return out


def parse_script(path):
    script = []   # [(duration_frames, [keyname, ...]), ...]
    checkpoints = []
    frame = 0
    with open(path) as f:
        for ln, line in enumerate(f, 1):
            line = line.split('#', 1)[0].strip()
            if not line:
                continue
            if line.startswith('CHECKPOINT'):
                checkpoints.append(int(line.split()[1]))
                continue
            m = re.match(r'^(\d+|-)\s+(.+)$', line)
            if not m:
                sys.exit(f'{path}:{ln}: не понимаю: {line!r}')
            dur = None if m.group(1) == '-' else int(m.group(1))
            keys = m.group(2).split()
            if keys == ['NONE']:
                keys = []
            for k in keys:
                for part in k.split('+'):
                    if part not in KEYS and k not in KEMPSTON:
                        sys.exit(f'{path}:{ln}: неизвестная клавиша {part!r}')
            script.append((frame, dur, keys))
            if dur is not None:
                frame += dur
    return script, checkpoints


class ScriptedTracer(Tracer):
    def __init__(self, simulator, script, checkpoints, png_every, out_prefix,
                 ovc=False):
        super().__init__(simulator, 7, 0, 0, [0] * 16, 0, True)
        self.keyboard = [0] * 8
        self.script = script
        self.checkpoints = set(checkpoints)
        self.png_every = png_every
        self.out_prefix = out_prefix
        self.si = 0
        self.frame = -1
        self.kemp = 0
        self.fh = None  # открытый .z80 чекпоинт пишем через write_snapshot
        # --- наблюдатель оверлеев ($8000-$CFFF) ---
        self.ovc = ovc
        self.ov_hashes = set()
        self.ov_count = 0
        self.ov_last = -1
        # исполняемые адреса кода движка (из merged_exec.map) — стабильный идентификатор модуля
        try:
            ex = sorted(int(l.strip().lstrip('$'), 16)
                        for l in open('hate/data/merged_exec.map') if l.strip())
            self.ov_code = [a for a in ex if 0xCF32 <= a < 0xF2E0]
        except FileNotFoundError:
            self.ov_code = list(range(0xCF32, 0xF2E0))

    # --- пофреймовая машина входных данных -------------------------------
    def _apply_frame(self, frame):
        if frame == self.frame:
            return
        self.frame = frame
        while (self.si + 1 < len(self.script) and
               self.script[self.si + 1][0] is not None and
               frame >= self.script[self.si + 1][0]):
            self.si += 1
        _, _, keys = self.script[self.si]
        kb = self.keyboard           # ВАЖНО: мутация in-place — C-симулятор держит ссылку
        for row in range(8):
            kb[row] = 0
        self.kemp = 0
        for name in keys:
            if name in KEMPSTON:
                self.kemp |= KEMPSTON[name]
                continue
            for row, bits in key_to_rowbit(name):
                kb[row] |= bits
        # чекпоинты
        if frame in self.checkpoints:
            self._dump_snapshot(frame)
        if self.png_every and frame % self.png_every == 0:
            self._dump_png(frame)
        if self.ovc and frame != self.ov_last and frame % 16 == 0:
            self.ov_last = frame
            # ключ = только исполняемые адреса кода движка (backbuffer/данные не учитываем)
            mem = self.simulator.memory
            win = bytes(mem[a] for a in self.ov_code)
            h = hash(win)
            if h not in self.ov_hashes:
                self.ov_hashes.add(h)
                self.ov_count += 1
                self._dump_snapshot(f'ov{self.ov_count:02d}x{frame}')

    def _dump_snapshot(self, frame):
        sim = self.simulator
        regs = sim.registers
        from skoolkit.simutils import get_state
        ram, registers, state, machine = get_state(sim)
        fname = (f'{self.out_prefix}_{frame}.z80' if isinstance(frame, str)
                 else f'{self.out_prefix}_{frame:05d}.z80')
        write_snapshot(fname, ram, registers, state, machine)
        m = sim.memory
        print(f'Wrote {fname}  C28F={m[0xC28F]} C290={m[0xC290]:02X} '
              f'F0DB={m[0xF0DB]:02X} E68B={m[0xE68B]} C000={m[0xC000]:02X} '
              f'C2F5={m[0xC2F5]} C2F7={m[0xC2F7]} '
              f'C32E={m[0xC32E]:02X} C32F={m[0xC32F]:02X} PC=${regs[24]:04X}')

    def _dump_png(self, frame):
        sim = self.simulator
        scr = scr_udgs(sim.memory, 0, 0, 32, 24)
        frame_obj = Frame(scr, 2)
        fname = f'{self.out_prefix}_{frame:05d}.png'
        with open(fname, 'wb') as f:
            get_image_writer().write_image([frame_obj], f)

    # --- перехват draw(simulator->python) --------------------------------
    def draw(self, scr, frame, border, keyboard=None):
        self._apply_frame(frame)
        return True

    # --- порт FE: читаем EAR-бит из состояния ленты не нужно (игра без ленты)
    def read_port(self, registers, port):
        if port & 0xC002 == 0xC000 and self.outfffd < 16:
            return self.ay[self.outfffd]
        if port % 2 == 0:
            self._apply_frame(registers[25] // FRAME_DURATION)
            h = (port // 256) ^ 0xFF
            v = 0x40
            i = 0
            while h:
                if h % 2:
                    v |= self.keyboard[i]
                h //= 2
                i += 1
            return v ^ 0xFF
        # Kempston ZX1: порт $xx1F
        if port & 0x1F == 0x1F and not port & 0xE0:
            self._apply_frame(registers[25] // FRAME_DURATION)
            return self.kemp
        return 0xFF


def main():
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    script_path, out_prefix = sys.argv[1:3]
    tstates = 60 * 50 * FRAME_DURATION  # 60 секунд эму-времени по умолчанию
    png_every = 250
    ovc = False
    for i, a in enumerate(sys.argv):
        if a == '--tstates' and i + 1 < len(sys.argv):
            tstates = int(sys.argv[i + 1])
        if a == '--png-every' and i + 1 < len(sys.argv):
            png_every = int(sys.argv[i + 1])
        if a == '--ovc':
            ovc = True

    script, checkpoints = parse_script(script_path)

    snapshot = Snapshot.get('hate/tmp/HATE.z80')
    sim_cls = CSimulator or Simulator
    simulator = from_snapshot(sim_cls, snapshot, {}, {'iff': 1},
                              {'fast_djnz': True, 'fast_ldir': True})
    tracer = ScriptedTracer(simulator, script, checkpoints, png_every, out_prefix,
                            ovc=ovc)
    simulator.set_tracer(tracer)

    exec_map = set()
    start = snapshot.pc
    print(f'CIM: {sim_cls.__name__}, старт с ${start:04X}, бюджет {tstates} T '
          f'(~{tstates // FRAME_DURATION} кадров)')
    stop_cond, ops = simulator.trace(start, None, 0, tstates, 1,
                                     tracer.draw, exec_map, tracer.keyboard,
                                     None, None)
    print(f'Стоп (cond={stop_cond}), операций: {ops}, '
          f'кадр: {simulator.registers[25] // FRAME_DURATION}')

    with open(out_prefix + '_exec.map', 'w') as f:
        for addr in sorted(exec_map):
            f.write(f'${addr:04X}\n')
    ram_addrs = [a for a in exec_map if a >= 0x4000]
    print(f'Wrote {out_prefix}_exec.map: всего {len(exec_map)}, в RAM {len(ram_addrs)}')
    if ovc:
        print(f'Оверлеев ($8000-$CFFF) уникальных: {tracer.ov_count}')


if __name__ == '__main__':
    main()
