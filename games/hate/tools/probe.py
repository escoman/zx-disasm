"""Run to a frame, then stop at a list of addresses and dump state.

  python3 tools/probe.py <script> <frame> <addr[,addr...]> [--skip N] [--max K]

Each time execution reaches one of the addresses (up to `max` hits), prints a
line with the register file and a handful of watch locations.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hate_z80  # noqa: F401,E402

HATE = os.path.dirname(HERE)

from skoolkit import CSimulator                       # noqa: E402
from skoolkit.simutils import from_snapshot           # noqa: E402
from skoolkit.snapshot import Snapshot                 # noqa: E402
from skoolkit.traceutils import disassemble            # noqa: E402

sys.path.insert(0, HATE)
from coverage import parse_script, FRAME_DURATION, ScriptedTracer  # noqa: E402

A, F, B, C, D, E, H, L, IXh, IXl, IYh, IYl, SP, SP2, I, R = range(16)
PC, T = 24, 25

WATCH = [
    ('C32E', 0xC32E), ('C32F', 0xC32F), ('C3F8', None), ('C3FA', None),
    ('C3FC', None), ('D14D', None), ('D14F', None), ('C2F6', 0xC2F6),
]


class Stop(ScriptedTracer):
    def __init__(self, simulator, script, target):
        super().__init__(simulator, script, [], 0, '/tmp/x', ovc=False)
        self.target = target

    def draw(self, scr, frame, border, keyboard=None):
        self._apply_frame(frame)
        return frame <= self.target


def main():
    script_path, target = sys.argv[1], int(sys.argv[2])
    addrs = [int(x, 16) for x in sys.argv[3].split(',')]
    skip = 0
    maxhits = 40
    for i, a in enumerate(sys.argv):
        if a == '--skip':
            skip = int(sys.argv[i + 1])
        if a == '--max':
            maxhits = int(sys.argv[i + 1])
    bits = int(sys.argv[sys.argv.index('--read') + 1]) if '--read' in sys.argv else 0
    budget = float(sys.argv[sys.argv.index('--budget') + 1]) if '--budget' in sys.argv else 3.0
    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tracer = Stop(sim, script, target)
    sim.set_tracer(tracer)
    sim.trace(snapshot.pc, 0x10000, 0, 10 ** 12, 1, tracer.draw, None,
              tracer.keyboard, None, None)
    mem = sim.memory
    regs = sim.registers

    def rd16(a):
        return mem[a] | (mem[(a + 1) & 0xFFFF] << 8)

    hits = 0
    while hits < maxhits:
        start = regs[PC]
        cond, _ = sim.trace(start, addrs[0] if len(addrs) == 1 else 0x10000, 0,
                            int(regs[T] + budget * FRAME_DURATION), 1, None, None,
                            tracer.keyboard, None, None)
        pc = regs[PC]
        if pc not in addrs:
            print('timeout after %.0f frames (PC=%04X)' % (budget, pc))
            break
        if skip > 0:
            skip -= 1
            continue
        hits += 1
        extra = ''
        if bits:
            extra = ' mem[%04X]=%s' % (bits, bytes(mem[bits:bits + 32]).hex())
        print('%04X SP=%04X HL=%04X DE=%04X BC=%02X%02X A=%02X '
              'C32E=%02X C32F=%02X C3F8=%04X C3FA=%04X C3FC=%04X D14D=%04X D14F=%04X%s'
              % (pc, regs[SP], rd16(6), rd16(4), regs[B], regs[C], regs[A],
                 mem[0xC32E], mem[0xC32F], rd16(0xC3F8), rd16(0xC3FA),
                 rd16(0xC3FC), rd16(0xD14D), rd16(0xD14F), extra))


if __name__ == '__main__':
    main()
