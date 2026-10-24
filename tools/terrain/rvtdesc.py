import kitpaths as KP
import struct, sys

import pak_msw as P
PAK = (KP.MAPS + r"/mp_rr_desertlands_mu5/inv/mp_rr_desertlands_mu5.rpak.dec.rpak")
d, pages, ao0, ac, _ = P.load(PAK)
R = lambda pg, off: P._res(pages, pg, off)
vt = R(0x39, 0); b = bytes(d[vt:vt + 216])
for o in range(0, 0xd8, 8):
    pg, off = struct.unpack_from("<ii", b, o)
    s = "  vt+%02x (%d,%#x)" % (o, pg, off)
    if 0 < pg < 200 and off >= 0:
        try:
            h = R(pg, off); fl = struct.unpack_from("<12f", d, h)
            s += "  f: " + " ".join("%.3f" % x for x in fl)
        except Exception as e: s += " ?"
    print(s)
import json
L = json.load(open((KP.WORK + "/mu5_terrain/layers.json")))
h = R(57, 0x45d774)
out = []
for i in range(28):
    t = struct.unpack_from("<3fIIff I3fI", d, h + 64 * i)
    out.append(dict(layer=L[i], tint=t[0:3], decal=t[3], flags=t[4], ramp=t[5], mid=t[6], stoch=t[7], approx=t[8:11]))
    print(i, "%-50s tint %.3f %.3f %.3f  flags %#x ramp %.2f mid %.2f stoch %d approx %.3f %.3f %.3f" % (L[i], *t[0:3], t[4], t[5], t[6], t[7], *t[8:11]))
json.dump(out, open((KP.WORK + "/mu5_terrain/rvt_layer_descs.json"), "w"), indent=1)
