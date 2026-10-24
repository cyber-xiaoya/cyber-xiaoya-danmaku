import base64
from bilibili_client import _pb_fields

pb = ('EgROKioqIgEBKAEw2a2dCjjA26XVBkCQ39muijRKKwjt143PARAHGgbohIbpsqggnvf1AijAgYMGMMCBgwY4wIGDBmDZrZ0KaEpiAHiH3pi4sM/i6hiaAQCyAcABElMKBE4qKioSS2h0dH')
raw = base64.b64decode(pb + '=' * (-len(pb) % 4))
print('hex head:', raw[:24].hex(' '))
for f, wt, v in _pb_fields(raw):
    if isinstance(v, bytes):
        print(f'field={f} wt={wt} len={len(v)} head={v[:16].hex(" ")}')
        try:
            for f2, wt2, v2 in _pb_fields(v[:64]):
                if isinstance(v2, int):
                    print('   inner', f2, wt2, v2)
                else:
                    print('   inner', f2, wt2, len(v2), v2[:20].hex(' '))
        except Exception as e:
            print('   inner parse err:', e)
    else:
        print(f'field={f} wt={wt} val={v}')
