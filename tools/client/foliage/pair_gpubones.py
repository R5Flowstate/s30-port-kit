import kitpaths as KP
import struct, sys, collections

import patch_shdr_cbremap as P
MDL = struct.unpack("<I", b"mdl_")[0]
FIX = lambda o: (o & 0xFFFE) << (4 * (o & 1))
def models(path):
    d = open(path, "rb").read()
    pages, ao0, ac, _ = P.parse(d, aligned=False)
    out = {}
    for i in range(ac):
        ao = ao0 + i * 80
        if P.u32(d, ao + 0x4C) != MDL: continue
        ho = P.res(pages, P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
        if ho is None: continue
        no = P.res(pages, P.i32(d, ho + 0x08), P.i32(d, ho + 0x0C))
        nm = "" if no is None else d[no:d.index(b"\0", no)].decode("latin1")
        sh = P.res(pages, P.i32(d, ho), P.i32(d, ho + 4))
        if sh is None: continue
        n = struct.unpack_from("<h", d, sh + 0xDA)[0]
        off = FIX(P.u16(d, sh + 0xDC))
        recs = [d[sh+off+32*k: sh+off+32*k+32] for k in range(n)] if n > 0 and off else []
        out[nm.lower()] = recs
    return out
hu = models(sys.argv[1]); mu = models(sys.argv[2])
rot = lambda r: r[12:16] + r[0:12] + r[16:32]
stat = collections.Counter()
for nm, recs in sorted(mu.items()):
    if not recs: continue
    h = hu.get(nm)
    if h is None: stat["no-twin"] += 1; print("NOTWIN", nm, len(recs)); continue
    if len(h) != len(recs): stat["count-diff"] += 1; print("COUNT", nm, len(h), len(recs)); continue
    if h == recs: k = "identical"
    elif all(rot(r) == x for r, x in zip(recs, h)): k = "rot-match"
    else: k = "other"
    stat[k] += 1
    print(k.upper(), nm, len(recs))
    if k == "other":
        for a, b in list(zip(h, recs))[:2]:
            print("   hu", a.hex(" ", 4)); print("   mu", b.hex(" ", 4))
print(stat)
