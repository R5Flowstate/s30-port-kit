"""S30 terrain layers (llyr) -> 2048 S21 textures per layer, full mip chains.

A llyr carries its own textures in the starpak: six 128 px BC7 low mips (8 levels, 0x5570 B
each) followed by two Oodle tile streams. Stream 0 tile = three 256x256 BC7 textures:
  t0 rgb albedo (linear), a normal X | t1 b gloss, a normal Y | t2 rgb spec (linear), a ao^2.2
85 tiles = mip0 8x8 + mip1 4x4 + mip2 2x2 + mip3 1, row-major. Mip0 = 2048 px, 8 texels/unit.
Out: col BC7 UNORM (raw blocks), nml BC5, gls BC4, ao BC4 (gamma like the S30 material maps).
"""
import kitpaths as KP
import json, os, struct, sys
import numpy as np
import texture2ddecoder as T


import tiles
from string_to_guid import string_to_guid as H

OUT = (KP.MAPS + r"/mp_rr_desertlands_mu5/extract/texture")
LOW = 0x5570
LOW_DIMS = [128, 64, 32, 16, 8, 4, 2, 1]
LOW_SIZES = [16384, 4096, 1024, 256, 64, 16, 16, 16]
LAYOUT = [(0, 8), (64, 4), (80, 2), (84, 1)]  # first tile, tiles per side; tile = 256 px
JSON_TMPL = {"streamLayout": ["permanent"] * 8 + ["mandatory"] * 4,
             "resourceFlags": "0x0", "usageFlags": "0x0"}


def bc7_px(raw, w):
    d = max(w, 4)
    px = np.frombuffer(T.decode_bc7(raw, d, d), np.uint8).reshape(d, d, 4)[:w, :w]
    return px[..., [2, 1, 0, 3]]  # RGBA


def bc4_encode(v):
    """v: (H, W) uint8 -> BC4 bytes (H, W >= 4, multiples of 4)."""
    h, w = v.shape
    b = v.reshape(h // 4, 4, w // 4, 4).transpose(0, 2, 1, 3).reshape(-1, 16).astype(np.int32)
    e0 = b.max(1); e1 = b.min(1)
    span = np.maximum(e0 - e1, 1)
    p = np.clip(np.rint((e0[:, None] - b) * 7.0 / span[:, None]), 0, 7).astype(np.uint64)
    idx = np.where(p == 0, 0, np.where(p == 7, 1, p + 1)).astype(np.uint64)
    idx[e0 == e1] = 0
    bits = np.zeros(len(b), np.uint64)
    for k in range(16):
        bits |= idx[:, k] << np.uint64(3 * k)
    out = np.zeros((len(b), 8), np.uint8)
    out[:, 0] = e0; out[:, 1] = e1
    for k in range(6):
        out[:, 2 + k] = ((bits >> np.uint64(8 * k)) & np.uint64(255)).astype(np.uint8)
    return out.tobytes()


def bc4_any(v):
    n = v.shape[0]
    if n >= 4:
        return bc4_encode(v)
    return bc4_encode(np.pad(v, ((0, 4 - n), (0, 4 - n)), mode="edge"))


def bc5_encode(x, y):
    a = np.frombuffer(bc4_any(x), np.uint8).reshape(-1, 8)
    b = np.frombuffer(bc4_any(y), np.uint8).reshape(-1, 8)
    return np.concatenate([a, b], 1).tobytes()


def blocks_reassemble(tile_raws, per_side):
    """BC7 tiles (64x64 blocks, 16 B each) row-major -> one block grid."""
    g = np.zeros((per_side * 64, per_side * 64, 16), np.uint8)
    for i, r in enumerate(tile_raws):
        ty, tx = divmod(i, per_side)
        g[ty * 64:(ty + 1) * 64, tx * 64:(tx + 1) * 64] = np.frombuffer(r, np.uint8).reshape(64, 64, 16)
    return g.tobytes()


def dds(path, dxgi, w, levels):
    hdr = bytearray(148)
    hdr[0:4] = b"DDS "
    struct.pack_into("<7I", hdr, 4, 124, 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000, w, w, len(levels[0]), 0, len(levels))
    struct.pack_into("<2I4s", hdr, 76, 32, 0x4, b"DX10")
    struct.pack_into("<I", hdr, 108, 0x1000 | 0x8 | 0x400000)
    struct.pack_into("<5I", hdr, 128, dxgi, 3, 0, 1, 0)
    with open(path, "wb") as f:
        f.write(hdr)
        for lv in levels:
            f.write(lv)


def layer(L):
    base = L["star"]
    tiles._f.seek(base); low = tiles._f.read(6 * LOW)
    raw = [tiles.tile(L, 0, k) for k in range(85)]
    if any(r is None for r in raw):
        raise RuntimeError("tile decompress failed for " + L["name"])
    col, nml, gls, ao = [], [], [], []
    for first, side in LAYOUT:
        ts = raw[first:first + side * side]
        col.append(blocks_reassemble([t[0:65536] for t in ts], side))
        n = side * 256
        img = [np.zeros((n, n, 4), np.uint8) for _ in range(3)]
        for i, t in enumerate(ts):
            ty, tx = divmod(i, side)
            for c in range(3):
                img[c][ty * 256:(ty + 1) * 256, tx * 256:(tx + 1) * 256] = bc7_px(t[c * 65536:(c + 1) * 65536], 256)
        nml.append(bc5_encode(img[0][..., 3], img[1][..., 3]))
        gls.append(bc4_encode(img[1][..., 2]))
        ao.append(bc4_encode(ao_gamma(img[2][..., 3])))
    off = 0
    for lv, (dim, size) in enumerate(zip(LOW_DIMS, LOW_SIZES)):
        c0 = low[0 * LOW + off:0 * LOW + off + size]
        c1 = low[1 * LOW + off:1 * LOW + off + size]
        c2 = low[2 * LOW + off:2 * LOW + off + size]
        off += size
        p0, p1, p2 = bc7_px(c0, dim), bc7_px(c1, dim), bc7_px(c2, dim)
        col.append(c0)
        nml.append(bc5_encode(p0[..., 3], p1[..., 3]))
        gls.append(bc4_any(p1[..., 2]))
        ao.append(bc4_any(ao_gamma(p2[..., 3])))
    return col, nml, gls, ao


def ao_gamma(a):
    return np.rint(255.0 * (a.astype(np.float32) / 255.0) ** (1 / 2.2)).astype(np.uint8)


def tex_guid(layer_path, kind):
    return H("texture/world/terrain_s30/%s_%s.rpak" % (layer_path.split("/")[-1], kind))


def main():
    out = {}
    for L in sorted(tiles.layers().values(), key=lambda x: x["name"]):
        name = L["name"]
        maps = layer(L)
        rec = {}
        for kind, dxgi, levels in zip(("col", "nml", "gls", "ao"), (98, 83, 80, 80), maps):
            assert len(levels) == 12, (name, kind, len(levels))
            g = tex_guid(name, kind)
            p = os.path.join(OUT, "0x%X" % g)
            dds(p + ".dds", dxgi, 2048, levels)
            json.dump(JSON_TMPL, open(p + ".json", "w"), indent="\t")
            rec[kind] = "0x%X" % g
        out[name] = rec
        print(name, rec, flush=True)
    json.dump(out, open((KP.WORK + r"/mu5_s30blend/layer_textures.json"), "w"), indent=1)


if __name__ == "__main__":
    main()
