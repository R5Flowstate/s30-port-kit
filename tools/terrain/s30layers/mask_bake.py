"""Per layer-pair height blend masks for the terrain_s30 materials (BC4, 1024, 11 mips).

S30 RVT weights the top layer by w' = sat(w*(1+r) + r*(fade(w)*h - mid)) (texelIDsAndWeights CS). The blend shader
computes blend = sat(alpha*(1+k) + k*(mask-1)); with mask = h(top) (top = slot 1 layer) or 1 - h(top) (top = slot 0),
k = ramp(top) and the vertex alpha from matmap.s30_pair_alpha it is the same per-texel formula.
Height = llyr tile texture 1 red channel. Both layers share the same uv, so the mask is texel aligned with them.
"""
import kitpaths as KP
import json, os, re, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tiles
import llyr_extract as X
from string_to_guid import string_to_guid as H

TERR = (KP.WORK + "/mu5_terrain/")
MAP_STEM = "mu5"
JSON_TMPL = {"streamLayout": ["permanent"] * 8 + ["mandatory"] * 3,
             "resourceFlags": "0x0", "usageFlags": "0x0"}


def height_levels(L):
    """mip 1024 .. 1 of the layer height (float 0..1)."""
    tiles._f.seek(L["star"] + X.LOW); low = tiles._f.read(X.LOW)
    raw = [tiles.tile(L, 0, k) for k in range(64, 85)]
    out = []
    for first, side in X.LAYOUT[1:]:
        n = side * 256
        img = np.zeros((n, n), np.float32)
        for i, t in enumerate(raw[first - 64:first - 64 + side * side]):
            ty, tx = divmod(i, side)
            img[ty * 256:(ty + 1) * 256, tx * 256:(tx + 1) * 256] = X.bc7_px(t[65536:131072], 256)[..., 0] / 255.0
        out.append(img)
    off = 0
    for dim, size in zip(X.LOW_DIMS, X.LOW_SIZES):
        out.append(X.bc7_px(low[off:off + size], dim)[..., 0].astype(np.float32) / 255.0)
        off += size
    return out


def mask_guid(a, b):
    return H("texture/world/terrain_s30/%s_l%02d_l%02d_mask.rpak" % (MAP_STEM, a, b))


def main():
    layers = [l.replace("\\", "/") for l in json.load(open(TERR + "layers.json"))]
    tm = json.load(open(TERR + "terrain_materials.json"))
    tops = dict(zip(tm["materials"], tm["top"]))
    names = [m for m in tm["materials"] if m.startswith("world/terrain_s30/")]
    by_name = {L["name"]: L for L in tiles.layers().values()}
    hcache = {}

    def heights(i):
        n = layers[i]
        if n not in hcache:
            hcache[n] = height_levels(by_name[n])
        return hcache[n]

    out = {}
    for name in names:
        a, b = map(int, re.search(r"_l(\d+)_l(\d+)_bm$", name).groups())
        if a == b:
            continue
        ha, hb = heights(a), heights(b)
        top = tops[name]
        src = ha if top == a else [1 - y for y in hb]
        levels = [X.bc4_any(np.rint(np.clip(x, 0, 1) * 255).astype(np.uint8)) for x in src]
        g = mask_guid(a, b)
        p = os.path.join(X.OUT, "0x%X" % g)
        X.dds(p + ".dds", 80, 1024, levels)
        json.dump(JSON_TMPL, open(p + ".json", "w"), indent="\t")
        out["%d_%d" % (a, b)] = "0x%X" % g
        print(name, "0x%X" % g, "mean h %.3f %.3f" % (ha[0].mean(), hb[0].mean()), flush=True)
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pair_mask.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
