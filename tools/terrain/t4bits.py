"""Histogram the spare bits of type-4 triangle leaves (per-tri u32 bits 29-31, header u16 bits) in a BSP world BVH."""
import struct, sys, collections
D, B, ext = sys.argv[1], sys.argv[2], sys.argv[3]
lo = int(sys.argv[4]) if len(sys.argv) > 4 else 0; hi = int(sys.argv[5]) if len(sys.argv) > 5 else 1 << 40
L = lambda i: open("%s/%s.%04x%s" % (D, B, i, ext), "rb").read()
nodes, leaf, models = L(0x12), L(0x13), L(0x0E)
wn = struct.unpack_from("<I", models, 64 + 32)[0]
hb = collections.Counter(); tb = collections.Counter(); n = 0
def t4(o):
    global n
    h0, h1 = struct.unpack_from("<HH", leaf, o); k = (h0 >> 12) + 1; base = (h1 << 10) + 1
    for j in range(k):
        x = struct.unpack_from("<I", leaf, o + 4 + 4 * j)[0]
        base += x & 0x7FF
        if lo <= base < hi:
            tb[x >> 29] += 1; n += 1
for i in range(wn):
    w = struct.unpack_from("<4I", nodes, i * 64 + 48)
    ty = [w[2] & 0xF, (w[2] >> 4) & 0xF, w[3] & 0xF, (w[3] >> 4) & 0xF]
    for c in range(4):
        t, ix = ty[c], w[c] >> 8
        if t == 4: t4(ix * 4)
        elif t == 3:
            bo = ix * 4; m = struct.unpack_from("<I", leaf, bo)[0]; p = bo + 4 + 4 * m
            for kk in range(m):
                ci, st, sz = struct.unpack_from("<BBH", leaf, bo + 4 + 4 * kk)
                if st == 4: t4(p)
                p += sz * 4
print("tris", n, "top3 bits", sorted(tb.items()))
