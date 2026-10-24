"""Map each mdl_ guid to the on-disk path under converted/model_v17."""
import kitpaths as KP
import os
import struct
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))

from string_to_guid import string_to_guid

DEC = os.path.join(ROOT, "inv", "mp_rr_desertlands_mu5.rpak.dec.rpak")
OUT = os.path.join(ROOT, "converted", "model_v17")


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


def main():
    d = open(DEC, "rb").read()
    assets_off, nasset, bases = pages_of(d)
    rows = []
    missing = hash_miss = 0
    for i in range(nasset):
        ao = assets_off + i * 0x50
        if d[ao + 0x4C:ao + 0x50] != b"mdl_":
            continue
        guid = struct.unpack_from("<Q", d, ao)[0]
        h = res(bases, *struct.unpack_from("<ii", d, ao + 0x10))
        if h is None:
            missing += 1
            continue
        ns = res(bases, *struct.unpack_from("<ii", d, h + 8))
        if ns is None:
            missing += 1
            continue
        rel = d[ns:ns + 240].split(b"\x00", 1)[0].decode("latin1", "replace").replace("\\", "/")
        if not rel.endswith(".rmdl"):
            rel += ".rmdl"
        disk = os.path.join(OUT, rel.replace("/", os.sep))
        if not os.path.isfile(disk):
            missing += 1
            continue
        if string_to_guid(rel) != guid:
            hash_miss += 1
        rows.append((guid, rel))
    path = os.path.join(ROOT, "inv", "mdl_paths.txt")
    with open(path, "w", encoding="utf-8") as f:
        for guid, rel in rows:
            f.write("%016x %s\n" % (guid, rel))
    print("mapped %d missing %d hash_mismatch %d" % (len(rows), missing, hash_miss))
    return 0 if rows and not missing else 1


if __name__ == "__main__":
    sys.exit(main())
