import kitpaths as KP
import struct, sys, collections
B = (KP.MAPS + "/mp_rr_desertlands_mu5/bsp/%s/mp_rr_desertlands_mu5.bsp.%04x.bsp_lump")
D = sys.argv[1]; want = sys.argv[2]
L = lambda i: open(B % (D, i), "rb").read()
td, strs, ms, me = L(2), L(0xF), L(0x52), L(0x50)
names = [strs[struct.unpack_from("<i", td, k*16)[0]:strs.index(b"\0", struct.unpack_from("<i", td, k*16)[0])].decode().lower().replace("\\","/") for k in range(len(td)//16)]
n = 0; tris = 0; fl = collections.Counter(); lm = collections.Counter()
for k in range(len(me)//28):
    f = struct.unpack_from("<IH8hHI", me, k*28)
    t, l, _, _, vo = struct.unpack_from("<4hI", ms, f[10]*12)
    if want in names[t]: n += 1; tris += f[1]; fl[hex(f[11])] += 1; lm[l] += 1
print(D, want, "meshes", n, "tris", tris, dict(fl), dict(lm))
