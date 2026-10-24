"""Find DXBC in the packed v15 shader entries and run the S21 converter.

Repak wrote a 56-byte header whose type byte is not the v14 layout the
shared scanner keys on, so that scanner reports zero blobs. The cpu page
still points at DXBC.
"""
import kitpaths as KP
import os
import shutil
import struct
import sys


import cb_remap
import dxbc

PAK = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                   "out", "mp_rr_desertlands_mu5.rpak")


def u16(d, o):
    return struct.unpack_from("<H", d, o)[0]


def u32(d, o):
    return struct.unpack_from("<I", d, o)[0]


def i32(d, o):
    return struct.unpack_from("<i", d, o)[0]


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


def blobs(d):
    assets_off, nasset, bases = pages_of(d)
    seen = {}
    order = []
    for i in range(nasset):
        ao = assets_off + i * 0x50
        if d[ao + 0x4C:ao + 0x50] != b"shdr":
            continue
        guid = struct.unpack_from("<Q", d, ao)[0]
        cidx, coff = struct.unpack_from("<ii", d, ao + 0x18)
        co = res(bases, cidx, coff)
        if co is None:
            continue
        # entries are 16 bytes (ptr, size, pad) until the pointer stops resolving
        for k in range(32):
            eo = co + k * 16
            if eo + 16 > len(d):
                break
            pi, po = struct.unpack_from("<ii", d, eo)
            dsz = u32(d, eo + 8)
            ptr = res(bases, pi, po)
            if ptr is None or d[ptr:ptr + 4] != b"DXBC":
                break
            tot = u32(d, ptr + 24)
            if not (32 < tot <= dsz + 64):
                tot = dsz
            if ptr not in seen and 32 < tot <= dsz + 64:
                seen[ptr] = (ptr, tot, guid)
                order.append(ptr)
    return [seen[p] for p in order]


def main():
    apply_ = "--apply" in sys.argv
    data = bytearray(open(PAK, "rb").read())
    found = blobs(bytes(data))
    n_patch = n_skip = n_size = 0
    passes = {}
    for off, sz, guid in found:
        blob = bytes(data[off:off + sz])
        patched, report = cb_remap.convert_blob(blob)
        if not report.get("patched"):
            n_skip += 1
            continue
        if report.get("size_changed") or len(patched) != sz:
            n_size += 1
            print("SIZE-CHANGED %016x %d->%d" % (guid, sz, len(patched)))
            continue
        if not dxbc.verify_hash(patched):
            raise SystemExit("checksum fail %016x" % guid)
        data[off:off + sz] = patched
        n_patch += 1
        for k, v in report.items():
            if isinstance(v, dict) and v.get("patched"):
                passes[k] = passes.get(k, 0) + 1
    print("blobs=%d patched=%d untouched=%d size_changed=%d" % (len(found), n_patch, n_skip, n_size))
    print("per-pass:", passes)
    if n_size:
        raise SystemExit("size-changed blobs -- not applied")
    if apply_ and n_patch:
        bak = PAK + ".pre_cbremap"
        if not os.path.isfile(bak):
            shutil.copy2(PAK, bak)
        open(PAK, "wb").write(data)
        print("APPLIED")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
