"""Bake the S21 material header fields RSX does not export into the S30 material jsons.

S30 v23.2 header -> S21 v23 json key (equal on every same-GUID S21 desertlands_hu pair):
  +0x90 colorModulationParams (float2) -> unk_C0
  +0x98 colorModulationInterval        -> unk_EA (Repak writes it to 0xEC)
  +0xA4 colorModulationScales.y        -> unk_E8 (S21 default 1.0)
unk_CC has no S30 field; S21 derives it per shaderset. Taken from S21 retail by GUID, then
by name, then the unanimous value of S21 materials sharing this S30 shaderset, else 0.

    py fix_matl_hdr_from_s30.py <s30 map .dec.rpak> <s21 oracle .dec.rpak>... [--s30 <pak>]... [--apply]
"""
import kitpaths as KP
import collections
import csv
import glob
import json
import os
import struct
import sys


import pak_msw as P  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
FCC_MATL = struct.unpack("<I", b"matl")[0]


def headers(pak):
    d, pages, ao0, ac, _ = P.load(pak)
    out = {}
    for i in range(ac):
        ao = ao0 + i * 80
        if P.u32(d, ao + 0x4C) != FCC_MATL:
            continue
        h = P._res(pages, P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
        no = P._res(pages, P.i32(d, h + 0x18), P.i32(d, h + 0x1C))
        nm = d[no:d.index(b"\0", no)].decode("latin1") if no is not None else ""
        out[P.u64(d, ao)] = (bytes(d[h:h + 0x100]), nm)
    P._CACHE.clear()
    return out


def main():
    argv = sys.argv[1:]
    apply = "--apply" in argv
    s30_extra = [argv[i + 1] for i, a in enumerate(argv) if a == "--s30"]
    pos = [a for i, a in enumerate(argv) if not a.startswith("--") and (i == 0 or argv[i - 1] != "--s30")]
    if len(pos) < 2:
        sys.exit(__doc__)
    s30 = headers(pos[0])
    for p in s30_extra:
        for g, v in headers(p).items():
            s30.setdefault(g, v)
    s21, s21_name = {}, {}
    for p in pos[1:]:
        for g, (h, nm) in headers(p).items():
            cc = struct.unpack_from("<I", h, 0xCC)[0]
            s21.setdefault(g, cc)
            s21_name.setdefault(nm, cc)
    by_shds = collections.defaultdict(collections.Counter)
    for g, cc in s21.items():
        if g in s30:
            by_shds[struct.unpack_from("<Q", s30[g][0], 0x58)[0]][cc] += 1

    guids = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "inv", "base.csv"), encoding="utf-8", errors="replace")):
        if r["type"] == "matl":
            guids[r["asset_name"].replace("\\", "/").lower()] = int(r["guid"], 16)
    extra = os.path.join(ROOT, "inv", "extra_matl.txt")
    if os.path.isfile(extra):
        for line in open(extra):
            p = line.split()
            if len(p) == 2:
                guids.setdefault(p[1].lower(), int(p[0], 16))

    st = collections.Counter()
    for tree in ("assets", "extract"):
        root = os.path.join(ROOT, tree, "material")
        for p in glob.glob(os.path.join(root, "**", "*.json"), recursive=True):
            try:
                j = json.load(open(p, encoding="utf-8"))
            except Exception:
                continue
            if "blendStates" not in j:
                continue
            rel = "material/" + os.path.relpath(p, root).replace("\\", "/")[:-5] + ".rpak"
            g = guids.get(rel.lower())
            if g not in s30:
                st["no-s30-header"] += 1
                continue
            h, nm = s30[g]
            new = dict(j)
            new["unk_C0"] = "0x%X" % struct.unpack_from("<Q", h, 0x90)[0]
            new["unk_EA"] = "0x%X" % struct.unpack_from("<I", h, 0x98)[0]
            new["unk_E8"] = "0x%X" % struct.unpack_from("<I", h, 0xA4)[0]
            shds = by_shds.get(struct.unpack_from("<Q", h, 0x58)[0])
            if g in s21:
                cc, how = s21[g], "guid"
            elif nm in s21_name:
                cc, how = s21_name[nm], "name"
            elif shds and len(shds) == 1:
                cc, how = next(iter(shds)), "shds"
            else:
                cc, how = 0, "default"
            new["unk_CC"] = "0x%X" % cc
            st["cc-" + how] += 1
            if new != j:
                st["written"] += 1
                if apply:
                    json.dump(new, open(p, "w", encoding="utf-8"), indent=1)
    print(("applied" if apply else "dry run"), dict(st))
    sys.exit(1 if st["no-s30-header"] else 0)


if __name__ == "__main__":
    main()
