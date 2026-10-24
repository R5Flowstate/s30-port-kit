import kitpaths as KP
import struct, sys, collections, pickle

import patch_shdr_cbremap as P
MATL = struct.unpack("<I", b"matl")[0]
def mats(path):
    d = open(path, "rb").read()
    pages, ao0, ac, _ = P.parse(d, aligned=False)
    out = {}
    for i in range(ac):
        ao = ao0 + i * 80
        if P.u32(d, ao + 0x4C) != MATL: continue
        g = struct.unpack_from("<Q", d, ao)[0]
        ho = P.res(pages, P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
        if ho is None: continue
        no = P.res(pages, P.i32(d, ho + 0x18), P.i32(d, ho + 0x1C))
        nm = d[no:d.index(b"\0", no)].decode("latin1") if no is not None else ""
        shds = struct.unpack_from("<Q", d, ho + 0xC8 - 0x70 + 0x70)[0] if False else None
        out[g] = (nm.lower(), d[ho:ho+0x100], ao)
    return out
if __name__ == "__main__":
    D=(KP.DEPLOY + r'/paks/Win64/')
    orc={}
    for p in ['mp_rr_district','mp_rr_tropic_island_mu2','mp_rr_tropic_island_mu2(01)','mp_rr_olympus_mu2','mp_rr_canyonlands_hu','mp_rr_divided_moon_mu1','mp_rr_divided_moon_mu1(01)','mp_rr_desertlands_hu','mp_rr_desertlands_hu(01)','common','common_mp','common(01)','common_mp(01)','mp_rr_thunderdome','mp_rr_canyonlands_staging_mu1','mp_rr_party_crasher','mp_rr_freedm_skulltown','mp_rr_arena_habitat','mp_lobby']:
        for g,(nm,h,_) in mats(D+p+'.rpak.dec.rpak').items():
            orc.setdefault(g,(nm,h,p))
    pickle.dump(orc,open('orc_mat.pkl','wb'))
    print('oracle mats',len(orc))
