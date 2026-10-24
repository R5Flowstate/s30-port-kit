import kitpaths as KP
import os, sys, glob, struct, collections

import msw
SH = (KP.MAPS + r'\mp_rr_desertlands_mu5\extract\shader_s21')
S21 = (KP.WORK + r'\dxil2dxbc\s21all')
CLS = {0: 'b', 1: 't', 2: 't', 3: 's', 4: 'u', 5: 't', 6: 'u', 7: 't', 8: 'u', 9: 'u', 10: 'u', 11: 'u'}

def chunk(b, fcc):
    n = struct.unpack_from('<I', b, 28)[0]
    for k in range(n):
        o = struct.unpack_from('<I', b, 32 + 4 * k)[0]
        if b[o:o + 4] == fcc:
            return o + 8
    return None

def binds(b):
    r = chunk(b, b'RDEF')
    out = []
    if r is None:
        return out, None
    cbn, cbo, bn, bo = struct.unpack_from('<IIII', b, r)
    ver = struct.unpack_from('<I', b, r + 16)[0]
    stride = 40 if (ver & 0xFFFF) >= 0x501 else 32
    for i in range(bn):
        e = r + bo + stride * i
        no, ty, rt, dim, ns, bp, bc, fl = struct.unpack_from('<8I', b, e)
        space = struct.unpack_from('<I', b, e + 32)[0] if stride == 40 else 0
        name = b[r + no:b.index(b'\0', r + no)].decode()
        out.append((CLS.get(ty, '?%d' % ty), bp, bc, space, ty, name))
    return out, ver >> 16

def stage(b):
    s = chunk(b, b'SHEX') or chunk(b, b'SHDR')
    v = struct.unpack_from('<I', b, s)[0]
    return {0: 'ps', 1: 'vs'}.get(v >> 16, str(v >> 16)), (v >> 4) & 0xF, v & 0xF

ref = {'vs': collections.Counter(), 'ps': collections.Counter()}
for p in glob.glob(os.path.join(S21, '*.dxbc')):
    b = open(p, 'rb').read()
    st, ma, mi = stage(b)
    for c, bp, bc, sp, ty, nm in binds(b)[0]:
        for k in range(bc):
            ref.setdefault(st, collections.Counter())[(c, bp + k, sp)] += 1

bad = collections.defaultdict(set)
models = collections.Counter()
for f in glob.glob(os.path.join(SH, '*.msw')):
    m = msw.read_msw(f)
    for i, e in enumerate(m.entries):
        if not e.data:
            continue
        b = bytes(e.data)
        st, ma, mi = stage(b)
        models[(st, ma, mi)] += 1
        for c, bp, bc, sp, ty, nm in binds(b)[0]:
            for k in range(bc):
                if (c, bp + k, sp) not in ref[st]:
                    bad[(st, c, bp + k, sp, nm)].add(os.path.basename(f))
print('models', dict(models))
for k, v in sorted(bad.items()):
    print(k, len(v), sorted(v)[:4])
print('slots with no S21 precedent:', len(bad))
