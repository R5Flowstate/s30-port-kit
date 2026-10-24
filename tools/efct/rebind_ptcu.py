"""Bind S30 particle materials whose shaderset is bindless-only to the S21-resident shaderset that the
same-GUID S21 material uses (S30 0xCE7A6E802B08A999 == S21 0x1B948D35BB854480 on flare_hoop01_add_noz_ptcu),
with the S21 blend states of that same pair."""
import glob, json, os, sys

W = sys.argv[1]  # map workspace
MAP = {"0xCE7A6E802B08A999": ("0x1B948D35BB854480", {0: ("0x70030206", "0xF0138086"), 7: ("0x138286", "0x0")})}
n = 0
for tree in ("extract", "assets"):
    for p in glob.glob(W + "/%s/material/**/*.json" % tree, recursive=True):
        j = json.load(open(p, encoding="utf-8"))
        m = MAP.get(j.get("shaderSet", "").upper().replace("0X", "0x"))
        if not m:
            continue
        j["shaderSet"] = m[0]
        for i, (s30, s21) in m[1].items():
            if j["blendStates"][i].upper() == s30.upper():
                j["blendStates"][i] = s21
        json.dump(j, open(p, "w", encoding="utf-8"), indent=1)
        n += 1
        print("rebound", os.path.relpath(p, W))
print("materials", n)
