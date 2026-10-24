"""World blend materials for the generated terrain, built only from S30 parts.

Every material gen_terrain2 picked under world/terrain_s30/ (name ..._lAA_lBB_bm, A <= B) becomes a clone
of an S30 two-layer world blend material (converted S30 shader) whose slots carry the S30 llyr textures
written by llyr_extract.py: slot 1 side (albedo2/normal2/gloss2/ao2, uv1, L1 constants) = layer A,
slot 0 side (albedo/normal/gloss/ao, uv2, L0 constants) = layer B. Vertex alpha 255 shows layer A.
Detail (overlaid on slot 0) is a flat 128 neutral texture. Blend = sat(a*(1+k) + k*(mask-1)) with the per-pair
height mask from mask_bake.py and k = mean RVT ramp of the two layers (S30 height-weighted edges).

Tiling: the S30 runtime virtual texture samples a 2048 px layer at the virtual texture's density
(rvt worldSize 91000 / 728000 texels = 8 texels per unit) -> one repeat per 256 units, anchored at the
rvt worldPosition. Colour maps are per layer index from col_bake.py (stochastic LUT, macro, tint
baked in); spec comes from the layer's spec map.
"""
import kitpaths as KP
import json, os, re, struct, sys
import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from string_to_guid import string_to_guid as H

TERR = (KP.WORK + "/mu5_terrain/")
M = (KP.MAPS + "/mp_rr_desertlands_mu5/")
LAYER_TEX = (KP.WORK + "/mu5_s30blend/layer_textures.json")
TEMPLATE = "world/snow/desertlands_snow_04_wld_to_snow_03_wld_bm"
PREFIX = "world/terrain_s30/"
SCALE = 1.0 / 256.0
ORIGIN = 45500.0
SLOT0 = (0, 2, 4, 6)   # albedo, normal, gloss, ao  (layer B, uv2, L0)
SLOT1 = (1, 3, 5, 7)   # albedo2, normal2, gloss2, ao2 (layer A, uv1, L1)
KINDS = ("col", "nml", "gls", "ao")
OFF_UV1, OFF_UV2 = 0, 24
OFF_RAMP = 140
SLOT_DETAIL = 8   # overlaid on slot 0 albedo; 128 = exact no-op
SLOT_MASK = 10    # layer blend mask = 0.5 + hA - hB (mask_bake.py)
OFF_L0_TINT, OFF_L1_TINT = 356, 404          # c_zUpBlendingVertexAlphaAndL0_albedoTint.yzw, c_L0_perfGlossAndL1_albedoTint.yzw
OFF_L0_SPEC, OFF_L1_SPEC = 388, 432          # c_L0_perfSpecColorXYZ, c_L1_perfSpecColorAndSplineMinPixelPercent.xyz
SURF = (("ice", "ice"), ("snow", "snow"), ("moss", "grass"), ("flowers", "grass"), ("asphalt", "asphalt"),
        ("rubble", "gravel"), ("sulfur_water", "water"), ("dry_mud", "mud"), ("volcanic", "lava_rock"),
        ("sulfur", "rock"), ("stone", "rock"), ("rock", "rock"), ("dirt", "dirt"), ("soil", "dirt"))


def surface(layer):
    for k, v in SURF:
        if k in layer:
            return v
    return "dirt"


def uv_block(f, off):
    struct.pack_into("<6f", f, off, SCALE, 0.0, 0.0, SCALE, ORIGIN, -ORIGIN)


def spec_means(layers):
    import tiles, texture2ddecoder as T
    out = {}
    for L in tiles.layers().values():
        tiles._f.seek(L["star"] + 2 * 0x5570)
        raw = tiles._f.read(16384)
        px = np.frombuffer(T.decode_bc7(raw, 128, 128), np.uint8).reshape(-1, 4)[:, [2, 1, 0]]
        out[L["name"]] = (px.mean(0) / 255.0).tolist()
    return out


def main():
    tm = json.load(open(TERR + "terrain_materials.json"))
    tops = dict(zip(tm["materials"], tm["top"]))
    names = [m for m in tm["materials"] if m.startswith(PREFIX)]
    layers = json.load(open(TERR + "layers.json"))
    descs = json.load(open(TERR + "rvt_layer_descs.json"))
    ltex = json.load(open(LAYER_TEX))
    lcol = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "layer_col.json")))
    pmask = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "pair_mask.json")))
    spec = spec_means(layers)
    tw = json.load(open(M + "assets/material/" + TEMPLATE + "_wldc.json"))
    tc = json.load(open(M + "assets/material/" + TEMPLATE + "_colpass_wldc.json"))
    tub = open(M + "assets/material/" + TEMPLATE + "_wldc.uber", "rb").read()
    tcub = open(M + "assets/material/" + TEMPLATE + "_colpass_wldc.uber", "rb").read()
    extra_path = M + "inv/extra_matl.txt"
    extra = [l for l in open(extra_path, encoding="utf-8").read().splitlines()
             if l.strip() and " material/" + PREFIX not in l]
    out_dir = M + "assets/material/" + PREFIX
    os.makedirs(out_dir, exist_ok=True)
    for f in os.listdir(out_dir):
        os.remove(os.path.join(out_dir, f))
    rec = []
    for name in names:
        a, b = map(int, re.search(r"_l(\d+)_l(\d+)_bm$", name).groups())
        la, lb = layers[a].replace("\\", "/"), layers[b].replace("\\", "/")
        tex = dict(tw["$textures"])
        for s, k in zip(SLOT0, KINDS):
            tex[str(s)] = lcol[str(b)] if k == "col" else ltex[lb][k]
        for s, k in zip(SLOT1, KINDS):
            tex[str(s)] = lcol[str(a)] if k == "col" else ltex[la][k]
        tex[str(SLOT_DETAIL)] = "0x%X" % H("texture/world/terrain_s30/neutral_detail.rpak")
        if a != b:
            tex[str(SLOT_MASK)] = pmask["%d_%d" % (a, b)]
        ramp = 0.0 if a == b else float(descs[tops[name]]["ramp"])  # k = RVT ramp of the top layer (s30_pair_alpha)
        cp_guid = H("material/" + name + "_colpass_wldc.rpak")
        mat_guid = H("material/" + name + "_wldc.rpak")
        for js, ub, suffix, base in ((tc, tcub, "_colpass_wldc", name + "_colpass"), (tw, tub, "_wldc", name)):
            j = dict(js)
            j["name"] = base
            j["$textures"] = tex
            j["surfaceProp"] = surface(lb)
            j["surfaceProp2"] = surface(la)
            if suffix == "_wldc":
                j["$colpassMaterial"] = "0x%X" % cp_guid
            f = bytearray(ub)
            uv_block(f, OFF_UV1)
            uv_block(f, OFF_UV2)
            struct.pack_into("<f", f, OFF_RAMP, ramp)
            struct.pack_into("<3f", f, OFF_L0_TINT, 1.0, 1.0, 1.0)  # tint is baked into the colour map
            struct.pack_into("<3f", f, OFF_L1_TINT, 1.0, 1.0, 1.0)
            struct.pack_into("<3f", f, OFF_L0_SPEC, *spec[lb])
            struct.pack_into("<3f", f, OFF_L1_SPEC, *spec[la])
            p = M + "assets/material/" + name + suffix
            json.dump(j, open(p + ".json", "w"), indent="\t")
            open(p + ".uber", "wb").write(bytes(f))
        extra.append("%016X material/%s_wldc.rpak" % (mat_guid, name))
        extra.append("%016X material/%s_colpass_wldc.rpak" % (cp_guid, name))
        rec.append({"material": name, "slot1": la, "slot0": lb, "guid": "0x%X" % mat_guid})
    open(extra_path, "w", encoding="utf-8").write("\n".join(extra) + "\n")
    json.dump(rec, open(TERR + "s30_materials.json", "w"), indent=1)
    print("materials", len(rec), "extra_matl lines", len(extra))


if __name__ == "__main__":
    main()
