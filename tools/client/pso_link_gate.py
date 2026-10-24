"""Linkage gate using the engine's PSO prewarm pairing:
VS entry = d0*sc0 + d1*sc1 + d2*sc2 (VS strides), PS entry the same tuple with PS strides.
d0 in {0,1}; d1 only if VS sc1; d2 only if VS sc2 or PS sc2. Live half only."""
import kitpaths as KP
import os, sys, glob, struct, collections, json


import msw, pak_msw as P, link_gate as L
ROOT = (KP.MAPS + r'\mp_rr_desertlands_mu5')
SH = os.path.join(ROOT, r'extract\shader_s21'); SS = os.path.join(ROOT, r'extract\shaderset_s21')
S21 = glob.glob((KP.WORK + r'\paks_dec\*.dec.rpak'))
SYS = {'SV_POSITION', 'SV_ISFRONTFACE', 'SV_SAMPLEINDEX', 'SV_PRIMITIVEID', 'SV_COVERAGE'}
cache = {}
def load(g):
    if g in cache: return cache[g]
    p = os.path.join(SH, '0x%X.msw' % g)
    r = os.path.join((KP.WORK + r'/dxil2dxbc/rsx_s21/shader'), '0x%X.msw' % g)
    if not os.path.exists(p) and os.path.exists(r):
        m = msw.read_msw(r); f = bytes(m.features)
        ents = [(e.flags, bytes(e.data) if e.data else None, e.ref_index) for e in m.entries]; src = 's21'
    elif os.path.exists(p):
        m = msw.read_msw(p); f = bytes(m.features)
        ents = [(e.flags, bytes(e.data) if e.data else None, e.ref_index) for e in m.entries]; src = 'ours'
    else:
        s = None
        for pak in S21:
            s = P.read_shader(pak, g)
            if s and s['type'] <= 5: break
            s = None
        if not s: cache[g] = None; return None
        f = bytes(s['features']); ents = [(e['flags'], e['buf'], e['ref']) for e in s['entries']]; src = 's21'
    res = []
    for k, (fl, b, ref) in enumerate(ents):
        seen = 0
        while b is None and ref != 0xFFFF and ref < len(ents) and seen < 8:
            fl2, b, ref = ents[ref]; seen += 1
        res.append(b)
    cache[g] = (f, res, src); return cache[g]

def pairs(vs, ps):
    vf, pf = vs[0], ps[0]
    for d0 in (0, 1):
        for d1 in ((0, 1) if vf[2] else (0,)):
            for d2 in ((0, 1) if (vf[3] or pf[3]) else (0,)):
                yield (d0, d1, d2), d0 * vf[1] + d1 * vf[2] + d2 * vf[3], d0 * pf[1] + d1 * pf[2] + d2 * pf[3]

c = collections.Counter(); bad = []
for f in sorted(glob.glob(os.path.join(SS, '*.msw'))):
    b = open(f, 'rb').read()
    ps_g, vs_g = struct.unpack_from('<QQ', b, 5)
    vs = load(vs_g) if vs_g else None; ps = load(ps_g) if ps_g else None
    if not vs or not ps: c['skip'] += 1; continue
    if vs[0][0] != 0xFF or ps[0][0] != 0xFF: c['nonFF_env'] += 1; continue
    for dims, vi, pi in pairs(vs, ps):
        vb = vs[1][vi] if vi < len(vs[1]) else 'OOR'; pb = ps[1][pi] if pi < len(ps[1]) else 'OOR'
        c['pairs'] += 1
        if vb == 'OOR' or pb == 'OOR':
            bad.append((os.path.basename(f), '%X' % vs_g, vs[2], '%X' % ps_g, ps[2], dims, vi, pi, ['index out of range'])); continue
        if not vb or not pb: c['null_entry'] += 1; continue
        vout = {(n, i): (r, m) for n, i, sv, r, m in L.sig(vb, b'OSGN')}
        errs = []
        for n, i, sv, r, m in L.sig(pb, b'ISGN'):
            if n in SYS: continue
            o = vout.get((n, i))
            if o is None: errs.append('missing %s%d' % (n, i))
            elif o[0] != r or (m & ~o[1]): errs.append('%s%d reg %d/%d mask %x/%x' % (n, i, o[0], r, o[1], m))
        if errs:
            bad.append((os.path.basename(f), '%X' % vs_g, vs[2], '%X' % ps_g, ps[2], dims, vi, pi, errs))
print(dict(c)); print('bad', len(bad))
agg = collections.Counter((x[2], x[4], e.split(' reg')[0]) for x in bad for e in x[8])
for k, v in agg.most_common(): print(v, k)
for x in bad[:40]: print(x)
json.dump(bad, open((KP.WORK + r'\mu5_dedi\pso\link2.json'), 'w'), indent=1)
