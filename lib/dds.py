#!/usr/bin/env python3
"""Minimal DDS reader: returns (eTextureFormat_idx, w, h, arr, mipCount)."""
import struct


# DXGI_FORMAT -> eTextureFormat index (matches Texture_DXGIToImageFormat in
# <REPAK>/src/public/texture.h).
DXGI_TO_FMT = {
    71: 0,  72: 1,  74: 2,  75: 3,  77: 4,  78: 5,
    80: 6,  81: 7,  83: 8,  84: 9,  95: 10, 96: 11, 98: 12, 99: 13,
    2: 14,  3: 15,  4: 16,  6: 17,  7: 18,  8: 19,
    10: 20, 11: 21, 12: 22, 13: 23, 14: 24,
    16: 25, 17: 26, 18: 27, 24: 28, 25: 29, 26: 30,
    28: 31, 29: 32, 30: 33, 31: 34, 32: 35,
    34: 36, 35: 37, 36: 38, 37: 39, 38: 40,
    41: 41, 42: 42, 43: 43, 49: 44, 50: 45, 51: 46, 52: 47,
    54: 48, 56: 49, 57: 50, 58: 51, 59: 52,
    61: 53, 62: 54, 63: 55, 64: 56, 65: 57,
    67: 58, 68: 59, 40: 60, 55: 61,
}


def fourcc_to_dxgi(fourcc):
    # DDS legacy fourcc -> DXGI_FORMAT
    # S10 BC4/BC5 use BC4U/BC4S/BC5U/BC5S. S21+/legacy use ATI1/ATI2.
    table = {
        b'DXT1': 71,    # BC1_UNORM (we map _SRGB at the dxgi level via dxt10 hdr)
        b'DXT3': 74,
        b'DXT5': 77,
        b'ATI1': 80,    # BC4_UNORM
        b'BC4U': 80,    # BC4_UNORM (S10 variant)
        b'BC4S': 81,    # BC4_SNORM
        b'ATI2': 83,    # BC5_UNORM
        b'BC5U': 83,    # BC5_UNORM (S10 variant)
        b'BC5S': 84,    # BC5_SNORM
    }
    return table.get(fourcc, 0)


def read_dds_meta(path):
    """Return (fmt_idx, width, height, arraySize, mipCount). Raises on bad DDS."""
    with open(path, "rb") as f:
        head = f.read(148)
    if head[:4] != b'DDS ':
        raise ValueError(f"not a DDS: {path}")
    # DDS_HEADER at offset 4: dwSize(4), dwFlags(4), dwHeight(4), dwWidth(4),
    # dwPitchOrLinearSize(4), dwDepth(4), dwMipMapCount(4)
    height = struct.unpack_from("<I", head, 12)[0]
    width  = struct.unpack_from("<I", head, 16)[0]
    mips   = struct.unpack_from("<I", head, 28)[0] or 1
    # DDS_PIXELFORMAT at +76: size(4), flags(4), fourCC(4)
    pf_flags = struct.unpack_from("<I", head, 80)[0]
    fourcc   = head[84:88]
    arr = 1
    dxgi = 0
    if fourcc == b'DX10':
        # DDS_HEADER_DXT10 at +128: dxgiFormat(4), resourceDimension(4),
        # miscFlag(4), arraySize(4), miscFlags2(4)
        dxgi = struct.unpack_from("<I", head, 128)[0]
        arr  = struct.unpack_from("<I", head, 140)[0] or 1
    elif pf_flags & 0x4:
        dxgi = fourcc_to_dxgi(fourcc)
    else:
        # Uncompressed path: RGB/RGBA via bit masks. Map only the common cases
        # we actually see (R8G8B8A8 UNORM).
        bit_count = struct.unpack_from("<I", head, 88)[0]
        if bit_count == 32:
            dxgi = 28  # R8G8B8A8_UNORM
        elif bit_count == 16:
            dxgi = 49  # R8G8_UNORM
        elif bit_count == 8:
            dxgi = 61  # R8_UNORM
        else:
            raise ValueError(f"uncompressed DDS bit_count={bit_count} not mapped: {path}")
    fmt_idx = DXGI_TO_FMT.get(dxgi, 0xFFFF)
    if fmt_idx == 0xFFFF:
        raise ValueError(f"unmapped DXGI_FORMAT {dxgi} in {path}")
    return (fmt_idx, width, height, arr, mips)


if __name__ == "__main__":
    import sys
    for p in sys.argv[1:]:
        print(p, read_dds_meta(p))
