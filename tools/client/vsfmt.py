import kitpaths as KP
import os, sys, glob, struct, collections


import msw, pak_msw as P, link_gate as L
SH = (KP.MAPS + r'\mp_rr_desertlands_mu5\extract\shader_s21')

def isg(b):
    return frozenset((n, i) for n, i, sv, r, m in L.sig(b, b'ISGN') if not n.startswith('SV_'))

# S21: semantic -> set of flags0 seen; and for each semantic the AND of flags (bits always set when present)
need = {}
pairs = collections.Counter()
for pak in glob.glob((KP.WORK + r'\paks_dec\*.dec.rpak')):
    d, pages, ao0, ac, rel = P.load(pak)
    for i in range(ac):
        ao = ao0 + i * 80
        if P.u32(d, ao + 0x4C) != P.FCC_SHDR: continue
        g = P.u64(d, ao)
        try: s = P.read_shader(pak, g)
        except Exception: continue
        if not s or s['type'] != 1: continue
        for e in s['entries']:
            if not e['buf'] or e['buf'][:4] != b'DXBC': continue
            f0 = e['flags'][0] & 0xFFFFFFFF
            for sem in isg(e['buf']):
                need[sem] = need.get(sem, 0xFFFFFFFF) & f0
            pairs[(isg(e['buf']), f0)] += 1
print('S21 semantic -> bits always set:')
for k, v in sorted(need.items()): print('  ', k, hex(v))
bad = 0
for f in glob.glob(os.path.join(SH, '*.msw')):
    m = msw.read_msw(f)
    for k, e in enumerate(m.entries):
        if not e.data or bytes(e.data)[:4] != b'DXBC': continue
        b = bytes(e.data)
        if L.stage(b) if hasattr(L,'stage') else False: pass
        sh = struct.unpack_from('<I', b, (lambda: [struct.unpack_from('<I', b, 32+4*j)[0] for j in range(struct.unpack_from('<I', b, 28)[0]) if b[struct.unpack_from('<I', b, 32+4*j)[0]:][:4] in (b'SHEX', b'SHDR')][0])() + 8)[0] >> 16
        if sh != 1: continue
        f0 = e.flags[0] & 0xFFFFFFFF
        for sem in isg(b):
            if sem not in need:
                print('NO-S21-PRECEDENT', os.path.basename(f), k, sem, hex(f0)); bad += 1
            elif need[sem] & ~f0:
                print('MASK-MISSING', os.path.basename(f), k, sem, 'needs', hex(need[sem]), 'has', hex(f0)); bad += 1
print('bad', bad)
