"""Record the blitter's actual (screen address <- ring address) walk.

  python3 tools/blitwalk.py <script.txt> <frame> <out.json>

The blitter at $D35E pops screen addresses off $D5B2 and copies 4 or 8 ring
bytes to each of them.  The order of the POPs is fixed by the code:

    $D378  POP HL                     once   per outer iteration C
    $D38A  POP HL                     15x
    $D3AC  POP HL                     once
    $D3C5  POP HL                     16x

so one pass performs 16 * 33 = 528 POPs, and after each POP the next
instruction is `LD A,(DE)` at $D379 / $D38B / $D3AD / $D3C6.  We stop there and
read DE (the ring source) and HL (the screen destination): the walk is then
measured, not guessed.  Along with the walk we keep the ring, the screen, the
attributes and the display lists of that frame.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hate_z80  # noqa: F401,E402

HATE = os.path.dirname(HERE)
sys.path.insert(0, HATE)

from skoolkit import CSimulator                       # noqa: E402
from skoolkit.simutils import from_snapshot           # noqa: E402
from skoolkit.snapshot import Snapshot                 # noqa: E402

from coverage import parse_script, FRAME_DURATION, ScriptedTracer  # noqa: E402

PC, T, D, E, H, L = 24, 25, 4, 5, 6, 7
A, F, B, C = 0, 1, 2, 3
IXH, IXL = 8, 9

# (stop address immediately after the POP, run length) per outer iteration.
# $D388 loads B with $0F, so the first DJNZ loop turns 15 times; $D3C3 loads B
# with $10, so the second turns 16.  4 + 15*8 + 4 + 16*8 = 256 screen bytes.
SEQ = [(0xD379, 4)] + [(0xD38B, 8)] * 15 + [(0xD3AD, 4)] + [(0xD3C6, 8)] * 16


def main():
    script_path, frame_target, out_path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tracer = ScriptedTracer(sim, script, [], 0, '/tmp/blit', ovc=False)
    sim.set_tracer(tracer)
    mem, regs = sim.memory, sim.registers

    def run_to(addr, budget=3):
        for _ in range(4):
            if regs[PC] == addr:
                return True
            sim.trace(regs[PC], addr, 0, regs[T] + budget * FRAME_DURATION, 1,
                      None, None, tracer.keyboard, None, None)
        return regs[PC] == addr

    def step(n):
        """Execute exactly n instructions from the current PC."""
        sim.trace(regs[PC], 0x10000, n, regs[T] + 10 ** 6, 1, None, None,
                  tracer.keyboard, None, None)

    sim.trace(snapshot.pc, 0x10000, 0, frame_target * FRAME_DURATION, 1,
              tracer.draw, None, tracer.keyboard, None, None)
    if not run_to(0xD35E):
        print('$D35E не достигнут')
        return
    frame = regs[T] // FRAME_DURATION
    ring = [mem[a] for a in range(0x8000, 0xC000)]
    walk = []
    for _c in range(16):
        for addr, length in SEQ:
            if not run_to(addr):
                print('сорвано на %d-м POP: ждали %04X' % (len(walk), addr))
                break
            walk.append([regs[H] * 256 + regs[L], regs[D] * 256 + regs[E],
                         length, regs[C]])
            step(1)                       # execute LD A,(DE) and move on
        else:
            continue
        break
    run_to(0xD3F2)
    screen = [mem[a] for a in range(0x4000, 0x5800)]
    attr = [mem[a] for a in range(0x5800, 0x5B00)]
    with open(out_path, 'w') as f:
        json.dump({'frame': frame, 'c32e': mem[0xC32E], 'c32f': mem[0xC32F],
                   'd14d': mem[0xD14D] | (mem[0xD14E] << 8),
                   'd14f': mem[0xD14F] | (mem[0xD150] << 8),
                   'walk': walk, 'ring': ring, 'screen': screen,
                   'attr': attr}, f)
    print('кадр %d: POP-ов %d из %d, C32E=%02X C32F=%02X -> %s'
          % (frame, len(walk), 16 * len(SEQ), mem[0xC32E], mem[0xC32F], out_path))
    for w in walk[:12] + walk[17:26]:
        print('   ->%04X  <-%04X  len %d  C=%02X' % (w[0], w[1], w[2], w[3]))


if __name__ == '__main__':
    main()
