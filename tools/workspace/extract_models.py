#!/usr/bin/env python3
"""Pull mdl_ v20 bodies out of the decompressed map pak.

The pak wrapper is the 96-byte header. The body is the S30 studiohdr
(-v19s30). Vertices live in pc_all.starpak as a stored slice; rmdlconv
decompresses that slice, so the .vg written here is the raw entry.
"""
import kitpaths as KP
import os
import struct
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
DEC = os.path.join(ROOT, "inv", "mp_rr_desertlands_mu5.rpak.dec.rpak")
STAR = (KP.S30_GAME + r"\paks\Win64\pc_all.starpak")
OUT = os.path.join(ROOT, "mdl_in")


def load_starpak_index(path):
    sz = os.path.getsize(path)
    with open(path, "rb") as f:
        f.seek(sz - 8)
        count = struct.unpack("<Q", f.read(8))[0]
        f.seek(sz - 8 - count * 16)
        raw = f.read(count * 16)
    idx = {}
    for i in range(count):
        off, size = struct.unpack_from("<QQ", raw, i * 16)
        if size:
            idx[off] = size
    return idx


def pages_of(d):
    sp, opt = struct.unpack_from("<hh", d, 0x48)
    nseg, npg = struct.unpack_from("<HH", d, 0x4C)
    nptr, nasset, nguid, ndep = struct.unpack_from("<iiii", d, 0x54)
    nextern, extsz = struct.unpack_from("<ii", d, 0x64)
    u74, u78 = struct.unpack_from("<II", d, 0x74)
    pages_off = 0x80 + sp + opt + nseg * 16
    sizes = [struct.unpack_from("<IIi", d, pages_off + i * 12)[2] for i in range(npg)]
    assets_off = pages_off + npg * 12 + nptr * 8
    fo = assets_off + nasset * 0x50 + nguid * 8 + ndep * 4 + nextern * 4 + extsz + u74 + u78
    bases = []
    for sz in sizes:
        bases.append(fo)
        fo += sz
    return assets_off, nasset, bases


def res(bases, idx, off):
    if idx < 0 or idx >= len(bases):
        return None
    return bases[idx] + off


def safe_rel(name):
    name = name.replace("\\", "/").split("\x00", 1)[0].strip()
    if not name or name.startswith("/") or ".." in name.split("/"):
        return None
    return name


def main():
    print("reading pak")
    d = open(DEC, "rb").read()
    assets_off, nasset, bases = pages_of(d)
    print("reading starpak index")
    sp_idx = load_starpak_index(STAR)
    star = open(STAR, "rb")

    jobs = []
    seen = {}
    n_mdl = n_vg = n_phy = n_static = n_skip = 0
    for i in range(nasset):
        ao = assets_off + i * 0x50
        if d[ao + 0x4C:ao + 0x50] != b"mdl_":
            continue
        n_mdl += 1
        guid = struct.unpack_from("<Q", d, ao)[0]
        hidx, hoff = struct.unpack_from("<ii", d, ao + 0x10)
        cidx, coff = struct.unpack_from("<ii", d, ao + 0x18)
        h = res(bases, hidx, hoff)
        cpu = res(bases, cidx, coff)
        if h is None or cpu is None:
            n_skip += 1
            continue
        ni, no = struct.unpack_from("<ii", d, h + 8)
        ns = res(bases, ni, no)
        rel = safe_rel(d[ns:ns + 240].split(b"\x00", 1)[0].decode("latin1", "replace")) if ns is not None else None
        if not rel:
            rel = "mdl/unnamed/%016x.rmdl" % guid
        if not rel.endswith(".rmdl"):
            rel += ".rmdl"
        if rel in seen:
            stem, ext = os.path.splitext(rel)
            rel = "%s_%016x%s" % (stem, guid, ext)
        seen[rel] = guid
        si, so = struct.unpack_from("<ii", d, h)
        body = res(bases, si, so)
        ds_phys, ds_mdl = struct.unpack_from("<ii", d, cpu + 8)
        if body is None or ds_mdl <= 0 or body + ds_mdl > len(d):
            n_skip += 1
            continue
        pi, po = struct.unpack_from("<ii", d, cpu)
        phy_at = res(bases, pi, po) if ds_phys > 0 else None
        raw_sp = struct.unpack_from("<q", d, ao + 0x20)[0]
        vg = None
        if raw_sp != -1:
            sp_off = raw_sp & ~0xFFF
            vg = sp_idx.get(sp_off)
            if vg is not None:
                vg = (sp_off, vg)
        st_i, st_o = struct.unpack_from("<ii", d, h + 0x18)
        st_sz = struct.unpack_from("<I", d, h + 0x2C)[0]
        st_at = res(bases, st_i, st_o) if st_sz else None
        jobs.append((rel, body, ds_mdl, phy_at, ds_phys, vg, st_at, st_sz))

    print("models %d jobs %d skip %d" % (n_mdl, len(jobs), n_skip))
    os.makedirs(OUT, exist_ok=True)
    # starpak reads in offset order; bodies come from the already-loaded pak
    vg_jobs = [(j[5][0], n) for n, j in enumerate(jobs) if j[5]]
    vg_jobs.sort()
    vg_blobs = {}
    for off, n in vg_jobs:
        size = jobs[n][5][1]
        star.seek(off)
        vg_blobs[n] = star.read(size)
        n_vg += 1
    star.close()

    for n, (rel, body, ds_mdl, phy_at, ds_phys, vg, st_at, st_sz) in enumerate(jobs):
        dest = os.path.join(OUT, rel.replace("/", os.sep))
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        with open(dest, "wb") as f:
            f.write(d[body:body + ds_mdl])
        if phy_at is not None and phy_at + ds_phys <= len(d):
            with open(os.path.splitext(dest)[0] + ".phy", "wb") as f:
                f.write(d[phy_at:phy_at + ds_phys])
            n_phy += 1
        if n in vg_blobs:
            with open(os.path.splitext(dest)[0] + ".vg", "wb") as f:
                f.write(vg_blobs[n])
        if st_at is not None and st_sz and st_at + st_sz <= len(d):
            with open(os.path.splitext(dest)[0] + ".vg_static", "wb") as f:
                f.write(d[st_at:st_at + st_sz])
            n_static += 1
    print("wrote rmdl %d vg %d phy %d vg_static %d -> %s" % (len(jobs), n_vg, n_phy, n_static, OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
