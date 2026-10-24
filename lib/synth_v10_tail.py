#!/usr/bin/env python3
"""
Deterministic v10 [0x14..0x37] tail synthesizer for S21 textures converted
from S10 v8 sources. Algorithm:

  dataSize       = streamed-only sum of aligned mip bytes (or full chain for
                   non-streamable textures)            -> R1
  perm/mand/opt  = passed through from RSX streamLayout -> R2
  cdn (+0x1B)    = 1 iff (streamable && arr == 1)      -> R3 (validated 100%)
  minStreamMips  = 0                                    -> R4 (validated 100%)
  type (+0x1D)   = SLOT_TYPE_RULE[(slot, fmt)]         -> R5 (mined from common matls)
                   | SLOT_FALLBACK[slot]
                   | FMT_FALLBACK[fmt] | 0
  compTypePacked = 0                                    -> R6 (packedStreamCompAlg:
                                                         raw-BCn in starpak, no per-mip
                                                         compression -> bitmask 0)
  packedStreamSize[i] = (rawAlignedSize_i - 1)//4096    -> R7 (THE streaming fix)
                   per streamed mip (largest first). Validated EXACT 78/78 vs every
                   retail raw texture (packedStreamCompAlg==0) in common.rpak.
  CDNKeyA/CDNKeyB= 0                                    -> R8 (no CDN; numCDN==0)
  rmsError       = zeros                                -> R9 (per-mip streaming-PRIORITY
                                                         heuristic, not a correctness
                                                         field; engine still streams)

On-disk layout (TextureAssetDiskSubset, 56B header):
  +0x14 totalByteCount   +0x18 numUnstreamableMips(perm)  +0x19 numMandatoryStreamableMips
  +0x1A numOptStreamableMips  +0x1B minStreamableMipsToLoad(=1 for all retail streamable)
  +0x1C numCDNStreamableMips(=0, no CDN)  +0x1D texSemantic  +0x1E packedStreamCompAlg(u16)
  +0x20 packedStreamSize(u16[7])  +0x2E CDNKeyA(u16)  +0x30 rmsError(u8[7])  +0x37 CDNKeyB
The texture stream manager reads packedStreamSize to size each streamed mip's starpak read.
Our v8->v10 converter zeroed it -> engine reads 0 bytes/streamed-mip -> high mips never load
-> textures fall back to the smallest permanent mip -> "blurry / wrong mip up close".

`slot` is the texture's binding slot name from a material's $textureTypes,
which is the authoritative semantic class (`albedoTexture`, `normalTexture`,
`glossTexture`, etc). Discovered by walking the pak's material .json files.

Tables generated from canonical S21 corpus (mp_rr_district.rpak.dec.rpak +
common.rpak.dec.rpak + common.rpak materials extracted via RSX):
  build_slot_type_table.py builds SLOT_TYPE_RULE + SLOT_FALLBACK
  build_type_table.py     builds FMT_FALLBACK
"""
import argparse
import csv
import glob
import json
import os
import struct
import sys
from collections import Counter, defaultdict


# (slot_name, eTextureFormat) -> eTextureType
# Derived from 15040 common.rpak materials joined with 16270 common.rpak v10 tails.
# All entries are >=99% pure unless flagged.
SLOT_TYPE_RULE = {
    ('albedoTexture',                1):  4,  ('albedoTexture',                6): 45,
    ('albedoTexture',               13): 45,  ('albedoTexture',               32): 45,
    ('albedoTexture',               53): 45,
    ('albedo2Texture',               1):  4,
    ('albedoMultiplyTexture',        1): 45,  ('albedoMultiplyTexture',       13): 45,
    ('albedoMultiplyTexture',       32): 45,  ('albedoMultiplyTexture',       53): 45,
    ('normalTexture',                0): 12,  ('normalTexture',                8): 12,
    ('normalTexture',               44): 12,
    ('normal2Texture',               8): 12,
    ('detailNormalTexture',          8): 12,
    ('glossTexture',                 6): 17,
    ('gloss2Texture',                6): 17,
    ('specTexture',                  1): 13,
    ('spec2Texture',                 1): 13,
    ('aoTexture',                    6): 25,
    ('ao2Texture',                   6): 25,
    ('cavityTexture',                6): 26,
    ('emissiveTexture',              1): 19,  ('emissiveTexture',             12): 23,
    ('emissiveTexture',             13): 45,  ('emissiveTexture',             32): 19,
    ('emissive2Texture',             1): 19,
    ('emissiveMultiplyTexture',      1): 19,  ('emissiveMultiplyTexture',      6): 22,
    ('emissiveMultiplyTexture',     12): 24,  ('emissiveMultiplyTexture',     13): 19,
    ('emissiveMultiplyTexture',     32): 19,
    ('opacityMultiplyTexture',       0):  6,  ('opacityMultiplyTexture',       1): 19,
    ('opacityMultiplyTexture',       6):  5,  ('opacityMultiplyTexture',       8):  8,
    ('opacityMultiplyTexture',      13): 45,  ('opacityMultiplyTexture',      53):  5,
    ('detailTexture',                0):  6,  ('detailTexture',                1):  4,
    ('colorRampTexture',             1): 45,  ('colorRampTexture',            13): 45,
    ('colorRampTexture',            32): 45,
    ('iridescenceRampTexture',       1):  4,
    ('layerBlendTexture',            6): 37,
    ('anisoSpecDirTexture',          6): 14,  ('anisoSpecDirTexture',          8): 16,
    ('scatterThicknessTexture',      0): 28,  ('scatterThicknessTexture',      6): 30,
    ('scatterThicknessTexture',     12): 31,
    ('transmittanceTintTexture',     1): 27,
    ('uvDistortionTexture',          0): 12,  ('uvDistortionTexture',          8): 12,
    ('uvDistortionTexture',         44): 12,
    ('uvDistortion2Texture',         8): 12,
    # Suffix-keyed entries (some materials use the filename suffix as slot name
    # when the shader didn't expose names) -- same semantics:
    ('_ao',   6): 25, ('_asa',  8): 16, ('_bm',   6): 37, ('_cav',  6): 26,
    ('_col',  1):  4, ('_col', 13): 45, ('_det',  0):  6, ('_ehl', 12): 23,
    ('_gls',  6): 17, ('_ilm',  1): 19, ('_msk',  6):  5, ('_nml',  0): 12,
    ('_nml',  8): 12, ('_opa',  8):  8, ('_spc',  1): 13, ('_vxd',  6): 33,
}

# slot -> mode type across all formats (when (slot, fmt) is missing)
SLOT_FALLBACK = {
    'albedoTexture':           4,  'albedo2Texture':          4,
    'albedoMultiplyTexture':  45,
    'normalTexture':          12,  'normal2Texture':         12,
    'detailNormalTexture':    12,
    'glossTexture':           17,  'gloss2Texture':          17,
    'specTexture':            13,  'spec2Texture':           13,
    'aoTexture':              25,  'ao2Texture':             25,
    'cavityTexture':          26,
    'emissiveTexture':        19,  'emissive2Texture':       19,
    'emissiveMultiplyTexture':24,
    'opacityMultiplyTexture':  5,
    'detailTexture':           6,
    'colorRampTexture':       45,
    'iridescenceRampTexture':  4,
    'layerBlendTexture':      37,
    'anisoSpecDirTexture':    16,
    'scatterThicknessTexture':31,
    'transmittanceTintTexture':27,
    'uvDistortionTexture':    12,  'uvDistortion2Texture':   12,
    '_ao':25, '_asa':16, '_bm':12, '_cav':26, '_col':4, '_det':6, '_ehl':23,
    '_gls':17, '_ilm':19, '_msk':5, '_nml':12, '_opa':5, '_spc':13, '_vxd':33,
}

# fmt -> mode type when no slot is known. From common-named-corpus analysis.
FMT_FALLBACK = {
    0: 6, 1: 13, 6: 17, 8: 12, 10: 4, 12: 24, 13: 45, 32: 45, 44: 12, 53: 45,
}


BLOCK_FMT_TABLE = [
    (8, 4),  (8, 4),  (16, 4), (16, 4), (16, 4), (16, 4),
    (8, 4),  (8, 4),  (16, 4), (16, 4), (16, 4), (16, 4),
    (16, 4), (16, 4),
    (16, 1), (16, 1), (16, 1),
    (12, 1), (12, 1), (12, 1),
    (8, 1),  (8, 1),  (8, 1),  (8, 1),  (8, 1),  (8, 1),  (8, 1),  (8, 1),
    (4, 1),  (4, 1),  (4, 1),
    (4, 1),  (4, 1),  (4, 1),  (4, 1),  (4, 1),
    (4, 1),  (4, 1),  (4, 1),  (4, 1),  (4, 1),
    (4, 1),  (4, 1),  (4, 1),
    (2, 1),  (2, 1),  (2, 1),  (2, 1),  (2, 1),  (2, 1),  (2, 1),  (2, 1),  (2, 1),
    (1, 1),  (1, 1),  (1, 1),  (1, 1),  (1, 1),
    (4, 1),  (4, 1),  (4, 1),  (2, 1),
]


def mip_size_aligned(w, h, fmt):
    if not (0 <= fmt < len(BLOCK_FMT_TABLE)):
        return 0
    bpp, blk = BLOCK_FMT_TABLE[fmt]
    if blk == 4:
        bw = max(1, (w + 3) // 4); bh = max(1, (h + 3) // 4)
        sz = bw * bh * bpp
    else:
        sz = max(1, w) * max(1, h) * bpp
    return (sz + 15) & ~15


def total_mip_count(w, h):
    m = 1
    while w > 1 or h > 1:
        w = max(1, w // 2); h = max(1, h // 2); m += 1
    return m


def derive_type(slot, fmt):
    """Slot-driven type derivation. slot may be None / '' / 'unavailable'."""
    if slot and slot != 'unavailable':
        if (slot, fmt) in SLOT_TYPE_RULE:
            return SLOT_TYPE_RULE[(slot, fmt)]
        if slot in SLOT_FALLBACK:
            return SLOT_FALLBACK[slot]
    return FMT_FALLBACK.get(fmt, 0)


def synth_v10_tail(fmt, w, h, arr, perm, mand, opt, slot=None, misc_flags=0):
    """Return the 36-byte [0x14..0x37] tail block for a v10 TXTR header.

    `misc_flags` = the texture's resourceFlags / header miscFlags (+0x11); bit 1 (0x2) = cubemap.
    """
    streamed = mand + opt
    arr_eff = max(1, arr)
    is_streamable = (streamed > 0) and (arr_eff == 1)

    # +0x14 totalByteCount = STREAMED bytes only (sum of mand+opt mips * arr); 0 for
    # non-streamable. Validated 100% vs genuine S21 (16548 txtr: every non-streamable = 0,
    # every streamable = streamed-mip-sum). The old full-chain value for non-streamable was wrong.
    ds = 0
    if is_streamable:
        ww, hh = w, h
        for _ in range(streamed):
            ds += mip_size_aligned(ww, hh, fmt)
            ww = max(1, ww // 2); hh = max(1, hh // 2)
        ds *= arr_eff

    cdn = 1 if is_streamable else 0
    # texSemantic (eTextureType): genuine S21 stamps it on EVERY texture (0% are 0), streamable
    # or not -- so derive it ALWAYS (the old `if is_streamable else 0` zeroed it for our 85/4097
    # non-streamable txtr). The map env cubemap (cubemaps_hdr) is a cube (misc_flags & 2) with no
    # material slot -> derive_type yields 0 -> stamp the cubemap semantic 18 (matches retail
    # olympus_mu2 exactly: its sole cube txtr is type 18).
    ttype = derive_type(slot, fmt)
    if (misc_flags & 2) and (not slot or slot == 'unavailable'):
        ttype = 18  # env cubemap (cubemaps_hdr): cube bit + no material slot -> always 18

    tail = bytearray(36)
    struct.pack_into("<I", tail, 0x00, ds)          # +0x14 totalByteCount
    tail[0x04] = perm                                # +0x18 numUnstreamableMips
    tail[0x05] = mand                                # +0x19 numMandatoryStreamableMips
    tail[0x06] = opt                                 # +0x1A numOptStreamableMips
    tail[0x07] = cdn                                 # +0x1B minStreamableMipsToLoad (retail=1)
    tail[0x08] = 0                                   # +0x1C numCDNStreamableMips (no CDN)
    tail[0x09] = ttype                               # +0x1D texSemantic
    # +0x0A..0x0B packedStreamCompAlg = 0 (raw BCn in starpak, no per-mip compression)

    # +0x0C..0x19 packedStreamSize[7] (u16): per-streamed-mip packed size in 4KB pages, minus
    # one (= last-page index of the mip's bytes in the starpak), LARGEST mip first. Our pipeline
    # stores RAW, so storedSize == rawAlignedSize. EXACT vs retail raw corpus (78/78).
    if is_streamable:
        ww, hh = w, h
        for i in range(min(streamed, 7)):
            ssz = mip_size_aligned(ww, hh, fmt)
            struct.pack_into("<H", tail, 0x0C + i * 2, min(0xFFFF, max(0, (ssz - 1) // 4096)))
            ww = max(1, ww // 2); hh = max(1, hh // 2)
    # +0x1A..0x1B CDNKeyA, +0x1C..0x22 rmsError[7], +0x23 CDNKeyB: left 0.
    # rmsError is a per-mip streaming-priority benefit metric (RMS error), content-dependent and
    # not carried by v8 sources; 0 is correctness-safe (mandatory mips still stream). If extreme
    # close-ups stay soft, populate it so the stream manager prioritizes the optional tier.
    return bytes(tail)


def build_slot_map(matl_dirs):
    """Walk material .json files in `matl_dirs`, return texture_guid_lower -> slot_name.
    `texture_guid_lower` is the hex GUID with no '0x' prefix and no leading zeros.
    On a guid bound to multiple slots across materials, last-wins."""
    g2slot = {}
    for d in matl_dirs:
        for f in glob.glob(os.path.join(d, "**", "*.json"), recursive=True):
            try:
                doc = json.load(open(f))
            except Exception:
                continue
            txs = doc.get("$textures", {})
            tys = doc.get("$textureTypes", {})
            for k, gx in txs.items():
                if not isinstance(gx, str) or not gx.startswith("0x"):
                    continue
                g = gx[2:].lower().lstrip("0") or "0"
                slot = tys.get(k, "")
                if slot and slot != "unavailable":
                    g2slot[g] = slot
    return g2slot


def validate_against_csv(csv_path, slot_map_dir=None):
    """Reproduce each row's tail. Optionally use a slot map built from materials."""
    rows = list(csv.DictReader(open(csv_path, "r", encoding="utf-8", errors="replace")))
    slot_map = {}
    if slot_map_dir:
        slot_map = build_slot_map([slot_map_dir])
        print(f"[validate] loaded {len(slot_map)} guid->slot bindings from {slot_map_dir}")

    per_byte_match = [0] * 36
    exact = 0
    for r in rows:
        actual = bytes.fromhex(r["hdrTail_hex"])
        gh = r["guid_hex"].lstrip("0") or "0"
        slot = slot_map.get(gh)
        synth = synth_v10_tail(
            fmt=int(r["fmt"]), w=int(r["w"]), h=int(r["h"]),
            arr=int(r["arr"]),
            perm=int(r["perm"]), mand=int(r["mand"]), opt=int(r["opt"]),
            slot=slot,
        )
        for i in range(36):
            if actual[i] == synth[i]:
                per_byte_match[i] += 1
        if actual == synth:
            exact += 1
    n = len(rows)
    print(f"\nrows={n} exact={exact} ({100*exact/max(1,n):.2f}%)")
    fields = ['dataSz+0','dataSz+1','dataSz+2','dataSz+3','perm','mand','opt','cdn',
              'minStream','type','cTP+0','cTP+1'] + [f'cB[{i//2}]+{i%2}' for i in range(14)] + \
             ['u2E+0','u2E+1'] + [f'u30[{i}]' for i in range(8)]
    for i in range(36):
        pct = 100 * per_byte_match[i] / max(1, n)
        print(f"  [{i:#04x}] {fields[i]:12s} {pct:6.2f}%   matched={per_byte_match[i]}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--validate", required=True)
    ap.add_argument("--slot-map-dir", default=None,
                    help="dir of material .jsons to derive guid->slot for validation")
    args = ap.parse_args()
    validate_against_csv(args.validate, args.slot_map_dir)


if __name__ == "__main__":
    main()
