"""How the two HUD panels get painted.

  python3 tools/hud.py <script.txt> <frame> <out.json> [--frames N] [--walk]

$DDE7 walks the display list at $FE64 record by record ($DDED is the head, the
terminator is class $FF, the tail jumps to $EAE3).  Every record is one panel
element, and the walk does two things to it:

  1. plot a mask into the scratch page at $6500;
  2. AND that mask onto a destination taken from the record itself.

Record layout (8 bytes, the same list as the background elements of §4.7):

  [0] class     <4 -> wide row, 17 bytes  ($DE08/$DECB)
                4..$0A -> narrow row, 33  ($DF0F/$DFAA)
                >=$0B -> third branch $E008
  [1] E         sub-shape selector 0..3: template offset + primitive program
  [2,3] BC      pointer to the element's descriptor stream; each primitive
                consumes one byte, `& $1F` gives the scratch column it ends at
  [4] rows      how many rows the blit writes
  [5] offset    column of the scratch page the blit starts from
  [6,7] dest    where the mask is ANDed to

The plotters ($DE08/$DF0F) first clear the scratch to $FF, then run a fixed
program of `LD D,n / CALL $E0xx`.  Every $E0xx is `scratch &= template`, where
the template is a row of the panel bank at $5B60..$5EF0 - the page paged in at
$4000 - and the primitive's shape (runs of 1/2/3/4/6 bytes with +7/+11 skips)
comes from $E139..$E15B.  The scratch page therefore ends up as the AND of the
panel's template rows, and $DECB/$DFAA composites it into the destination with
the same +$1FC per-row shear the unit sprites use ($DF01..$DF07).

This tool stops at every primitive and at every blit, so the mask grows one run
at a time in the capture and each destination byte is attributable.
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hate_z80  # noqa: F401,E402

HATE = os.path.dirname(HERE)
sys.path.insert(0, HATE)

from skoolkit import CSimulator                        # noqa: E402
from skoolkit.simutils import from_snapshot            # noqa: E402
from skoolkit.snapshot import Snapshot                 # noqa: E402
from skoolkit.traceutils import disassemble            # noqa: E402

from coverage import parse_script, FRAME_DURATION, ScriptedTracer  # noqa: E402

PC, T = 24, 25
B, C, D, E, H, L, A = 2, 3, 4, 5, 6, 7, 1

WATCH = {
    0xDE08: 'plot wide', 0xDF0F: 'plot narrow', 0xE008: 'plot third',
    0xDECB: 'blit 17B/row', 0xDFAA: 'blit 33B/row', 0xE03A: 'blit third',
    0xDDED: 'record head', 0xEAE3: 'end of walk', 0xD35E: 'landscape blit',
    0xD3F2: 'before object pass', 0xD9D6: 'list builder', 0xD050: 'frame logic',
    0xEDCA: 'window engine', 0xEE17: 'window text',
}
# the primitive bodies, in disassembly order
PLOT = (0xE05C, 0xE06B, 0xE075, 0xE084, 0xE08E, 0xE09D, 0xE0A7, 0xE0B6,
        0xE0C6, 0xE0D5, 0xE0DF, 0xE0EE, 0xE0F8, 0xE107, 0xE118, 0xE127)

# The plot programs, transcribed from $DE2C..$DEC8 (wide) and $DF34..$DFA7
# (narrow).  Each step is (scratch offset D, primitive address), except the
# first of every mode, which loads the whole scratch address instead.
WIDE = {
    0: [(0x6502, 0xE0D5), (4, 0xE0DF), (6, 0xE0F8), (8, 0xE118),
        (0x17, 0xE0C6), (0x19, 0xE0DF)],
    1: [(0x6503, 0xE0EE), (5, 0xE0F8), (7, 0xE118),
        (0x16, 0xE0C6), (0x18, 0xE0DF), (0x1A, 0xE0F8)],
    2: [(0x6504, 0xE107), (6, 0xE118), (0x15, 0xE0C6),
        (0x17, 0xE0DF), (0x19, 0xE0F8), (0x1B, 0xE118)],
    3: [(0x6505, 0xE127), (0x14, 0xE0C6), (0x16, 0xE0DF),
        (0x18, 0xE0F8), (0x1A, 0xE118), (0x29, 0xE0C6)],
}
NARROW = {
    0: [(0x6502, 0xE09D), (4, 0xE0A7), (0x0D, 0xE05C), (0x0F, 0xE075)],
    1: [(0x6503, 0xE0B6), (0x0C, 0xE05C), (0x0E, 0xE075), (0x10, 0xE08E)],
    2: [(0x6500, 0xE06B), (2, 0xE075), (4, 0xE08E), (6, 0xE0A7)],
    3: [(0x6501, 0xE084), (3, 0xE08E), (5, 0xE0A7), (0x0E, 0xE05C)],
}
# template base per class band: $DE1C..$DE29 and $DF23..$DF31
WIDE_BASE = {0: 0x5DE8, 2: 0x5EF0}
NARROW_BASE = {10: 0x5B60}
# mode offset added to the base, $DE3C/$DE5E/$DE83/$DEA8 and $DF44/...
WIDE_STEP = {0: 0, 1: 0x40, 2: 0x83, 3: 0xC7}
NARROW_STEP = {0: 0x40, 1: 0x60, 2: 0, 3: 0x20}


def program(cls, mode):
    """The (scratch offset, primitive) calls this record will make."""
    return (WIDE if cls < 4 else NARROW)[mode]


def template_base(cls, mode):
    """HL at the first primitive: which panel-bank row the mask comes from."""
    if cls < 4:
        base = WIDE_BASE.get(cls, 0x5CE0)
        return base + WIDE_STEP[mode]
    if cls == 10:
        return 0x5B60
    return (0x5C60 if cls >= 8 else 0x5BE0) + NARROW_STEP[mode]


def main():
    script_path = sys.argv[1]
    frame_target = int(sys.argv[2])
    out_path = sys.argv[3]
    nframes = int(sys.argv[sys.argv.index('--frames') + 1]) \
        if '--frames' in sys.argv else 1
    walk = '--walk' in sys.argv

    script, _ = parse_script(script_path)
    snapshot = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(CSimulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tracer = ScriptedTracer(sim, script, [], 0, '/tmp/hud', ovc=False)
    sim.set_tracer(tracer)
    mem, regs = sim.memory, sim.registers

    def word(addr):
        return mem[addr] | (mem[(addr + 1) & 0xFFFF] << 8)

    def grab(addr, n):
        return [mem[(addr + k) & 0xFFFF] for k in range(n)]

    def run_to(addr, budget=3):
        """Run until PC == addr; the instruction at addr is NOT executed."""
        for _ in range(4):
            if regs[PC] == addr:
                return True
            sim.trace(regs[PC], addr, 0, regs[T] + budget * FRAME_DURATION, 1,
                      None, None, tracer.keyboard, None, None)
        return regs[PC] == addr

    def step():
        sim.trace(regs[PC], 0x10000, 1, regs[T] + 10 ** 6, 1, None, None,
                  tracer.keyboard, None, None)

    # --- phase 1: reach the requested frame -------------------------------
    sim.trace(snapshot.pc, 0x10000, 0, frame_target * FRAME_DURATION, 1,
              tracer.draw, None, tracer.keyboard, None, None)

    # --- phase 2: a few frames with a hit recorder, to fix the order ------
    hits = []
    watching = [True]
    base = regs[T]

    def tf(pc, instr, t):
        if watching[0] and (pc in WATCH or pc in PLOT):
            hits.append([pc, t - base, regs[H] * 256 + regs[L],
                         regs[D] * 256 + regs[E], regs[B] * 256 + regs[C],
                         regs[A], word(0xDD66)])

    # the C simulator only invokes the trace callback when a disassembler is
    # supplied alongside it, so give it the cheapest one that works
    sim.trace(regs[PC], 0x10000, 0, base + nframes * FRAME_DURATION, 1,
              tracer.draw, None, tracer.keyboard,
              lambda pc: disassemble(mem, pc)[0], tf)
    watching[0] = False

    names = {p: ('call $%04X' % p) for p in PLOT}
    names.update(WATCH)
    print('кадр %d: %d попаданий' % (frame_target, len(hits)))
    for pc, t, hl, de, bc, a, dd66 in hits[:140]:
        print('  t=%6d %04X %-18s HL=%04X DE=%04X BC=%04X A=%02X ($DD66)=%04X'
              % (t, pc, names.get(pc, '?'), hl, de, bc, a, dd66))
    if len(hits) > 140:
        print('  ... ещё %d' % (len(hits) - 140))

    out = {'script': script_path, 'frame': frame_target,
           'hits': [{'pc': h[0], 't': h[1], 'hl': h[2], 'de': h[3], 'bc': h[4],
                     'a': h[5], 'dd66': h[6]} for h in hits]}

    # --- phase 3: walk $FE64 record by record ------------------------------
    if walk:
        recs = []
        while True:
            if regs[PC] == 0xEAE3:
                break
            if not run_to(0xDDED):
                print('  стоянка: PC=%04X' % regs[PC])
                break
            head = word(0xDD66)
            rec = grab(head, 8)
            cls, mode = rec[0], rec[1]
            if cls == 0xFF:
                break
            entry = 0xDECB if cls < 4 else (0xDFAA if cls < 0x0B else 0xE03A)
            r = {'addr': head, 'rec': rec, 'cls': cls, 'mode': mode,
                 'dest': rec[6] | (rec[7] << 8), 'rows': rec[4],
                 'off': rec[5], 'base': template_base(cls, mode),
                 'desc': grab(rec[2] | (rec[3] << 8), 8), 'steps': []}
            for scratch_at, prim in program(cls, mode):
                if not run_to(prim):
                    break
                r['steps'].append({
                    'prim': prim, 'hl': regs[H] * 256 + regs[L],
                    'de': regs[D] * 256 + regs[E],
                    'd': regs[D], 'bc': regs[B] * 256 + regs[C],
                    'dde6': mem[0xDDE6], 'buf': grab(0x6500, 0x44),
                    'tpl': grab(regs[H] * 256 + regs[L], 0x20),
                })
                step()
            if not run_to(entry):
                break
            pre = {'buf': grab(0x6500, 0x44),
                   'ring': grab(0x8000, 0x4000),
                   'scr': grab(0x4000, 0x1A00),
                   'page': grab(0x5B00, 0x500)}
            stop = {0xDECB: 0xDF0C, 0xDFAA: 0xE005, 0xE03A: 0xE059}[entry]
            # $DF0C/$E005 sit past the row loop, so they are hit once per record
            if not run_to(stop):
                break
            r['end_hl'] = regs[H] * 256 + regs[L]
            step()
            if not run_to(0xDDED):
                break
            post = {'ring': grab(0x8000, 0x4000), 'scr': grab(0x4000, 0x1A00)}
            r['blit'] = {'entry': entry,
                         'ring_changed': [[i + 0x8000, pre['ring'][i],
                                          post['ring'][i]]
                                         for i in range(0x4000)
                                         if pre['ring'][i] != post['ring'][i]],
                         'scr_changed': [[i + 0x4000, pre['scr'][i],
                                          post['scr'][i]]
                                         for i in range(0x1A00)
                                         if pre['scr'][i] != post['scr'][i]]}
            # the byte-exact check `(dest & mask) == dest_after` is done by the
            # caller from 'buf', 'blit.ring_changed' and the record fields
            r['buf'] = pre['buf']
            r['page'] = pre['page']
            recs.append(r)
            print('запись %04X: класс %02X режим %d строк %d см %d dest %04X, '
                  'кольцо +%d экран +%d'
                  % (head, cls, mode, rec[4], rec[5], r['dest'],
                     len(r['blit']['ring_changed']), len(r['blit']['scr_changed'])))
        out['records'] = recs

    with open(out_path, 'w') as f:
        json.dump(out, f)
    print('записано -> %s' % out_path)


if __name__ == '__main__':
    main()
