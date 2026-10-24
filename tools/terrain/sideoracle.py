"""Empirical blend sides of hu ground materials: S30 splat layer under hu vertices with alpha 255 (slot 1 shown) vs alpha 0
(slot 0 shown), and the albedo texture of each slot. Writes side_oracle.json."""
import kitpaths as KP
import struct, json, collections, sys
import numpy as np

import matmap
HU = (KP.WORK + "/mu5_terrain/hu/wrap/maps/mp_rr_desertlands_hu.bsp.%04x.bsp_lump.client")
L = lambda i: np.fromfile(HU % i, np.uint8)
td, strs = L(2).tobytes(), L(0xF).tobytes()
names = []
for k in range(len(td) // 16):
    ni = struct.unpack_from("<i", td, k * 16)[0]
    names.append(strs[ni:strs.index(b"\0", ni)].decode().lower().replace("\\", "/"))
me, ms = L(0x50).tobytes(), L(0x52).tobytes()
pos = L(3).view("<f4").reshape(-1, 3); idx = L(0x4F).view("<u2")
vb = L(0x49).view(np.dtype([("p", "<u4"), ("n", "<u4"), ("uv", "<f4", 2), ("c", "u1", 4), ("lm", "<f4", 2), ("t", "<u4")]))
act, wts = matmap.sample_layers()
SX, O = 91000.0 / 4095, -45500.0
acc = collections.defaultdict(lambda: [collections.Counter(), collections.Counter()])
for k in range(len(me) // 28):
    fi, tri = struct.unpack_from("<IH", me, k * 28); fl = struct.unpack_from("<I", me, k * 28 + 24)[0]
    if fl != 0x100A00: continue
    si = struct.unpack_from("<H", me, k * 28 + 22)[0]
    t, _, _, _, vo = struct.unpack_from("<4hI", ms, si * 12)
    nm = names[t]
    if "_bm" not in nm: continue
    v = np.unique(idx[fi:fi + tri * 3].astype(np.int64) + vo)
    a = vb["c"][v, 3]; p = pos[vb["p"][v]]
    gx = np.round((p[:, 0] - O) / SX).astype(int); gy = np.round((p[:, 1] - O) / SX).astype(int)
    ok = (gx >= 0) & (gx < 4096) & (gy >= 0) & (gy < 4096)
    for side, sel in ((1, ok & (a >= 230)), (0, ok & (a <= 25))):
        aa, ww = act[gy[sel], gx[sel]], wts[gy[sel], gx[sel]]
        top = np.take_along_axis(aa, np.argmax(ww, -1)[:, None], 1)[:, 0]
        acc[nm][side].update(top[top != 255].tolist())
out = {}
for nm, (c0, c1) in sorted(acc.items()):
    tot0, tot1 = sum(c0.values()), sum(c1.values())
    if tot0 < 50 or tot1 < 50: continue
    l1, n1 = c1.most_common(1)[0]; l0, n0 = c0.most_common(1)[0]
    out[nm] = {"slot1": matmap.TOK[l1], "slot1_share": n1 / tot1, "n1": tot1, "slot0": matmap.TOK[l0], "slot0_share": n0 / tot0, "n0": tot0}
    print("%-72s s1 %-30s %.2f (%d)  s0 %-30s %.2f (%d)" % (nm.split("/")[-1], matmap.TOK[l1], n1 / tot1, tot1, matmap.TOK[l0], n0 / tot0, tot0))
json.dump(out, open((KP.WORK + "/mu5_terrain/side_oracle.json"), "w"), indent=1)
