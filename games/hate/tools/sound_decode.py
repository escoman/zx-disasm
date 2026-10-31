"""Decode H.A.T.E. sound data (title event stream + in-game SFX table).

  python3 tools/sound_decode.py            # human summary
  python3 tools/sound_decode.py --json      # machine-readable dump

Sources (already-generated listings; the engine bank is only resident in the
mission base, not in tmp/HATE.z80, so we read the -final.skool DEFB rows):
  data/HATE_title_final.skool   -> title bytecode + event stream $8400-$85FF
  data/HATE_engine_final.skool  -> in-game SFX generator + table $F2A3/$F2E2
"""
import json
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
HATE = os.path.dirname(HERE)
DATA = os.path.join(HATE, 'data')

# matches both plain " $F2E8 DEFB ..." rows and bank/label rows
# "b$F2E0 DEFB ...", "c$CF32 ..." — capture the first address only.
DEB = re.compile(r'^[bc*\$]*\$(?:([0-9A-Fa-f]{4})\s+)?DEFB\s+(.*)$')
ADDRROW = re.compile(r'^\s*\$?([0-9A-Fa-f]{4})\s+DEFB\s+(.*)$')
STARTROW = re.compile(r'^[bc]\$(?:([0-9A-Fa-f]{4})\s+DEFB\s+(.*))?$')


def load_mem(path):
    """Return {addr: byte} from every DEFB row of a skool file."""
    mem = {}
    with open(path) as f:
        for line in f:
            if 'DEFB' not in line and 'DEFW' not in line:
                continue
            # leading optional bank/code marker: 'b$1234 DEFB' / 'c$..' / ' $1234 DEFB'
            m = re.match(r'^\s*[bc*]?\$?([0-9A-Fa-f]{4})\s+DEFB\s+(.*)$', line)
            if not m:
                continue
            addr = int(m.group(1), 16)
            for i, tok in enumerate(re.findall(r'\$([0-9A-Fa-f]{2})', m.group(2))):
                mem[addr + i] = int(tok, 16)
    return mem


def get(mem, a):
    return mem.get(a, 0)


def dump(mem, lo, hi):
    return [get(mem, a) for a in range(lo, hi)]


# ------------------------------------------------------------- in-game SFX
# Generator LF1AA_20 ($F2A3): record = [flag, B, C, D, E]; flag!=0 plays a
# frequency-swept beeper pulse:
#   HL = (B<<8)|C   sweep base (drives per-step delta:  D = D - H each step)
#   D              inner DJNZ count  ~ half-period (coarse pitch)
#   E              number of steps   (sweep length)
# Generator LF1AA_16 ($F288, via $F284 when flag==0): record = [00, lo, hi, ..];
#   HL = (hi<<8)|lo  -> pitch = low byte (toggles bit4 every 256 D-steps),
#                       length = high byte. Plain square-wave tone.
# Request map: LF1AA_24 ($F2C0) reads the 5-bit bitset $F2BF; set bit k selects
#   table slot k at $F2E2 + 5*k.  Slots: 0..4.
def decode_engine(mem):
    print('== in-game SFX: generator code ==')
    print('  $F288 LF1AA_16  square-wave tone   (pitch=L, length=H)')
    print('  $F2A3 LF1AA_20  swept pulse        (HL=B:C, inner=D, steps=E)')
    print('  $F2C0 LF1AA_24  resolver: $F2BF bit k -> slot $F2E2+5*k')
    print('  $F2D9 LF1AA_27  flag gate: 0 -> tone($F284), else -> swept($F2A3)')
    print('\n== $F2BF bit -> sound slot (5 records @ $F2E2, 5 bytes each) ==')
    names = {0: 'bit0', 1: 'bit1', 2: 'bit2', 3: 'bit3', 4: 'bit4'}
    slots = {}
    for k in range(5):
        base = 0xF2E2 + 5 * k
        flag, b, c, d, e = dump(mem, base, base + 5)
        if flag:
            hl = (b << 8) | c
            kind = 'swept'
            detail = f'HL=${hl:04X} inner=${d:02X} steps=${e:02X}'
        else:
            hl = (c << 8) | b
            kind = 'tone'
            detail = f'HL=${hl:04X} (pitch=${hl & 0xFF:02X}, len=${hl >> 8:02X})'
        slots[k] = dict(base=base, raw=[flag, b, c, d, e], kind=kind)
        print(f'  slot {k} (${names[k]}) @${base:04X}: [{flag:02X},{b:02X},'
              f'{c:02X},{d:02X},{e:02X}]  {kind}: {detail}')
    # the 5 request bits are set by these gameplay sites:
    print('\n== who sets $F2BF (SET n,(HL), HL=$F2BF) ==')
    print('  bit0 @LE3BF: firing / shooting')
    print('  bit1 @LE9CE_30 & LE3BF_18: target hit / explosion')
    print('  bit2 @LE3BF_14: crash / being hit')
    print('  bit3 @LE5C9_4: game-over / mission-end sting')
    print('  bit4 @LE889_0: (score/pickup tick)')
    return slots


# ------------------------------------------------------------- title music
# Interpreter $8000 runs one bytecode step per frame from the INT/RST8 handler
# and clocks a two-voice pulse-proportional beeper.
#   L8243 ($8243) per-frame sound dispatcher: walks event stream via self-mod
#     pointer $8250/$8251; scan sentinel (L8243_0) resets it to $85A3 on code 1.
#     event codes: 1=wrap  2=rest(L8323_5)  3=noise(L8323_8, explosion/impact)
#                  4,5,6=tone families (L839A / L83C6 / L8398)
#   L82C1 voice A / L8323 voice B : DJNZ+OUT($FE) square waves; pitch/duration
#     live in self-modified cells ($82CA,$82CC,$82D0,$82D4 / $832C,$832E,$8332,
#     $8336) programmed by handlers L813C / L82A3 / L8305.
# Animation bytecode stream @ $8473 (opcodes 0A/09/0B/0C ...), dispatch tables
#   at $83FC (handler stubs) and $843D (per-subdemo stream pointers).
def decode_title(mem):
    print('== title music: player code ==')
    for a, t in ((0x8000, 'interpreter core (per frame)'),
                 (0x813C, 'opcode handler: start note'),
                 (0x8243, 'sound dispatcher (reads event stream)'),
                 (0x82A3, 'voice A period advance'),
                 (0x82C1, 'voice A OUT square wave'),
                 (0x8305, 'voice B period advance'),
                 (0x8323, 'voice B OUT square wave / noise')):
        print(f'  ${a:04X}  {t}')
    print('\n== event streams (self-mod pointer $8250 scans, wrap on code 1) ==')
    for lo, hi, lbl in ((0x85A3, 0x85B1, 'stream A'),
                        (0x85B1, 0x85C7, 'stream B')):
        ev = dump(mem, lo, hi)
        print(f'  ${lo:04X} {lbl}: ' + ','.join(f'{v:02X}' for v in ev))
    print('\n== animation bytecode @ $8473 (op 0A=LD A,(stream) 09=END '
          '0B/0C=family) ==')
    bc = dump(mem, 0x8473, 0x84C0)
    print('  ' + ' '.join(f'{v:02X}' for v in bc))
    print('\n== dispatch tables ==')
    ptrs = [(get(mem, 0x843D + 2 * i) | (get(mem, 0x843E + 2 * i) << 8))
            for i in range(7)]
    print('  $843D sub-demo stream pointers: ' +
          ', '.join(f'${p:04X}' for p in ptrs))


def main():
    engine = load_mem(os.path.join(DATA, 'HATE_engine_final.skool'))
    title = load_mem(os.path.join(DATA, 'HATE_title_final.skool'))
    decode_title(title)
    print()
    slots = decode_engine(engine)
    if '--json' in sys.argv:
        out = {
            'engine_sfx': {'resolver': '$F2C0', 'bit': '$F2BF',
                           'table': '$F2E2', 'slots': slots},
        }
        print('\n' + json.dumps(out, indent=2))


if __name__ == '__main__':
    main()
