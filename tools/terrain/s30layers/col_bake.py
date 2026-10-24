"""Per RVT layer albedo, baked the way the S30 RVT composite shades it, as BC1 sRGB 2048 (12 mips).

Composite (startup_runtime_virtual_texture composite CS), per layer desc d (64 B, rvt_layer_descs.json):
  s = albedo sample (linear)                    ; d.flags & 1 (stochastic): s_c = LUT[byte(s_c)].c
  m = d.macro (+8)                              ; d.flags & 2: overlay(m, sqrt(s))^2, else 2*m*s
  out = that * d.tint (+0)
LUT = llyr +0x10, 256 dwords, R low byte / G >>8 / B >>16 (byte / 255).
Layers 5/6 share one llyr with different descs, so colour textures are per layer index.
"""
import kitpaths as KP
import json, os, struct, sys
import numpy as np
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import tiles
import llyr_extract as X
from string_to_guid import string_to_guid as H

TERR = (KP.WORK + "/mu5_terrain/")
MAP_STEM = "mu5"


def albedo_levels(L):
    tiles._f.seek(L["star"]); low = tiles._f.read(X.LOW)
    raw = [tiles.tile(L, 0, k) for k in range(85)]
    out = []
    for first, side in X.LAYOUT:
        n = side * 256
        img = np.zeros((n, n, 3), np.uint8)
        for i, t in enumerate(raw[first:first + side * side]):
            ty, tx = divmod(i, side)
            img[ty * 256:(ty + 1) * 256, tx * 256:(tx + 1) * 256] = X.bc7_px(t[0:65536], 256)[..., :3]
        out.append(img)
    off = 0
    for dim, size in zip(X.LOW_DIMS, X.LOW_SIZES):
        out.append(X.bc7_px(low[off:off + size], dim)[..., :3])
        off += size
    return out


def lut_of(L):
    p = tiles.ptr(L["h"], 0x10)
    if p is None:
        return None
    w = np.array(struct.unpack_from("<256I", tiles.d, p), np.uint32)
    return np.stack([w & 255, (w >> 8) & 255, (w >> 16) & 255], -1).astype(np.float32) / 255.0


def bake(img, desc, lut):
    s = img.astype(np.float32) / 255.0
    if desc["flags"] & 1:
        if lut is None:
            raise RuntimeError("stochastic layer without LUT: " + desc["layer"])
        idx = np.clip(s * 255.0, 0, 255).astype(np.int32)
        s = np.stack([lut[idx[..., c], c] for c in range(3)], -1)
    m = np.array(desc["approx"], np.float32)
    if desc["flags"] & 2:
        q = np.sqrt(s)
        o = q * 2 * m if (m < 0.5).any() else 1 - (1 - m) * 2 * (1 - q)
        s = o * o
    else:
        s = 2 * m * s
    return np.clip(s * np.array(desc["tint"], np.float32), 0, 1)


def to_srgb(lin):
    return np.where(lin <= 0.0031308, lin * 12.92, 1.055 * np.power(lin, 1 / 2.4) - 0.055)


def bc1_encode(rgb):
    """rgb: (H, W, 3) float sRGB 0..1, H, W multiples of 4 -> BC1 bytes (4-colour mode)."""
    h, w, _ = rgb.shape
    b = rgb.reshape(h // 4, 4, w // 4, 4, 3).transpose(0, 2, 1, 3, 4).reshape(-1, 16, 3) * 255.0
    mean = b.mean(1, keepdims=True)
    d = b - mean
    cov = np.einsum("nki,nkj->nij", d, d)
    v = np.ones((len(b), 3), np.float32)
    for _ in range(6):
        v = np.einsum("nij,nj->ni", cov, v)
        v /= np.maximum(np.linalg.norm(v, axis=1, keepdims=True), 1e-6)
    t = np.einsum("nki,ni->nk", d, v)
    lo = mean[:, 0] + v * t.min(1, keepdims=True)
    hi = mean[:, 0] + v * t.max(1, keepdims=True)

    def q565(c):
        c = np.clip(c, 0, 255)
        r = np.rint(c[:, 0] * 31 / 255).astype(np.uint32)
        g = np.rint(c[:, 1] * 63 / 255).astype(np.uint32)
        bb = np.rint(c[:, 2] * 31 / 255).astype(np.uint32)
        return (r << 11) | (g << 5) | bb

    def unq(p):
        return np.stack([((p >> 11) & 31) * 255 / 31, ((p >> 5) & 63) * 255 / 63, (p & 31) * 255 / 31], -1).astype(np.float32)

    c0, c1 = q565(hi), q565(lo)
    swap = c0 < c1
    c0, c1 = np.where(swap, c1, c0), np.where(swap, c0, c1)
    eq = c0 == c1
    c0 = np.where(eq & (c0 < 0xFFFF), c0 + 1, c0)
    c1 = np.where(eq & (c0 == 0xFFFF) & (c1 > 0), c1 - 1, c1)
    p0, p1 = unq(c0), unq(c1)
    pal = np.stack([p0, p1, (2 * p0 + p1) / 3, (p0 + 2 * p1) / 3], 1)
    dist = ((b[:, :, None, :] - pal[:, None, :, :]) ** 2).sum(-1)
    idx = dist.argmin(-1).astype(np.uint32)
    bits = np.zeros(len(b), np.uint32)
    for k in range(16):
        bits |= idx[:, k] << np.uint32(2 * k)
    out = np.zeros((len(b), 8), np.uint8)
    out[:, 0] = c0 & 255; out[:, 1] = c0 >> 8; out[:, 2] = c1 & 255; out[:, 3] = c1 >> 8
    for k in range(4):
        out[:, 4 + k] = (bits >> np.uint32(8 * k)) & 255
    return out.tobytes()


def bc1_any(rgb):
    n = rgb.shape[0]
    if n < 4:
        rgb = np.pad(rgb, ((0, 4 - n), (0, 4 - n), (0, 0)), mode="edge")
    return bc1_encode(rgb)


def col_guid(i):
    return H("texture/world/terrain_s30/%s_l%02d_col.rpak" % (MAP_STEM, i))


def main():
    layers = [l.replace("\\", "/") for l in json.load(open(TERR + "layers.json"))]
    descs = json.load(open(TERR + "rvt_layer_descs.json"))
    by_name = {L["name"]: L for L in tiles.layers().values()}
    cache = {}
    out = {}
    for i, (lname, desc) in enumerate(zip(layers, descs)):
        L = by_name[lname]
        if lname not in cache:
            cache = {lname: (albedo_levels(L), lut_of(L))}
        levels, lut = cache[lname]
        enc = [bc1_any(to_srgb(bake(lv, desc, lut))) for lv in levels]
        g = col_guid(i)
        p = os.path.join(X.OUT, "0x%X" % g)
        X.dds(p + ".dds", 72, 2048, enc)
        json.dump(X.JSON_TMPL, open(p + ".json", "w"), indent="\t")
        mean = bake(levels[4], desc, lut).reshape(-1, 3).mean(0)
        out[str(i)] = "0x%X" % g
        print(i, lname.split("/")[-1], "flags", desc["flags"], "macro", desc["approx"], "baked mean lin", mean.round(3), flush=True)
    json.dump(out, open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "layer_col.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
