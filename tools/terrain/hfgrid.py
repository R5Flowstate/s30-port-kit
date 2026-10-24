"""Rasterize the mu5 collision heightfield (BVH type-10 patches) into a dense float32 grid (NaN = hole)."""
import kitpaths as KP
import struct, sys, os
import numpy as np

from bvhlib import load_hf, patch

D = (KP.MAPS + r"/mp_rr_desertlands_mu5/bsp/client_v51_prehf")
L = "mp_rr_desertlands_mu5"
P = "%s/%s.bsp." % (D, L)
nodes = open(P + "0012.bsp_lump", "rb").read()
leaf = open(P + "0013.bsp_lump", "rb").read()

hfs, heights = load_hf(D.replace("client_v51_prehf", "client_v51"), L)
hf = hfs[0]
heights = np.frombuffer(heights, np.uint8)
NX, NY = hf["dims"]
G = np.full((NY, NX), np.nan, np.float32)
SP = np.full((NY, NX), -1, np.int16)
GMIN = np.full((NY, NX), np.inf, np.float32); GMAX = np.full((NY, NX), -np.inf, np.float32)

offs = set()
models = open(P.replace("client_v51_prehf","client_v51") + "000e.bsp_lump", "rb").read()
nn = struct.unpack_from("<I", models, 64 + 32)[0]
for i in range(nn):
    w = struct.unpack_from("<4I", nodes, i * 64 + 48)
    types = [w[2] & 0xF, (w[2] >> 4) & 0xF, w[3] & 0xF, (w[3] >> 4) & 0xF]
    for c in range(4):
        t, idx = types[c], w[c] >> 8
        if t == 10:
            offs.add(idx)
        elif t == 3:
            bo = idx * 4
            n = struct.unpack_from("<I", leaf, bo)[0]
            p = bo + 4 + 4 * n
            for k in range(n):
                ci, st, sz = struct.unpack_from("<BBH", leaf, bo + 4 + 4 * k)
                if st == 10:
                    offs.add(p // 4)
                p += sz * 4
print("patches", len(offs))

for off in offs:
    p = patch(leaf, off * 4)
    q = p["qpp"]
    spr = (p["dims"][0] - 1) // q + 1
    spc = (p["dims"][1] - 1) // q + 1
    hv = heights[p["hoff"]:p["hoff"] + spr * spc].reshape(spc, spr).astype(np.float32)
    z = np.where(hv == 0, np.nan, (hv - 1) * p["hs"] + p["bias"])
    x0, y0 = p["coord"]
    # fill every grid sample inside the patch from the patch's triangulation
    for r in range(spc - 1):
        for c in range(spr - 1):
            a, b, cc, d = z[r, c], z[r, c + 1], z[r + 1, c], z[r + 1, c + 1]
            u = np.arange(q + 1, dtype=np.float32)[None, :] / q   # along x (c)
            v = np.arange(q + 1, dtype=np.float32)[:, None] / q   # along y (r)
            if p["ttb"] == 0:   # split a-d
                zz = np.where(u >= v, a + u * (b - a) + v * (d - b), a + v * (cc - a) + u * (d - cc))
            else:               # split b-cc
                zz = np.where(u + v <= 1, a + u * (b - a) + v * (cc - a), d + (1 - u) * (cc - d) + (1 - v) * (b - d))
            gy, gx = y0 + r * q, x0 + c * q
            sl = G[gy:gy + q + 1, gx:gx + q + 1]
            sub = zz[:sl.shape[0], :sl.shape[1]]
            m = ~np.isnan(sub)
            sl[m] = sub[m]
            mn_ = GMIN[gy:gy + q + 1, gx:gx + q + 1]; mx_ = GMAX[gy:gy + q + 1, gx:gx + q + 1]
            mn_[m] = np.minimum(mn_[m], sub[m]); mx_[m] = np.maximum(mx_[m], sub[m])
            SP[gy:gy + q + 1, gx:gx + q + 1][m] = p["sp"][0]
np.save((KP.WORK + r"/mu5_terrain/hf_grid.npy"), G)
np.save((KP.WORK + r"/mu5_terrain/hf_sp.npy"), SP)
d = GMAX - GMIN; d = d[np.isfinite(d)]
print("seam dz: samples", d.size, "p50 %.2f p99 %.2f p99.9 %.2f max %.1f  >8u: %d  >18u: %d" % (np.percentile(d,50), np.percentile(d,99), np.percentile(d,99.9), d.max(), (d>8).sum(), (d>18).sum()))
print("patch qpp hist", __import__("collections").Counter(patch(leaf, o*4)["qpp"] for o in offs))
print("dims", NX, NY, "valid", np.count_nonzero(~np.isnan(G)), "zmin", np.nanmin(G), "zmax", np.nanmax(G))
