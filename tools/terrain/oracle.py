"""Derive S30 splat layer pair -> S21 terrain material (and blend encoding) from hu's ground meshes."""
import kitpaths as KP
import struct, json, collections
import numpy as np
HU = (KP.WORK + r"/mu5_terrain/hu/wrap/maps/mp_rr_desertlands_hu.bsp.%04X.bsp_lump.client")
L = lambda i: open(HU % i, "rb").read()
TEX = (KP.MAPS + r"/mp_rr_desertlands_mu5/extract/texture/")
LAYERS = json.load(open((KP.WORK + r"/mu5_terrain/layers.json")))
td, strs, ms, me = L(2), L(0xF), L(0x52), L(0x50)
names = []
for k in range(len(td) // 16):
    ni = struct.unpack_from("<i", td, k * 16)[0]
    names.append(strs[ni:strs.index(b"\0", ni)].decode().lower().replace("\\", "/"))
terr = set(l.split(None, 1)[1].strip() for l in open((KP.WORK + r"/mu5_terrain/terrain_mats.txt")))
pos = np.frombuffer(L(3), np.float32).reshape(-1, 3)
vlb = np.frombuffer(L(0x49), np.dtype([("p", "<u4"), ("n", "<u4"), ("uv", "<f4", 2), ("m1", "<i4"), ("lm", "<f4", 2), ("c", "u1", 4)]))
idx = np.frombuffer(L(0x4F), np.uint16)
ids = np.fromfile(TEX + "0xFA20A4BD02632A22.dds", np.uint8)[148:].view(np.uint32).reshape(1024, 1024, 4)
W = np.load((KP.WORK + r"/mu5_terrain/splat_w.npy"))
O, S = -45500.0, 91000.0 / 4096

def splat(x, y):
    gx = int((x - O) / S); gy = int((y - O) / S)
    if not (0 <= gx < 4096 and 0 <= gy < 4096): return None
    blk = ids[gy >> 2, gx >> 2]; k = (gy & 3) * 4 + (gx & 3)
    word, sh = (k // 6, (k % 6) * 5)
    m = (int(blk[word]) >> sh) & 31
    pal = [(int(blk[3]) >> (8 * i)) & 255 for i in range(4)]
    act = [pal[i] for i in range(4) if m >> i & 1 and pal[i] != 255]
    r, g = W[gy, gx] / 255.0
    w = [r, g, max(0.0, 1 - r - g)][:len(act)] if len(act) > 1 else [1.0] * len(act)
    return sorted(zip(w, act), reverse=True)

pairs = collections.defaultdict(collections.Counter)
corr = collections.defaultdict(list)
flags = collections.defaultdict(collections.Counter)
for k in range(len(me) // 28):
    f = struct.unpack_from("<IH8hHI", me, k * 28)
    first, ntri, msort, fl = f[0], f[1], f[10], f[11]
    t, lm, _, _, vo = struct.unpack_from("<4hI", ms, msort * 12)
    nm = names[t]
    if nm not in terr or (fl & 0x600) != 0x200 and (fl & 0xF00) not in (0x200, 0xA00): 
        if nm in terr: flags[nm][hex(fl)] += 1
        continue
    flags[nm][hex(fl)] += 1
    vs = np.unique(idx[first:first + ntri * 3].astype(np.int64) + vo)
    for v in vs[::3]:
        p = pos[vlb["p"][v]]
        s = splat(p[0], p[1])
        if not s: continue
        top = [LAYERS[a] for _, a in s[:2]]
        pairs[nm][tuple(sorted(top))] += 1
        if len(s) >= 2:
            corr[nm].append((s[0][0], LAYERS[s[0][1]], vlb["c"][v][3], vlb["c"][v][0]))
out = {}
for nm in sorted(pairs, key=lambda n: -sum(pairs[n].values())):
    tot = sum(pairs[nm].values())
    out[nm] = [(list(k), n) for k, n in pairs[nm].most_common(4)]
    print(tot, nm, dict(flags[nm]), [("+".join(a.split("/")[-1] for a in k), round(n / tot, 2)) for k, n in pairs[nm].most_common(3)])
json.dump(out, open((KP.WORK + r"/mu5_terrain/oracle_pairs.json"), "w"), indent=1)
# vertex colour usage sample
for nm in list(pairs)[:3]:
    c = np.array([(a, b) for _, _, a, b in corr[nm][:5000]], float) if corr[nm] else None
    if c is not None: print(nm, "alpha mean/std", c[:, 0].mean().round(1), c[:, 0].std().round(1), "red", c[:, 1].mean().round(1))
