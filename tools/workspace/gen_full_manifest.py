#!/usr/bin/env python3
"""Client rpak manifest for mp_rr_desertlands_mu5.

Models are the converted v17 tree (csv names are guid stubs because the
extractor could not read v20 headers). Texture tails are synthesized.
A guid the live client already has resident is not packed.
"""
import kitpaths as KP
import csv
import glob
import json
import os
import shutil
import struct
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
SALHAM = (KP.MAPS + r"\mp_rr_salham")


from string_to_guid import string_to_guid

MAP = "mp_rr_desertlands_mu5"
INV = os.path.join(ROOT, "inv", "base.csv")
ORACLE = os.path.join(ROOT, "inv", "s21_oracle.csv")
ASSETS = os.path.join(ROOT, "assets")
EXTRACT = os.path.join(ROOT, "extract")
MATL_SRC = os.path.join(EXTRACT, "material")
MDL_ROOT = os.path.join(ROOT, "converted", "model_v17", "mdl")
SKIP = {"vers", "Ptch", "efct", "msnp", "asqd", "rson", "uiia", "rmap", "wrap",
        "cube", "llyr", "aseq", "arig", "stgs", "stlt"}
TYPE_RANK = {"mdl_": 0, "arig": 0.5, "dtbl": 1, "shdr": 2, "shds": 3, "txan": 4, "txtr": 5,
             "matl": 6}
REF_KEYS = ["$depthShadowMaterial", "$depthPrepassMaterial", "$depthVSMMaterial",
            "$depthShadowTightMaterial", "$colpassMaterial", "shaderSet", "$textureAnimation"]
JUNCTIONS = {
    "texture": os.path.join(EXTRACT, "texture"),
    "texture_s21": os.path.join(EXTRACT, "texture_s21"),
    "mdl": MDL_ROOT,
    "shader": os.path.join(EXTRACT, "shader_s21"),
    "shaderset": os.path.join(EXTRACT, "shaderset_s21"),
    "texture_anim": os.path.join(EXTRACT, "texture_anim"),
    "datatable": os.path.join(EXTRACT, "datatable"),
    "txtx": os.path.join(EXTRACT, "txtx"),
    # S21 rigs/sequences converted from the map's S30 arig/aseq/asqd by map_anims.py
    "animrig": os.path.join(EXTRACT, "anim_s21", "animrig"),
    "animseq": os.path.join(EXTRACT, "anim_s21", "animseq"),
}


# not loaded while a match runs, so a guid found only there must be packed
NON_RESIDENT_PAKS = ("mp_lobby",)


def load_oracle(path):
    out = set()
    if not os.path.isfile(path):
        sys.exit("oracle missing: " + path)
    for r in csv.DictReader(open(path, newline="", encoding="utf-8", errors="replace")):
        if r.get("file_name", "").lower().startswith(NON_RESIDENT_PAKS):
            continue
        try:
            out.add(int(r["guid"], 16))
        except Exception:
            continue
    return out


def conv_ref(v):
    if not isinstance(v, str) or not v:
        return v
    if v.lower().startswith("0x"):
        return v
    return "0x%016X" % string_to_guid(v.replace("\\", "/"))


def convert_material(src, dst):
    raw = open(src, encoding="utf-8").read()
    if not raw.strip():
        raise ValueError("empty")
    d = json.loads(raw)
    tex = d.get("$textures")
    if isinstance(tex, dict):
        for k in list(tex):
            tex[k] = conv_ref(tex[k])
    for key in REF_KEYS:
        if key in d:
            d[key] = conv_ref(d[key])
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    json.dump(d, open(dst, "w"), indent=1)


def setup_assets():
    os.makedirs(ASSETS, exist_ok=True)
    for name in JUNCTIONS:
        p = os.path.join(ASSETS, name)
        if os.path.isdir(p):
            subprocess.run(["cmd", "/c", "rmdir", p], capture_output=True)
    for name, target in JUNCTIONS.items():
        if not os.path.isdir(target):
            print("  no dir", name)
            continue
        r = subprocess.run(["cmd", "/c", "mklink", "/J", os.path.join(ASSETS, name), target],
                           capture_output=True, text=True)
        if r.returncode != 0:
            print("  mklink FAIL", name, (r.stderr or "").strip())
    existing = glob.glob(os.path.join(ASSETS, "material", "**", "*.json"), recursive=True)
    if len(existing) >= 5600:
        print("  materials already staged", len(existing))
        return
    n = bad = 0
    for src in glob.glob(os.path.join(MATL_SRC, "**", "*.json"), recursive=True):
        rel = os.path.relpath(src, MATL_SRC)
        try:
            convert_material(src, os.path.join(ASSETS, "material", rel))
        except Exception as ex:
            bad += 1
            if bad <= 8:
                print("  bad matl", rel, ex)
            continue
        uber = os.path.splitext(src)[0] + ".uber"
        if os.path.isfile(uber):
            shutil.copy2(uber, os.path.join(ASSETS, "material", os.path.splitext(rel)[0] + ".uber"))
        n += 1
    print("  materials converted", n, "bad", bad)


def load_mdl_paths():
    out = {}
    p = os.path.join(ROOT, "inv", "mdl_paths.txt")
    for line in open(p, encoding="utf-8"):
        guid, rel = line.split(None, 1)
        out[int(guid, 16)] = rel.strip().replace("\\", "/")
    return out


def tex_json(name):
    return os.path.join(EXTRACT, name + ".json")


def tex_dds(name):
    return os.path.join(EXTRACT, name + ".dds")


def synth_tex_meta(pairs):
    from synth_v10_tail import synth_v10_tail, build_slot_map
    from dds import read_dds_meta
    slot_map = build_slot_map([os.path.join(ASSETS, "material")])
    meta = {}
    for guid, name in pairs:
        dds = tex_dds(name)
        js = tex_json(name)
        if not os.path.isfile(dds):
            continue
        try:
            fmt, w, h, arr, mip_count = read_dds_meta(dds)
        except Exception:
            continue
        sm = om = 0
        try:
            sl = json.load(open(js, encoding="utf-8")).get("streamLayout", [])
            sm = sum(1 for x in sl if x == "mandatory")
            om = sum(1 for x in sl if x == "optional")
        except Exception:
            pass
        perm = max(0, mip_count - sm - om)
        gh = ("%x" % guid).lstrip("0") or "0"
        tail = synth_v10_tail(fmt=fmt, w=w, h=h, arr=arr, perm=perm, mand=sm, opt=om,
                              slot=slot_map.get(gh))
        meta[guid] = {"strm": sm, "opt": om, "tail": tail.hex()}
    return meta


def file_for(typ, path):
    rel = path.replace("/", os.sep)
    if typ == "txtr":
        return os.path.join(ASSETS, rel + ".dds")
    if typ in ("shdr", "shds"):
        return os.path.join(ASSETS, rel + ".msw")
    if typ == "mdl_":
        return os.path.join(ASSETS, rel)
    if typ == "matl":
        stem = rel[:-5] if rel.lower().endswith(".rpak") else rel
        return os.path.join(ASSETS, stem + ".json")
    if typ == "txan":
        return os.path.join(ASSETS, rel + ".txan")
    if typ == "dtbl":
        return os.path.join(ASSETS, rel + ".csv")
    if typ == "txtx":
        return os.path.join(ASSETS, rel)
    return os.path.join(ASSETS, rel)


def main():
    oracle = load_oracle(ORACLE)
    print("oracle guids:", len(oracle))
    mdl_paths = load_mdl_paths()
    print("setting up assets...")
    setup_assets()
    rows = list(csv.DictReader(open(INV, newline="", encoding="utf-8", errors="replace")))
    tex_pairs = []
    for r in rows:
        if r["type"] != "txtr":
            continue
        g = int(r["guid"], 16)
        if g in oracle:
            continue
        tex_pairs.append((g, r["asset_name"].replace("\\", "/")))
    print("synthesizing texture tails...")
    tex_meta = synth_tex_meta(tex_pairs)
    print("  tails", len(tex_meta))

    # a shaderset binding a shader nobody supplies would dangle, and so would every
    # material that uses it
    avail = {int(os.path.basename(p)[:-4], 16)
             for p in glob.glob(os.path.join(EXTRACT, "shader_s21", "*.msw"))} | oracle
    dead_ss = set()
    for p in glob.glob(os.path.join(EXTRACT, "shaderset_s21", "*.msw")):
        ps, vs = struct.unpack_from("<QQ", open(p, "rb").read(), 5)
        if any(s and s not in avail for s in (ps, vs)):
            dead_ss.add(int(os.path.basename(p)[:-4], 16))

    # animated props: repak packs every aseq named in $sequences; rigs are listed explicitly
    anim_json = os.path.join(ROOT, "inv", "anim_deps.json")
    anim = json.load(open(anim_json)) if os.path.isfile(anim_json) else {}
    files = []
    counts = {}
    skipped = {}
    absent = {}
    dropped = {}
    for r in rows:
        typ = r["type"]
        guid = int(r["guid"], 16)
        name = r["asset_name"].replace("\\", "/")
        if typ in SKIP or guid in oracle:
            skipped[typ] = skipped.get(typ, 0) + 1
            continue
        if typ == "shds" and guid in dead_ss:
            dropped[typ] = dropped.get(typ, 0) + 1
            continue
        if typ == "matl":
            mp = file_for(typ, name)
            try:
                ss = json.load(open(mp, encoding="utf-8")).get("shaderSet")
            except Exception:
                ss = None
            if ss and int(ss, 16) in dead_ss:
                dropped[typ] = dropped.get(typ, 0) + 1
                continue
        if typ == "mdl_":
            name = mdl_paths.get(guid)
            if not name:
                absent[typ] = absent.get(typ, 0) + 1
                continue
        path = file_for(typ, name)
        if typ in ("txtr", "shdr", "shds", "mdl_", "matl", "txan", "dtbl", "txtx") and not os.path.isfile(path):
            absent[typ] = absent.get(typ, 0) + 1
            continue
        e = {"_type": typ, "_path": name, "$guid": guid}
        if typ == "mdl_" and name in anim:
            if anim[name].get("animrigs"):
                e["$animrigs"] = anim[name]["animrigs"]
            if anim[name].get("sequences"):
                e["$sequences"] = anim[name]["sequences"]
        if typ == "txtr" and guid in tex_meta:
            m = tex_meta[guid]
            e["$hdrTail"] = m["tail"]
            if m["strm"] or m["opt"]:
                e["$strmMips"] = m["strm"]
                e["$optMips"] = m["opt"]
        files.append(e)
        counts[typ] = counts.get(typ, 0) + 1

    for rel, dep in sorted(anim.items()):
        if rel.startswith("animrig/"):
            if not os.path.isfile(os.path.join(ASSETS, rel)):
                sys.exit("converted rig missing: " + rel)
            files.append({"_type": "arig", "_path": rel, "$guid": string_to_guid(rel), "$sequences": dep["sequences"]})
            counts["arig"] = counts.get("arig", 0) + 1
        for s in dep.get("sequences", []):
            if not os.path.isfile(os.path.join(ASSETS, s)):
                sys.exit("converted sequence missing: " + s)
    missing_anim = [m for m in anim if m.startswith("mdl/") and m not in {f["_path"] for f in files if f["_type"] == "mdl_"}]
    if missing_anim:
        sys.exit("animated models not packed: %s" % missing_anim)

    # models here use materials that live in the source build's common paks
    extra_matl = os.path.join(ROOT, "inv", "extra_matl.txt")
    if os.path.isfile(extra_matl):
        have = {f["$guid"] for f in files}
        for line in open(extra_matl, encoding="utf-8"):
            if not line.strip():
                continue
            g, name = line.split(None, 1)
            g = int(g, 16); name = name.strip()
            if g in have or g in oracle:
                continue
            mp = file_for("matl", name)
            if not os.path.isfile(mp):
                absent["matl"] = absent.get("matl", 0) + 1
                continue
            ss = json.load(open(mp, encoding="utf-8")).get("shaderSet")
            if ss and int(ss, 16) in dead_ss:
                dropped["matl"] = dropped.get("matl", 0) + 1
                continue
            files.append({"_type": "matl", "_path": name, "$guid": g})
            counts["matl_common"] = counts.get("matl_common", 0) + 1

    # shadersets here bind shaders that live in the source build's common paks; when the
    # resident set lacks them they ship in this pak
    have = {f["$guid"] for f in files}
    for p in sorted(glob.glob(os.path.join(EXTRACT, "shader_s21", "*.msw"))):
        guid = int(os.path.basename(p)[:-4], 16)
        if guid in have or guid in oracle:
            continue
        files.append({"_type": "shdr", "_path": "shader/" + os.path.basename(p)[:-4], "$guid": guid})
        counts["shdr_common"] = counts.get("shdr_common", 0) + 1
        have.add(guid)
    for p in sorted(glob.glob(os.path.join(EXTRACT, "shaderset_s21", "*.msw"))):
        guid = int(os.path.basename(p)[:-4], 16)
        if guid in have or guid in oracle or guid in dead_ss:
            continue
        files.append({"_type": "shds", "_path": "shaderset/" + os.path.basename(p)[:-4], "$guid": guid})
        counts["shds_common"] = counts.get("shds_common", 0) + 1
        have.add(guid)

    # materials sample textures that live in the source build's common paks
    tex_refs = set()
    for f in files:
        if f["_type"] != "matl":
            continue
        try:
            tex = json.load(open(file_for("matl", f["_path"]), encoding="utf-8")).get("$textures") or {}
        except Exception:
            continue
        for v in tex.values():
            if isinstance(v, str) and v.lower().startswith("0x") and int(v, 16):
                tex_refs.add(int(v, 16))
    extra = [(g, "texture/0x%X" % g) for g in sorted(tex_refs - have - oracle)
             if os.path.isfile(tex_dds("texture/0x%X" % g))]
    # S21 retail textures imported for S21 materials keep their original tails
    for g in sorted(tex_refs - have - oracle - {e[0] for e in extra}):
        name = "texture_s21/0x%X" % g
        if not os.path.isfile(tex_dds(name)):
            continue
        js = json.load(open(tex_json(name), encoding="utf-8"))
        sl = js.get("streamLayout", [])
        e = {"_type": "txtr", "_path": name, "$guid": g, "$hdrTail": js["hdrTail"]}
        sm, om = sl.count("mandatory"), sl.count("optional")
        if sm or om:
            e["$strmMips"] = sm
            e["$optMips"] = om
        files.append(e)
        counts["txtr_s21"] = counts.get("txtr_s21", 0) + 1
        have.add(g)
    extra_meta = synth_tex_meta(extra)
    for g, name in extra:
        e = {"_type": "txtr", "_path": name, "$guid": g}
        m = extra_meta.get(g)
        if m:
            e["$hdrTail"] = m["tail"]
            if m["strm"] or m["opt"]:
                e["$strmMips"] = m["strm"]
                e["$optMips"] = m["opt"]
        files.append(e)
        counts["txtr_common"] = counts.get("txtr_common", 0) + 1
        have.add(g)
    unresolved = sorted(tex_refs - have - oracle)

    # S21 cubemap array + ambient buffer, built from the source `cube` assets by build_cubemaps.py
    cube_json = os.path.join(ROOT, "inv", "cubemaps.json")
    if not os.path.isfile(cube_json):
        sys.exit("inv/cubemaps.json missing: run build_cubemaps.py")
    for e in json.load(open(cube_json)):
        if e["$guid"] not in have:
            files.append(e)
            have.add(e["$guid"])
            counts["cubemap_" + e["_type"]] = counts.get("cubemap_" + e["_type"], 0) + 1

    files.sort(key=lambda f: (TYPE_RANK.get(f["_type"], 99), f["_path"]))
    manifest = {
        "version": 8,
        "name": MAP,
        "assetsDir": ASSETS.replace("\\", "/") + "/",
        "outputDir": os.path.join(ROOT, "out").replace("\\", "/") + "/",
        "headerFlags": 32,
        "streamFileMandatory": "paks\\Win64\\pc_all_desertlands_mu5.starpak",
        "streamFileOptional": "paks\\Win64\\pc_all_desertlands_mu5.opt.starpak",
        "files": files,
    }
    os.makedirs(os.path.join(ROOT, "out"), exist_ok=True)
    out = os.path.join(ROOT, MAP + ".json")
    json.dump(manifest, open(out, "w"), indent=1)
    print("wrote %s: %d assets" % (out, len(files)))
    print("by type:", dict(sorted(counts.items(), key=lambda kv: -kv[1])))
    print("skipped:", skipped)
    print("absent:", absent)
    print("dropped (dead shaderset):", dropped, "dead shadersets", len(dead_ss))
    if unresolved:
        sys.exit("unresolved texture refs: %d e.g. %s" % (len(unresolved), ["0x%X" % g for g in unresolved[:8]]))


if __name__ == "__main__":
    main()
