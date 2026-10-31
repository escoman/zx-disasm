"""Extract H.A.T.E. beeper audio (title music + in-game SFX) to WAV.

  python3 tools/sound_extract.py title [frames]
  python3 tools/sound_extract.py sfx

title: runs tmp/HATE.z80 (attract/title), captures every OUT($FE) speaker flip
       and writes sound/title_music.wav + sound/title_music.txt (flip log with
       the PC of each OUT, i.e. the player routine that produced it).

sfx:   runs the snapshot through the menu into the mission (only to make the
       engine bank $F2E2 resident), then executes a 12-byte stub per request
       bit: "LD A,1<<k / LD ($F2BF),A / CALL $F2C0 / OUT ($FE),A / JP stop" —
       i.e. exactly the call the game makes each frame (LD050_1). Each run is
       written as sound/sfx_<k>_<kind>.wav, with no gameplay noise mixed in.

Both modes record with the SkoolKit beeper writer (skoolkit.audio), so the WAV
is a faithful reconstruction of the 1-bit speaker: the delay between flips is
measured in real Z80 T-states at 3.5 MHz (48K timing, 44100 Hz samples).
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import hate_z80  # noqa: F401,E402

HATE = os.path.dirname(HERE)
OUT = os.path.join(HATE, 'sound')

from skoolkit.simutils import from_snapshot           # noqa: E402
from skoolkit.snapshot import Snapshot                # noqa: E402
from skoolkit.audio import AudioWriter, BeeperOptions  # noqa: E402

sys.path.insert(0, HATE)
from coverage import FRAME_DURATION, ScriptedTracer   # noqa: E402

PC, T = 24, 25
CPU_HZ = 3500000  # 48K

TITLE_SCRIPT = [(1, 5, ['SPACE']), (6, None, [])]

# 30 idle -> any key (leave "press any key") -> menu -> '1' keyboard
# -> '6' START GAME -> SPACE (gate $F1D1) -> mission
MISSION_SCRIPT = [
    (30, 3, ['SPACE']), (33, 150, []), (183, 3, ['SPACE']), (186, 60, []),
    (246, 3, ['1']), (249, 60, []), (309, 3, ['6']), (312, 3, ['SPACE']),
    (315, None, []),
]
MISSION_FRAMES = 700     # enough to pass the menu and get $F2E2 resident

STUB = 0x9000            # scratch code area (backbuffer, no longer needed)
STACK = 0x9FFE
STOP = 0xFF00            # JP target that ends a stub run
MAX_T = 6 * CPU_HZ       # hard cap per sound


class AudioTracer(ScriptedTracer):
    """ScriptedTracer already logs speaker flips; we add the PC of each OUT."""

    def __init__(self, simulator, script, trigger=None):
        super().__init__(simulator, script, [], 0, os.path.join(HATE, 'tmp'),
                         ovc=False)
        self.flips = []          # (t_state, pc, value)
        self.spk4 = None         # last speaker bit seen by _capture
        self.trigger = trigger or {}
        self.write_port = self._capture

    def _capture(self, registers, port, value, offset):
        if port % 2 == 0 and self.spk4 != value & 0x10:
            self.spk4 = value & 0x10
            self.flips.append((registers[T], registers[PC], value & 0x18))
        self._write_port(registers, port, value, offset)

    def draw(self, scr, frame, border, keyboard=None):
        self._apply_frame(frame)
        if frame in self.trigger:
            # raise one request bit; the engine's per-frame service routine
            # (LD050_1: CALL $F2C0) plays it and clears $F2BF itself
            self.simulator.memory[0xF2BF] = 1 << self.trigger[frame]
            print(f'frame {frame}: $F2BF = {1 << self.trigger[frame]:02X}')
        return True


def make_sim():
    snap = Snapshot.get(os.path.join(HATE, 'tmp', 'HATE.z80'))
    sim = from_snapshot(__import__('skoolkit').CSimulator, snap, {}, {'iff': 1},
                        {})
    return snap, sim


def delays_of(flips):
    return [b[0] - a[0] for a, b in zip(flips, flips[1:])]


def wav(path, delays):
    if not delays:
        print(f'{path}: no speaker flips, skipped')
        return 0
    with open(path, 'wb') as f:
        AudioWriter().write_audio(f, delays,
                                  BeeperOptions(100, False, False, 0, False))
    print(f'{path}: {len(delays)} flips, {sum(delays) / CPU_HZ:.2f} s')
    return sum(delays) / CPU_HZ


def run_title(frames):
    snap, sim = make_sim()
    tr = AudioTracer(sim, TITLE_SCRIPT)
    sim.set_tracer(tr)
    sim.trace(snap.pc, 0x10000, 0, frames * FRAME_DURATION, 1, tr.draw, None,
              tr.keyboard, None, None)
    os.makedirs(OUT, exist_ok=True)
    wav(os.path.join(OUT, 'title_music.wav'), delays_of(tr.flips))
    with open(os.path.join(OUT, 'title_music.txt'), 'w') as f:
        f.write('# t_state frame pc value  (speaker flips, OUT ($FE),A)\n')
        for t, pc, v in tr.flips:
            f.write(f'{t} {t // FRAME_DURATION} ${pc:04X} {v:02X}\n')
    pcs = {}
    for _, pc, _ in tr.flips:
        pcs[pc] = pcs.get(pc, 0) + 1
    print('flips by OUT site: ' +
          ', '.join(f'${p:04X}x{n}' for p, n in sorted(pcs.items())))


class PlainTracer:
    def __init__(self, simulator):
        self.simulator = simulator
        self.border = 7
        self.spkr = None
        self.flips = []

    def write_port(self, registers, port, value, offset):
        if port % 2 == 0:
            # only a change of bit 4 is a speaker edge: the generators write
            # OUT ($FE),A on every DJNZ iteration with A unchanged in between
            if self.spkr != value & 0x10:
                self.spkr = value & 0x10
                self.flips.append((registers[T], registers[PC], value & 0x18))


def play_stub(sim, bit):
    """Run the engine's own sound generator for request bit `bit`.

    Stub mirrors the game's per-frame call (LD050_1: CALL $F2C0):
        LD A,1<<bit / LD ($F2BF),A / CALL $F2C0 / XOR A / OUT ($FE),A / JP STOP
    """
    mem = sim.memory
    code = [0x3E, 1 << bit, 0x32, 0xBF, 0xF2, 0xCD, 0xC0, 0xF2,
            0xAF, 0xD3, 0xFE, 0xC3, STOP & 0xFF, STOP >> 8]
    for i, b in enumerate(code):
        mem[STUB + i] = b
    reg = sim.registers
    reg[PC] = STUB
    reg[12] = STACK                 # SP is one 16-bit register
    reg[13] = 0                     # SP2 (alternate)
    reg[26] = 0                     # IFF (DI)
    tr = PlainTracer(sim)
    sim.set_tracer(tr)
    # NB: 4th arg of trace() is an absolute T-state threshold, not a duration
    sim.trace(STUB, STOP, 0, reg[T] + MAX_T, 0, None, None, None, None, None)
    return tr.flips


def run_sfx():
    snap, sim = make_sim()
    tr = AudioTracer(sim, MISSION_SCRIPT)
    sim.set_tracer(tr)
    sim.trace(snap.pc, 0x10000, 0, MISSION_FRAMES * FRAME_DURATION, 1,
              tr.draw, None, tr.keyboard, None, None)
    mem = sim.memory
    table = [bytes(mem[0xF2E2 + 5 * k:0xF2E7 + 5 * k]) for k in range(5)]
    print('$F2E2 table now in memory:')
    for k, rec in enumerate(table):
        print(f'  slot {k}: ' + ' '.join(f'{b:02X}' for b in rec))
    os.makedirs(OUT, exist_ok=True)
    preview = []                     # all five effects, as heard in game
    for k in range(5):
        flips = play_stub(sim, k)
        delays = delays_of(flips)
        kind = 'tone' if table[k][0] == 0 else 'swept'
        wav(os.path.join(OUT, f'sfx_{k}_{kind}.wav'), delays)
        sites = {}
        for _, pc, _ in flips:
            sites[pc] = sites.get(pc, 0) + 1
        print('  OUT sites: ' +
              ', '.join(f'${p:04X}x{n}' for p, n in sorted(sites.items())))
        # the engine re-requests the bit every frame while the condition holds,
        # so in game the effect stutters back to back; 3 repeats reproduce that
        preview += delays * 3 + [2 * FRAME_DURATION] * 25   # 0.5 s pause
    wav(os.path.join(OUT, 'sfx_preview.wav'), preview)


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ''
    if mode == 'title':
        run_title(int(sys.argv[2]) if len(sys.argv) > 2 else 2000)
    elif mode == 'sfx':
        run_sfx()
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    main()
