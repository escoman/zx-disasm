"""One screenshot of the game at given frames, no rendering model involved.

  python3 tools/shot.py <script.txt> <frame> <out.png> [more frames...]

Runs headless from tmp/HATE.z80 with the keyboard script and dumps the screen
memory at $4000/$5800 as an RGB PNG (2x), once per requested frame (the extra
frames are appended to the file name).  Unlike tools/frame_png.py, which reads a
units.py capture, this works in every game state - title, menu, HUD windows -
because it does not need the blitter to be reached.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hate_z80  # noqa: F401,E402

HATE = os.path.dirname(HERE)
sys.path.insert(0, HATE)

from skoolkit import CSimulator                       # noqa: E402
from skoolkit.simutils import from_snapshot           # noqa: E402
from skoolkit.snapshot import Snapshot                # noqa: E402

import frame_png                                      # noqa: E402
from coverage import parse_script, FRAME_DURATION, ScriptedTracer  # noqa: E402

PC, T = 24, 25


def main():
    script_path, frame0, out = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    targets = sorted(set([frame0] + [int(x) for x in sys.argv[4:]]))
    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tracer = ScriptedTracer(sim, script, [], 0, '/tmp/shot', ovc=False)
    sim.set_tracer(tracer)
    mem, regs = sim.memory, sim.registers
    for want in targets:
        sim.trace(regs[PC], 0x10000, 0, want * FRAME_DURATION, 1,
                  tracer.draw, None, tracer.keyboard, None, None)
        name = out if want == frame0 else out.replace('.png', '_%d.png' % want)
        pre = {'screen': list(mem[0x4000:0x5800]),
               'attr': list(mem[0x5800:0x5B00])}
        frame_png.frame(pre).save(name)
        print('кадр %d PC=%04X -> %s' % (regs[T] // FRAME_DURATION,
                                         regs[PC], name))


if __name__ == '__main__':
    main()
