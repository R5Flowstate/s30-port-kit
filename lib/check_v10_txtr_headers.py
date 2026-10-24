#!/usr/bin/env python3
"""
Dump every v10 TXTR subheader from a REPAK-BUILT rpak v8.

Parses the v8 header, walks segments+pages (Repak builds them CONTIGUOUSLY:
pageOffset = pageDataStart + sum(prev page dataSize)), reads the 80B asset
entries, and for each asset with magic=='TXTR' and version==10 extracts the
56B TextureAssetHeader_v10_t.

Then runs sanity checks the runtime cares about:
  * imgFormat in [0..61]
  * total mip count from dims == permanentMipLevels + streamedMipLevels + optStreamedMipLevels
  * dataSize == sum of per-mip aligned byte sizes (uses s_pBytesPerPixel for block-compressed)
  * unkMipLevels (v10-added field) value distribution -- 0 across the board is suspicious

Usage:
  python check_v10_txtr_headers.py <path-to.rpak> [--asset-name-substring foo] [--limit N]
"""
import argparse
import struct
import sys
import os
from collections import Counter, defaultdict

# eTextureFormat -> (bytesPerBlockOrPixel, blockWidth)
# Matches Repak src/public/texture.h s_pBytesPerPixel (block-comp uses 4x4 blocks).
# (x = bytesPerBlock for BC, y=4 for 4x4 blocks else 1 for linear)
BLOCK_FMT_TABLE = [
    (8, 4),  (8, 4),  (16, 4), (16, 4), (16, 4), (16, 4),
    (8, 4),  (8, 4),  (16, 4), (16, 4), (16, 4), (16, 4),
    (16, 4), (16, 4),
    (16, 1), (16, 1), (16, 1),
    (12, 1), (12, 1), (12, 1),
    (8, 1),  (8, 1),  (8, 1),  (8, 1),  (8, 1),
    (8, 1),  (8, 1),  (8, 1),
    (4, 1),  (4, 1),  (4, 1),
    (4, 1),  (4, 1),  (4, 1),  (4, 1),  (4, 1),
    (4, 1),  (4, 1),  (4, 1),  (4, 1),  (4, 1),
    (4, 1),  (4, 1),  (4, 1),
    (2, 1),  (2, 1),  (2, 1),  (2, 1),  (2, 1),  (2, 1),  (2, 1),  (2, 1),  (2, 1),
    (1, 1),  (1, 1),  (1, 1),  (1, 1),  (1, 1),
    (4, 1),  (4, 1),  (4, 1),  (2, 1),
]


def mip_size_bytes(w, h, fmt):
    """Size of a single uncompressed-in-rpak mip in bytes."""
    if fmt >= len(BLOCK_FMT_TABLE):
        return 0
    bpp_or_block, blk = BLOCK_FMT_TABLE[fmt]
    if blk == 4:
        bw = max(1, (w + 3) // 4)
        bh = max(1, (h + 3) // 4)
        return bw * bh * bpp_or_block
    return max(1, w) * max(1, h) * bpp_or_block


def total_mip_count(w, h):
    m = 1
    while w > 1 or h > 1:
        w = max(1, w // 2)
        h = max(1, h // 2)
        m += 1
    return m


def parse_pak(path):
    with open(path, "rb") as f:
        blob = f.read()

    if blob[:4] != b"RPak":
        raise SystemExit(f"not an rpak: {path}")

    version       = struct.unpack_from("<H", blob, 4)[0]
    flags         = blob[6]
    cmp_size      = struct.unpack_from("<Q", blob, 0x18)[0]
    dcmp_size     = struct.unpack_from("<Q", blob, 0x30)[0]
    sp_path_len   = struct.unpack_from("<H", blob, 0x48)[0]
    opt_path_len  = struct.unpack_from("<H", blob, 0x4A)[0]
    vseg_count    = struct.unpack_from("<H", blob, 0x4C)[0]
    page_count    = struct.unpack_from("<H", blob, 0x4E)[0]
    desc_count    = struct.unpack_from("<I", blob, 0x54)[0]
    asset_count   = struct.unpack_from("<I", blob, 0x58)[0]
    guiddesc_count= struct.unpack_from("<I", blob, 0x5C)[0]
    rel_count     = struct.unpack_from("<I", blob, 0x60)[0]
    ext_count     = struct.unpack_from("<I", blob, 0x64)[0]
    ext_size      = struct.unpack_from("<I", blob, 0x68)[0]

    print(f"v{version} flags=0x{flags:02x} cmp={cmp_size:,} dcmp={dcmp_size:,} "
          f"vseg={vseg_count} pages={page_count} assets={asset_count}",
          file=sys.stderr)

    # Table offsets.
    o = 0x80 + sp_path_len + opt_path_len
    seg_off  = o
    page_off = seg_off + vseg_count * 16
    desc_off = page_off + page_count * 12
    asset_off= desc_off + desc_count * 8
    guid_off = asset_off + asset_count * 80
    rel_off  = guid_off + guiddesc_count * 8
    page_data= rel_off + rel_count * 4 + ext_count * 4 + ext_size

    # Build contiguous page offset table (Repak-built paks only).
    page_offsets = []
    cur = page_data
    for i in range(page_count):
        seg_idx, align, dsz = struct.unpack_from("<III", blob, page_off + i * 12)
        page_offsets.append((cur, seg_idx, align, dsz))
        cur += dsz

    return blob, asset_off, asset_count, page_offsets


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("rpak")
    ap.add_argument("--asset-name-substring", default=None,
                    help="filter to assets whose path contains this substring")
    ap.add_argument("--limit", type=int, default=20)
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    blob, asset_off, asset_count, page_offsets = parse_pak(args.rpak)

    # Resolve a PagePtr_t (pageIdx, pageOff) to a file offset.
    def resolve(pidx, poff):
        if pidx >= len(page_offsets):
            return None
        return page_offsets[pidx][0] + poff

    # Magic 'TXTR' is stored little-endian as bytes 'T','X','T','R' = b'TXTR'
    # but Repak source uses 4cc literal as a uint32. We accept either order
    # by checking both spellings (also matches RSX export 'txtr').
    txtr_magics = {b"TXTR", b"txtr", b"RTXT", b"rtxt"}

    fmt_hist = Counter()
    type_hist = Counter()
    mip_per_dim_hist = Counter()
    unkMipLvls_hist = Counter()
    compTypePacked_hist = Counter()
    arraySize_hist = Counter()
    layerCount_hist = Counter()
    usageFlags_hist = Counter()
    unk_12_hist = Counter()
    unk_1B_hist = Counter()

    dataSize_ok = 0
    dataSize_bad = 0
    mipCount_ok = 0
    mipCount_bad = 0
    badFmt = 0

    bad_examples = []
    txtr_total = 0

    for i in range(asset_count):
        base = asset_off + i * 80
        guid = struct.unpack_from("<Q", blob, base)[0]
        sub_pidx = struct.unpack_from("<I", blob, base + 0x10)[0]
        sub_poff = struct.unpack_from("<I", blob, base + 0x14)[0]
        sub_size = struct.unpack_from("<I", blob, base + 0x30 + 0x14)[0]
        version  = struct.unpack_from("<I", blob, base + 0x30 + 0x18)[0]
        magic    = blob[base + 0x30 + 0x1C : base + 0x30 + 0x20]

        if magic not in txtr_magics:
            continue
        txtr_total += 1
        if version != 10:
            continue

        sub_file_off = resolve(sub_pidx, sub_poff)
        if sub_file_off is None or sub_file_off + 56 > len(blob):
            continue
        hdr = blob[sub_file_off : sub_file_off + 56]
        if len(hdr) < 56:
            continue

        # v10 layout per Repak src/public/texture.h.
        name_ptr     = struct.unpack_from("<Q", hdr, 0x00)[0]   # PagePtr_t (pageIdx+pageOff)
        imgFormat    = struct.unpack_from("<H", hdr, 0x08)[0]
        width        = struct.unpack_from("<H", hdr, 0x0A)[0]
        height       = struct.unpack_from("<H", hdr, 0x0C)[0]
        depth        = struct.unpack_from("<H", hdr, 0x0E)[0]
        arraySize    = hdr[0x10]
        layerCount   = hdr[0x11]
        unk_12       = hdr[0x12]
        usageFlags   = hdr[0x13]
        dataSize     = struct.unpack_from("<I", hdr, 0x14)[0]
        permMips     = hdr[0x18]
        strmMips     = hdr[0x19]
        optStrmMips  = hdr[0x1A]
        unk_1B       = hdr[0x1B]
        unkMipLvls   = hdr[0x1C]
        ttype        = hdr[0x1D]
        compTypePkd  = struct.unpack_from("<H", hdr, 0x1E)[0]
        compBytes    = list(struct.unpack_from("<7H", hdr, 0x20))
        unk_2E       = struct.unpack_from("<H", hdr, 0x2E)[0]
        unk_30       = list(hdr[0x30:0x38])

        # Resolve asset name from PagePtr_t in header[0..8] for display.
        name = ""
        try:
            np_idx = struct.unpack_from("<I", hdr, 0x00)[0]
            np_off = struct.unpack_from("<I", hdr, 0x04)[0]
            fp = resolve(np_idx, np_off)
            if fp is not None and fp < len(blob):
                end = blob.find(b"\x00", fp, fp + 512)
                if end != -1:
                    name = blob[fp:end].decode("latin1", errors="replace")
        except Exception:
            pass

        if args.asset_name_substring and args.asset_name_substring not in name:
            continue

        fmt_hist[imgFormat]      += 1
        type_hist[ttype]         += 1
        unkMipLvls_hist[unkMipLvls] += 1
        compTypePacked_hist[compTypePkd] += 1
        arraySize_hist[arraySize]+= 1
        layerCount_hist[layerCount] += 1
        usageFlags_hist[usageFlags] += 1
        unk_12_hist[unk_12]      += 1
        unk_1B_hist[unk_1B]      += 1

        # Mip count sanity vs dims.
        total_mips_dim = total_mip_count(width, height)
        sum_mips = permMips + strmMips + optStrmMips
        mip_per_dim_hist[(total_mips_dim, sum_mips)] += 1
        if sum_mips != total_mips_dim:
            mipCount_bad += 1
        else:
            mipCount_ok += 1

        # dataSize sanity: assumes mips are stored uncompressed (compTypePacked==0 path).
        # Each mip is aligned to 16B in rpak (mipLevel_t::mipSizeAligned).
        # CORRECTED 2026-07-19 (see synth_v10_tail.py's own docs + skulltown_s27 port):
        # dataSize is STREAMED-ONLY (mand+opt mips, largest-first), 0 for non-streamable --
        # NOT the whole perm+streamed+opt chain. The old full-chain assumption here was the
        # SAME wrong convention synth_v10_tail.py already fixed; this checker just never got
        # updated to match, so it flagged 1601/1601 correct textures as "bad" on skulltown_s27.
        if 0 <= imgFormat < len(BLOCK_FMT_TABLE):
            streamed_mips = strmMips + optStrmMips
            w, h = width, height
            mip_total = 0
            for _ in range(streamed_mips):
                msz = mip_size_bytes(w, h, imgFormat)
                # rpak aligns each mip to 16B before next mip starts.
                mip_total += (msz + 15) & ~15
                w = max(1, w // 2)
                h = max(1, h // 2)
            mip_total *= max(1, arraySize)
            # dataSize is the STREAMED-ONLY total (0 if non-streamable). If compressed,
            # dataSize is the COMPRESSED total; mismatch on a compTypePacked==0 texture is the smell.
            if compTypePkd == 0:
                if dataSize == mip_total:
                    dataSize_ok += 1
                else:
                    dataSize_bad += 1
                    if len(bad_examples) < args.limit:
                        bad_examples.append(
                            (name, width, height, imgFormat, arraySize, total_mips_dim,
                             sum_mips, dataSize, mip_total, permMips, strmMips, optStrmMips,
                             unkMipLvls, compTypePkd, ttype))
        else:
            badFmt += 1

        if args.verbose and (args.asset_name_substring or txtr_total <= args.limit):
            print(f"\nasset[{i}] guid={guid:#018x} ver=10 name='{name}'")
            print(f"  imgFormat={imgFormat:3d} dims={width}x{height}x{depth} array={arraySize} layer={layerCount}")
            print(f"  mips=(perm={permMips} streamed={strmMips} opt={optStrmMips}) sumMips={sum_mips} expected={total_mips_dim}")
            print(f"  dataSize={dataSize:,} type={ttype} usageFlags=0x{usageFlags:02x}")
            print(f"  unk_12=0x{unk_12:02x} unk_1B=0x{unk_1B:02x} unkMipLvls={unkMipLvls} compTypePkd=0x{compTypePkd:04x}")
            print(f"  compressedBytes={compBytes} unk_2E=0x{unk_2E:04x} unk_30={[hex(b) for b in unk_30]}")

    # ---- Summary ----
    print(f"\n========== SUMMARY for {os.path.basename(args.rpak)} ==========")
    print(f"txtr entries total      = {txtr_total}")
    print(f"  -> with version 10    = {sum(fmt_hist.values())}")
    print(f"mipCount sanity         ok={mipCount_ok}  bad={mipCount_bad}")
    print(f"dataSize sanity (compTypePkd==0)  ok={dataSize_ok}  bad={dataSize_bad}")
    print(f"unknown/oor imgFormat   = {badFmt}")

    def histline(name, h, top=10):
        items = sorted(h.items(), key=lambda kv: -kv[1])[:top]
        kvs = ", ".join(f"{k}={v}" for k, v in items)
        print(f"  {name}: {kvs}")

    print("\nField distributions (top values):")
    histline("imgFormat", fmt_hist)
    histline("type", type_hist)
    histline("arraySize", arraySize_hist)
    histline("layerCount", layerCount_hist)
    histline("usageFlags", usageFlags_hist)
    histline("unk_12", unk_12_hist)
    histline("unk_1B", unk_1B_hist)
    histline("unkMipLvls (v10 NEW)", unkMipLvls_hist)
    histline("compTypePacked (v10 NEW)", compTypePacked_hist)

    print("\nMip-count alignment (totalFromDims, sumOfFields) -> count:")
    for k, v in sorted(mip_per_dim_hist.items(), key=lambda kv: -kv[1])[:8]:
        print(f"  {k}: {v}")

    if bad_examples:
        print("\nFirst bad dataSize examples (compTypePkd==0; dataSize != raw mip sum):")
        print(f"{'name':<70} {'w':>5} {'h':>5} {'fmt':>4} {'arr':>3} {'mipsDim':>7} {'mipsSum':>7} "
              f"{'dataSize':>10} {'computed':>10} {'p':>2} {'s':>2} {'o':>2} {'uML':>3} {'cTP':>4} {'tp':>3}")
        for x in bad_examples:
            (name, w, h, f, ar, td, ts, ds, cm, p, s, o, uml, ctp, tp) = x
            print(f"{name[:70]:<70} {w:>5} {h:>5} {f:>4} {ar:>3} {td:>7} {ts:>7} "
                  f"{ds:>10,} {cm:>10,} {p:>2} {s:>2} {o:>2} {uml:>3} {ctp:>4x} {tp:>3}")


if __name__ == "__main__":
    main()
