"""Validate static-prop collision BVHs in dedi rmdl (v54) the way r5apex_ds walks them
: hdr = mdl + *(int*)(mdl+0x214); part k record = hdr ints [4+8k .. 11+8k]:
flags, nodes, verts, leaves (offsets from hdr), origin xy, z, scale."""
import struct, sys, os, glob, collections
ROOT = sys.argv[1]
LAY = {4: (0x7FF, 11, 20), 5: (0x7FF, 11, 20), 6: (0x3FF, 10, 19), 7: (0x3FF, 10, 19)}
st = collections.Counter(); bad = []
def check(path):
    b = open(path, 'rb').read()
    if len(b) < 0x220 or b[:4] != b'IDST': st['not_idst'] += 1; return
    off = struct.unpack_from('<i', b, 0x214)[0]
    if off <= 0: st['no_coll'] += 1; return
    sel = struct.unpack_from('<4H', b, 0x218)
    h = off
    masks, sprops, names = struct.unpack_from('<3i', b, h)
    used = sorted({s for s in sel if not s & 0x8000})
    errs = []
    for part in used:
        r = h + 4 * (4 + 8 * part)
        if r + 32 > len(b): errs.append('partrec_oob %d' % part); continue
        flags, nodes, verts, leaves = struct.unpack_from('<4i', b, r)
        st['flags%d' % (flags & 1)] += 1
        vsz = 6 if flags & 1 else 12
        # vertex count bound: up to the next section start after verts
        ends = sorted(x for x in (nodes, leaves, masks, sprops, names) if x > verts) + [len(b) - h]
        nv = (ends[0] - verts) // vsz
        stack = [0]; seen = set()
        while stack:
            i = stack.pop()
            if i in seen: continue
            seen.add(i)
            no = h + nodes + 64 * i
            if no + 64 > len(b): errs.append('node_oob'); break
            w = struct.unpack_from('<4I', b, no + 48)
            t = [w[2] & 0xF, (w[2] >> 4) & 0xF, w[3] & 0xF, (w[3] >> 4) & 0xF]
            for c in range(4):
                idx = w[c] >> 8
                if t[c] == 0: stack.append(idx); continue
                lo = h + leaves + 4 * idx
                subs = []
                if t[c] == 3:
                    n = struct.unpack_from('<I', b, lo)[0] if lo + 4 <= len(b) else 1 << 30
                    if n == 0 or n > 4096: errs.append('bundle n %d' % n); continue
                    p = lo + 4 + 4 * n
                    for k in range(n):
                        ci, s, sz = struct.unpack_from('<BBH', b, lo + 4 + 4 * k); subs.append((s, p)); p += 4 * sz
                else:
                    subs.append((t[c], lo))
                for s, p in subs:
                    if s in (10, 11) or s > 11: errs.append('type%d' % s); continue
                    if s not in LAY: continue
                    if p + 4 > len(b): errs.append('leaf_oob'); continue
                    h0, h1 = struct.unpack_from('<HH', b, p); n = (h0 >> 12) + 1
                    dm, s1, s2 = LAY[s]; base = h1 << 10; mx = 0
                    for j in range(n):
                        x = struct.unpack_from('<I', b, p + 4 + 4 * j)[0]; base += x & dm
                        mx = max(mx, base + 1 + ((x >> s1) & 0x1FF), base + 1 + ((x >> s2) & 0x1FF))
                    st['t%d' % s] += 1
                    if mx >= nv: errs.append('t%d vert %d>=%d' % (s, mx, nv))
    if errs:
        st['models_bad'] += 1
        if len(bad) < 25: bad.append((os.path.relpath(path, ROOT), errs[:3], len(errs)))
    else:
        st['models_ok'] += 1
for p in glob.glob(os.path.join(ROOT, '**', '*.rmdl'), recursive=True):
    check(p)
print(ROOT[-40:], dict(sorted(st.items())))
for x in bad: print(' ', x)
