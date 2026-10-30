"""Full instruction-level trace of one (or N) frames, with registers.

  PYTHONPATH=skoolkit python3 tools/xtrace.py <snapshot> <out.txt> [frames]

Uses SkoolKit's C simulator; the per-instruction callback receives PC and
t-states, and we read the register file directly.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import hate_z80  # noqa: F401  (sets up sys.path)

from skoolkit import CSimulator
from skoolkit.simutils import from_snapshot, get_state
from skoolkit.snapshot import Snapshot
from skoolkit.trace import Tracer
from skoolkit.traceutils import disassemble

FRAME = 69888


def main():
    snap_path, out_path = sys.argv[1], sys.argv[2]
    frames = float(sys.argv[3]) if len(sys.argv) > 3 else 1.0
    snapshot = Snapshot.get(snap_path)
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': False, 'fast_ldir': False})
    tracer = Tracer(sim, 7, 0, 0, [0] * 16, 0, True)
    tracer.keyboard = [0] * 8
    sim.set_tracer(tracer)
    regs = sim.registers
    mem = sim.memory
    t0 = regs[25]
    t_end = t0 + int(frames * FRAME)
    out = open(out_path, 'w')
    lines = []

    def tf(pc, instr, t):
        lines.append('%04X %s' % (pc, instr))
        if len(lines) > 20000:
            out.write('\n'.join(lines) + '\n')
            lines.clear()

    def df(pc):
        return disassemble(mem, pc)[0]

    start = regs[24]
    sim.trace(start, 0x10000, 0, t_end, 1, None, None, tracer.keyboard, df, tf)
    out.write('\n'.join(lines) + '\n')
    out.close()
    print('stopped at %04X, t=%d' % (regs[24], regs[25] - t0))


if __name__ == '__main__':
    main()
