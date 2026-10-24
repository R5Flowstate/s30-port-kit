"""Set the three edge-contact bits (29-31) on every type-4 triangle of a BSP world BVH that lacks them. In place on 0x13."""
import struct, sys
D, B = sys.argv[1], sys.argv[2]
P = "%s/%s.0013.bsp_lump" % (D, B)
nodes = open("%s/%s.0012.bsp_lump" % (D, B), "rb").read()
models = open("%s/%s.000e.bsp_lump" % (D, B), "rb").read()
leaf = bytearray(open(P, "rb").read())
wn = struct.unpack_from("<I", models, 64 + 32)[0]
seen = set(); fixed = total = 0
def t4(o):
    global fixed, total
    if o in seen: return
    seen.add(o)
    h0 = struct.unpack_from("<H", leaf, o)[0]
    for j in range((h0 >> 12) + 1):
        a = o + 4 + 4 * j
        x = struct.unpack_from("<I", leaf, a)[0]; total += 1
        if x >> 29 != 7:
            struct.pack_into("<I", leaf, a, x | (7 << 29)); fixed += 1
for i in range(wn):
    w = struct.unpack_from("<4I", nodes, i * 64 + 48)
    ty = [w[2] & 0xF, (w[2] >> 4) & 0xF, w[3] & 0xF, (w[3] >> 4) & 0xF]
    for c in range(4):
        t, ix = ty[c], w[c] >> 8
        if t == 4: t4(ix * 4)
        elif t == 3:
            bo = ix * 4; m = struct.unpack_from("<I", leaf, bo)[0]; p = bo + 4 + 4 * m
            for k in range(m):
                ci, st, sz = struct.unpack_from("<BBH", leaf, bo + 4 + 4 * k)
                if st == 4: t4(p)
                p += sz * 4
if "--apply" in sys.argv: open(P, "wb").write(leaf)
print("type-4 tris", total, "edge bits set", fixed, "(applied)" if "--apply" in sys.argv else "(dry run)")
