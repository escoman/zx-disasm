"""Drives H.A.T.E. headless, dumps the frame's render intermediates.

  python3 tools/hate_run.py <script.txt> <frames> <outdir> [--dump-at N]

Runs from tmp/HATE.z80 with the same keyboard injection as coverage.py, and at
frame N (the first frame >= N) writes:

  screen.png        the visible screen at that moment
  mem_XXXXXX.bin    raw dumps of the regions of interest
  trace.txt         per-instruction trace of that whole frame (optional)
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hate_z80  # noqa: F401,E402  (puts skoolkit on sys.path)

HATE = os.path.dirname(HERE)

from skoolkit import CSimulator                       # noqa: E402
from skoolkit.simutils import from_snapshot           # noqa: E402
from skoolkit.snapshot import Snapshot                 # noqa: E402
from skoolkit.graphics import Frame, scr_udgs          # noqa: E402
from skoolkit.components import get_image_writer       # noqa: E402

sys.path.insert(0, HATE)
from coverage import parse_script, FRAME_DURATION, ScriptedTracer  # noqa: E402

REGIONS = {
    'screen': (0x4000, 0x5B00),
    'band': (0x6000, 0x6300),
    'code83': (0x8000, 0x8400),
    'backbuf': (0x8400, 0xC000),
    'vars': (0xC000, 0xCF32),
    'engine': (0xCF32, 0xF2E0),
    'top': (0xF2E0, 0x10000),
    'all': (0x4000, 0x10000),
}


class Dumper(ScriptedTracer):
    def __init__(self, simulator, script, outdir, dump_at, trace_file=None):
        super().__init__(simulator, script, [], 0, outdir, ovc=False)
        self.outdir = outdir
        self.dump_at = dump_at
        self.done = False
        self.trace_file = trace_file

    def _dump(self, frame):
        sim = self.simulator
        os.makedirs(self.outdir, exist_ok=True)
        for name, (lo, hi) in REGIONS.items():
            with open(os.path.join(self.outdir, f'{name}.bin'), 'wb') as f:
                f.write(bytes(sim.memory[lo:hi]))
        scr = scr_udgs(sim.memory, 0, 0, 32, 24)
        with open(os.path.join(self.outdir, 'screen.png'), 'wb') as f:
            get_image_writer().write_image([Frame(scr, 2)], f)
        regs = sim.registers
        with open(os.path.join(self.outdir, 'frame.txt'), 'w') as f:
            f.write(f'frame={frame} t={regs[25]} PC=${regs[24]:04X} SP=${regs[12]:04X}\n')
        print(f'dumped frame {frame} to {self.outdir}')

    def draw(self, scr, frame, border, keyboard=None):
        self._apply_frame(frame)
        if not self.done and frame >= self.dump_at:
            self.done = True
            self._dump(frame)
            return False
        return True


def main():
    script_path, frames, outdir = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    dump_at = 0
    for i, a in enumerate(sys.argv):
        if a == '--dump-at':
            dump_at = int(sys.argv[i + 1])
    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tracer = Dumper(sim, script, outdir, dump_at)
    sim.set_tracer(tracer)
    sim.trace(snapshot.pc, 0x10000, 0, frames * FRAME_DURATION, 1,
              tracer.draw, None, tracer.keyboard, None, None)
    if not tracer.done:
        print('dump point not reached')


if __name__ == '__main__':
    main()
