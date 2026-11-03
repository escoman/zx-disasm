"""Dump the memory that is paged in while the object pass runs.

  python3 tools/bankdump.py <script.txt> <frame> <out.bin>

Stops at $D3F2 - the last instruction before `CALL $DD6C` - and writes the
$4000-$7FFF page plus the $C000-$FFFF engine RAM side by side, so a shape
pointer taken from a $FF00 element of the matching units.py capture can be
looked up in the art bank.  Also prints the 128K paging register contents.
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

from coverage import parse_script, FRAME_DURATION, ScriptedTracer  # noqa: E402

PC, T = 24, 25
STOP = 0xD3F2


def main():
    script_path, frame_target, out_path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tracer = ScriptedTracer(sim, script, [], 0, '/tmp/bankdump', ovc=False)
    sim.set_tracer(tracer)
    mem, regs = sim.memory, sim.registers

    sim.trace(snapshot.pc, 0x10000, 0, frame_target * FRAME_DURATION, 1,
              tracer.draw, None, tracer.keyboard, None, None)
    for _ in range(4):
        if regs[PC] == STOP:
            break
        sim.trace(regs[PC], STOP, 0, regs[T] + 3 * FRAME_DURATION, 1, None,
                  None, tracer.keyboard, None, None)
    if regs[PC] != STOP:
        print('остановка не достигнута, PC=%04X' % regs[PC])
        return
    page = bytes(mem[0x4000:0x8000])
    with open(out_path, 'wb') as f:
        f.write(page)
        f.write(bytes(mem[0xC000:0x10000]))
    print('кадр %d: %s = $4000-$7FFF (16K) + $C000-$FFFF (16K), PC=%04X'
          % (regs[T] // FRAME_DURATION, out_path, regs[PC]))
    for a in (0x6CE2, 0x7416, 0x747C):
        print('   %04X: %s' % (a, ' '.join('%02X' % mem[a + k]
                                           for k in range(32))))


if __name__ == '__main__':
    main()
