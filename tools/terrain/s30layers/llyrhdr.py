import kitpaths as KP
import struct, sys

import pak_msw as P
PAK = (KP.MAPS + r"/mp_rr_desertlands_mu5/inv/mp_rr_desertlands_mu5.rpak.dec.rpak")
d, pages, ao0, ac, relocs = P.load(PAK)
R = lambda pg, off: P._res(pages, pg, off)
fcc = struct.unpack("<I", b"llyr")[0]
want = sys.argv[1]
for i in range(ac):
    ao = ao0 + i * 80
    if P.u32(d, ao + 0x4C) != fcc: continue
    hp = (P.i32(d, ao + 0x10), P.i32(d, ao + 0x14)); h = R(*hp)
    name = bytes(d[R(*struct.unpack_from("<ii", d, h + 8)):][:80]).split(b"\0")[0].decode()
    if want not in name: continue
    print(name, "entry", bytes(d[ao:ao+80]).hex(" ", 8))
    print("starpak off @0x20", hex(P.u64(d, ao + 0x20)), "opt @0x28", hex(P.u64(d, ao + 0x28)))
    for o in range(0, 0x70, 8):
        isrel = (hp[0], hp[1] + o) in relocs
        print(" +%02x %s %s" % (o, bytes(d[h+o:h+o+8]).hex(" ", 4), "PTR" if isrel else ""))
    for o in range(0, 0x70, 8):
        if (hp[0], hp[1] + o) in relocs and o != 8:
            t = R(*struct.unpack_from("<ii", d, h + o))
            print(" -> +%02x" % o)
            for k in range(0, 0x80, 16): print("     ", bytes(d[t+k:t+k+16]).hex(" ", 4))
