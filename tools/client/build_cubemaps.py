"""S30 `cube` v2 assets -> the S21 cubemap pair.

S30 ships one `cube` asset per BSP cubemap sample (`cubemap/maps/<map>/cubemap_<i>.rpak`):
header +0x10 u16 w/h, +0x14 sample index, +0x18 compressed size, +0x1C size, +0x20 compression
(3 = Oodle), +0x24 ambient; payload in the starpak = BC6H_UF16, 9 mips, mip-major, 6 faces per mip.
S21 wants one BC6H cube array `texture/maps/<map>/cubemaps_hdr.rpak` (6 x samples slices) and the
ambient buffer `texture_extension/maps/<map>/cubemaps_hdr/cubemap_ambients.rpak` (txtx, count ==
samples). Sample count comes from client BSP lump 0x2A; asset i must exist for every sample.
The ambient buffer holds the RECIPROCAL the world shaders scale reflections by:
1 / max(0.001, |probe light vector| * lum(sky color) + lum(probe SH DC)), from the light probe
nearest the cubemap origin -- what S30 rebuilds every frame from the same probe. The cube asset's
own +0x24 value is the cubemap's mean luminance and is not that term.

    py build_cubemaps.py   -> extract/texture/<guid>.dds, extract/txtx/<guid>, inv/cubemaps.json
"""
import kitpaths as KP
import ctypes
import json
import os
import struct
import sys


import pak_msw as P  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
MAP = "mp_rr_desertlands_mu5"
SALHAM = (KP.MAPS + r"\mp_rr_salham")

from string_to_guid import string_to_guid  # noqa: E402

S30_PAK = os.path.join(ROOT, "inv", MAP + ".rpak.dec.rpak")
S30_DIR = KP.S30_GAME
LUMP_2A = os.path.join(ROOT, "bsp", "client_v51", MAP + ".bsp.002a.bsp_lump")
LUMP = os.path.join(ROOT, "bsp", "client_v51", MAP + ".bsp.%04x.bsp_lump")
ENV_ENT = os.path.join(ROOT, "bsp", "client_v51", MAP + "_env.ent")
OODLE = (KP.REPAK + r"\bin\Release\oo2core_8_win64.dll")
DXGI_BC6H_UF16 = 95
MIPS = 9
FACES = 6
TXTX_MAGIC = 0x54585854
# retail S21 cube array tail (District / desertlands_hu): 9 permanent mips, type 0x12
CUBE_TAIL = "000000000900000000120000000000000000000000000000000000000000000000000000"


def mip_bytes(w, m):
    s = max(1, w >> m)
    return max(1, (s + 3) // 4) ** 2 * 16


def oodle():
    oo = ctypes.WinDLL(OODLE)
    f = oo.OodleLZ_Decompress
    f.restype = ctypes.c_int64
    f.argtypes = [ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_int64, ctypes.c_int, ctypes.c_int,
                  ctypes.c_int, ctypes.c_void_p, ctypes.c_int64, ctypes.c_void_p, ctypes.c_void_p,
                  ctypes.c_void_p, ctypes.c_int64, ctypes.c_int]
    return f


def dds_header(w, mips, slices):
    h = struct.pack("<I", 0x20534444)
    flags = 0x1 | 0x2 | 0x4 | 0x1000 | 0x20000 | 0x80000
    pitch = mip_bytes(w, 0)
    h += struct.pack("<7I", 124, flags, w, w, pitch, 0, mips) + b"\0" * 44
    h += struct.pack("<2I4s5I", 32, 0x4, b"DX10", 0, 0, 0, 0, 0)
    h += struct.pack("<5I", 0x1000 | 0x400000 | 0x8, 0, 0, 0, 0)
    h += struct.pack("<5I", DXGI_BC6H_UF16, 3, 0, slices, 0)
    return h


def sky_luminance():
    """lum of the light_environment `_ambient` colour (sRGB 0-255 + brightness, 255 = 1.0)."""
    import re
    t = open(ENV_ENT, "rb").read().decode("latin1")
    m = re.search(r'\{[^{}]*"classname" "light_environment"[^{}]*\}', t)
    r, g, b, k = (float(x) for x in re.search(r'"_ambient" "([^"]+)"', m.group(0)).group(1).split())
    lin = [(c / 255.0) ** 2.2 for c in (r, g, b)]
    return (0.2126 * lin[0] + 0.7152 * lin[1] + 0.0722 * lin[2]) * k / 255.0


def ambient_reciprocals(origins):
    """Per cubemap origin: the reflection normalization from the nearest light probe (0x68 refs -> 0x65 probes)."""
    import numpy as np
    refs = np.fromfile(LUMP % 0x68, np.dtype([("p", "<f4", 3), ("i", "<u4"), ("a", "<u2"), ("b", "<u2")]))
    probes = np.fromfile(LUMP % 0x65, np.uint8).reshape(-1, 44)[:, :32].copy().view("<i2").reshape(-1, 16) / 8192.0
    sky = sky_luminance()
    out = []
    for o in origins:
        q = probes[refs["i"][int(np.argmin(((refs["p"] - o) ** 2).sum(1)))]]
        dc = 0.2126 * q[3] + 0.7152 * q[7] + 0.0722 * q[11]
        out.append(1.0 / max(0.001, float(np.sqrt((q[12:15] ** 2).sum())) * sky + dc))
    return out, sky


def main():
    lump = open(LUMP_2A, "rb").read()
    if len(lump) % 16:
        sys.exit("lump 0x2A size %d is not a multiple of 16" % len(lump))
    samples = len(lump) // 16
    want = {string_to_guid("cubemap/maps/%s/cubemap_%d.rpak" % (MAP, i)): i for i in range(samples)}

    d, pages, ao0, ac, _ = P.load(S30_PAK)
    sp_off = 0x80
    starpak = d[sp_off:sp_off + P.u16(d, 0x48)].split(b"\0")[0].decode()
    sp = open(os.path.join(S30_DIR, starpak), "rb")
    dec = oodle()
    fcc = struct.unpack("<I", b"cube")[0]
    faces, amb = {}, {}
    w0 = None
    for k in range(ac):
        ao = ao0 + k * 80
        if P.u32(d, ao + 0x4C) != fcc or P.u64(d, ao) not in want:
            continue
        h = P._res(pages, P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
        w, hh, idx, csize, usize, comp = struct.unpack_from("<HHIIIB", d, h + 0x10)
        if idx != want[P.u64(d, ao)] or w != hh or comp != 3:
            sys.exit("cube %d: unexpected header w=%d h=%d idx=%d comp=%d" % (want[P.u64(d, ao)], w, hh, idx, comp))
        w0 = w0 or w
        if w != w0 or usize != FACES * sum(mip_bytes(w, m) for m in range(MIPS)):
            sys.exit("cube %d: size %d does not match %dx%d BC6H, %d mips" % (idx, usize, w, w, MIPS))
        sp.seek(P.u64(d, ao + 0x20) & ~0xFFF)
        src = sp.read(csize)
        dst = ctypes.create_string_buffer(usize)
        if dec(src, csize, dst, usize, 1, 0, 0, None, 0, None, None, None, 0, 3) != usize:
            sys.exit("cube %d: Oodle decode failed" % idx)
        raw, o, per = dst.raw, 0, [[None] * MIPS for _ in range(FACES)]
        for m in range(MIPS):
            n = mip_bytes(w, m)
            for f in range(FACES):
                per[f][m] = raw[o:o + n]
                o += n
        faces[idx] = per
        amb[idx] = struct.unpack_from("<f", d, h + 0x24)[0]
    missing = sorted(set(range(samples)) - set(faces))
    if missing:
        sys.exit("BSP has %d samples but no cube asset for %s" % (samples, missing))

    tex_guid = string_to_guid("texture/maps/%s/cubemaps_hdr.rpak" % MAP)
    txtx_guid = string_to_guid("texture_extension/maps/%s/cubemaps_hdr/cubemap_ambients.rpak" % MAP)
    tex_path = os.path.join(ROOT, "extract", "texture", "0x%X.dds" % tex_guid)
    with open(tex_path, "wb") as fo:
        fo.write(dds_header(w0, MIPS, samples * FACES))
        for i in range(samples):
            for f in range(FACES):
                for m in range(MIPS):
                    fo.write(faces[i][f][m])
    os.makedirs(os.path.join(ROOT, "extract", "txtx"), exist_ok=True)
    origins = [struct.unpack_from("<3i", lump, i * 16) for i in range(samples)]
    rcp, sky = ambient_reciprocals(origins)
    blob = struct.pack("<%df" % samples, *rcp)
    with open(os.path.join(ROOT, "extract", "txtx", "0x%X" % txtx_guid), "wb") as fo:
        fo.write(struct.pack("<4I", TXTX_MAGIC, 1, samples, len(blob)) + blob)
    entries = [
        {"_type": "txtr", "_path": "texture/0x%X" % tex_guid, "$guid": tex_guid, "$hdrTail": CUBE_TAIL, "$layerCount": 2},
        {"_type": "txtx", "_path": "txtx/0x%X" % txtx_guid, "$guid": txtx_guid},
    ]
    json.dump(entries, open(os.path.join(ROOT, "inv", "cubemaps.json"), "w"), indent=1)

    # the map pak's own txtx (16 x RGBA8 on WE): same v2 layout as S21, 4 bytes per element
    fx = struct.unpack("<I", b"txtx")[0]
    for k in range(ac):
        ao = ao0 + k * 80
        if P.u32(d, ao + 0x4C) != fx:
            continue
        h = P._res(pages, P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
        fmt, cnt = struct.unpack_from("<II", d, h)
        dp = P._res(pages, P.i32(d, h + 8), P.i32(d, h + 12))
        with open(os.path.join(ROOT, "extract", "txtx", "0x%X" % P.u64(d, ao)), "wb") as fo:
            fo.write(struct.pack("<4I", TXTX_MAGIC, fmt, cnt, cnt * 4) + d[dp:dp + cnt * 4])
    print("samples %d, %dx%d, array %d, sky lum %.4g, ambient rcp %.4g..%.4g -> %s" % (
        samples, w0, w0, samples * FACES, sky, min(rcp), max(rcp), os.path.basename(tex_path)))


if __name__ == "__main__":
    main()
