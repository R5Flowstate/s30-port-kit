"""S30 terrain inputs from the map pak, found structurally (no per-map offsets).

rmap (MapAsset v7) +0x60 -> RuntimeVirtualTextureDesc: +0x00 splatmapWeights txtr guid (4096^2 BC5), +0x08 splatmapIDs
txtr guid (1024^2 R32G32B32A32_UINT), +0x80 u8 layer count, +0x10 -> layer descs (64 B each), +0xC8 -> layer llyr guids.
llyr +0x08 -> name = the S21 world material path of the layer.

    py terrain_inputs.py <map .dec.rpak> <map extract/texture dir> <out dir>
writes layers.json, rvt_layer_descs.json, splat_w.npy (uint8 [N,N,2] = BC5 R,G) and prints the splat ID texture guid."""
import kitpaths as KP
import json, os, struct, subprocess, sys, tempfile
import numpy as np

import pak_msw as P

TEXCONV = (KP.TEXCONV_DIR + r"\texconv.exe")

def main(pak, texdir, out):
    d, pages, ao0, ac, _ = P.load(pak)
    R = lambda pg, off: P._res(pages, pg, off)
    ptr = lambda h: R(*struct.unpack_from("<ii", d, h))
    assets = {}
    for i in range(ac):
        ao = ao0 + i * 80
        assets[P.u64(d, ao)] = (bytes(struct.pack("<I", P.u32(d, ao + 0x4C))), R(P.i32(d, ao + 0x10), P.i32(d, ao + 0x14)))
    rmap = [h for (t, h) in assets.values() if t == b"rmap"]
    if len(rmap) != 1:
        sys.exit("expected one rmap asset, found %d" % len(rmap))
    rvt = ptr(rmap[0] + 0x60)
    w_guid, id_guid = struct.unpack_from("<QQ", d, rvt)
    n = d[rvt + 0x80]
    descs = ptr(rvt + 0x10)
    lg = ptr(rvt + 0xC8)
    layers = []
    for i in range(n):
        g = struct.unpack_from("<Q", d, lg + 8 * i)[0]
        t, h = assets[g]
        if t != b"llyr":
            sys.exit("layer %d guid %#x is %r, not llyr" % (i, g, t))
        nh = ptr(h + 8)
        layers.append(bytes(d[nh:nh + 260]).split(b"\0")[0].decode().replace("\\", "/"))
    out_desc = []
    for i in range(n):
        t = struct.unpack_from("<3fIIff I3fI", d, descs + 64 * i)
        out_desc.append(dict(layer=layers[i], tint=t[0:3], decal=t[3], flags=t[4], ramp=t[5], mid=t[6], stoch=t[7], approx=t[8:11]))
    os.makedirs(out, exist_ok=True)
    json.dump(layers, open(os.path.join(out, "layers.json"), "w"))
    json.dump(out_desc, open(os.path.join(out, "rvt_layer_descs.json"), "w"), indent=1)
    src = os.path.join(texdir, "0x%X.dds" % w_guid)
    with tempfile.TemporaryDirectory(dir=out) as tmp:
        subprocess.run([TEXCONV, "-nologo", "-y", "-m", "1", "-f", "R8G8_UNORM", "-o", tmp, src], check=True, stdout=subprocess.DEVNULL)
        b = open(os.path.join(tmp, os.path.basename(src)), "rb").read()
    h, w = struct.unpack_from("<II", b, 12)
    off = 148 if b[84:88] == b"DX10" else 128
    np.save(os.path.join(out, "splat_w.npy"), np.frombuffer(b, np.uint8, h * w * 2, off).reshape(h, w, 2))
    print("layers", n, "weights 0x%X (%dx%d)" % (w_guid, w, h), "ids 0x%X" % id_guid)

if __name__ == "__main__":
    main(*sys.argv[1:4])
