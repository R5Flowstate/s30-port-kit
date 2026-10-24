"""Build the S30 RVT terrain material for the S21 client: layer texture arrays, layer params, the terrain PS
(rvt_shader.py output) + shaderset, and the single world material every generated terrain mesh uses.

Layer index = RVT layer slot (layers.json). Arrays are 28 slices, 2048 px, 12 mips, all permanent (Repak does
not stream texture arrays).
  albedo  BC1 sRGB  (col_bake.py: S30 LUT + macro + tint baked)
  normal  BC5       (llyr_extract.py)
  gloss   BC4, ao BC4 (llyr_extract.py)
  height  BC4       (llyr tile texture 1 red; drives the S30 height stacking)
  params  RGBA32F 32x2: row 0 (ramp, mid, 0, 0), row 1 (spec rgb linear, 0)
"""
import kitpaths as KP
import json, os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


import tiles
import llyr_extract as X
import pak_msw as P
from string_to_guid import string_to_guid as H

TERR = (KP.WORK + "/mu5_terrain/")
M = (KP.MAPS + "/mp_rr_desertlands_mu5/")
HERE = os.path.dirname(os.path.abspath(__file__))
STEM = "mu5"
MAT = "world/terrain_s30/%s_rvt" % STEM
TEMPLATE = "world/snow/desertlands_snow_04_wld_to_snow_03_wld_bm"
SRC_PS, SRC_SS = 0x08C913B24CC7DFD6, 0x5295C528B0A52A77
# Repak packs array textures whole (no streamed mips); the header must say so or the engine reads mips from the starpak
JSON_ARR = {"streamLayout": ["permanent"] * 12, "resourceFlags": "0x0", "usageFlags": "0x0"}


def g(kind):
    return H("texture/world/terrain_s30/%s_rvt_%s.rpak" % (STEM, kind))


def dds_levels(path):
    b = open(path, "rb").read()
    w = struct.unpack_from("<I", b, 16)[0]
    mips = struct.unpack_from("<I", b, 28)[0]
    fmt = struct.unpack_from("<I", b, 128)[0]
    bpb = 8 if fmt in (71, 72, 80) else 16
    out, off = [], 148
    for m in range(mips):
        d = max(w >> m, 1)
        n = max(d // 4, 1) ** 2 * bpb
        out.append(b[off:off + n]); off += n
    return fmt, w, out


def write_array(path, dxgi, w, slices):
    nm = len(slices[0])
    hdr = bytearray(148)
    hdr[0:4] = b"DDS "
    struct.pack_into("<7I", hdr, 4, 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000, w, w, len(slices[0][0]), 0, nm)
    struct.pack_into("<2I4s", hdr, 76, 32, 0x4, b"DX10")
    struct.pack_into("<I", hdr, 108, 0x1000 | 0x8 | 0x400000)
    struct.pack_into("<5I", hdr, 128, dxgi, 3, 0, len(slices), 0)
    with open(path + ".dds", "wb") as f:
        f.write(hdr)
        for s in slices:
            for lv in s:
                f.write(lv)


def height_levels_full(L):
    """mip 2048 .. 1 of the layer height as BC4 levels."""
    tiles._f.seek(L["star"] + X.LOW); low = tiles._f.read(X.LOW)
    raw = [tiles.tile(L, 0, k) for k in range(85)]
    out = []
    for first, side in X.LAYOUT:
        n = side * 256
        img = np.zeros((n, n), np.uint8)
        for i, t in enumerate(raw[first:first + side * side]):
            ty, tx = divmod(i, side)
            img[ty * 256:(ty + 1) * 256, tx * 256:(tx + 1) * 256] = X.bc7_px(t[65536:131072], 256)[..., 0]
        out.append(X.bc4_encode(img))
    off = 0
    for dim, size in zip(X.LOW_DIMS, X.LOW_SIZES):
        out.append(X.bc4_any(X.bc7_px(low[off:off + size], dim)[..., 0].copy()))
        off += size
    return out


def build_textures():
    layers = [l.replace("\\", "/") for l in json.load(open(TERR + "layers.json"))]
    descs = json.load(open(TERR + "rvt_layer_descs.json"))
    ltex = json.load(open(os.path.join(HERE, "layer_textures.json")))
    lcol = json.load(open(os.path.join(HERE, "layer_col.json")))
    by = {L["name"]: L for L in tiles.layers().values()}
    T = M + "extract/texture/"
    arrays = {}
    for kind, dxgi in (("albedo", 72), ("normal", 83), ("gloss", 80), ("ao", 80)):
        slices = []
        for i, n in enumerate(layers):
            src = lcol[str(i)] if kind == "albedo" else ltex[n][{"normal": "nml", "gloss": "gls", "ao": "ao"}[kind]]
            fmt, w, lv = dds_levels(T + src + ".dds")
            assert fmt == dxgi and w == 2048 and len(lv) == 12, (kind, n, fmt, w, len(lv))
            slices.append(lv)
        arrays[kind] = (dxgi, slices)
    hc = {}
    hs = []
    for n in layers:
        if n not in hc:
            hc[n] = height_levels_full(by[n])
        hs.append(hc[n])
    arrays["height"] = (80, hs)
    out = {}
    for kind, (dxgi, slices) in arrays.items():
        p = T + "0x%X" % g(kind)
        write_array(p, dxgi, 2048, slices)
        json.dump(JSON_ARR, open(p + ".json", "w"), indent="\t")
        out[kind] = "0x%X" % g(kind)
        print(kind, out[kind], len(slices), "slices")
    # params
    spec = {}
    import texture2ddecoder as TD
    for L in by.values():
        tiles._f.seek(L["star"] + 2 * X.LOW)
        px = np.frombuffer(TD.decode_bc7(tiles._f.read(16384), 128, 128), np.uint8).reshape(-1, 4)[:, [2, 1, 0]]
        spec[L["name"]] = (px.mean(0) / 255.0)
    prm = np.zeros((2, 32, 4), np.float32)
    for i, n in enumerate(layers):
        prm[0, i] = (descs[i]["ramp"], descs[i]["mid"], 0, 0)
        prm[1, i, :3] = spec[n]
    p = T + "0x%X" % g("params")
    hdr = bytearray(148)
    hdr[0:4] = b"DDS "
    struct.pack_into("<7I", hdr, 4, 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x8, 2, 32, 32 * 16, 0, 1)
    struct.pack_into("<2I4s", hdr, 76, 32, 0x4, b"DX10")
    struct.pack_into("<I", hdr, 108, 0x1000)
    struct.pack_into("<5I", hdr, 128, 2, 3, 0, 1, 0)
    open(p + ".dds", "wb").write(bytes(hdr) + prm.tobytes())
    json.dump({"streamLayout": ["permanent"], "resourceFlags": "0x0", "usageFlags": "0x0"}, open(p + ".json", "w"), indent="\t")
    out["params"] = "0x%X" % g("params")
    return out


def build_shaders():
    E = M + "extract/"
    d = open(E + "shader_s21/0x%X.msw" % SRC_PS, "rb").read()
    hi, nl, no = struct.unpack_from("<QII", d, 5)
    cnt = hi >> 56
    ents = []
    for k in range(cnt):
        sz, off, f0, f1 = struct.unpack_from("<IIQQ", d, 5 + 16 + k * 24)
        e = {"flags": (f0, f1), "buf": None, "ref": 0xFFFF}
        if sz != 0xFFFFFFFF:
            e["buf"] = d[5 + 16 + k * 24 + off:][:sz]
        ents.append(e)
    for k in (0, 1):
        ents[k]["buf"] = open(os.path.join(HERE, "rvt_ps", "rvt_%d.dxbc" % k), "rb").read()
    ps_guid = H("shader/terrain_s30/%s_rvt_ps.rpak" % STEM)
    sh = {"features": struct.pack("<Q", hi)[:7], "name": None, "entries": ents}
    P.write_shader_msw(sh, E + "shader_s21/0x%X.msw" % ps_guid)
    s = open(E + "shaderset_s21/0x%X.msw" % SRC_SS, "rb").read()
    psg, vsg, nps, nvs, nsm, frb, nres, _, _ = struct.unpack_from("<QQHHHBBII", s, 5)
    ss_guid = H("shaderset/terrain_s30/%s_rvt.rpak" % STEM)
    P.write_shaderset_msw({"pixelShaderGuid": ps_guid, "vertexShaderGuid": vsg, "numPixelShaderTextures": 8,
                           "numVertexShaderTextures": nvs, "numSamplers": nsm, "firstResourceBindPoint": frb,
                           "numResources": nres}, E + "shaderset_s21/0x%X.msw" % ss_guid)
    print("ps 0x%X shaderset 0x%X (vs 0x%X)" % (ps_guid, ss_guid, vsg))
    return ss_guid


def build_material(tex, ss_guid):
    A = M + "assets/material/"
    tw = json.load(open(A + TEMPLATE + "_wldc.json"))
    tc = json.load(open(A + TEMPLATE + "_colpass_wldc.json"))
    tub = open(A + TEMPLATE + "_wldc.uber", "rb").read()
    tcub = open(A + TEMPLATE + "_colpass_wldc.uber", "rb").read()
    order = ("albedo", "normal", "gloss", "ao", "height")
    texs = {str(i): tex[k] for i, k in enumerate(order)}
    texs.update({"5": "0xFA20A4BD02632A22", "6": "0xD87CF484DF1D0094", "7": tex["params"]})
    types = {"0": "_col", "1": "_nml", "2": "_gls", "3": "_ao", "4": "unavailable", "5": "unavailable", "6": "unavailable", "7": "unavailable"}
    cp_guid = H("material/" + MAT + "_colpass_wldc.rpak")
    mat_guid = H("material/" + MAT + "_wldc.rpak")
    os.makedirs(os.path.dirname(A + MAT), exist_ok=True)
    for js, ub, suffix, base in ((tc, tcub, "_colpass_wldc", MAT + "_colpass"), (tw, tub, "_wldc", MAT)):
        j = dict(js)
        j["name"] = base
        j["shaderSet"] = "0x%X" % ss_guid
        j["$textures"] = texs
        j["$textureTypes"] = types
        j["surfaceProp"] = "dirt"
        j["surfaceProp2"] = "snow"
        if suffix == "_wldc":
            j["$colpassMaterial"] = "0x%X" % cp_guid
        json.dump(j, open(A + base + suffix.replace("_colpass", "") + ".json" if False else A + MAT + suffix + ".json", "w"), indent="\t")
        open(A + MAT + suffix + ".uber", "wb").write(ub)
    extra_path = M + "inv/extra_matl.txt"
    extra = [l for l in open(extra_path, encoding="utf-8").read().splitlines()
             if l.strip() and " material/world/terrain_s30/" not in l]
    extra.append("%016X material/%s_wldc.rpak" % (mat_guid, MAT))
    extra.append("%016X material/%s_colpass_wldc.rpak" % (cp_guid, MAT))
    open(extra_path, "w", encoding="utf-8").write("\n".join(extra) + "\n")
    print("material", MAT, "0x%X" % mat_guid)


def main():
    tex = build_textures()
    ss = build_shaders()
    build_material(tex, ss)


if __name__ == "__main__":
    main()
