import kitpaths as KP
import os, struct, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__))); 
import oodle, pak_msw as P
PAK = (KP.MAPS + r"/mp_rr_desertlands_mu5/inv/mp_rr_desertlands_mu5.rpak.dec.rpak")
STAR = (KP.S30_GAME + r"/paks/Win64/pc_all.starpak")
NT, TILE_RAW, LOW = 85, 196608, 0x5570
d, pages, ao0, ac, relocs = P.load(PAK)
R = lambda pg, off: P._res(pages, pg, off)
FCC = struct.unpack("<I", b"llyr")[0]
def layers():
    out = {}
    for i in range(ac):
        ao = ao0 + i * 80
        if P.u32(d, ao + 0x4C) != FCC: continue
        h = R(P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
        name = bytes(d[R(*struct.unpack_from("<ii", d, h + 8)):][:96]).split(b"\0")[0].decode().replace(chr(92), "/")
        out[P.u64(d, ao)] = dict(name=name, star=P.u64(d, ao + 0x20), h=h)
    return out
def ptr(h, o): return R(*struct.unpack_from("<ii", d, h + o))
def streams(L):
    """-> list of (start, comp offsets[85], end) per stream"""
    h = L["h"]; base = L["star"]
    size1 = P.u32(d, h + 0x30); size2 = P.u32(d, h + 0x48)
    nlow = 3 * (1 + (size2 > 0))
    s1 = base + nlow * LOW
    o1 = list(struct.unpack_from("<85I", d, ptr(h, 0x38)))
    o2 = list(struct.unpack_from("<85I", d, ptr(h, 0x50)))
    return [(s1, o1, size1), (s1 + size1, o2, size2)]
_f = open(STAR, "rb")
def tile(L, stream, k):
    start, offs, size = streams(L)[stream]
    end = offs[k + 1] if k + 1 < NT else size
    _f.seek(start + offs[k]); comp = _f.read(end - offs[k])
    return oodle.decompress(comp, TILE_RAW)
