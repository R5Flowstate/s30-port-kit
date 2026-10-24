"""S30 map animations (arig v7, aseq v13/v14, asqd v1) -> S3 dedi (arig v4 / aseq v7) and S21 client (v17 / v11).

Without these, every animated prop ships with only its reference pose and the dedi Host_Errors the moment a
script plays a sequence (survival doors: 'Anim Sequence "open" not found for model ...').

1. RSX exports the map pak's arig/aseq (RSX >= aseq v14 support).
2. asqd blobs are dumped from the decompressed pak by LAYOUT (next blob in the same page / page end): RSX sizes
   asqd by a bone walk that truncates S30 clips (door open: 66 B of 5276) and skips clips whose owner is a
   model it cannot parse. Blobs referenced from other S30 paks are looked up in --extra-dec paks the same way.
3. Each animated model's rig / sequence deps come from its pak asset uses list (inv/mdl_paths.txt names it);
   self-rigged models get a .rson so R5-AnimConv uses the S30 model itself as the rig.
4. R5-AnimConv -i 30 -o 3 -> dedi_work/anim_s3, -o 21 -> extract/anim_s21; inv/anim_deps.json records
   {model path: {"animrigs": [...], "sequences": [...]}, rig path: {"sequences": [...]}} for stage.py and
   gen_full_manifest.py.
usage: map_anims.py <map> [--extra-dec <other S30 .dec.rpak> ...]
"""
import kitpaths as KP
import argparse, glob, json, mmap, os, shutil, struct, subprocess, sys


import pak_msw as P
from string_to_guid import string_to_guid as H

RSX = (KP.RSX + r"\bin\Release\rsx.exe")
ANIMCONV = (KP.ANIMCONV + r"\bin\Release_x64\R5-AnimConv.exe")
S30 = (KP.S30_GAME + r"\paks\Win64")
ROOT = KP.MAPS


def open_pak(path):
    f = open(path, "rb")
    m = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
    pages, ao0, ac, relocs, end = P._parse(m, False)
    if end != len(m):
        pages, ao0, ac, relocs, end = P._parse(m, True)
    return m, pages, ao0, ac


def asset_iter(pak, fourcc):
    m, pages, ao0, ac = pak
    fcc = struct.unpack("<I", fourcc)[0]
    for i in range(ac):
        ao = ao0 + i * 80
        if struct.unpack_from("<I", m, ao + 0x4C)[0] == fcc:
            yield ao


def asqd_blobs(pak):
    """guid -> bytes, sized by layout."""
    m, pages, ao0, ac = pak
    blobs = []
    for ao in asset_iter(pak, b"asqd"):
        h = P._res(pages, *struct.unpack_from("<ii", m, ao + 0x10))
        pg, off = struct.unpack_from("<ii", m, h)
        blobs.append((pg, off, struct.unpack_from("<Q", m, ao)[0]))
    blobs.sort()
    out = {}
    for k, (pg, off, g) in enumerate(blobs):
        nxt = blobs[k + 1] if k + 1 < len(blobs) and blobs[k + 1][0] == pg else None
        page_end = (pages[pg + 1] - pages[pg]) if pg + 1 < len(pages) else len(m) - pages[pg]
        out[g] = (pages[pg] + off, pages[pg] + (nxt[1] if nxt else page_end))
    return out


def uses(pak, ao):
    m, pages, ao0, ac = pak
    base = ao0 + ac * 80
    ui, uc = struct.unpack_from("<I", m, ao + 0x38)[0], struct.unpack_from("<I", m, ao + 0x40)[0]
    for k in range(uc):
        pg, off = struct.unpack_from("<II", m, base + (ui + k) * 8)
        yield struct.unpack_from("<Q", m, P._res(pages, pg, off))[0]


def run(cmd, log):
    with open(log, "w") as f:
        r = subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    if r.returncode:
        sys.exit("failed (%d): %s -- see %s" % (r.returncode, " ".join(cmd), log))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("map")
    ap.add_argument("--extra-dec", nargs="*", default=[])
    a = ap.parse_args()
    ws = os.path.join(ROOT, a.map)
    work = os.path.join(ws, "dedi_work", "anim_src")
    shutil.rmtree(work, ignore_errors=True)
    os.makedirs(work)
    run([RSX, "-nogui", "-nocachedb", "-export", "-exportfullpaths", "--exporttypes", "arig,aseq",
         "--exportdir", work, os.path.join(S30, a.map + ".rpak")], os.path.join(work, "rsx.log"))
    names = {}
    for f in glob.glob(os.path.join(work, "anim*", "**", "*.r*"), recursive=True):
        if f.endswith((".rseq", ".rrig")):
            rel = os.path.relpath(f, work).replace(os.sep, "/")
            names[H(rel)] = rel
    pak = open_pak(os.path.join(ws, "inv", a.map + ".rpak.dec.rpak"))
    need = set()
    for f in glob.glob(os.path.join(work, "animseq", "**", "*.json"), recursive=True):
        for dep in json.load(open(f)).get("dependencies", []):
            if dep.startswith("animseq_data/"):
                need.add(int(dep.split("/")[-1], 16))
            elif "/" not in dep and all(c in "0123456789abcdefABCDEF" for c in dep):
                need.add(int(dep, 16))   # asqd from another pak: RSX writes the bare guid
    os.makedirs(os.path.join(work, "animseq_data"), exist_ok=True)
    have = set()
    for src in [pak] + [open_pak(p) for p in a.extra_dec]:
        for g, (s, e) in asqd_blobs(src).items():
            if g in need and g not in have:
                open(os.path.join(work, "animseq_data", "0x%X.asqd" % g), "wb").write(src[0][s:e])
                have.add(g)
    if need - have:
        sys.exit("asqd not found in any pak: %s (pass the owning S30 pak with --extra-dec)" % ["0x%X" % g for g in sorted(need - have)])
    mdl_paths = {int(k, 16): v.strip() for k, v in (l.split(None, 1) for l in open(os.path.join(ws, "inv", "mdl_paths.txt")).read().splitlines() if l.strip())}
    deps = {}
    for ao in asset_iter(pak, b"mdl_"):
        g = struct.unpack_from("<Q", pak[0], ao)[0]
        refs = [names[u] for u in uses(pak, ao) if u in names]
        if not refs:
            continue
        mp = mdl_paths.get(g, "")
        if not mp:
            sys.exit("animated model 0x%X has no inv/mdl_paths.txt name" % g)
        rigs = sorted(r for r in refs if r.startswith("animrig/"))
        seqs = sorted(r for r in refs if r.startswith("animseq/"))
        deps[mp] = {"animrigs": rigs, "sequences": seqs}
        if seqs and not rigs:
            dst = os.path.join(work, mp)
            os.makedirs(os.path.dirname(dst), exist_ok=True)
            shutil.copyfile(os.path.join(ws, "mdl_in", mp), dst)
            open(dst[:-5] + ".rson", "w").write("seqs:\n[\n" + "".join("\t%s\n" % s.replace("/", "\\") for s in seqs) + "]\n")
    for f in glob.glob(os.path.join(work, "animrig", "**", "*.rson"), recursive=True):
        rel = os.path.relpath(f, work).replace(os.sep, "/")[:-5] + ".rrig"
        seqs = []
        for l in open(f):
            e = l.strip().replace("\\", "/")
            if not e.startswith("animseq"):
                continue
            tail = e.split("/")[-1]
            seqs.append(names[int(tail, 16)] if tail.lower().startswith("0x") else e)
        deps[rel] = {"sequences": sorted(seqs)}
    for out, tgt in ((os.path.join(ws, "dedi_work", "anim_s3"), "3"), (os.path.join(ws, "extract", "anim_s21"), "21")):
        shutil.rmtree(out, ignore_errors=True)
        run([ANIMCONV, work, "-i", "30", "-o", tgt, "-outpath", out, "-nopause", "-ne"], out + ".log")
        produced = {os.path.relpath(f, out).replace(os.sep, "/") for f in glob.glob(os.path.join(out, "**", "*.r*"), recursive=True)}
        missing = sorted({p for d in deps.values() for p in d.get("sequences", []) + d.get("animrigs", [])} - produced)
        if missing:
            sys.exit("R5-AnimConv -o %s did not produce: %s" % (tgt, missing))
    json.dump(deps, open(os.path.join(ws, "inv", "anim_deps.json"), "w"), indent=1, sort_keys=True)
    print("animated models/rigs", len(deps), "asqd", len(have), "->", os.path.join(ws, "inv", "anim_deps.json"))


if __name__ == "__main__":
    main()
