import kitpaths as KP
import struct, json, collections, sys
B = (KP.MAPS + "/mp_rr_desertlands_mu5/bsp/%s/mp_rr_desertlands_mu5.bsp.%04x.bsp_lump")
D = sys.argv[1] if len(sys.argv) > 1 else "client_v51_terrain"
L = lambda i: open(B % (D, i), "rb").read()
td, strs, ms, me = L(2), L(0xF), L(0x52), L(0x50)
names = [strs[struct.unpack_from("<i", td, k*16)[0]:strs.index(b"\0", struct.unpack_from("<i", td, k*16)[0])].decode().lower().replace("\\","/") for k in range(len(td)//16)]
hu = set(m.split("/")[-1].replace("_wldc.json","").replace("_colpass","") for m in json.load(open((KP.WORK + "/mu5_terrain/huimport/staged.json")))["materials"])
pg = collections.defaultdict(collections.Counter); fam = collections.defaultdict(collections.Counter)
for k in range(len(me)//28):
    f = struct.unpack_from("<IH8hHI", me, k*28)
    ntri, msort, fl = f[1], f[10], f[11]
    t, lm, _, _, vo = struct.unpack_from("<4hI", ms, msort*12)
    nm = names[t]; base = nm.split("/")[-1]
    isHu = any(base.startswith(h) for h in hu)
    pg[lm][nm] += ntri
    fam[lm][("hu_retail_ps" if isHu else "converted")] += ntri
for p in sorted(pg):
    print("page", p, dict(fam[p]), "materials", len(pg[p]))
    for nm, c in pg[p].most_common(6): print("    ", c, nm)
