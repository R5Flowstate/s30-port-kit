"""Walk every submodel BVH exactly as the S3 dedi addresses it:
nodes = 0x12 + (bvhNode + idx)*64, leaves = 0x13 + 4*(bvhLeaf + idx),
verts = flags&1 ? packed 0x14 (6 B) : float 0x03 (12 B), both + vertIndex."""
import struct, sys, os, collections
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bvhlib import Bvh
D, L = sys.argv[1], sys.argv[2]
B = Bvh(D, L)
P = "%s/%s.bsp." % (D, L)
npk = os.path.getsize(P + "0014.bsp_lump") // 6 if os.path.exists(P + "0014.bsp_lump") else 0
nm = len(B.models) // 64; nn = len(B.nodes) // 64; nf = len(B.verts) // 12; ld = len(B.leaf)
LAY = {4: (0x7FF, 11, 20), 5: (0x7FF, 11, 20), 6: (0x3FF, 10, 19), 7: (0x3FF, 10, 19)}
st = collections.Counter(); ex = []
def leaf(t, o, vb, nv):
    if o + 4 > ld: st['leafptr_oob'] += 1; return False
    if t in LAY:
        h0, h1 = struct.unpack_from("<HH", B.leaf, o); n = (h0 >> 12) + 1
        if o + 4 + 4 * n > ld: st['leafbody_oob'] += 1; return False
        dm, s1, s2 = LAY[t]; base = (h1 << 10) + vb; mx = 0
        for j in range(n):
            x = struct.unpack_from("<I", B.leaf, o + 4 + 4 * j)[0]; base += x & dm
            mx = max(mx, base + 1 + ((x >> s1) & 0x1FF), base + 1 + ((x >> s2) & 0x1FF))
        if mx >= nv: st['t%d_vert_oob' % t] += 1; return False
    st['t%d' % t] += 1
    return True
for mi in range(nm):
    m = struct.unpack_from('<6f6I', B.models, mi * 64)
    nb, lb, vb, vf = m[8], m[9], m[10], m[11]
    nv = npk if vf & 1 else nf
    stack = [0]; ok = True; seen = set()
    while stack:
        i = stack.pop()
        if nb + i >= nn: st['node_oob'] += 1; ok = False; continue
        if i in seen: continue
        seen.add(i)
        t, idx, bounds, w = B.node(nb + i)
        for c in range(4):
            if t[c] == 0: stack.append(idx[c])
            elif t[c] == 3:
                bo = (lb + idx[c]) * 4
                if bo + 4 > ld: st['bundle_oob'] += 1; ok = False; continue
                n = struct.unpack_from("<I", B.leaf, bo)[0]
                if n == 0 or n > 4096: st['bundle_bad'] += 1; ok = False; continue
                p = bo + 4 + 4 * n
                for k in range(n):
                    ci, s, sz = struct.unpack_from("<BBH", B.leaf, bo + 4 + 4 * k)
                    if s in (0, 1, 3, 10, 11) or s > 11: st['sub_badtype_%d' % s] += 1; ok = False
                    elif not leaf(s, p, vb, nv): ok = False
                    p += 4 * sz
            elif t[c] in (10, 11): st['top_badtype_%d' % t[c]] += 1; ok = False
            elif t[c] >= 4:
                if not leaf(t[c], (lb + idx[c]) * 4, vb, nv): ok = False
    st['models_ok' if ok else 'models_bad'] += 1
    if not ok and len(ex) < 6: ex.append((mi, m[6:12]))
print(L, 'models', nm, 'floatverts', nf, 'packed', npk, dict(sorted(st.items())))
for e in ex: print('  bad model', e)
