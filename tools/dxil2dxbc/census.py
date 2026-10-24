"""Corpus reflection census. S21 = every DXBC in the retail S21 map paks; S30 = mu5 blobs.

Writes census_s21.json / census_s30.json:
  cb[name] = {leafpath: [offset, type, arraycount, hits]}, sizes
  binds[name] = {bindpoint: hits}
"""
import kitpaths as KP
import sys, os, glob, json, hashlib, collections
from concurrent.futures import ThreadPoolExecutor

import pak_msw as P
import refl

S21_PAKS = glob.glob((KP.WORK + r'\paks_dec\*.dec.rpak'))


def extract_s21():
    os.makedirs('s21all', exist_ok=True)
    seen = set()
    owner = {}
    for pak in S21_PAKS:
        d, pages, ao0, ac, rel = P.load(pak)
        for i in range(ac):
            ao = ao0 + i * 80
            if P.u32(d, ao + 0x4C) != P.FCC_SHDR:
                continue
            g = P.u64(d, ao)
            try:
                s = P.read_shader(pak, g)
            except Exception:
                continue
            if not s or s['type'] > 5:
                continue
            for k, e in enumerate(s['entries']):
                if e['buf'] and e['buf'][:4] == b'DXBC':
                    h = hashlib.md5(e['buf']).hexdigest()
                    if h in seen:
                        continue
                    seen.add(h)
                    fn = 's21all/0x%016X_%d.dxbc' % (g, k)
                    if not os.path.exists(fn):
                        open(fn, 'wb').write(e['buf'])
                    owner[fn] = os.path.basename(pak)
    json.dump(owner, open('s21all_owner.json', 'w'))
    return sorted(owner)


def one(p):
    t, dx = refl.dump(p)
    return p, refl.layout(t, dx), refl.bindings(t, dx), refl.signature(t, dx, 'in'), refl.signature(t, dx, 'out')


def strip_wrap(cb, path, dxil):
    parts = path.split('.')
    if dxil and parts and parts[0] == cb:
        parts = parts[1:]
    if parts and parts[0] in ('c_staticConstants', 'c_dynamicConstants'):
        parts = parts[1:]
    return '.'.join(parts)


def census(files, dxil, out):
    with ThreadPoolExecutor(12) as ex:
        res = list(ex.map(one, files))
    cb = collections.defaultdict(dict)
    binds = collections.defaultdict(collections.Counter)
    per = {}
    for p, L, B, si, so in res:
        per[p] = {'cb': {k: [(strip_wrap(k, a, dxil), b, c, d) for a, b, c, d in v['leaves']] for k, v in L.items()},
                  'binds': B, 'in': si, 'out': so}
        for k, v in L.items():
            for a, b, c, d in v['leaves']:
                a = strip_wrap(k, a, dxil)
                ent = cb[k].setdefault(a, [b, c, d, 0, []])
                ent[3] += 1
                if ent[0] != b and b not in ent[4]:
                    ent[4].append(b)
        for b in B:
            binds[b[0]][b[4]] += 1
    json.dump({'cb': cb, 'binds': binds, 'per': per}, open(out, 'w'))
    return cb, binds


if __name__ == '__main__':
    which = sys.argv[1]
    if which == 's21':
        fs = extract_s21()
        print('s21 blobs', len(fs))
        census(fs, False, 'census_s21.json')
    else:
        fs = sorted(glob.glob('blobs/*.dxbc'))
        census(fs, True, 'census_s30.json')
