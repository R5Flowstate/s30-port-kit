"""Blend sides by TEXTURE identity. Slot 1 (albedo2Texture) is what vertex alpha 255 shows, slot 0 the other end.

An albedo image is identified by its decoded top-mip content (byte copies under another guid are the same image) and carries a
SET of layer labels: single-layer materials seed it; a blend material whose one slot image already carries one of the
material's name layers labels the other slot with the remaining name. Iterated to a fixed point. Name order is never used."""
import kitpaths as KP
import glob, hashlib, json, os, struct, sys

import matmap

MAPD = matmap.MAPD
MAT_DIRS = (MAPD + "assets/material/", (KP.WORK + "/mu5_terrain/huimport/x_all/material/"))
TEX_DIRS = (MAPD + "extract/texture/", MAPD + "extract/texture_s21/", (KP.WORK + "/mu5_terrain/huimport/x_all/texture/"))

def mat_json(m):
    for d in MAT_DIRS:
        p = d + m + "_wldc.json"
        if os.path.exists(p): return json.load(open(p))

_img = {}
def image(g):
    if g not in _img:
        _img[g] = g
        for d in TEX_DIRS:
            p = d + g + ".dds"
            if os.path.exists(p):
                b = open(p, "rb").read()
                h, w = struct.unpack_from("<II", b, 12)
                bpb = 8 if struct.unpack_from("<I", b, 128)[0] in (70, 71, 72) else 16
                n = max(1, w // 4) * max(1, h // 4) * bpb
                _img[g] = hashlib.sha1(b[148:148 + n]).hexdigest()[:16]
                break
    return _img[g]

def sides(cat):
    """-> {material: (frozenset slot1 layers, frozenset slot0 layers)} for every catalogue material with a json."""
    slots = {}
    for m, (src, a, b) in cat.items():
        d = mat_json(m)
        if not d: continue
        t = d["$textures"]
        names = frozenset(x for x in (a, b) if x >= 0)
        slots[m] = (image(t["0"]), image(t["1"]) if a != b and "1" in t else None, names, a == b)
    lab = {}
    for m, (i0, i1, names, single) in slots.items():
        if single: lab.setdefault(i0, set()).update(names)
    changed = True
    while changed:
        changed = False
        for m, (i0, i1, names, single) in slots.items():
            if single or i1 is None: continue
            for ia, ib in ((i0, i1), (i1, i0)):
                hit = lab.get(ia, set()) & names
                if len(hit) == 1:
                    rest = names - hit
                    if len(rest) == 1 and not rest <= lab.get(ib, set()):
                        lab.setdefault(ib, set()).update(rest); changed = True
    out = {}
    for m, (i0, i1, names, single) in slots.items():
        if single: out[m] = (frozenset(names), frozenset(names)); continue
        out[m] = (frozenset(lab.get(i1, ())), frozenset(lab.get(i0, ())))
    return out

if __name__ == "__main__":
    cat = matmap.catalogue()
    s = sides(cat)
    nm = lambda S: "+".join(sorted(matmap.TOK[x] for x in S)) or "?"
    for m in sorted(s):
        if cat[m][1] == cat[m][2]: continue
        print("%s %-68s s1 %-40s s0 %s" % (cat[m][0], m.split("/")[-1], nm(s[m][0]), nm(s[m][1])))
