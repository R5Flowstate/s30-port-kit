"""Gate: terrain render surface == collision surface, both decoded from the built client BSP.

Render = up-facing (clockwise from above, retail winding) triangles of the terrain mesh range (lumps 0x50/0x52/0x4F/0x49/0x03).
Collision = type-4 triangle leaves of the world BVH whose vertices lie in the heightfield vertex block (0x12/0x13/0x03).
Both are rasterized at an offset sample lattice; reports |dz| where both cover a point. Exit 1 if max |dz| > 2 u."""
import os, struct, sys
import numpy as np

D, B = sys.argv[1], sys.argv[2]
lo, hi = int(sys.argv[3]), int(sys.argv[4])
hf_v0, hf_v1 = int(sys.argv[5]), int(sys.argv[6])     # heightfield vertex block in 0x03
L = lambda i: np.fromfile(os.path.join(D, "%s.%04x.bsp_lump" % (B, i)), np.uint8)
pos = L(0x03).view("<f4").reshape(-1, 3).astype(np.float64)
Ox, Oy, SX = -45500.0, -45500.0, 91000.0 / 4095

# ---- render
me = L(0x50).tobytes(); ms = L(0x52).tobytes(); idx = L(0x4F).view("<u2"); vb = L(0x49).view("<u4").reshape(-1, 8)
R = []
for k in range(lo, hi):
    fi, tri = struct.unpack_from("<IH", me, k * 28)
    si = struct.unpack_from("<H", me, k * 28 + 22)[0]
    vo = struct.unpack_from("<4hI", ms, si * 12)[4]
    ii = idx[fi:fi + tri * 3].astype(np.int64).reshape(-1, 3) + vo
    R.append(pos[vb[ii, 0]])
R = np.concatenate(R)
nz = np.cross(R[:, 1] - R[:, 0], R[:, 2] - R[:, 0])
up = -nz[:, 2] / np.maximum(np.linalg.norm(nz, axis=1), 1e-12)
print("render tris", len(R), "up-facing", int((up > 0.05).sum()), "down-facing (skirt backs only)", int((up < -0.05).sum()))
R = R[up > 0.05]

# ---- collision: walk the world BVH
nodes = L(0x12).tobytes(); leaf = L(0x13).tobytes()
models = L(0x0E).tobytes()
world_nodes = struct.unpack_from("<I", models, 64 + 32)[0]
def tris4(o):
    h0, h1 = struct.unpack_from("<HH", leaf, o)
    n = (h0 >> 12) + 1; base = (h1 << 10) + 1; out = []
    for k in range(n):
        x = struct.unpack_from("<I", leaf, o + 4 + 4 * k)[0]
        base += x & 0x7FF
        out.append((base, base + 1 + ((x >> 11) & 0x1FF), base + 1 + ((x >> 20) & 0x1FF)))
    return out
C = []
for i in range(world_nodes):
    w = struct.unpack_from("<4I", nodes, i * 64 + 48)
    types = [w[2] & 0xF, (w[2] >> 4) & 0xF, w[3] & 0xF, (w[3] >> 4) & 0xF]
    for c in range(4):
        t, ix = types[c], w[c] >> 8
        if t == 4:
            C.extend(tris4(ix * 4))
        elif t == 3:
            bo = ix * 4; n = struct.unpack_from("<I", leaf, bo)[0]; p = bo + 4 + 4 * n
            for kk in range(n):
                ci, st, sz = struct.unpack_from("<BBH", leaf, bo + 4 + 4 * kk)
                if st == 4: C.extend(tris4(p))
                p += sz * 4
C = np.array(C, np.int64)
C = C[np.all((C >= hf_v0) & (C < hf_v1), 1)]
C = pos[C]
print("collision heightfield tris", len(C))

def raster(T):
    """max z per lattice point (i + 0.37, j + 0.61) covered by a triangle."""
    gx = (T[:, :, 0] - Ox) / SX - 0.37; gy = (T[:, :, 1] - Oy) / SX - 0.61
    x0 = np.ceil(gx.min(1)).astype(np.int64); x1 = np.floor(gx.max(1)).astype(np.int64)
    y0 = np.ceil(gy.min(1)).astype(np.int64); y1 = np.floor(gy.max(1)).astype(np.int64)
    w = np.maximum(x1 - x0 + 1, 0); h = np.maximum(y1 - y0 + 1, 0)
    Z = np.full(4096 * 4096, -np.inf)
    for bw in np.unique(np.maximum(w, h)):
        sel = np.flatnonzero(np.maximum(w, h) == bw)
        if bw == 0 or len(sel) == 0: continue
        for dy in range(bw):
            for dx in range(bw):
                s = sel[(dx < w[sel]) & (dy < h[sel])]
                if not len(s): continue
                px = x0[s] + dx; py = y0[s] + dy
                X = Ox + (px + 0.37) * SX; Y = Oy + (py + 0.61) * SX
                a, b, c = T[s, 0], T[s, 1], T[s, 2]
                den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
                l1 = ((b[:, 1] - c[:, 1]) * (X - c[:, 0]) + (c[:, 0] - b[:, 0]) * (Y - c[:, 1])) / den
                l2 = ((c[:, 1] - a[:, 1]) * (X - c[:, 0]) + (a[:, 0] - c[:, 0]) * (Y - c[:, 1])) / den
                l3 = 1 - l1 - l2
                ok = (l1 >= -1e-9) & (l2 >= -1e-9) & (l3 >= -1e-9) & (px >= 0) & (px < 4096) & (py >= 0) & (py < 4096) & np.isfinite(den) & (den != 0)
                z = l1 * a[:, 2] + l2 * b[:, 2] + l3 * c[:, 2]
                np.maximum.at(Z, (py * 4096 + px)[ok], z[ok])
    return Z
Zr, Zc = raster(R), raster(C)
both = np.isfinite(Zr) & np.isfinite(Zc)
dz = np.abs(Zr[both] - Zc[both])
only_r = int((np.isfinite(Zr) & ~np.isfinite(Zc)).sum()); only_c = int((~np.isfinite(Zr) & np.isfinite(Zc)).sum())
print("points both %d  render-only %d  collision-only %d" % (both.sum(), only_r, only_c))
print("|dz| p50 %.4f p99 %.4f p99.9 %.4f max %.4f  >2u %d  >4u %d" % (np.percentile(dz, 50), np.percentile(dz, 99), np.percentile(dz, 99.9), dz.max(), (dz > 2).sum(), (dz > 4).sum()))
sys.exit(0 if dz.max() <= 2.0 and (dz > 4).sum() == 0 else 1)
