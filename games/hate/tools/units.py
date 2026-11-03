"""Capture the object (unit) patch stream the engine replays into the ring.

  python3 tools/units.py <script.txt> <frame> <out.json> [--frames N] [--bank]

Every frame, right after the blitter has finished, the engine runs

    $D3F2  LD SP,($C331)
    $D3F6  CALL $DD6C

and $DD6C walks two display lists ($FE64, then $FF00), pulling runs of bytes out
of the patch stream at $F800 and LDIR-ing them into the ring ($DDC9).  So the
whole of the object pass is a pure function of ($F800 stream, $FE64, $FF00) and
we can record it by bracketing the call:

  * stop at $D3F2  - the ring/stream state *before* the pass;
  * stop at $D3F9  - the state *after* it (the CALL has returned).

The diff of the two ring images is exactly what the units drew, and the run
lengths are given by the class code of each list element, so the diff can also
be regenerated offline and compared - that comparison is the test of the whole
model of the object renderer.

Output JSON: per frame, ring before/after, the $F800 stream, both lists, the 16
records at $C000, the $C300 shape table, the $C400 pattern bank, the screen and
its attributes, plus $C32E/$C32F/$D14D and $DD6A.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hate_z80  # noqa: F401,E402  (puts skoolkit on sys.path)

HATE = os.path.dirname(HERE)
sys.path.insert(0, HATE)

from skoolkit import CSimulator                       # noqa: E402
from skoolkit.simutils import from_snapshot           # noqa: E402
from skoolkit.snapshot import Snapshot                 # noqa: E402

from coverage import parse_script, FRAME_DURATION, ScriptedTracer  # noqa: E402

PC, T, D, E, H, L = 24, 25, 4, 5, 6, 7
RING_LO, RING_HI = 0x8000, 0xC000
BEFORE, AFTER = 0xD3F2, 0xD3F9

# The blitter's POP order ($D378 once, $D38A 15x, $D3AC once, $D3C5 16x per
# outer iteration); each POP is followed by LD A,(DE) at the address below.
SEQ = [(0xD379, 4)] + [(0xD38B, 8)] * 15 + [(0xD3AD, 4)] + [(0xD3C6, 8)] * 16


def main():
    script_path, frame_target, out_path = sys.argv[1], int(sys.argv[2]), sys.argv[3]
    nframes = int(sys.argv[sys.argv.index('--frames') + 1]) \
        if '--frames' in sys.argv else 6
    want_bank = '--bank' in sys.argv
    # force the player's unit kind, see $D242: ($C008) = 2 * (kind & 15)
    kind = int(sys.argv[sys.argv.index('--kind') + 1]) \
        if '--kind' in sys.argv else None

    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tracer = ScriptedTracer(sim, script, [], 0, '/tmp/units', ovc=False)
    sim.set_tracer(tracer)
    mem, regs = sim.memory, sim.registers

    def grab(addr, n):
        return [mem[(addr + k) & 0xFFFF] for k in range(n)]

    def word(addr):
        return mem[addr] | (mem[(addr + 1) & 0xFFFF] << 8)

    def run_to(addr, budget=3):
        """Run until PC == addr; the instruction at addr is NOT executed.

        The time limit is absolute, so it is re-based on every attempt.
        """
        for _ in range(4):
            if regs[PC] == addr:
                return True
            sim.trace(regs[PC], addr, 0, regs[T] + budget * FRAME_DURATION, 1,
                      None, None, tracer.keyboard, None, None)
        return regs[PC] == addr

    def step():
        """Execute exactly one instruction from the current PC."""
        sim.trace(regs[PC], 0x10000, 1, regs[T] + 10 ** 6, 1, None, None,
                  tracer.keyboard, None, None)

    def capture_walk():
        """Record the blitter's (screen <- ring) walk of the current pass.

        Stored as (screen address, block index, offset inside the page): the
        block index b says which ring page is read, page = (C32E + 2b) % 64.
        """
        if not run_to(0xD35E):
            return None
        c32e, c32f = mem[0xC32E], mem[0xC32F]
        walk = []
        for _c in range(16):
            for addr, length in SEQ:
                if not run_to(addr):
                    return None
                src = regs[D] * 256 + regs[E]
                page = (src >> 8) - 0x80
                walk.append([regs[H] * 256 + regs[L],
                             ((page - c32e) % 64) // 2, (src & 0xFF) - c32f,
                             length])
                step()
        return {'c32e': c32e, 'c32f': c32f, 'walk': walk}

    # --- phase 1: reach the requested frame --------------------------------
    sim.trace(snapshot.pc, 0x10000, 0, frame_target * FRAME_DURATION, 1,
              tracer.draw, None, tracer.keyboard, None, None)

    # --- phase 2: bracket the patch pass for the next few frames -----------
    blit = capture_walk()
    frames = []
    for _ in range(nframes):
        if not run_to(BEFORE):
            break
        if kind is not None:
            # the $FF00 class byte is the record type's low nibble and the shape
            # pointer is the word at $C300 + 2 * that, so these two pokes make
            # the player draw as any kind; the list of *this* pass was built
            # before them, so the first captured pass can still show the old one
            mem[0xC000] = kind
            mem[0xC008] = 2 * (kind & 0x0F)
        f = regs[T] // FRAME_DURATION
        if want_bank:
            # the art page paged in for exactly this pass, so that a shape
            # pointer of this capture can be looked up without guessing
            name = '%s_f%d.bin' % (out_path.rsplit('.', 1)[0], f)
            with open(name, 'wb') as g:
                g.write(bytes(mem[0x4000:0x8000]))
                g.write(bytes(mem[0xC000:0x10000]))
        pre = {
            'ring': grab(RING_LO, RING_HI - RING_LO),
            'stream': grab(0xF800, 0x800),
            'ff00': grab(0xFF00, 0x100),
            'fe64': grab(0xFE64, 0x100),
            'recs': grab(0xC000, 0x100),
            'c300': grab(0xC300, 0x40),
            'c400': grab(0xC400, 0x200),
            'screen': grab(0x4000, 0x1800),
            'attr': grab(0x5800, 0x300),
            'dd6a': word(0xDD6A), 'c32e': mem[0xC32E], 'c32f': mem[0xC32F],
            'd14d': word(0xD14D), 'd14f': word(0xD14F), 'sp': regs[PC],
        }
        if not run_to(AFTER):
            break
        post = {
            'ring': grab(RING_LO, RING_HI - RING_LO),
            'stream': grab(0xF800, 0x800),
            'ff00': grab(0xFF00, 0x100),
            'fe64': grab(0xFE64, 0x100),
            'dd6a': word(0xDD6A),
        }
        changed = [i for i in range(len(pre['ring']))
                   if pre['ring'][i] != post['ring'][i]]
        frames.append({'frame': f, 'pre': pre, 'post': post,
                       'changed': len(changed)})
        print('кадр %d: кольцо изменено %d байт, $FE64[0]=%02X $FF00[0]=%02X '
              '$DD6A=%04X C32E=%02X' % (f, len(changed), pre['fe64'][0],
              pre['ff00'][0], pre['dd6a'], pre['c32e']))

    with open(out_path, 'w') as f:
        json.dump({'script': script_path, 'blit': blit, 'frames': frames}, f)
    print('записано %d кадров -> %s' % (len(frames), out_path))


if __name__ == '__main__':
    main()
