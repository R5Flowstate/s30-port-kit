import kitpaths as KP
import numpy as np, sys, collections

from cellbsp import CellBsp
D = (KP.MAPS + "/mp_rr_desertlands_mu5/bsp/client_v51_terrain"); B = "mp_rr_desertlands_mu5.bsp"
cb = CellBsp(D, B, ".bsp_lump", -1.0)
G = np.load((KP.WORK + "/mu5_terrain/hf_grid.npy"))
O, S = -45500.0, 91000.0 / 4095
ys, xs = np.mgrid[0:4096:32, 0:4096:32]
z = G[ys, xs]; ok = ~np.isnan(z)
P = np.stack([O + xs[ok] * S, O + ys[ok] * S, z[ok] + 50], 1)
c = cb.cells(P)
u = collections.Counter(c.tolist())
print("terrain sample points", len(P), "z range", P[:, 2].min(), P[:, 2].max())
for cell, n in u.most_common(12): print("cell %4d %6d pts box %s..%s" % (cell, n, np.round(cb.rmn[cell]), np.round(cb.rmx[cell])))
