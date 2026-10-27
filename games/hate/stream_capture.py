#!/usr/bin/env python3
"""stream_capture.py — захват работы векторного растеризатора $D0EF/$D105.

C-симулятор, CSimulator.trace со stop-адресом: $D0EF (вход объекта) ->
$D105 (итерация потока) -> адрес возврата (вершина стека). На каждый объект:
  * stream_ptr (BC на $D105), байт S, idx=S>>1, табличный забег C9FE[idx..FF];
  * дифф буфера $6000-$62FF по 8-байтным слотам.

  $ PYTHONPATH=. python3 hate/stream_capture.py [кадров=3] [снапшот]

Выход: hate/tmp/streams.jsonl (объект в строку) + сводка в stdout.
"""
import json
import sys

from skoolkit import CSimulator
from skoolkit.simulator import Simulator
from skoolkit.simutils import from_snapshot
from skoolkit.snapshot import Snapshot
from skoolkit.trace import Tracer

FRAME = 69888
BUF_LO, BUF_HI = 0x6000, 0x6300

def rd16(m, a):
    return m[a] | (m[a + 1] << 8)

def main():
    frames = float(sys.argv[1]) if len(sys.argv) > 1 else 3
    base = sys.argv[2] if len(sys.argv) > 2 else 'hate/tmp/br4_01200.z80'
    snapshot = Snapshot.get(base)
    sim = from_snapshot(CSimulator or Simulator, snapshot, {}, {'iff': 1},
                        {'fast_djnz': True, 'fast_ldir': True})
    tracer = Tracer(sim, 7, 0, 0, [0] * 16, 0, True)
    tracer.keyboard = [0] * 8
    sim.set_tracer(tracer)
    regs = sim.registers
    mem = sim.memory
    start_ts = regs[25]
    budget = frames * FRAME
    out = open('hate/tmp/streams.jsonl', 'w')
    objs = 0
    frames_seen = set()

    def run_to(stop):
        # max_operations=0 (безлимит), max_time — крупный запас
        return sim.trace(regs[24], stop, 0, 10**12, 1,
                         lambda *a: True, set(), tracer.keyboard, None, None)

    while regs[25] - start_ts < budget:
        run_to(0xD0EF)
        if regs[24] != 0xD0EF:
            break  # бюджет/иной стоп
        frame = (regs[25] - start_ts) // FRAME
        ret_addr = rd16(mem, regs[12])          # SP: вершина стека = адрес возврата
        buf_before = bytes(mem[BUF_LO:BUF_HI])
        run_to(0xD105)
        stream_ptr = (regs[2] << 8) | regs[3]   # B,C
        s_byte = mem[stream_ptr]
        idx = s_byte >> 1
        run_tbl = []
        a = idx
        while a < 0x100 and mem[0xC9FE + a] != 0xFF:
            run_tbl.append(mem[0xC9FE + a])
            a += 1
        run_to(ret_addr)
        buf_after = bytes(mem[BUF_LO:BUF_HI])
        changed = {}
        for i in range(BUF_HI - BUF_LO):
            if buf_before[i] != buf_after[i]:
                changed.setdefault(i // 8, []).append(
                    (i % 8, buf_before[i], buf_after[i]))
        rec = {'f': int(frame), 'sp': stream_ptr, 's': s_byte, 'idx': idx,
               'tbl': run_tbl[:48], 'slots': {str(k): v for k, v in changed.items()}}
        out.write(json.dumps(rec) + '\n')
        objs += 1
        frames_seen.add(int(frame))
    out.close()
    print(f'объектов: {objs}, кадров затронуто: {len(frames_seen)}')

if __name__ == '__main__':
    main()
