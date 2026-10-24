import kitpaths as KP
import json, glob, os, collections, csv
A = (KP.MAPS + r"/mp_rr_desertlands_mu5/assets/material")
NEW = (KP.WORK + r"/mu5_dedi/matl/out/material")
new = {os.path.relpath(p, NEW).replace("\\", "/").lower(): p for p in glob.glob(NEW + "/**/*.json", recursive=True)}
c = collections.Counter(); unmatched = []
for p in glob.glob(A + "/**/*.json", recursive=True):
    rel = os.path.relpath(p, A).replace("\\", "/").lower()
    if rel not in new:
        unmatched.append(rel); continue
    d = json.load(open(p)); n = json.load(open(new[rel]))
    nt = n.get("$textures") or {}
    if any(not str(v).startswith("0x") for v in nt.values()):
        c["new-not-guid"] += 1; continue
    if d.get("$textures") != nt:
        d["$textures"] = nt
        json.dump(d, open(p, "w"), indent=1)
        c["patched"] += 1
    else:
        c["same"] += 1
print(dict(c), "unmatched", len(unmatched), unmatched[:20])
