"""Minimal helpers: load a .z80 snapshot's memory and disassemble ranges.

Used by the rendering-engine investigation (ENGINE.MD) and by render_objects.py.
Run from the repository root with PYTHONPATH pointing at skoolkit/.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.abspath(os.path.join(HERE, "..", "..", ".."))
SKOOLKIT = os.path.join(ROOT, "skoolkit")
if SKOOLKIT not in sys.path:
    sys.path.insert(0, SKOOLKIT)

from skoolkit.snapshot import get_snapshot          # noqa: E402
from skoolkit.disassembler import Disassembler    # noqa: E402
from skoolkit.snaskool import DisassemblerConfig, Instruction  # noqa: E402

HATE = os.path.abspath(os.path.join(HERE, ".."))
TMP = os.path.join(HATE, "tmp")


class Mem:
    def __init__(self, path):
        self.ram = bytes(get_snapshot(path))

    def __getitem__(self, key):
        if isinstance(key, slice):
            return self.ram[key]
        return self.ram[key]

    def word(self, addr):
        return self.ram[addr] | (self.ram[(addr + 1) & 0xFFFF] << 8)

    def dump(self, start, end):
        return self.ram[start:end]


def load(name="run10_56000.z80"):
    return Mem(os.path.join(TMP, name))


CONFIG = DisassemblerConfig(False, False, 8, 65, 1, 0, Instruction, '', 0)


def disasm(mem, start, end, show_addr=True):
    """Disassemble [start, end) from memory using SkoolKit's engine."""
    d = Disassembler(mem.ram, CONFIG)
    out = []
    addr = start
    while addr < end:
        op = d.disassemble(addr, min(addr + 4, 0x10000), 'h')[0]
        size = len(op.bytes)
        if show_addr:
            out.append("%04X  %-14s %s" % (addr, " ".join("%02X" % b for b in op.bytes), op.operation))
        else:
            out.append("        " + op.operation)
        addr += size
    return "\n".join(out)


if __name__ == "__main__":
    mem = load()
    a = int(sys.argv[1], 16)
    b = int(sys.argv[2], 16)
    print(disasm(mem, a, b))
