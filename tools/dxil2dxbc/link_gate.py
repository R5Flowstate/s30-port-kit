"""Shaderset linkage gate: every PS input must be produced by the paired VS output
in the same register with a covering mask, for every VS/PS entry pair the engine
can combine (instanced entries with instanced, static with static; a PS without an
instancing dimension serves both).

Shaders come from our converted MSWs first, else from a resident S21 pak.
"""
import kitpaths as KP
import os, sys, glob, struct, collections, json


import msw
import pak_msw as P

ROOT = (KP.MAPS + r'\mp_rr_desertlands_mu5')
SH = os.path.join(ROOT, r'extract\shader_s21')
SS = os.path.join(ROOT, r'extract\shaderset_s21')
S21 = glob.glob((KP.WORK + r'\paks_dec\*.dec.rpak'))
SYS_PS = {'SV_POSITION', 'SV_ISFRONTFACE', 'SV_SAMPLEINDEX', 'SV_PRIMITIVEID', 'SV_COVERAGE'}


def sig(blob, fcc):
    n = struct.unpack_from('<I', blob, 28)[0]
    for k in range(n):
        o = struct.unpack_from('<I', blob, 32 + 4 * k)[0]
        if blob[o:o + 4] == fcc:
            base = o + 8
            cnt = struct.unpack_from('<I', blob, base)[0]
            out = []
            for e in range(cnt):
                no, si, sv, ct, reg, mask = struct.unpack_from('<IIIIIB', blob, base + 8 + 24 * e)
                name = blob[base + no:blob.index(b'\0', base + no)].decode()
                out.append((name.upper(), si, sv, reg, mask))
            return out
    return []


def load(g):
    p = os.path.join(SH, '0x%X.msw' % g)
    r = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'rsx_s21', 'shader', '0x%X.msw' % g)
    if not os.path.exists(p) and os.path.exists(r):
        m = msw.read_msw(r)
        ents = [(e.flags, bytes(e.data) if e.data else None, e.ref_index) for e in m.entries]
        src = 's21'
    elif os.path.exists(p):
        m = msw.read_msw(p)
        ents = [(e.flags, bytes(e.data) if e.data else None, e.ref_index) for e in m.entries]
        src = 'ours'
    else:
        s = None
        for pak in S21:
            s = P.read_shader(pak, g)
            if s and s['type'] <= 5:
                break
            s = None
        if not s:
            return None, 'missing'
        ents = [(e['flags'], e['buf'], e['ref']) for e in s['entries']]
        src = 's21'
    live = ents[:len(ents) // 2] if len(ents) > 1 else ents
    out = []
    for k, (fl, b, ref) in enumerate(live):
        if b is None and ref != 0xFFFF and ref < len(live):
            b = live[ref][1]
        if b:
            out.append((fl, b))
    return out, src


def main():
    c = collections.Counter()
    bad = []
    for f in sorted(glob.glob(os.path.join(SS, '*.msw'))):
        b = open(f, 'rb').read()
        ps_g, vs_g = struct.unpack_from('<QQ', b, 5)
        vs, vsrc = load(vs_g) if vs_g else (None, 'none')
        ps, psrc = load(ps_g) if ps_g else (None, 'none')
        c['src_%s_%s' % (vsrc if isinstance(vsrc, str) else 'x', psrc if isinstance(psrc, str) else 'x')] += 1
        if not vs or not ps:
            continue
        ps_has_inst = any(fl[0] & 0x40 for fl, _ in ps) and any(not fl[0] & 0x40 for fl, _ in ps)
        for vfl, vb in vs:
            vinst = bool(vfl[0] & 0x40)
            vout = {(n, i): (r, m) for n, i, sv, r, m in sig(vb, b'OSGN')}
            for pfl, pb in ps:
                if ps_has_inst and bool(pfl[0] & 0x40) != vinst:
                    continue
                c['pairs'] += 1
                errs = []
                for n, i, sv, r, m in sig(pb, b'ISGN'):
                    if n in SYS_PS:
                        continue
                    o = vout.get((n, i))
                    if o is None:
                        errs.append('missing %s%d' % (n, i))
                    elif o[0] != r or (m & ~o[1]):
                        errs.append('%s%d reg %d/%d mask %x/%x' % (n, i, o[0], r, o[1], m))
                if errs:
                    c['bad_pairs'] += 1
                    bad.append((os.path.basename(f), '%X' % vs_g, vsrc, '%X' % ps_g, psrc, vinst, errs))
    print(dict(c))
    agg = collections.Counter()
    for x in bad:
        for e in x[6]:
            agg[(x[2], x[4], e.split(' reg')[0] if 'reg' in e else e)] += 1
    for k, v in agg.most_common(30):
        print(v, k)
    json.dump(bad, open('link_gate.json', 'w'), indent=1)


if __name__ == '__main__':
    main()
