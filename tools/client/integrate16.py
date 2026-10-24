import kitpaths as KP
import os, sys, glob, json, shutil, csv, struct, collections
R = (KP.MAPS + r"/mp_rr_desertlands_mu5")
X = (KP.WORK + r"/mu5_dedi/matl16")

from string_to_guid import string_to_guid
import gen_full_manifest as G
import pak_msw as P
from fix_shaderset_msw import read_v15
oracle = set()
for r in csv.DictReader(open(R + "/inv/s21_oracle.csv", encoding="utf-8", errors="replace")):
    if not r["file_name"].lower().startswith("mp_lobby"): oracle.add(int(r["guid"], 16))
c = collections.Counter()
for f in glob.glob(X + "/texture/*"):
    d = R + "/extract/texture/" + os.path.basename(f)
    if not os.path.exists(d): shutil.copyfile(f, d); c["tex"] += 1
new_shdr = []
for f in glob.glob(X + "/shader/*.msw"):
    g = int(os.path.basename(f)[:-4], 16)
    if g in oracle or os.path.exists(R + "/extract/shader/" + os.path.basename(f)): continue
    shutil.copyfile(f, R + "/extract/shader/" + os.path.basename(f)); new_shdr.append(f); c["shdr"] += 1
DECS = [(KP.WORK + r'\mu5_dedi\s30\common.rpak.dec.rpak'), (KP.WORK + r'\mu5_dedi\s30\common_mp.rpak.dec.rpak'), R + r'\inv\mp_rr_desertlands_mu5.rpak.dec.rpak']
for f in glob.glob(X + "/shaderset/*.msw"):
    g = int(os.path.basename(f)[:-4], 16)
    out = R + "/extract/shaderset_s21/" + os.path.basename(f)
    if g in oracle or os.path.exists(out): continue
    ss = None
    for d in DECS:
        try: ss = read_v15(d, g); break
        except Exception: continue
    assert ss, hex(g)
    P.write_shaderset_msw(ss, out); c["shds"] += 1
lst = []
for f in glob.glob(X + "/material/**/*.json", recursive=True):
    rel = os.path.relpath(f, X + "/material").replace("\\", "/")
    name = "material/" + rel[:-5] + ".rpak"
    g = string_to_guid(name)
    if g in oracle: c["matl_oracle"] += 1; continue
    for base in (R + "/extract/material/", R + "/assets/material/"):
        dst = base + rel
        os.makedirs(os.path.dirname(dst), exist_ok=True)
        if base.endswith("assets/material/"): G.convert_material(f, dst)
        else: shutil.copyfile(f, dst)
        u = f[:-5] + ".uber"
        if os.path.isfile(u): shutil.copyfile(u, dst[:-5] + ".uber")
    lst.append("%016X %s" % (g, name)); c["matl"] += 1
open(R + "/inv/extra_matl.txt", "w").write("\n".join(sorted(lst)) + "\n")
open((KP.WORK + r"/mu5_dedi/new_shdr16.txt"), "w").write("\n".join(new_shdr))
print(dict(c))
