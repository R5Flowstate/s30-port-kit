"""Collision heightfield patches of mu5 as flat numpy quad arrays (exactly the hf2tris triangulation)."""
import kitpaths as KP
import struct, sys, os
import numpy as np

from bvhlib import load_hf, patch

MAPD = (KP.MAPS + r"/mp_rr_desertlands_mu5/bsp/")
L = "mp_rr_desertlands_mu5"

def load():
    P = MAPD + "client_v51_prehf/%s.bsp." % L
    nodes = open(P + "0012.bsp_lump", "rb").read(); leaf = open(P + "0013.bsp_lump", "rb").read()
    hfs, heights = load_hf(MAPD + "client_v51", L)
    hf = hfs[0]; heights = np.frombuffer(heights, np.uint8)
    models = open(MAPD + "client_v51/%s.bsp.000e.bsp_lump" % L, "rb").read()
    nn = struct.unpack_from("<I", models, 64 + 32)[0]
    offs = set()
    for i in range(nn):
        w = struct.unpack_from("<4I", nodes, i * 64 + 48)
        types = [w[2] & 0xF, (w[2] >> 4) & 0xF, w[3] & 0xF, (w[3] >> 4) & 0xF]
        for c in range(4):
            t, idx = types[c], w[c] >> 8
            if t == 10: offs.add(idx)
            elif t == 3:
                bo = idx * 4; n = struct.unpack_from("<I", leaf, bo)[0]; p = bo + 4 + 4 * n
                for k in range(n):
                    ci, st, sz = struct.unpack_from("<BBH", leaf, bo + 4 + 4 * k)
                    if st == 10: offs.add(p // 4)
                    p += sz * 4
    # per quad: grid x0,y0 (samples), q, ttb, corner heights a(r,c) b(r,c+1) c(r+1,c) d(r+1,c+1) (NaN = hole), patch id
    X, Y, Q, T, ZA, ZB, ZC, ZD, PID = [], [], [], [], [], [], [], [], []
    PX, PY, LC, LR, LAST_C, LAST_R = [], [], [], [], [], []
    for pid, off in enumerate(sorted(offs)):
        p = patch(leaf, off * 4); q = p["qpp"]
        spr = (p["dims"][0] - 1) // q + 1; spc = (p["dims"][1] - 1) // q + 1
        hv = heights[p["hoff"]:p["hoff"] + spr * spc].reshape(spc, spr).astype(np.float64)
        z = np.where(hv == 0, np.nan, (hv - 1) * p["hs"] + p["bias"])
        r, c = np.mgrid[0:spc - 1, 0:spr - 1]
        r = r.ravel(); c = c.ravel()
        X.append(p["coord"][0] + c * q); Y.append(p["coord"][1] + r * q)
        Q.append(np.full(len(r), q)); T.append(np.full(len(r), p["ttb"]))
        ZA.append(z[r, c]); ZB.append(z[r, c + 1]); ZC.append(z[r + 1, c]); ZD.append(z[r + 1, c + 1]); PID.append(np.full(len(r), pid))
        PX.append(np.full(len(r), p["coord"][0])); PY.append(np.full(len(r), p["coord"][1])); LC.append(c); LR.append(r)
        LAST_C.append(c == spr - 2); LAST_R.append(r == spc - 2)
    cat = lambda a, t: np.concatenate(a).astype(t)
    return dict(hf=hf, x=cat(X, np.int32), y=cat(Y, np.int32), q=cat(Q, np.int32), ttb=cat(T, np.int8),
                za=cat(ZA, np.float64), zb=cat(ZB, np.float64), zc=cat(ZC, np.float64), zd=cat(ZD, np.float64), pid=cat(PID, np.int32),
                px=cat(PX, np.int32), py=cat(PY, np.int32), lc=cat(LC, np.int32), lr=cat(LR, np.int32),
                last_c=cat(LAST_C, bool), last_r=cat(LAST_R, bool))

if __name__ == "__main__":
    import collections
    d = load(); hf = d["hf"]
    print("hf origin", hf["origin"], "scale", hf["scale"], "dims", hf["dims"])
    print("quads", len(d["x"]), "q hist", collections.Counter(d["q"].tolist()))
    ok = lambda *z: np.all([~np.isnan(v) for v in z], 0)
    t0 = np.where(d["ttb"] == 0, ok(d["za"], d["zd"], d["zc"]).astype(int) + ok(d["za"], d["zb"], d["zd"]), ok(d["za"], d["zb"], d["zc"]).astype(int) + ok(d["zb"], d["zd"], d["zc"]))
    print("tris", t0.sum(), "patches", d["pid"].max() + 1)
