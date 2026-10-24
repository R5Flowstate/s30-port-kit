"""Border-consistent vertex alpha for per-block pair materials.

A grid vertex on a block border is drawn by every block that touches it. Each block's pair only knows two layers,
so the same vertex can show different colours on each side -> straight cuts along block edges. For every vertex
touched by blocks with different pairs, if the pairs share a layer, all of them draw that layer fully at the vertex.
"""
import numpy as np


def forced_layer(pick, side, N, BLOCK):
    """-> int16 [N, N] layer forced at each sample vertex (-1 = none)."""
    nb = pick.shape[0]
    pa = np.full(len(side) + 1, -1, np.int32); pb = np.full(len(side) + 1, -1, np.int32)
    for i, (s1, s0) in enumerate(side):
        pa[i], pb[i] = next(iter(s1)), next(iter(s0))
    gy, gx = np.mgrid[0:N, 0:N]
    blocks = []
    for dy in (0, -1):
        for dx in (0, -1):
            by = np.clip((gy + dy) // BLOCK, 0, nb - 1)
            bx = np.clip((gx + dx) // BLOCK, 0, nb - 1)
            blocks.append(pick[by, bx])
    P = np.stack(blocks, -1)  # [N, N, 4] material index of the touching blocks
    differ = (P != P[..., :1]).any(-1)
    out = np.full((N, N), -1, np.int16)
    yy, xx = np.nonzero(differ)
    if len(yy) == 0:
        return out
    Pv = P[yy, xx]
    A = pa[Pv]; B = pb[Pv]
    # candidate layers = the 2 layers of the first block; keep those present in all 4 pairs
    for cand_slot in (0, 1):
        c = (A if cand_slot == 0 else B)[:, 0]
        ok = ((A == c[:, None]) | (B == c[:, None])).all(-1)
        sel = ok & (out[yy, xx] == -1)
        out[yy[sel], xx[sel]] = c[sel]
    return out
