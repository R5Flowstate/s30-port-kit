"""Stage the hu (S21 retail) terrain materials chosen by gen_terrain2 (terrain_materials.json, source H) into the mu5 tree.

The previous run's imports (huimport/staged.json) are removed first, so only the current set ships.
materials (+ uber, + their colpass) -> extract/material and assets/material, listed in inv/extra_matl.txt
textures not resident -> extract/texture_s21 (dds + json; tails are the S21 originals)
shadersets not resident -> extract/shaderset_s21; their alias pixel shaders (type 9) -> extract/shader_s21 as a copy of
the parent shader's bytes under the alias guid."""
import kitpaths as KP
import csv, glob, json, os, shutil, struct, sys

import pak_msw as P

ROOT = (KP.WORK + "/mu5_terrain/")
X = ROOT + "huimport/x_all/"
HU_DEC = ROOT + "hudec/mp_rr_desertlands_hu.rpak.dec.rpak"
M = (KP.MAPS + "/mp_rr_desertlands_mu5/")
EX = M + "extract/"
REC = ROOT + "huimport/staged.json"

hu_rows = {r[-1].lower().replace("\\", "/"): int(r[3], 16) for r in csv.reader(open(ROOT + "s30inv/hu.csv")) if r and r[0] == "matl"}
extra_path = M + "inv/extra_matl.txt"
extra = [l for l in open(extra_path, encoding="utf-8").read().splitlines() if l.strip()]

def rm(p):
    if os.path.isfile(p): os.remove(p)

old = json.load(open(REC)) if os.path.isfile(REC) else {"materials": [], "textures": [], "shadersets": [], "shaders": []}
drop = set()
for rel in old["materials"]:
    for root in (EX + "material/", M + "assets/material/"):
        rm(root + rel); rm(root + rel[:-5] + ".uber")
    g = hu_rows.get(("material/" + rel[:-5] + ".rpak").lower())
    if g is not None: drop.add(g)
extra = [l for l in extra if int(l.split()[0], 16) not in drop]
for g in old["textures"]:
    rm(EX + "texture_s21/%s.dds" % g); rm(EX + "texture_s21/%s.json" % g)
for g in old["shadersets"]: rm(EX + "shaderset_s21/%s.msw" % g)
for g in old["shaders"]: rm(EX + "shader_s21/%s.msw" % g)
print("removed previous imports: materials %d textures %d shadersets %d shaders %d" % (
    len(old["materials"]), len(old["textures"]), len(old["shadersets"]), len(old["shaders"])))

def guids(path):
    return {int(r[3], 16) for r in csv.reader(open(path)) if r and r[0] != "type"}
resident = guids(M + "inv/s21_oracle.csv") | guids(M + "inv/base.csv")
conv_sh = {int(os.path.basename(p)[:-4], 16) for p in glob.glob(EX + "shader_s21/*.msw")}
conv_ss = {int(os.path.basename(p)[:-4], 16) for p in glob.glob(EX + "shaderset_s21/*.msw")}
tm = json.load(open(ROOT + "terrain_materials.json"))
chosen = [m for m, s in zip(tm["materials"], tm["source"]) if s == "H"]

rec = {"materials": [], "textures": [], "shadersets": [], "shaders": []}
have_extra = {int(l.split()[0], 16) for l in extra}
tex_need, ss_need = set(), set()
for m in chosen:
    for suf in ("_wldc", "_colpass_wldc"):
        rel = m + suf + ".json"
        js = X + "material/" + rel
        if not os.path.isfile(js):
            if suf == "_wldc": sys.exit("chosen material not exported: " + js)
            continue
        g = hu_rows.get(("material/" + m + suf + ".rpak").lower())
        if g is None: sys.exit("no hu guid for " + rel)
        if g in resident: continue
        for root in (EX + "material/", M + "assets/material/"):
            dst = root + rel
            if os.path.isfile(dst): sys.exit("material already present at " + dst)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copy2(js, dst); shutil.copy2(js[:-5] + ".uber", dst[:-5] + ".uber")
        rec["materials"].append(rel)
        if g not in have_extra:
            extra.append("%016X %s" % (g, "material/" + m + suf + ".rpak")); have_extra.add(g)
        d = json.load(open(js))
        tex_need |= {int(v, 16) for v in d["$textures"].values() if int(v, 16)}
        ss_need.add(int(d["shaderSet"], 16))
open(extra_path, "w", encoding="utf-8").write("\n".join(extra) + "\n")

os.makedirs(EX + "texture_s21", exist_ok=True)
for g in sorted(tex_need - resident):
    src = X + "texture/0x%X" % g
    if not os.path.isfile(src + ".dds"): sys.exit("texture 0x%X not exported" % g)
    for ext in (".dds", ".json"):
        shutil.copy2(src + ext, EX + "texture_s21/0x%X%s" % (g, ext))
    rec["textures"].append("0x%X" % g)

for g in sorted(ss_need - resident):
    if g in conv_ss: sys.exit("shaderset 0x%X collides with a converted one" % g)
    src = X + "shaderset/0x%X.msw" % g
    ps, vs = struct.unpack_from("<QQ", open(src, "rb").read(), 5)
    shutil.copy2(src, EX + "shaderset_s21/0x%X.msw" % g); rec["shadersets"].append("0x%X" % g)
    for sg in (ps, vs):
        if sg in resident or sg in conv_sh: continue
        sh = P.read_shader(HU_DEC, sg)
        if sh is None: sys.exit("shader 0x%X not in hu" % sg)
        par = sh["parent"] if sh["type"] > 5 else sg
        psrc = X + "shader/0x%X.msw" % par
        if not os.path.isfile(psrc): sys.exit("parent shader 0x%X of 0x%X not exported" % (par, sg))
        shutil.copy2(psrc, EX + "shader_s21/0x%X.msw" % sg); rec["shaders"].append("0x%X" % sg); conv_sh.add(sg)
json.dump(rec, open(REC, "w"), indent=1)
print("staged: materials %d textures %d shadersets %d shaders %d" % tuple(len(rec[k]) for k in ("materials", "textures", "shadersets", "shaders")))
