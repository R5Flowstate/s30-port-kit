"""Terrain material choice for mu5: S30 splat layers -> S21 world materials whose NAME carries those layers.

Catalogue = world `_wldc` materials resident in the mu5 pak (R) or in the S21 hu pak (H), found by the layer names in their
material name. Which layer sits in which blend slot comes from texture identity (texsides.py), never from name order.
Ties go to the material hu's own ground uses most.
Per BLOCK x BLOCK samples the material with the largest covered splat weight wins (resident preferred on ties, hu only when
it covers at least MIN_GAIN more); blocks no material covers fall back to the hu-derived oracle in gen_terrain.py.
Vertex alpha = weight(first) / (weight(first) + weight(second)); 255 shows the first-named layer."""
import kitpaths as KP
import csv, json, sys
import numpy as np

ROOT = (KP.WORK + "/mu5_terrain/")
MAP_STEM = "mu5"
MAPD = (KP.MAPS + "/mp_rr_desertlands_mu5/")
BLOCK, MIN_GAIN, MIN_HU_AREA, N = 4, 0.05, 0.0005, 4096
LAYERS = json.load(open(ROOT + "layers.json"))
TOK = [l.split("/")[-1].replace("_wld_dn", "_wld") for l in LAYERS]
SKIP = ("colpass", "_b2t", "prepass", "_vtx", "transparent", "_nosub", "decal")

def catalogue():
    def names(path, pak):
        out = {}
        for r in csv.reader(open(path)):
            if not r or r[0] != "matl" or (pak and r[4] != pak): continue
            a = r[-1].lower().replace("\\", "/")
            if not a.startswith("material/world/") or not a.endswith("_wldc.rpak"): continue
            out[a[len("material/"):-len("_wldc.rpak")]] = int(r[3], 16)
        return out
    res = names(MAPD + "inv/base.csv", None)
    hu = names(ROOT + "s30inv/hu.csv", "mp_rr_desertlands_hu.rpak")
    cat = {}
    for src, tbl in (("H", hu), ("R", res)):
        for m in tbl:
            base = m.split("/")[-1]
            if any(s in base for s in SKIP): continue
            if base in TOK:
                l = TOK.index(base); cat[m] = (src, l, l); continue
            if "_bm" not in base: continue
            hits = sorted((base.find(t), i) for i, t in enumerate(TOK) if base.find(t) >= 0)
            hits = [h for h in hits if not any(h[0] > g[0] and h[0] < g[0] + len(TOK[g[1]]) for g in hits)]
            if not hits: continue
            if len(hits) == 1:
                first = hits[0][1] if hits[0][0] == 0 else -1
                second = -1 if hits[0][0] == 0 else hits[0][1]
            else:
                first, second = hits[0][1], hits[1][1]
            cat[m] = (src, first, second)
    return cat

def sample_layers():
    """per sample: 3 (layer, weight) slots; layer 255 = empty. Sample i reads splat texel floor(i * 4096 / 4095)."""
    ids = np.fromfile(MAPD + "extract/texture/0xFA20A4BD02632A22.dds", np.uint8)[148:].view(np.uint32).reshape(1024, 1024, 4)
    W = np.load(ROOT + "splat_w.npy").astype(np.float32) / 255.0
    t = np.minimum((np.arange(N) * 4096) // 4095, N - 1)
    ty, tx = np.meshgrid(t, t, indexing="ij")
    blk = ids[ty >> 2, tx >> 2]
    k = (ty & 3) * 4 + (tx & 3)
    word = np.take_along_axis(blk, (k // 6)[..., None], 2)[..., 0].astype(np.int64)
    mask = (word >> ((k % 6) * 5)) & 31
    pal = np.stack([(blk[..., 3].astype(np.int64) >> (8 * i)) & 255 for i in range(4)], -1)
    del blk, word
    act = np.full((N, N, 3), 255, np.int16); cnt = np.zeros((N, N), np.int8)
    for i in range(4):
        on = ((mask >> i) & 1).astype(bool) & (pal[..., i] != 255) & (cnt < 3)
        yi, xi = np.nonzero(on)
        act[yi, xi, cnt[yi, xi]] = pal[yi, xi, i]
        cnt += on
    r, g = W[ty, tx, 0], W[ty, tx, 1]
    wts = np.stack([np.where(cnt == 1, 1.0, r), np.where(cnt >= 2, g, 0.0), np.where(cnt == 3, np.clip(1 - r - g, 0, 1), 0.0)], -1).astype(np.float32)
    wts[act == 255] = 0
    return act, wts

def layer_weight(act, wts, l, ys=None, xs=None):
    if ys is None: return (wts * (act == l)).sum(-1)
    return (wts[ys, xs] * (act[ys, xs] == l)).sum(-1)

def hu_use():
    out = {}
    for l in open(ROOT + "terrain_mats.txt"):
        n, m = l.split(None, 1); out[m.strip()] = int(n)
    return out

def choose(act, wts, valid, oracle_mat):
    """-> material names, sources, sides [M] of (slot1 layer set, slot0 layer set), block material grid, fallback mask."""
    import texsides
    cat = catalogue()
    sd = texsides.sides(cat)
    use = hu_use()
    names = sorted((m for m in sd if sd[m][0] | sd[m][1]), key=lambda m: (-use.get(m, 0), m))
    nb = N // BLOCK
    LW = np.zeros((nb, nb, len(LAYERS)), np.float32)
    for l in range(len(LAYERS)):
        lw = layer_weight(act, wts, l) * valid
        LW[..., l] = lw.reshape(nb, BLOCK, nb, BLOCK).sum((1, 3))
    tot = LW.sum(-1)
    side = [sd[m] for m in names]
    src = np.array([cat[m][0] for m in names])
    def cover(s):
        v = np.zeros((nb, nb), np.float32)
        for l in s[0] | s[1]: v += LW[..., l]
        return v
    best_r = np.full((nb, nb), -1); sc_r = np.zeros((nb, nb), np.float32)
    for i, s in enumerate(side):
        if src[i] != "R": continue
        v = cover(s); up = v > sc_r + 1e-6; best_r[up] = i; sc_r[up] = v[up]
    allowed = src == "H"
    while True:
        best_h = np.full((nb, nb), -1); sc_h = np.zeros((nb, nb), np.float32)
        for i, s in enumerate(side):
            if not allowed[i]: continue
            v = cover(s); up = v > sc_h + 1e-6; best_h[up] = i; sc_h[up] = v[up]
        use_h = (best_h >= 0) & (sc_h > sc_r + MIN_GAIN * np.maximum(tot, 1e-6))
        a = np.bincount(best_h[use_h], tot[use_h], len(names)) / tot.sum()
        drop = allowed & (a < MIN_HU_AREA)
        if not drop.any(): break
        allowed &= ~drop
    pick = np.where(use_h, best_h, best_r)
    fb = (pick < 0) | (np.maximum(sc_r, sc_h) <= 0)
    return names, src, side, pick, fb, tot, cat

def s30_stack(act, wts):
    """S30 RVT layer weighting at the samples (texelIDsAndWeights CS): slots from the top palette slot down,
    w' = sat(((w - mid) + fade(w) * h) * ramp + w) * rem, rem -= w', then normalised. h = layer mean height
    (layer_heights.json); the per-texel height breakup is the material's height mask."""
    D = json.load(open(ROOT + "rvt_layer_descs.json"))
    Hm = {int(k): v for k, v in json.load(open(ROOT + "layer_heights.json")).items()}
    mid = np.array([d["mid"] for d in D] + [0.0], np.float32)
    ramp = np.array([d["ramp"] for d in D] + [0.0], np.float32)
    hh = np.array([Hm[i] for i in range(len(D))] + [0.0], np.float32)
    li = np.where(act == 255, len(D), act).astype(np.int32)
    out = np.zeros_like(wts)
    rem = np.ones(act.shape[:2], np.float32)
    for s in (2, 1, 0):
        w = wts[..., s]
        l = li[..., s]
        fade = np.where(w < 0.25, 1 - np.clip(1 - 4 * w, 0, 1) ** 4, 1.0)
        wp = np.clip(((w - mid[l]) + fade * hh[l]) * ramp[l] + w, 0, 1) * rem
        wp = np.where(act[..., s] == 255, 0.0, wp)
        rem = np.clip(rem - wp, 0, 1)
        out[..., s] = wp
    tot = out.sum(-1, keepdims=True)
    return np.where(tot > 0, out / np.maximum(tot, 1e-9), 0.0).astype(np.float32)


def _fade(w):
    return np.where(w < 0.25, 1 - np.clip(1 - 4 * w, 0, 1) ** 4, 1.0)


def s30_pair_top(act, a, b):
    """Which of the two layers the RVT stacks on top (higher palette slot) where both are painted."""
    both = (act == a).any(-1) & (act == b).any(-1)
    if not both.any():
        return a
    ia = np.argmax(act == a, -1)[both]
    ib = np.argmax(act == b, -1)[both]
    return a if (ia > ib).mean() >= 0.5 else b


def s30_pair_alpha(wa, wb, a, b, top):
    """Vertex alpha for a pair material whose shader computes blend = sat(alpha*(1+k) + k*(mask-1)) with
    k = ramp(top) and mask = h(top) (top == a) or 1 - h(top) (top == b). With raw splat weights this reproduces
    the RVT top-layer weight sat(w*(1+r) + r*(fade(w)*h - mid)) per texel; fade uses the layer's mean height."""
    D = json.load(open(ROOT + "rvt_layer_descs.json"))
    Hm = {int(k): v for k, v in json.load(open(ROOT + "layer_heights.json")).items()}
    r, mid, hb = D[top]["ramp"], D[top]["mid"], Hm[top]
    if top == a:
        return np.clip(wa + r * (1 - mid - (1 - _fade(wa)) * hb) / (1 + r), 0, 1)
    return np.clip((1 + r * mid + r * (1 - _fade(wb)) * hb) / (1 + r) - wb, 0, 1)


S30_PREFIX = "world/terrain_s30/"
MIN_SECOND = 0.02
MIN_PAIR_AREA = 0.0003
CLOSED_BLOCKS = True


def s30_name(a, b):
    return "%s%s_l%02d_l%02d_bm" % (S30_PREFIX, MAP_STEM, a, b)


RVT_SINGLE = True


def choose_rvt(act, wts, valid):
    """One material for all ground: the RVT terrain shader composites every S30 layer per pixel from the splat
    textures (build_rvt.py), so there are no per-block pairs and the vertex alpha is unused."""
    nb = N // BLOCK
    tot = (wts.sum(-1) * valid).reshape(nb, BLOCK, nb, BLOCK).sum((1, 3))
    fb = tot <= 0
    pick = np.zeros((nb, nb), np.int64)
    pick[fb] = -1
    return ["%s%s_rvt" % (S30_PREFIX, MAP_STEM)], np.array(["S"]), [({0}, {0})], pick, fb, tot, None


def choose_s30(act, wts, valid, oracle_mat):
    if RVT_SINGLE:
        return choose_rvt(act, wts, valid)
    """Every block gets the blend of its two strongest S30 layers (both layers' own textures, built by
    llyr_extract.py + synth_s30_mats.py). Same return shape as choose(); source "S"."""
    nb = N // BLOCK
    LW = np.zeros((nb, nb, len(LAYERS)), np.float32)
    for l in range(len(LAYERS)):
        LW[..., l] = (layer_weight(act, wts, l) * valid).reshape(nb, BLOCK, nb, BLOCK).sum((1, 3))
    tot = LW.sum(-1)
    if CLOSED_BLOCKS:
        # choose from the closed block (its own samples + the edge samples shared with the next blocks), so a
        # layer present on a shared border is in both neighbours' pairs and the seam blends instead of stepping
        for l in range(len(LAYERS)):
            f = layer_weight(act, wts, l) * valid
            e = np.zeros((N + 1, N + 1), np.float32); e[:N, :N] = f
            LW[..., l] += e[BLOCK::BLOCK, :N].reshape(nb, nb, BLOCK).sum(-1)[:nb] + e[:N, BLOCK::BLOCK].reshape(nb, BLOCK, nb).sum(1)[:, :nb]
    order = np.argsort(-LW, -1)
    a, b = order[..., 0], order[..., 1]
    wa = np.take_along_axis(LW, a[..., None], -1)[..., 0]
    wb = np.take_along_axis(LW, b[..., None], -1)[..., 0]
    b = np.where(wb > MIN_SECOND * np.maximum(wa + wb, 1e-6), b, a)
    lo, hi = np.minimum(a, b), np.maximum(a, b)
    key = lo * 64 + hi
    fb = tot <= 0
    T = tot.sum()
    while True:
        ks, inv = np.unique(key[~fb], return_inverse=True)
        area = np.bincount(inv, tot[~fb]) / T
        drop = ks[area < MIN_PAIR_AREA]
        if not len(drop):
            break
        keep = ks[area >= MIN_PAIR_AREA]
        cov = np.stack([LW[..., k // 64] + (LW[..., k % 64] if k // 64 != k % 64 else 0) for k in keep], -1)
        best = keep[np.argmax(cov, -1)]
        m = np.isin(key, drop)
        key = np.where(m, best, key)
    keys = sorted(set(key[~fb].ravel().tolist()))
    names = [s30_name(k // 64, k % 64) for k in keys]
    side = [({k // 64}, {k % 64}) for k in keys]
    idx = {k: i for i, k in enumerate(keys)}
    pick = np.vectorize(lambda k: idx.get(k, -1))(key)
    pick[fb] = -1
    src = np.array(["S"] * len(names))
    return names, src, side, pick, fb, tot, None


if __name__ == "__main__":
    import collections
    G = np.load(ROOT + "hf_grid.npy")
    valid = ~np.isnan(G)
    act, wts = sample_layers()
    names, src, side, pick, fb, tot, cat = choose(act, wts, valid, None)
    area = collections.Counter()
    for i, a in zip(pick[~fb].ravel(), tot[~fb].ravel()): area[i] += a
    T = tot.sum()
    print("blocks", pick.size, "fallback area %.2f%%" % (100 * tot[fb].sum() / T))
    nm = lambda S: "+".join(sorted(TOK[x] for x in S)) or "?"
    for i, a in area.most_common(60):
        print("%6.2f%% %s %s  slot1 %s / slot0 %s" % (100 * a / T, src[i], names[i], nm(side[i][0]), nm(side[i][1])))
    covered = 0.0
    nb = N // BLOCK
    for l in range(len(LAYERS)):
        lw = (layer_weight(act, wts, l) * valid).reshape(nb, BLOCK, nb, BLOCK).sum((1, 3))
        has = np.array([l in (s_[0] | s_[1]) for s_ in side])
        ok = has[np.maximum(pick, 0)]
        covered += lw[ok & ~fb].sum()
    print("splat weight shown by its own layer: %.2f%%" % (100 * covered / T))
    print("hu materials used:", len({names[i] for i in area if src[i] == "H"}))
