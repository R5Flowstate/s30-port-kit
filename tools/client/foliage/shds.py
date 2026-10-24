import kitpaths as KP
import struct, sys

import patch_shdr_cbremap as P
SHDS = struct.unpack("<I", b"shds")[0]
def shds(path):
    d = open(path, "rb").read()
    pages, ao0, ac, _ = P.parse(d, aligned=False)
    out = {}
    for i in range(ac):
        ao = ao0 + i * 80
        if P.u32(d, ao + 0x4C) != SHDS: continue
        ho = P.res(pages, P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
        if ho is None: continue
        out[struct.unpack_from("<Q", d, ao)[0]] = struct.unpack_from("<QQ", d, ho + 0x40)
    return out
