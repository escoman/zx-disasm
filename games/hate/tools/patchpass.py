"""Replay $DD6C offline from a units.py capture and check it against the ring.

  python3 tools/patchpass.py <capture.json>

$DD6C walks the $FE64 list and then the $FF00 list, and for every element calls
$DDC9, which LDIRs `count` runs of `width` bytes from the patch stream at $F800
into the ring, advancing the destination by $1FC per run.  `width` depends only
on the size class, and the two walks use different class -> width tables:

    $FE64 (restore):  class < 4 -> $11,  4 <= class < $0B -> $0B,  else $04
    $FF00 (draw)   :  class < 4 -> $15,  4 <= class < $0B -> $11,  else $0D

If the replay reproduces the captured ring byte for byte, the object renderer is
fully understood: the stream at $F800 is the patch, and the list element says
where each patch goes.
"""
import json
import sys

RING_LO = 0x8000
STRIDE = 0x1FC          # DEC E x4, INC D x2, RES 6,D  -> +$200 - 4


def w_restore(cls):
    return 0x11 if cls < 4 else (0x0B if cls < 0x0B else 0x04)


def w_draw(cls):
    return 0x15 if cls < 4 else (0x11 if cls < 0x0B else 0x0D)


def walk(lst, stride):
    out = []
    i = 0
    while i + stride <= len(lst):
        cls = lst[i]
        if cls == 0xFF:
            break
        out.append(lst[i:i + stride])
        i += stride
    return out


def replay(pre, verbose=False):
    """Return (ring, patches).  ring is the replayed ring image."""
    ring = list(pre['ring'])
    stream = pre['stream']
    pos = 0
    patches = []
    for el in walk(pre['fe64'], 8):
        cls, width, count, dest = el[0], w_restore(el[0]), el[4], el[6] | (el[7] << 8)
        data = stream[pos:pos + width * count]
        pos += width * count
        patches.append({'list': 'fe64', 'class': cls, 'count': count,
                        'width': width, 'dest': dest, 'data': data})
        if not RING_LO <= dest < 0xC000:
            continue
        for j in range(count):
            base = (dest - RING_LO + j * STRIDE) & 0x3FFF
            for i in range(width):
                ring[(base + i) & 0x3FFF] = data[j * width + i]
    for el in walk(pre['ff00'], 6):
        cls, width, count, dest = el[0], w_draw(el[0]), el[1], el[2] | (el[3] << 8)
        data = stream[pos:pos + width * count]
        pos += width * count
        patches.append({'list': 'ff00', 'class': cls, 'count': count,
                        'width': width, 'dest': dest, 'data': data,
                        'ptr': el[4] | (el[5] << 8)})
        if not RING_LO <= dest < 0xC000:
            continue
        for j in range(count):
            base = (dest - RING_LO + j * STRIDE) & 0x3FFF
            for i in range(width):
                ring[(base + i) & 0x3FFF] = data[j * width + i]
    return ring, patches, pos


def main():
    d = json.load(open(sys.argv[1]))
    for fr in d['frames']:
        pre, post = fr['pre'], fr['post']
        ring, patches, used = replay(pre)
        want = post['ring']
        same = sum(1 for a, b in zip(ring, want) if a == b)
        real = [i for i in range(len(pre['ring'])) if pre['ring'][i] != want[i]]
        pred = [i for i in range(len(ring)) if ring[i] != pre['ring'][i]]
        print('кадр %-5d потоков %4d байт, элементов %d, кольцо совпало '
              '%d/%d, реально изменено %d, предсказано %d'
              % (fr['frame'], used, len(patches), same, len(want),
                 len(real), len(pred)))
        bad = [i for i in pred if ring[i] != want[i]]
        if bad:
            print('   расходится %d байт, первые: %s' % (len(bad),
                  ', '.join('%04X pred=%02X real=%02X' % (i + RING_LO, ring[i],
                       want[i]) for i in bad[:8])))
        if len(sys.argv) > 2:
            for p in patches:
                print('   %-4s cls=%02X count=%d width=%02X dest=%04X ptr=%s  %s'
                      % (p['list'], p['class'], p['count'], p['width'], p['dest'],
                         '%04X' % p['ptr'] if 'ptr' in p else '-',
                         ' '.join('%02X' % b for b in p['data'][:24])))


if __name__ == '__main__':
    main()
