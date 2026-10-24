"""Write S21 dxStates into S30 material jsons from their msnp snapshots.

S30 v23.2 materials keep their dxStates in an `msnp` asset (guid at header +0x08); RSX only
resolves it when the snapshot's pak is loaded, so materials whose snapshot lives in S30 common
export as all-zero (blend write mask 0 -> invisible). Snapshot bytes 0x00..0x30 are
u32 blend[8], u32 mask, u16 depthStencil, u16 raster, u64 unk28 -- same encoding as S21.

S21 adds the render-target-set bits to the mask (the engine reads mask & 0xC): 0x8 when
glueFlags has 0x20000, else 0x4 (S21 District: 5206/5206). S30 never sets them.

    py fix_dxstate_from_msnp.py <s30 map pak .dec.rpak> <s30 common .dec.rpak> [more paks] [--apply]

Exit 1 if any material is left with a zero render-target write mask or a missing RT-set bit,
or references a snapshot none of the paks hold. Depth materials (material/depth/...) write no
colour by design (blend 0 in both S30 and S21).
"""
import kitpaths as KP
import csv
import glob
import json
import os
import struct
import sys


import pak_msw as P  # noqa: E402

ROOT = os.path.dirname(os.path.abspath(__file__))
TREES = ("assets", "extract")
FCC_MATL = struct.unpack("<I", b"matl")[0]
FCC_MSNP = struct.unpack("<I", b"msnp")[0]
GLUE_RTSET8 = 0x20000


def scan(pak, want_matl):
    d, pages, ao0, ac, _ = P.load(pak)
    snaps, snapof = {}, {}
    for i in range(ac):
        ao = ao0 + i * 80
        f = P.u32(d, ao + 0x4C)
        if f != FCC_MSNP and not (want_matl and f == FCC_MATL):
            continue
        h = P._res(pages, P.i32(d, ao + 0x10), P.i32(d, ao + 0x14))
        if f == FCC_MSNP:
            snaps[P.u64(d, ao)] = bytes(d[h:h + 0x30])
        else:
            snapof[P.u64(d, ao)] = P.u64(d, h + 0x08)
    P._CACHE.clear()
    return snaps, snapof


def material_guids():
    out = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "inv", "base.csv"), encoding="utf-8", errors="replace")):
        if r["type"] == "matl":
            out[r["asset_name"].replace("\\", "/").lower()] = int(r["guid"], 16)
    extra = os.path.join(ROOT, "inv", "extra_matl.txt")
    if os.path.isfile(extra):
        for line in open(extra):
            p = line.split()
            if len(p) == 2:
                out.setdefault(p[1].lower(), int(p[0], 16))
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    apply = "--apply" in sys.argv
    if len(args) < 2:
        sys.exit(__doc__)
    db, snapof = scan(args[0], True)
    for pak in args[1:]:
        s, _ = scan(pak, False)
        for g, b in s.items():
            db.setdefault(g, b)
    guids = material_guids()
    print("snapshots", len(db), "map materials with snapshot ref", len(snapof))

    stats = {"written": 0, "same": 0, "nosnap_ref": 0, "unresolved": 0, "bad": 0}
    bad = []
    for tree in TREES:
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
            s = snapof.get(g, 0)
            new = dict(j)
            if s:
                b = db.get(s)
                if b is None:
                    stats["unresolved"] += 1
                    bad.append((rel, "snapshot %016X not in any pak" % s))
                    continue
                bs = struct.unpack_from("<8I", b, 0)
                mask, ds, rs = struct.unpack_from("<IHH", b, 0x20)
                new["blendStates"] = ["0x%X" % x for x in bs]
                new["blendStateMask"] = "0x%X" % mask
                new["depthStencilFlags"] = "0x%X" % ds
                new["rasterizerFlags"] = "0x%X" % rs
                new["dxStateUnk28"] = "0x%X" % struct.unpack_from("<Q", b, 0x28)[0]
            else:
                stats["nosnap_ref"] += 1
            mask = int(new["blendStateMask"], 16)
            if not mask & 0xC:
                mask |= 0x8 if int(new.get("glueFlags", "0"), 16) & GLUE_RTSET8 else 0x4
                new["blendStateMask"] = "0x%X" % mask
            depth = rel.lower().startswith("material/depth/")
            if not depth and int(new["blendStates"][0], 16) >> 28 == 0:
                bad.append((rel, "zero RT0 write mask on a colour material"))
            if new == j:
                stats["same"] += 1
                continue
            stats["written"] += 1
            if apply:
                json.dump(new, open(p, "w", encoding="utf-8"), indent=1)
    for rel, why in bad[:20]:
        print("  BAD", rel, why)
    stats["bad"] = len(bad)
    print(("applied" if apply else "dry run"), stats)
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
