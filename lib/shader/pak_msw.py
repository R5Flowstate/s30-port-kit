#!/usr/bin/env python3
"""Recover an RSX-compatible MSW for a shdr/shds asset straight out of a pak.

A shader shared between sibling MAP paks (rather than living in common) is
packed by each map that uses it, so a port that needs one has no export on disk
and RSX must re-parse every loaded pak to produce one. Everything Repak's MSW
reader consumes is recoverable from the built asset, so read it directly.

Round-trip gate: extracting 0x61597065D75EAA3B out of the composite pak Repak
built from RSX's own export reproduces that export byte for byte.

A v15 shdr with type > 5 is a CHILD: no bytecode, just the parent guid at +0x18.
It has no MSW at all -- pack it from the manifest with "$parentShader".
"""
from __future__ import annotations

import os
import struct
import sys

u16 = lambda b, o: struct.unpack_from("<H", b, o)[0]
u32 = lambda b, o: struct.unpack_from("<I", b, o)[0]
i32 = lambda b, o: struct.unpack_from("<i", b, o)[0]
u64 = lambda b, o: struct.unpack_from("<Q", b, o)[0]

FCC_SHDR = struct.unpack("<I", b"shdr")[0]
FCC_SHDS = struct.unpack("<I", b"shds")[0]
MSW_MAGIC = ord("M") | (ord("S") << 8) | (ord("W") << 16)
MSW_VER = 3
DESC_SIZE = 24

_CACHE = {}


def _parse(d, aligned):
    sp = u16(d, 0x48); opt = u16(d, 0x4A); slabs = u16(d, 0x4C); pc = u16(d, 0x4E)
    ptrc = u32(d, 0x54); ac = u32(d, 0x58); usec = u32(d, 0x5C); depc = u32(d, 0x60)
    u7 = u32(d, 0x64); u8 = u32(d, 0x68); e74 = u32(d, 0x74)
    pages_off = 0x80 + sp + opt + slabs * 16
    desc_off = pages_off + pc * 12
    assets_off = desc_off + ptrc * 8
    pdata = assets_off + ac * 80 + usec * 8 + depc * 4 + u7 * 4 + u8 + e74
    pages = []
    fo = pdata
    for i in range(pc):
        al = u32(d, pages_off + i * 12 + 4)
        ds = i32(d, pages_off + i * 12 + 8)
        if aligned and al:
            fo = (fo + al - 1) // al * al
        pages.append(fo)
        fo += ds
    relocs = set()
    for i in range(ptrc):
        relocs.add((u32(d, desc_off + i * 8), u32(d, desc_off + i * 8 + 4)))
    return pages, assets_off, ac, relocs, fo


def load(pak):
    """(bytes, pages, assetsOff, assetCount, relocSet). Repak writes pages back
    to back; a pak that does not is re-parsed with page alignment on."""
    key = os.path.abspath(pak)
    hit = _CACHE.get(key)
    if hit is not None:
        return hit
    d = open(pak, "rb").read()
    pages, ao0, ac, relocs, end = _parse(d, False)
    if end != len(d):
        pages, ao0, ac, relocs, end = _parse(d, True)
    hit = (d, pages, ao0, ac, relocs)
    _CACHE[key] = hit
    return hit


def _res(pages, i, off):
    return None if i < 0 or i >= len(pages) else pages[i] + off


def _cstr(d, o):
    return d[o:d.index(b"\0", o)].decode("latin1")


def find_asset(pak, guid, fourcc=None):
    d, pages, ao0, ac, relocs = load(pak)
    for i in range(ac):
        ao = ao0 + i * 80
        if u64(d, ao) != guid:
            continue
        if fourcc is not None and u32(d, ao + 0x4C) != fourcc:
            continue
        return ao
    return None


def asset_type(pak, guid):
    ao = find_asset(pak, guid)
    if ao is None:
        return None
    d = load(pak)[0]
    return struct.pack("<I", u32(d, ao + 0x4C)).decode("ascii", "replace")


def _entry_count(d, ho):
    """envType at +0x09 is a literal count unless it is 0xFF, where the count is
    derived from envOptScales[0..3] and doubled again by the +0x10 mask."""
    env = d[ho + 9]
    if env != 0xFF:
        return env
    sc = d[ho + 0x0A:ho + 0x10]
    n = (1 if sc[0] else 0) + 1
    for k in (1, 2, 3):
        if sc[k]:
            n *= 2
    if d[ho + 0x10]:
        n *= 2
    return n


def read_shader(pak, guid):
    ao = find_asset(pak, guid, FCC_SHDR)
    if ao is None:
        return None
    d, pages, _, _, relocs = load(pak)
    hpi, hpo = i32(d, ao + 0x10), i32(d, ao + 0x14)
    cpi, cpo = i32(d, ao + 0x18), i32(d, ao + 0x1C)
    ho = _res(pages, hpi, hpo)
    sh = {"guid": guid, "version": u32(d, ao + 0x48), "type": d[ho + 8],
          "features": bytes(d[ho + 9:ho + 0x10]), "name": None,
          "parent": None, "entries": []}
    if (hpi, hpo) in relocs:
        sh["name"] = _cstr(d, _res(pages, i32(d, ho), i32(d, ho + 4)))
    if sh["type"] > 5:
        sh["parent"] = u64(d, ho + 0x18)
        return sh
    co = _res(pages, cpi, cpo)
    fo = _res(pages, i32(d, ho + 0x20), i32(d, ho + 0x24))
    esz = 24 if sh["type"] == 1 else 16
    for k in range(_entry_count(d, ho)):
        eo = co + k * esz
        size = u32(d, eo + 8)
        e = {"buf": None, "ref": 0xFFFF,
             "flags": (u64(d, fo + k * 16), u64(d, fo + k * 16 + 8)) if fo else (0, 0)}
        if (cpi, cpo + k * esz) in relocs:
            e["buf"] = d[_res(pages, i32(d, eo), i32(d, eo + 4)):][:size]
        elif size:
            e["ref"] = (~size) & 0xFFFF   # Repak stores a reference as ~refIndex
        sh["entries"].append(e)
    return sh


def read_shaderset(pak, guid):
    ao = find_asset(pak, guid, FCC_SHDS)
    if ao is None:
        return None
    d, pages, _, _, relocs = load(pak)
    ver = u32(d, ao + 0x48)
    ho = _res(pages, i32(d, ao + 0x10), i32(d, ao + 0x14))
    if ver >= 12:
        vs_off, ps_off = 0x40, 0x48
    elif ver >= 11:
        vs_off, ps_off = 0x30, 0x38
    else:
        vs_off, ps_off = 0x48, 0x50
    tic0, tic1 = u16(d, ho + 0x18), u16(d, ho + 0x1A)
    if ver >= 11:
        tic1 -= tic0                      # Repak folds tic0 into tic1 on write
    return {"guid": guid, "version": ver,
            "vertexShaderGuid": u64(d, ho + vs_off),
            "pixelShaderGuid": u64(d, ho + ps_off),
            "numPixelShaderTextures": tic0, "numVertexShaderTextures": tic1,
            "numSamplers": u16(d, ho + 0x1C),
            "firstResourceBindPoint": d[ho + 0x1E], "numResources": d[ho + 0x1F]}


def _pack_shader(sh, base):
    """Serialize one shader block. nameOffset is absolute in the file, so `base`
    is where this block starts."""
    ents = sh["entries"]
    body = bytearray(16 + len(ents) * DESC_SIZE)
    wp = len(body)
    for i, e in enumerate(ents):
        do = 16 + i * DESC_SIZE
        if e["buf"] is not None:
            body.extend(e["buf"])
            struct.pack_into("<II", body, do, len(e["buf"]), wp - do)
            wp += len(e["buf"])
        elif e["ref"] != 0xFFFF:
            struct.pack_into("<II", body, do, e["ref"], 0)
        else:
            struct.pack_into("<II", body, do, 0xFFFFFFFF, 0xFFFFFFFF)
        struct.pack_into("<QQ", body, do + 8, e["flags"][0], e["flags"][1])
    nb = (sh["name"] or "").encode("latin1")
    name_off = 0
    if nb:
        name_off = base + len(body)
        body.extend(nb + b"\0")
    hi = struct.unpack("<Q", sh["features"] + bytes([len(ents)]))[0]
    struct.pack_into("<QII", body, 0, hi, len(nb) + 1 if nb else 0, name_off)
    return bytes(body)


def write_shader_msw(sh, path):
    out = bytearray(struct.pack("<I", MSW_MAGIC)[:3] + bytes([MSW_VER, 0]))
    out.extend(_pack_shader(sh, len(out)))
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(bytes(out))
    return len(out)


def write_shaderset_msw(ss, path, vs=None, ps=None):
    out = bytearray(struct.pack("<I", MSW_MAGIC)[:3] + bytes([MSW_VER, 1]))
    hdr_at = len(out)
    out.extend(b"\0" * 32)
    ps_off = vs_off = 0
    if ps is not None:
        ps_off = len(out)
        out.extend(_pack_shader(ps, ps_off))
    if vs is not None:
        vs_off = len(out)
        out.extend(_pack_shader(vs, vs_off))
    struct.pack_into("<QQHHHBBII", out, hdr_at, ss["pixelShaderGuid"], ss["vertexShaderGuid"],
                     ss["numPixelShaderTextures"], ss["numVertexShaderTextures"],
                     ss["numSamplers"], ss["firstResourceBindPoint"], ss["numResources"],
                     ps_off, vs_off)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "wb").write(bytes(out))
    return len(out)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        print("usage: pak_msw.py <pak> <0xGUID> [out.msw]")
        return 1
    pak, guid = sys.argv[1], int(sys.argv[2], 16)
    kind = asset_type(pak, guid)
    print("0x%016X  type=%s" % (guid, kind))
    if kind == "shdr":
        sh = read_shader(pak, guid)
        print("  v%d shaderType=%d features=%s name=%r" %
              (sh["version"], sh["type"], sh["features"].hex(), sh["name"]))
        if sh["parent"] is not None:
            print("  CHILD -> $parentShader 0x%016X (%d)" % (sh["parent"], sh["parent"]))
            return 0
        for i, e in enumerate(sh["entries"]):
            what = ("buf %d" % len(e["buf"])) if e["buf"] is not None else (
                "ref %d" % e["ref"] if e["ref"] != 0xFFFF else "null")
            print("   [%2d] %-10s %016X %016X" % (i, what, e["flags"][0], e["flags"][1]))
        if len(sys.argv) > 3:
            print("  wrote %s (%d bytes)" % (sys.argv[3], write_shader_msw(sh, sys.argv[3])))
    elif kind == "shds":
        ss = read_shaderset(pak, guid)
        print("  v%d vs=0x%016X ps=0x%016X texPS=%d texVS=%d samp=%d res=%d+%d" %
              (ss["version"], ss["vertexShaderGuid"], ss["pixelShaderGuid"],
               ss["numPixelShaderTextures"], ss["numVertexShaderTextures"],
               ss["numSamplers"], ss["firstResourceBindPoint"], ss["numResources"]))
        if len(sys.argv) > 3:
            print("  wrote %s (%d bytes)" % (sys.argv[3], write_shaderset_msw(ss, sys.argv[3])))
    else:
        print("  no MSW for this type")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
