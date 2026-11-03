"""Render the captured screen of a units.py pass into a PNG.

  python3 tools/frame_png.py <capture.json> [pass] [out.png]

Nothing is modelled here: the bytes are the real screen memory at $4000/$5800
when the blitter has just finished, so the picture is what the emulator showed.
It is the reference the reconstructions (tools/ringscreen.py, tools/unit_shot.py)
have to match.
"""
import json
import sys

sys.path.insert(0, __file__.rsplit('/', 1)[0])
import unit_shot as us
from PIL import Image

PAL = us.PAL


def frame(pre, scale=2):
    scr = bytes(pre['screen'])
    attr = pre['attr']
    img = Image.new('RGB', (256 * scale, 192 * scale))
    px = img.load()
    for y in range(192):
        row = (y // 8) * 32
        for bx in range(32):
            b = scr[us.scr_off(bx, y)]
            att = attr[row + bx]
            br = 8 if att & 0x80 else 0
            ink = PAL[(att & 7) + br]
            paper = PAL[(att >> 3) & 7]
            for k in range(8):
                col = ink if (b >> (7 - k)) & 1 else paper
                x0 = bx * 8 * scale + k * scale
                for sy in range(scale):
                    yy = y * scale + sy
                    for sx in range(scale):
                        px[x0 + sx, yy] = col
    return img


def main():
    d = json.load(open(sys.argv[1]))
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 0
    out = sys.argv[3] if len(sys.argv) > 3 else \
        'tmp/frame_%d.png' % d['frames'][n]['frame']
    fr = d['frames'][n]
    frame(fr['pre']).save(out)
    print('кадр %d -> %s' % (fr['frame'], out))


if __name__ == '__main__':
    main()
