"""Offline A/B of terrain blending: S30 RVT weighting (reference) vs the terrain_s30 blend materials.

Reference (texelIDsAndWeights CS): per texel bilinear splat weights; layers processed from the top palette slot
down: w_i' = sat(((w_i - mid_i) + fade(w_i) * h_i) * ramp_i + w_i) * rem, rem -= w_i'; bottom slot = rem.
Ours: per 4x4 sample block one (A, B) pair, vertex alpha a = wA/(wA+wB) interpolated, blend = sat(a(1+k)+k(mask-1)),
mask = 0.5 + hA - hB. Colours = col_bake textures (mip 256 px per 256 u -> 1 px per unit).
"""
import kitpaths as KP
import json, os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import matmap, tiles, mask_bake
import texture2ddecoder as T
from PIL import Image

TERR = matmap.ROOT
SP = 91000.0 / 4095.0
ORIGIN = -45500.0
LAYERS = [l.replace("\\", "/") for l in json.load(open(TERR + "layers.json"))]
DESCS = json.load(open(TERR + "rvt_layer_descs.json"))
LCOL = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "layer_col.json")))
TEX = (KP.MAPS + r"/mp_rr_desertlands_mu5/extract/texture/")
BY = {L["name"]: L for L in tiles.layers().values()}
_hc, _cc = {}, {}


def height256(l):
    n = LAYERS[l]
    if n not in _hc:
        _hc[n] = mask_bake.height_levels(BY[n])[2]
    return _hc[n]


def col256(l):
    if l not in _cc:
        b = open(TEX + LCOL[str(l)] + ".dds", "rb").read()
        off = 148
        for d in (2048, 1024, 512):
            off += d * d // 2
        px = np.frombuffer(T.decode_bc1(b[off:off + 256 * 256 // 2], 256, 256), np.uint8).reshape(256, 256, 4)[..., [2, 1, 0]] / 255.0
        _cc[l] = np.where(px <= 0.04045, px / 12.92, ((px + 0.055) / 1.055) ** 2.4)
    return _cc[l]


def tex_at(img, wx, wy):
    """world xy -> texel (256 px per 256 u, uv = (x + 45500, -45500 - y) / 256)."""
    u = np.mod(wx + 45500.0, 256.0).astype(np.int32)
    v = np.mod(-45500.0 - wy, 256.0).astype(np.int32)
    return img[v, u]


def fade(w):
    return np.where(w < 0.25, 1 - np.clip(1 - 4 * w, 0, 1) ** 4, 1.0)


def render(act, wts, pick, names, side, y0, x0, ns, k_override=None, variant="ours"):
    """ns x ns samples starting at sample (y0, x0); 1 px per world unit."""
    n = int(ns * SP)
    py, px = np.mgrid[0:n, 0:n].astype(np.float32)
    sy, sx = y0 + py / SP, x0 + px / SP
    wy, wx = ORIGIN + sy * SP, ORIGIN + sx * SP
    iy, ix = np.floor(sy).astype(int), np.floor(sx).astype(int)
    fy, fx = sy - iy, sx - ix
    L = len(LAYERS)
    lw = np.zeros((L, ns + 2, ns + 2), np.float32)
    for l in range(L):
        lw[l] = matmap.layer_weight(act[y0:y0 + ns + 2, x0:x0 + ns + 2], wts[y0:y0 + ns + 2, x0:x0 + ns + 2], l)
    ly, lx = iy - y0, ix - x0

    def bil(f):
        return (f[ly, lx] * (1 - fx) * (1 - fy) + f[ly, lx + 1] * fx * (1 - fy) + f[ly + 1, lx] * (1 - fx) * fy + f[ly + 1, lx + 1] * fx * fy)
    out = np.zeros((n, n, 3), np.float32)
    if variant in ("ref", "refw"):
        pal = act[iy + np.rint(fy).astype(int), ix + np.rint(fx).astype(int)]  # nearest sample palette order
        rem = np.ones((n, n), np.float32)
        W = {}
        for slot in (2, 1, 0):
            l = pal[..., slot]
            for li in np.unique(l):
                if li == 255:
                    continue
                m = l == li
                w = bil(lw[li])[m]
                h = tex_at(height256(li), wx[m], wy[m])
                d = DESCS[li]
                wp = np.clip(((w - d["mid"]) + fade(w) * h) * d["ramp"] + w, 0, 1) * rem[m]
                rem[m] = np.clip(rem[m] - wp, 0, 1)
                W.setdefault(li, np.zeros((n, n), np.float32))[m] += wp
        tot = sum(W.values())
        if variant == "refw":
            return {li: w / np.maximum(tot, 1e-6) for li, w in W.items()}
        for li, w in W.items():
            out += (w / np.maximum(tot, 1e-6))[..., None] * tex_at(col256(li), wx, wy)
        return out
    bk = pick[(iy // matmap.BLOCK), (ix // matmap.BLOCK)]
    if variant == "texel":
        # raw pair weights at the vertices, S30 top-layer sharpening per texel (mask = top height, k = top ramp)
        Lr = np.zeros((len(LAYERS), ns + 2, ns + 2), np.float32)
        for l in range(len(LAYERS)):
            Lr[l] = matmap.layer_weight(act[y0:y0 + ns + 2, x0:x0 + ns + 2], RAW[y0:y0 + ns + 2, x0:x0 + ns + 2], l)
        for b in np.unique(bk):
            m = bk == b
            A, B = next(iter(side[b][0])), next(iter(side[b][1]))
            ca, cb = tex_at(col256(A), wx[m], wy[m]), tex_at(col256(B), wx[m], wy[m])
            if A == B:
                out[m] = ca
                continue
            top = TOP[(A, B)]
            d = DESCS[top]; r, mid = d["ramp"], d["mid"]
            # vertex alpha at the sample grid (what the game stores), then interpolated like the GPU does
            VA = matmap.s30_pair_alpha(Lr[A], Lr[B], A, B, top)
            if FORCED is not None:
                F = FORCED[y0:y0 + ns + 2, x0:x0 + ns + 2]
                VA = np.where(F == A, 1.0, np.where(F == B, 0.0, VA))
            a2 = (VA[ly, lx] * (1 - fx) * (1 - fy) + VA[ly, lx + 1] * fx * (1 - fy) + VA[ly + 1, lx] * (1 - fx) * fy + VA[ly + 1, lx + 1] * fx * fy)[m]
            mask = tex_at(height256(A), wx[m], wy[m]) if top == A else 1 - tex_at(height256(B), wx[m], wy[m])
            bl = np.clip(a2 * (1 + r) + r * (mask - 1), 0, 1)
            out[m] = cb + (ca - cb) * bl[:, None]
        return out
    if variant == "pairoracle":
        Wr = render(act, RAW, pick, names, side, y0, x0, ns, variant="refw")
        for b in np.unique(bk):
            m = bk == b
            A, B = next(iter(side[b][0])), next(iter(side[b][1]))
            wa, wb = Wr.get(A, np.zeros((n, n)))[m], Wr.get(B, np.zeros((n, n)))[m]
            s = np.maximum(wa + wb, 1e-6)
            out[m] = (wa / s)[:, None] * tex_at(col256(A), wx[m], wy[m]) + (wb / s)[:, None] * tex_at(col256(B), wx[m], wy[m])
        return out
    for b in np.unique(bk):
        m = bk == b
        A, B = next(iter(side[b][0])), next(iter(side[b][1]))
        wa, wb = bil(lw[A])[m], bil(lw[B])[m]
        a = np.where(wa + wb > 0, wa / np.maximum(wa + wb, 1e-9), 1.0)
        if A == B:
            bl = np.ones_like(a)
        else:
            k = 0.5 * (DESCS[A]["ramp"] + DESCS[B]["ramp"]) if k_override is None else k_override
            if os.environ.get("DEV") == "1":
                HM = json.load(open(TERR + "layer_heights.json"))
                mask = np.clip(0.5 + (tex_at(height256(A), wx[m], wy[m]) - HM[str(A)]) - (tex_at(height256(B), wx[m], wy[m]) - HM[str(B)]), 0, 1)
            else:
                mask = np.clip(0.5 + tex_at(height256(A), wx[m], wy[m]) - tex_at(height256(B), wx[m], wy[m]), 0, 1)
            bl = np.clip(a * (1 + k) + k * (mask - 1), 0, 1)
        ca, cb = tex_at(col256(A), wx[m], wy[m]), tex_at(col256(B), wx[m], wy[m])
        out[m] = cb + (ca - cb) * bl[:, None]
    return out


def srgb8(x):
    s = np.where(x <= 0.0031308, x * 12.92, 1.055 * np.clip(x, 0, 1) ** (1 / 2.4) - 0.055)
    return (np.clip(s, 0, 1) * 255).astype(np.uint8)


def main():
    if os.environ.get("CLOSED") is not None:
        matmap.CLOSED_BLOCKS = os.environ["CLOSED"] == "1"
    if os.environ.get("BLOCK"):
        matmap.BLOCK = int(os.environ["BLOCK"])
    act, wts = matmap.sample_layers()
    raw = wts
    global RAW
    RAW = raw
    if os.environ.get("STACK", "1") == "1":
        wts = matmap.s30_stack(act, raw)
    valid = ~np.isnan(np.load(TERR + "hf_grid.npy"))
    names, src, side, pick, fb, tot, _ = matmap.choose_s30(act, wts, valid, None)
    want = [n for n in sys.argv[1:]] or ["world/terrain_s30/mu5_l00_l18_bm"]
    for name in want:
        ns = 32
        if "," in name:
            y0, x0 = map(int, name.split(","))
        else:
            i = names.index(name)
            by, bx = np.argwhere(pick == i)[len(np.argwhere(pick == i)) // 2]
            y0, x0 = max(by * matmap.BLOCK - ns // 2, 0), max(bx * matmap.BLOCK - ns // 2, 0)
        ref = render(act, raw, pick, names, side, y0, x0, ns, variant="ref")
        global FORCED
        FORCED = None
        if os.environ.get("BORDER") == "1":
            import border
            FORCED = border.forced_layer(pick, side, matmap.N, matmap.BLOCK)
        if os.environ.get("TEXEL") == "1":
            global TOP
            TOP = {}
            for s_ in side:
                A, B = next(iter(s_[0])), next(iter(s_[1]))
                both = ((act == A).any(-1) & (act == B).any(-1))
                ia = np.argmax(act == A, -1); ib = np.argmax(act == B, -1)
                TOP[(A, B)] = A if (ia[both] > ib[both]).mean() >= 0.5 else B
            ours = render(act, wts, pick, names, side, y0, x0, ns, variant="texel")
        else:
            ours = render(act, wts, pick, names, side, y0, x0, ns, k_override=(float(os.environ["K"]) if os.environ.get("K") else None))
        po = render(act, wts, pick, names, side, y0, x0, ns, variant="pairoracle")
        err = np.abs(srgb8(ref).astype(int) - srgb8(ours).astype(int)).mean()
        print("   pair-oracle err %.2f" % np.abs(srgb8(ref).astype(int) - srgb8(po).astype(int)).mean())
        tag = name.split("/")[-1].replace(",", "_")
        Image.fromarray(np.concatenate([srgb8(ref), srgb8(ours)], 1)).save("ab_%s.png" % tag)
        print(tag, "sample", (y0, x0), "mean |ref-ours| (sRGB 0-255) %.2f" % err)


if __name__ == "__main__":
    main()
