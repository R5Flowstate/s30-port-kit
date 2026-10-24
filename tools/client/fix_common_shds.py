import kitpaths as KP
import os, sys, glob, collections, csv, struct

import pak_msw as P

from fix_shaderset_msw import read_v15
ROOT = (KP.MAPS + r'\mp_rr_desertlands_mu5')
OUT = os.path.join(ROOT, r'extract\shaderset_s21')
DECS = [(KP.WORK + r'\mu5_dedi\s30\common.rpak.dec.rpak'), (KP.WORK + r'\mu5_dedi\s30\common_mp.rpak.dec.rpak')]
oracle = set()
for r in csv.DictReader(open(ROOT + r'\inv\s21_oracle.csv', encoding='utf-8', errors='replace')):
    if not r['file_name'].lower().startswith('mp_lobby'):
        oracle.add(int(r['guid'], 16))
have = {int(os.path.basename(p)[:-4], 16) for p in glob.glob(ROOT + r'\extract\shader_s21\*.msw')}
c = collections.Counter(); need = set()
for f in sorted(glob.glob((KP.WORK + r'\mu5_dedi\s30common\shaderset\*.msw'))):
    g = int(os.path.basename(f)[:-4], 16)
    ss = None
    for d in DECS:
        try:
            ss = read_v15(d, g)
            break
        except Exception:
            continue
    if ss is None:
        c['unread'] += 1; print('unread', hex(g)); continue
    b = open(f, 'rb').read()
    assert P.u64(b, 5) == ss['pixelShaderGuid'] and P.u64(b, 13) == ss['vertexShaderGuid']
    P.write_shaderset_msw(ss, os.path.join(OUT, os.path.basename(f)))
    c['written'] += 1
    for s in (ss['vertexShaderGuid'], ss['pixelShaderGuid']):
        if s and s not in have and s not in oracle:
            need.add(s)
print(dict(c), 'shaders to convert', len(need))
open((KP.WORK + r'\mu5_dedi\need_shdr.txt'), 'w').write('\n'.join('0x%016X' % s for s in sorted(need)))
