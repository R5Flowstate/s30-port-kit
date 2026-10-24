"""Replace BVH heightfield leaves (type 10, absent from the S3 dedi) with bundles of
uncompressed triangle leaves (type 4) in a dedi BSP. In place on lumps 0x03/0x12/0x13."""
import struct, sys, collections, shutil, os
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from bvhlib import Bvh, load_hf, patch, patch_tris

D, L = sys.argv[1], sys.argv[2]
P = "%s/%s.bsp." % (D, L)
for n in ("0003", "0012", "0013"):
    if not os.path.isfile(P + n + ".bsp_lump.prehf"):
        shutil.copyfile(P + n + ".bsp_lump", P + n + ".bsp_lump.prehf")
B = Bvh.__new__(Bvh)
B.nodes = bytearray(open(P + "0012.bsp_lump.prehf", "rb").read())
B.leaf = open(P + "0013.bsp_lump.prehf", "rb").read()
verts = bytearray(open(P + "0003.bsp_lump.prehf", "rb").read())
models = open(P + "000e.bsp_lump", "rb").read()
world_nodes = struct.unpack_from("<I", models, 64 + 32)[0]
VB = struct.unpack_from("<I", models, 16 + 24)[0] if False else 1
hfs, heights = load_hf(sys.argv[3], L)
leaf_out = bytearray(B.leaf)
while len(leaf_out) % 4: leaf_out += b"\0"

# bits 29-31 = per-edge contact flags read by the hull sweep; retail sets all three on every triangle
EDGE_ALL = 7 << 29
patch_cache = {}
stat = collections.Counter()

def emit_patch_leaves(off):
    """-> list of (type, bytes) type-4 leaves for the patch at leaf dword offset."""
    if off in patch_cache: return patch_cache[off]
    p = patch(B.leaf, off * 4)
    tris, spr, spc = patch_tris(p, hfs[p["hidx"]], heights)
    # vertex block: one vertex per grid sample, row-major
    start = len(verts) // 12
    vidx = {}
    for t in tris:
        for v in t[:3]:
            if v not in vidx:
                vidx[v] = len(vidx)
    for v in sorted(vidx, key=vidx.get):
        verts.extend(struct.pack("<3f", *v))
    enc = []
    for v0, v1, v2, sp in tris:
        ids = [start + vidx[v0] - VB, start + vidx[v1] - VB, start + vidx[v2] - VB]
        k = ids.index(min(ids))
        ids = ids[k:] + ids[:k]          # cyclic rotation keeps winding
        enc.append((ids, sp & 0xFFF))
    enc.sort(key=lambda e: e[0][0])
    leaves = []
    i = 0
    while i < len(enc):
        sp = enc[i][1]
        grp = [enc[i]]; i += 1
        while i < len(enc) and len(grp) < 16 and enc[i][1] == sp:
            grp.append(enc[i]); i += 1
        base = (grp[0][0][0] >> 10) << 10
        h1 = base >> 10
        assert h1 < 65536
        out = bytearray(struct.pack("<HH", sp | ((len(grp) - 1) << 12), h1))
        for ids, _ in grp:
            d = ids[0] - base; o1 = ids[1] - ids[0] - 1; o2 = ids[2] - ids[0] - 1
            assert 0 <= d < 2048 and 0 <= o1 < 512 and 0 <= o2 < 512, (d, o1, o2)
            out += struct.pack("<I", d | (o1 << 11) | (o2 << 20) | EDGE_ALL)
            base = ids[0]
        leaves.append(bytes(out))
    stat["patches"] += 1; stat["tris"] += len(enc); stat["leaves4"] += len(leaves)
    patch_cache[off] = leaves
    return leaves

def write_bundle(entries):
    """entries: list of (contents, type, bytes). Returns dword offset."""
    off = len(leaf_out) // 4
    leaf_out.extend(struct.pack("<I", len(entries)))
    for ci, t, b in entries:
        assert len(b) % 4 == 0 and len(b) // 4 < 65536
        leaf_out.extend(struct.pack("<BBH", ci, t, len(b) // 4))
    for _, _, b in entries:
        leaf_out.extend(b)
    return off

bundle_cache = {}
for i in range(world_nodes):
    o = i * 64
    w = list(struct.unpack_from("<4I", B.nodes, o + 48))
    types = [w[2] & 0xF, (w[2] >> 4) & 0xF, w[3] & 0xF, (w[3] >> 4) & 0xF]
    contents = w[0] & 0xFF
    changed = False
    for c in range(4):
        t = types[c]; idx = w[c] >> 8
        new = None
        if t == 10:
            if ("hf", idx) not in bundle_cache:
                bundle_cache[("hf", idx)] = write_bundle([(contents, 4, b) for b in emit_patch_leaves(idx)])
            new = bundle_cache[("hf", idx)]
            stat["top_hf"] += 1
        elif t == 3:
            bo = idx * 4
            n = struct.unpack_from("<I", B.leaf, bo)[0]
            hdr = [struct.unpack_from("<BBH", B.leaf, bo + 4 + 4 * k) for k in range(n)]
            if any(h[1] == 10 for h in hdr):
                if ("b", idx) not in bundle_cache:
                    ents = []; p = bo + 4 + 4 * n
                    for ci, st, sz in hdr:
                        if st == 10:
                            ents += [(ci, 4, b) for b in emit_patch_leaves(p // 4)]
                            stat["sub_hf"] += 1
                        else:
                            ents.append((ci, st, B.leaf[p:p + 4 * sz]))
                        p += 4 * sz
                    bundle_cache[("b", idx)] = write_bundle(ents)
                new = bundle_cache[("b", idx)]
        if new is not None:
            assert new < (1 << 24), "leaf index overflow"
            w[c] = (w[c] & 0xFF) | (new << 8)
            if c >= 2:
                sh = (c - 2)
            if t == 10:
                if c == 0: w[2] = (w[2] & ~0xF) | 3
                elif c == 1: w[2] = (w[2] & ~0xF0) | 0x30
                elif c == 2: w[3] = (w[3] & ~0xF) | 3
                else: w[3] = (w[3] & ~0xF0) | 0x30
            changed = True
    if changed:
        struct.pack_into("<4I", B.nodes, o + 48, *w)
        stat["nodes"] += 1

open(P + "0012.bsp_lump", "wb").write(B.nodes)
open(P + "0013.bsp_lump", "wb").write(leaf_out)
open(P + "0003.bsp_lump", "wb").write(verts)
# the engine reads each external lump at the length the .bsp header declares
hdr = bytearray(open("%s/%s.bsp" % (D, L), "rb").read())
for n, data in ((0x03, verts), (0x12, B.nodes), (0x13, leaf_out)):
    struct.pack_into("<I", hdr, 16 + 16 * n + 4, len(data))
open("%s/%s.bsp" % (D, L), "wb").write(hdr)
print(dict(stat), "leaf dwords", len(leaf_out) // 4, "verts", len(verts) // 12)
