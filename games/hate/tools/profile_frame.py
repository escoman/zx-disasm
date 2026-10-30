"""Run to a frame, then profile / trace exactly one frame.

  python3 tools/profile_frame.py <script> <frame> [outfile]

Writes a per-address execution histogram of the frame (and, if outfile is
given, the full instruction trace of that frame).
"""
import collections
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


class Stop(ScriptedTracer):
    def __init__(self, simulator, script, target):
        super().__init__(simulator, script, [], 0, '/tmp/x', ovc=False)
        self.target = target

    def draw(self, scr, frame, border, keyboard=None):
        self._apply_frame(frame)
        return frame <= self.target


def main():
    script_path, target = sys.argv[1], int(sys.argv[2])
    outfile = sys.argv[3] if len(sys.argv) > 3 else None
    span = int(sys.argv[4]) if len(sys.argv) > 4 else 1
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
    hist = collections.Counter()
    lines = []
    t_end = regs[25] + span * FRAME_DURATION

    def tf(pc, instr, t):
        hist[pc] += 1
        if outfile:
            lines.append('%04X %s' % (pc, instr))

    def df(pc):
        return disassemble(mem, pc)[0]

    # run to the start of the next frame so the frame we trace is complete
    sim.trace(regs[24], 0x10000, 0, t_end, 1, None, None, tracer.keyboard, df, tf)
    if outfile:
        with open(outfile, 'w') as f:
            f.write('\n'.join(lines) + '\n')
    print('frame %d (%d frames): %d instructions, %d distinct addresses'
          % (target, span, sum(hist.values()), len(hist)))
    for addr, n in hist.most_common(40):
        print('  %04X  %6d' % (addr, n))


if __name__ == '__main__':
    main()
