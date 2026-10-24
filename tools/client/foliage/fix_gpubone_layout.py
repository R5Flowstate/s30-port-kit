#!/usr/bin/env python3
"""Rotate per-model GPU-bone (foliage sway) records from the S25/S26 field
order into the S21 field order, in place.

The S21 engine copies each model's GPU-bone blob RAW into the g_gpuBones
structured buffer (numGpuBones @ studiohdr+0xDA, FIX_OFFSET gpuBoneOffset @
+0xDC, 32 bytes per record) and both consumers -- the retail
foliage_prepass_cs wind compute shader and the (post-P10) foliage VS --
expect the S21 record layout:

    S21     : { packed u32 @0, sway pivot float3 @4, quat float4 @16 }
    S25/S26 : { sway pivot float3 @0, packed u32 @12, quat float4 @16 }

so a ported model feeds the wind pass a pivot of (pivot.y, pivot.z, packed)
and the vertices are displaced out of the frustum: foliage invisible from
most angles, flickering while the camera is still. Proven by pairing the 41
same-name models of retail mp_rr_district against the MU1 port: every record
of all 41 is EXACTLY rec[12:16]+rec[0:12]+rec[16:32] of its twin.

The transform is that byte rotation of the first 16 bytes of each record.
Size-preserving, idempotent via a per-model layout classifier (a packed u32
read as a float is denormal garbage; a real pivot component is 0 or a sane
world coordinate), unanimity required per model or the model is skipped loud.

    py fix_gpubone_layout.py <pak-or-mdl-dir> [...] [--apply] [--filter=sub]

Backup: <pak>.pre_gpubone_layout (first apply only). Model dirs are patched
file-by-file with no backup -- re-copy from extract/ to undo.
"""
from __future__ import annotations

import collections
import os
import struct
import sys

import kitpaths as KP  # noqa: F401
import patch_shdr_cbremap as P  # noqa: E402

u32, i32 = P.u32, P.i32
FCC_MDL = struct.unpack("<I", b"mdl_")[0]
REC = 32


def fix_offset(o):
    return (o & 0xFFFE) << (4 * (o & 1))


def plausible_pivot(b):
    ok = 0
    for i in range(3):
        f = struct.unpack_from("<f", b, 4 * i)[0]
        if f != f or abs(f) > 1.0e6:
            return False
        if f == 0.0 or 1.0e-6 < abs(f):
            ok += 1
    return ok == 3


def classify_rec(rec):
    """'s21', 's26', or None when the rotation is a no-op / undecidable."""
    if rec[0:16] == rec[12:16] + rec[0:12]:
        return None
    s21 = plausible_pivot(rec[4:16])
    s26 = plausible_pivot(rec[0:12])
    p21 = rec[2] == 0 and rec[3] == 0
    p26 = rec[14] == 0 and rec[15] == 0
    if s21 and p21 and not (s26 and p26):
        return "s21"
    if s26 and p26 and not (s21 and p21):
        return "s26"
    return None


def pak_models(pak):
    d = bytearray(open(pak, "rb").read())
    best = None
    for al in (False, True):
        try:
            pages, ao0, ac, rel = P.parse(bytes(d), aligned=al)
        except Exception:
            continue
        n = sum(1 for i in range(ac) if u32(d, ao0 + i * 80 + 0x4C) == FCC_MDL)
        if best is None or n > best[0]:
            best = (n, pages, ao0, ac)
    _, pages, ao0, ac = best
    out = []
    for i in range(ac):
        ao = ao0 + i * 80
        if u32(d, ao + 0x4C) != FCC_MDL:
            continue
        ho = P.res(pages, i32(d, ao + 0x10), i32(d, ao + 0x14))
        if ho is None:
            continue
        no = P.res(pages, i32(d, ho + 0x08), i32(d, ho + 0x0C))
        nm = "?" if no is None else d[no:d.index(b"\x00", no)].decode("ascii", "replace")
        so = P.res(pages, i32(d, ho + 0x00), i32(d, ho + 0x04))
        if so is None or so + 0xE0 > len(d):
            continue
        out.append((nm, so))
    return d, out


def dir_models(root):
    for dp, _dn, fn in os.walk(root):
        for f in fn:
            if f.endswith(".rmdl"):
                p = os.path.join(dp, f)
                yield os.path.relpath(p, root).replace("\\", "/"), p


def fix_blob(d, so, nm, tally, examples):
    n = struct.unpack_from("<h", d, so + 0xDA)[0]
    off = struct.unpack_from("<H", d, so + 0xDC)[0]
    if n <= 0 or off == 0:
        tally["nogpubones"] += 1
        return False
    base = so + fix_offset(off)
    if base + REC * n > len(d):
        tally["OOB"] += 1
        examples.append((nm, "records out of bounds"))
        return False
    votes = collections.Counter()
    for i in range(n):
        v = classify_rec(bytes(d[base + REC * i:base + REC * i + REC]))
        if v:
            votes[v] += 1
    if not votes:
        tally["noop"] += 1
        return False
    if len(votes) > 1:
        tally["MIXED"] += 1
        examples.append((nm, "mixed votes %s" % dict(votes)))
        return False
    if votes.most_common(1)[0][0] == "s21":
        tally["already-s21"] += 1
        return False
    for i in range(n):
        r = base + REC * i
        d[r:r + 16] = d[r + 12:r + 16] + d[r:r + 12]
    tally["rotated"] += 1
    return True


def main():
    apply_ = "--apply" in sys.argv
    filt = None
    targets = []
    for a in sys.argv[1:]:
        if a == "--apply":
            continue
        if a.startswith("--filter="):
            filt = a[9:].lower()
        else:
            targets.append(a)
    for tgt in targets:
        tally = collections.Counter()
        examples = []
        if os.path.isdir(tgt):
            changed = 0
            for nm, p in dir_models(tgt):
                if filt and filt not in nm.lower():
                    continue
                d = bytearray(open(p, "rb").read())
                if len(d) < 0xE0:
                    continue
                if fix_blob(d, 0, nm, tally, examples) and apply_:
                    open(p, "wb").write(d)
                    changed += 1
            print("\n%s  %s  %s" % (tgt, dict(tally),
                                    "APPLIED %d files" % changed if apply_ else "dry run"))
        else:
            d, mods = pak_models(tgt)
            changed = 0
            for nm, so in mods:
                if filt and filt not in nm.lower():
                    continue
                if fix_blob(d, so, nm, tally, examples):
                    changed += 1
            print("\n%s  models=%d  %s" % (os.path.basename(tgt), len(mods), dict(tally)))
            if apply_ and changed:
                bak = tgt + ".pre_gpubone_layout"
                if not os.path.exists(bak):
                    os.replace(tgt, bak)
                    open(tgt, "wb").write(d)
                else:
                    open(tgt, "wb").write(d)
                print("   APPLIED %d models (backup %s)" % (changed, os.path.basename(bak)))
            elif changed:
                print("   dry run: %d models would rotate" % changed)
        for nm, why in examples[:8]:
            print("   !! %-60s %s" % (nm[-60:], why))


if __name__ == "__main__":
    main()
