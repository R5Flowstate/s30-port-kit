import kitpaths as KP
import struct, sys, pickle

import patch_shdr_cbremap as P
MDL = struct.unpack("<I", b"mdl_")[0]
FIX = lambda o: (o & 0xFFFE) << (4 * (o & 1))
rot = lambda r: r[12:16] + r[0:12] + r[16:32]
want, pak, srcfile = sys.argv[1], sys.argv[2], sys.argv[3]
hu, _ = pickle.load(open('oracle.pkl', 'rb'))
twin = hu[want]
def patch(buf, sh):
    n = struct.unpack_from("<h", buf, sh + 0xDA)[0]; off = FIX(struct.unpack_from("<H", buf, sh + 0xDC)[0])
    recs = [bytes(buf[sh+off+32*k: sh+off+32*k+32]) for k in range(n)]
    if recs == twin: return 'already'
    new = [rot(r) for r in recs]
    assert new == twin, 'rotation does not reproduce the S21 twin'
    for k, r in enumerate(new): buf[sh+off+32*k: sh+off+32*k+32] = r
    return 'rotated'
d = bytearray(open(pak, 'rb').read())
pages, ao0, ac, _ = P.parse(bytes(d), aligned=False)
for i in range(ac):
    ao = ao0 + i * 80
    if P.u32(d, ao + 0x4C) != MDL: continue
    ho = P.res(pages, P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
    no = P.res(pages, P.i32(d, ho + 0x08), P.i32(d, ho + 0x0C))
    if no is None or d[no:d.index(b"\0", no)].decode("latin1").lower() != want: continue
    sh = P.res(pages, P.i32(d, ho), P.i32(d, ho + 4))
    print('pak', patch(d, sh)); open(pak, 'wb').write(d)
s = bytearray(open(srcfile, 'rb').read())
print('src', patch(s, 0)); open(srcfile, 'wb').write(s)
