import kitpaths as KP
import csv, glob, os, sys
from pathlib import Path

import map_model_origin as M

from string_to_guid import string_to_guid
ROOT = Path((KP.MAPS + r"\mp_rr_desertlands_mu5"))
L = "mp_rr_desertlands_mu5"
req, src = M.collect_required_models(ROOT / "bsp" / "dedi_v47", L)
loaded = {}
for f in glob.glob((KP.WORK + r"\mu5_dedi\lists\*.csv")):
    pak = os.path.basename(f)[:-4]
    for r in csv.DictReader(open(f, encoding="utf-8", errors="replace")):
        if not r["file_name"].lower().startswith(pak.lower()):
            continue
        loaded[int(r["guid"], 16)] = pak
v54 = ROOT / "dedi_work" / "mdl_v54"
have = {}
for p in v54.rglob("*.rmdl"):
    rel = p.relative_to(v54).as_posix()
    have[string_to_guid(rel)] = rel
print("required", len(req), "loaded guids", len(loaded), "v54", len(have))
pack = [rel for g, rel in have.items() if g not in loaded]
dup = [rel for g, rel in have.items() if g in loaded]
miss = []
for r in sorted(req):
    g = string_to_guid(r)
    if g not in loaded and g not in have:
        miss.append(r)
print("pack", len(pack), "already-loaded", len(dup), "MISSING", len(miss))
for m in miss: print("  MISS", m, sorted(src.get(m, []))[:3])
open((KP.WORK + r"\mu5_dedi\pack.txt"), "w").write("\n".join(sorted(pack)))
open((KP.WORK + r"\mu5_dedi\missing.txt"), "w").write("\n".join(miss))
