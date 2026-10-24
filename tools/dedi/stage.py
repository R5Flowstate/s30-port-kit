import kitpaths as KP
import json, os, shutil
A = KP.ASSETS
S = A + "/s21-to-s3-server"
V54 = A + "/s21-maps/mp_rr_desertlands_mu5/dedi_work/mdl_v54"
OUT = S + "/mp_rr_desertlands_mu5"
L = "mp_rr_desertlands_mu5"
if os.path.isdir(OUT):
    shutil.rmtree(OUT)
srcs = {}
for rel in open((KP.WORK + r"/mu5_dedi/pack.txt")).read().split("\n"):
    if rel:
        srcs[rel] = V54 + "/" + rel
srcs["mdl/props/weapon_crate/weapon_crate_01.rmdl"] = (KP.WORK + r"/mu5_dedi/mdl_v54/mdl/props/weapon_crate/weapon_crate_01.rmdl")
srcs["mdl/beacon/construction_plastic_mat_white_01.rmdl"] = S + "/mp_rr_desertlands_hu/mdl/beacon/construction_plastic_mat_white_01.rmdl"
node = S + "/mp_rr_aqueduct/mdl/dev/editor_effect_node_128.rmdl"
for n in (128, 256, 512, 1024):
    srcs["mdl/dev/editor_effect_node_%d.rmdl" % n] = node
# Animated props: rigs + sequences converted by workspace/map_anims.py (S30 arig/aseq/asqd -> S3 v4/v7).
# repak packs every aseq named in $sequences; rigs are listed after the models like the twin dedi paks.
ANIM_DIR = A + "/s21-maps/%s/dedi_work/anim_s3" % L
ANIM = json.load(open(A + "/s21-maps/%s/inv/anim_deps.json" % L))
missing = [m for m in ANIM if m.startswith("mdl/") and m not in srcs]
if missing:
    raise SystemExit("animated models not in the pack set: %s" % missing)
files = []
nphy = 0
for rel, src in sorted(srcs.items()):
    dst = OUT + "/" + rel
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    shutil.copyfile(src, dst)
    phy = os.path.splitext(src)[0] + ".phy"
    if os.path.isfile(phy):
        shutil.copyfile(phy, os.path.splitext(dst)[0] + ".phy")
        nphy += 1
    e = {"_type": "mdl_", "_path": rel}
    dep = ANIM.get(rel, {})
    if dep.get("animrigs"):
        e["$animrigs"] = dep["animrigs"]
    if dep.get("sequences"):
        e["$sequences"] = dep["sequences"]
    files.append(e)
nanim = 0
for rel, dep in sorted(ANIM.items()):
    paths = ([rel] if rel.startswith("animrig/") else []) + dep.get("sequences", [])
    for p in paths:
        os.makedirs(os.path.dirname(OUT + "/" + p), exist_ok=True)
        shutil.copyfile(ANIM_DIR + "/" + p, OUT + "/" + p)
        nanim += 1
    if rel.startswith("animrig/"):
        files.append({"_type": "arig", "_path": rel, "$sequences": dep["sequences"]})
# Map datatables (zones table etc.) are read by SERVER scripts too: RSX csv exports with the type row last.
DT = A + "/s21-maps/%s/extract/datatable" % L
ndt = 0
for f in sorted(os.listdir(DT)) if os.path.isdir(DT) else []:
    if f.endswith(".csv"):
        os.makedirs(OUT + "/datatable", exist_ok=True)
        shutil.copyfile(DT + "/" + f, OUT + "/datatable/" + f)
        files.append({"_type": "dtbl", "_path": "datatable/%s.rpak" % f[:-4], "$guid": f[:-4]})
        ndt += 1
json.dump({"version": 8, "keepDevOnly": True, "name": L,
           "assetsDir": "s21-to-s3-server/%s/" % L, "outputDir": "./build/",
           "compressLevel": 6, "compressWorkers": 16, "files": files},
          open(S + "/%s.json" % L, "w"), indent=1)
json.dump({"version": 8, "dedi": True, "keepDevOnly": False, "keepClientOnly": False,
           "outputDir": "./paks/Win64_server/", "showDebugInfo": False,
           "paks": ["s21-to-s3-server/%s.json" % L]},
          open(A + "/build_list_%s_dedi.json" % L, "w"), indent=1)
print("entries", len(files), "phy", nphy, "anim files", nanim, "datatables", ndt)
