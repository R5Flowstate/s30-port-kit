import kitpaths as KP
import numpy as np, sys, struct
def probe_terms(L, cubemap_lump):
    dt = np.dtype([('p', '<f4', 3), ('i', '<u4'), ('a', '<u2'), ('b', '<u2')])
    refs = np.fromfile(L % 0x68, dt)
    pr = np.fromfile(L % 0x65, np.uint8).reshape(-1, 44)
    s = pr[:, :32].copy().view('<i2').reshape(-1, 16).astype(np.float64) / 8192.0
    cm = np.frombuffer(open(L % cubemap_lump, 'rb').read(), np.int32).reshape(-1, 4)[:, :3].astype(np.float64)
    out = []
    for o in cm:
        d = ((refs['p'] - o) ** 2).sum(1); k = int(np.argmin(d))
        q = s[refs['i'][k]]
        amb = 0.2126 * q[3] + 0.7152 * q[7] + 0.0722 * q[11]
        v = np.sqrt((q[12:15] ** 2).sum())
        out.append((amb, v, np.sqrt(d[k]), q[15]))
    return np.array(out)
if __name__ == '__main__':
    H = (KP.WORK + "/mu5_terrain/hu/wrap/maps/mp_rr_desertlands_hu.bsp.%04X.bsp_lump.client")
    t = probe_terms(H, 0x2A)
    import pak_msw as P
    from string_to_guid import string_to_guid
    g = string_to_guid("texture_extension/maps/mp_rr_desertlands_hu/cubemaps_hdr/cubemap_ambients.rpak")
    pd, pages, ao0, ac, _ = P.load((KP.WORK + r"/paks_dec/mp_rr_desertlands_hu.rpak.dec.rpak"))
    for k in range(ac):
        ao = ao0 + k * 80
        if P.u64(pd, ao) == g:
            h = P._res(pages, P.i32(pd, ao + 0x10), P.i32(pd, ao + 0x14)); cnt = struct.unpack_from("<I", pd, h + 4)[0]
            dp = P._res(pages, P.i32(pd, h + 8), P.i32(pd, h + 12)); r = np.frombuffer(bytes(pd[dp:dp + 4 * cnt]), np.float32).astype(np.float64)
    x = 1 / r
    for i in range(len(r)): print(i, "1/rcp %.5f  amb %.5f  |v| %.3f  w %.3f dist %.0f   ratio %.3f" % (x[i], t[i, 0], t[i, 1], t[i, 3], t[i, 2], x[i] / max(t[i, 0], 1e-9)))
    m = r < 999
    A = np.stack([t[m, 0], t[m, 1]], 1); c, res, *_ = np.linalg.lstsq(A, x[m], rcond=None)
    print("fit 1/rcp = %.4f*amb + %.5f*|v|   resid %s" % (c[0], c[1], res))
