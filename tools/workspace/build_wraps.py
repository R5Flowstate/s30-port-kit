#!/usr/bin/env python3
"""Stage the BSP for both engines and emit the two client wrap manifests.

  stage     bsp/extract (RSX)      -> bsp/work_client, bsp/work_dedi   (.client stripped)
  convert   bspconv                -> bsp/client_v51,  bsp/dedi_v47
  assemble  converted client lumps -> wrap_build/{perm,temp}           (.client restored)

Which lump lands in perm vs temp is decided by the SOURCE pak it came from
(inv/perm.csv, inv/temp.csv) -- lump 0x2A is perm, 0x2B is temp, and getting that
backwards moves the cubemap ambient buffer into the wrong pak.
"""
import kitpaths as KP
import csv
import json
import os
import shutil
import subprocess
import sys

MAP = "mp_rr_desertlands_mu5"
ROOT = os.path.dirname(os.path.abspath(__file__))
BSPCONV = (KP.BSPCONV + r"\x64\Release\bspconv.exe")
EXTRACT = os.path.join(ROOT, "bsp", "extract", "wrap")
LOGS = os.path.join(ROOT, "logs")


def wrap_names(csv_path):
    out = []
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        for r in csv.DictReader(f):
            if r["type"] == "wrap":
                out.append(r["asset_name"].replace("\\", "/"))
    return out


def stage(names, dst):
    """copy the extracted wrap files into dst, stripping any .client suffix"""
    shutil.rmtree(dst, ignore_errors=True)
    n = 0
    for name in names:
        src = os.path.join(EXTRACT, name.replace("/", os.sep))
        if not os.path.isfile(src):
            print("  MISSING", name)
            continue
        rel = name[:-len(".client")] if name.endswith(".client") else name
        out = os.path.join(dst, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(src, out)
        n += 1
    return n


def run_bspconv(work, outdir, dedi, log):
    args = [BSPCONV, os.path.join(work, "maps", MAP + ".bsp")]
    if dedi:
        args.append("-dedi")
    args += ["-out", outdir + os.sep]
    with open(os.path.join(LOGS, log), "w") as f:
        rc = subprocess.run(args, stdout=f, stderr=subprocess.STDOUT).returncode
    print("  bspconv %s rc=%d" % ("dedi" if dedi else "client", rc))
    return rc


def header_filelen(conv_dir, base):
    """filelen from the converted bsp header, or None if this name is not a lump."""
    mark = ".bsp."
    i = base.find(mark)
    if i < 0 or not base.endswith(".bsp_lump"):
        return None
    hexpart = base[i + len(mark):].split(".", 1)[0]
    try:
        slot = int(hexpart, 16)
    except ValueError:
        return None
    hdr_path = os.path.join(conv_dir, base[:i] + ".bsp")
    if not os.path.isfile(hdr_path):
        return None
    with open(hdr_path, "rb") as f:
        f.seek(16 + slot * 16 + 4)
        return int.from_bytes(f.read(4), "little")


def assemble(names, conv_dir, dst):
    """copy converted lumps back under their in-pak names (.client restored)"""
    shutil.rmtree(dst, ignore_errors=True)
    missing, unconverted = [], []
    for name in names:
        rel = name[:-len(".client")] if name.endswith(".client") else name
        base = os.path.basename(rel)
        src = os.path.join(conv_dir, base)
        if not os.path.isfile(src):
            # A zero filelen is the converter deleting the lump. Do not put
            # the retail bytes back under a header that no longer points at them.
            if header_filelen(conv_dir, base) == 0:
                print("  dropped (header filelen 0):", name)
                continue
            if ".bsp" in base:
                unconverted.append(name)
            src = os.path.join(EXTRACT, name.replace("/", os.sep))
            if not os.path.isfile(src):
                missing.append(name)
                continue
        out = os.path.join(dst, name.replace("/", os.sep))
        os.makedirs(os.path.dirname(out), exist_ok=True)
        shutil.copy2(src, out)
    for n in unconverted:
        print("  UNCONVERTED (source bytes used):", n)
    return missing


def manifest(name, assets_dir, files, out):
    m = {"version": 8, "keepDevOnly": False, "name": name,
         "assetsDir": assets_dir.replace("\\", "/") + "/",
         "outputDir": os.path.join(ROOT, "wrap_build", "paks", "Win64").replace("\\", "/") + "/",
         "compressLevel": 0, "compressWorkers": 8, "headerFlags": 32, "files": files}
    json.dump(m, open(out, "w"), indent=1)
    print("  wrote %s (%d assets)" % (out, len(files)))


def main():
    conv_override = sys.argv[sys.argv.index("--assemble-only") + 1] if "--assemble-only" in sys.argv else None
    perm = wrap_names(os.path.join(ROOT, "inv", "perm.csv"))
    temp = wrap_names(os.path.join(ROOT, "inv", "temp.csv"))
    print("wrap assets: perm %d, temp %d" % (len(perm), len(temp)))

    if conv_override:
        return write_wraps(perm, temp, os.path.join(ROOT, "bsp", conv_override))
    work_c = os.path.join(ROOT, "bsp", "work_client")
    work_d = os.path.join(ROOT, "bsp", "work_dedi")
    print("staging client")
    print("  staged", stage(perm + temp, work_c))
    print("staging dedi")
    print("  staged", stage(perm + temp, work_d))

    if run_bspconv(work_c, os.path.join(ROOT, "bsp", "client_v51"), False, "bspconv_client.log"):
        return 1
    if run_bspconv(work_d, os.path.join(ROOT, "bsp", "dedi_v47"), True, "bspconv_dedi.log"):
        return 1

    return write_wraps(perm, temp, os.path.join(ROOT, "bsp", "client_v51"))


def minimap(dst):
    """The overviews KeyValues (wrap) and the minimap image (uiia) the client reads at level load."""
    src = os.path.join(ROOT, "extract", "minimap")
    kv = os.path.join(src, MAP + ".txt")
    img = [f for f in os.listdir(src) if f.endswith(".uiia")] if os.path.isdir(src) else []
    if not os.path.isfile(kv) or len(img) != 1:
        sys.exit("minimap: need extract/minimap/%s.txt and exactly one .uiia" % MAP)
    guid = int(img[0][2:-5], 16)
    os.makedirs(os.path.join(dst, "resource", "overviews"), exist_ok=True)
    os.makedirs(os.path.join(dst, "ui_image", "ui_image"), exist_ok=True)
    shutil.copy2(kv, os.path.join(dst, "resource", "overviews", MAP + ".txt"))
    shutil.copy2(os.path.join(src, img[0]), os.path.join(dst, "ui_image", "ui_image", "0x%X.rpak" % guid))
    return [{"_type": "wrap", "_path": "resource\\overviews\\%s.txt" % MAP},
            {"_type": "uiia", "_path": "ui_image/ui_image/0x%X.rpak" % guid, "$guid": guid}]


def map_effects(dst):
    """The map's own particle effects (S30 efct converted to v16 by efct_port convert.py into extract/efct).
    They ride in perm, which loads after the main pak, so their material refs resolve there."""
    src = os.path.join(ROOT, "extract", "efct")
    man = os.path.join(src, "_manifest.json")
    if not os.path.isfile(man):
        return []
    out = os.path.join(dst, "effect")
    if os.path.isdir(out):
        shutil.rmtree(out)
    shutil.copytree(os.path.join(src, "effect"), out)
    files = [{"_type": e["_type"], "_path": e["_path"], "$guid": e["$guid"]} for e in json.load(open(man))]
    print("  perm: %d map effects" % len(files))
    return files


def write_wraps(perm, temp, conv):
    for label, names in (("perm", perm), ("temp", temp)):
        dst = os.path.join(ROOT, "wrap_build", label)
        miss = assemble(names, conv, dst)
        if miss:
            print("  %s: %d assets with no source" % (label, len(miss)))
            for m in miss:
                print("     ", m)
        files = [{"_type": "wrap", "_path": n.replace("/", "\\")} for n in names
                 if os.path.isfile(os.path.join(dst, n.replace("/", os.sep)))]
        if label == "perm":
            files += minimap(dst)
            files += map_effects(dst)
        manifest("%s_client_%s" % (MAP, label), dst, files,
                 os.path.join(ROOT, "wrap_build", "%s_client_%s.json" % (MAP, label)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
