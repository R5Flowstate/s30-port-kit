import kitpaths as KP
import pickle, struct, sys, os, csv, json, shutil

import patch_shdr_cbremap as P
ROOT = (KP.MAPS + r"\mp_rr_desertlands_mu5")
PAK = (KP.DEPLOY + '/paks/Win64/mp_rr_desertlands_mu5.rpak')
orc = pickle.load(open('orc_mat.pkl', 'rb'))
bit2 = pickle.load(open('bit2_plan.pkl', 'rb'))
MATL = struct.unpack("<I", b"matl")[0]
d = bytearray(open(PAK, 'rb').read())
pages, ao0, ac, _ = P.parse(bytes(d), aligned=False)
want = {}
for i in range(ac):
    ao = ao0 + i * 80
    if P.u32(d, ao + 0x4C) != MATL: continue
    g = struct.unpack_from('<Q', d, ao)[0]
    ho = P.res(pages, P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
    cur = struct.unpack_from('<I', d, ho + 0xCC)[0]
    new = cur
    if g in orc:
        new = struct.unpack_from('<I', orc[g][1], 0xCC)[0]
    elif g in bit2:
        new = bit2[g][2]
    if new != cur:
        want[g] = (ho, cur, new)
print('pak changes', len(want))
bak = PAK + '.pre_foliage_cc'
if not os.path.exists(bak): shutil.copyfile(PAK, bak)
for g, (ho, cur, new) in want.items(): struct.pack_into('<I', d, ho + 0xCC, new)
open(PAK, 'wb').write(d)
# bake into the build jsons
names = {}
for r in csv.DictReader(open(os.path.join(ROOT, 'inv', 'base.csv'), encoding='utf-8', errors='replace')):
    if r['type'] == 'matl': names[int(r['guid'], 16)] = r['asset_name'].replace(chr(92), '/')
for line in open(os.path.join(ROOT, 'inv', 'extra_matl.txt')):
    p = line.split()
    if len(p) == 2: names.setdefault(int(p[0], 16), p[1])
baked = miss = 0
for g, (ho, cur, new) in want.items():
    n = names.get(g)
    js = os.path.join(ROOT, 'assets', n[:-5] + '.json') if n else None
    if not js or not os.path.isfile(js): miss += 1; continue
    j = json.load(open(js))
    j['unk_CC'] = '0x%X' % new
    json.dump(j, open(js, 'w'), indent=4)
    baked += 1
print('json baked', baked, 'missing', miss)
