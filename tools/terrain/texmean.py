"""Mean sRGB colour of a BC1/BC7-free check: decodes BC1 top mip of a DX10 dds (other formats reported)."""
import kitpaths as KP
import struct, sys, os
import numpy as np
E = (KP.MAPS + "/mp_rr_desertlands_mu5/extract/")
def path(g):
    for d in ("texture", "texture_s21"):
        p = E + d + "/" + g + ".dds"
        if os.path.exists(p): return p
def mean(g):
    b = open(path(g), "rb").read()
    h, w = struct.unpack_from("<II", b, 12)
    fmt = struct.unpack_from("<I", b, 128)[0]
    if fmt not in (71, 72): return fmt, None
    nb = (w // 4) * (h // 4)
    blk = np.frombuffer(b, np.uint8, nb * 8, 148).reshape(-1, 8)
    c0 = blk[:, 0].astype(np.uint32) | (blk[:, 1].astype(np.uint32) << 8); c1 = blk[:, 2].astype(np.uint32) | (blk[:, 3].astype(np.uint32) << 8)
    rgb = lambda c: np.stack([((c >> 11) & 31) * 255 / 31, ((c >> 5) & 63) * 255 / 63, (c & 31) * 255 / 31], 1)
    p0, p1 = rgb(c0), rgb(c1)
    four = c0 > c1
    pal = np.stack([p0, p1, np.where(four[:, None], (2 * p0 + p1) / 3, (p0 + p1) / 2), np.where(four[:, None], (p0 + 2 * p1) / 3, 0)], 1)
    bits = blk[:, 4:8].astype(np.uint32); bits = bits[:, 0] | (bits[:, 1] << 8) | (bits[:, 2] << 16) | (bits[:, 3] << 24)
    idx = np.stack([(bits >> (2 * i)) & 3 for i in range(16)], 1)
    cols = np.take_along_axis(pal, idx[:, :, None].repeat(3, 2), 1)
    return fmt, tuple(np.round(cols.reshape(-1, 3).mean(0) / 255, 3))
if __name__ == "__main__":
    for g in sys.argv[1:]: print(g, *mean(g))
