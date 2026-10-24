"""Fit generated terrain into the S21 per-frame world index budget.

S21 builds one dynamic index buffer per frame for every visible world mesh, 0x900000 indices
(3,145,728 triangles). Meshes are appended in mesh-index order; the first mesh that does not fit
stops the walk, so everything after it is not drawn. Terrain is inserted after the map's own
meshes, so an over-budget terrain vanishes as soon as enough of the map is in view (high camera).

This re-triangulates the terrain surface as one right-triangle irregular network (crack-free by
construction) over the npz lattice, splitting where:
  - the vertical error at a split point exceeds E (binary-searched to meet the triangle budget),
  - the triangle would span more than one source mesh (material / cell-node partition kept exact),
  - the blend weight (vertex alpha) of the owning mesh is not linear within ALPHA_TOL.
Each output triangle keeps its source mesh (material, flags, cell refs). Skirts are dropped: every
mesh samples the same height grid, so shared edges match exactly.

Usage (standalone):  python terrain_lod.py <in.npz> <out.npz> --tris N
inject_terrain.py calls decimate() itself when the npz does not fit the budget left by the BSP.
"""
import argparse
import sys

import numpy as np

S21_WORLD_IDX_BUF_ELEMS = 0x900000
S21_WORLD_TRI_BUDGET = S21_WORLD_IDX_BUF_ELEMS // 3
ALPHA_TOL = 24.0 / 255.0
SPLIT_OWNERS = True


def _lattice(pos):
    xs = np.unique(np.round(pos[:, 0], 2))
    d = np.diff(xs)
    s = float(np.median(d[d > 1.0]))
    ox, oy = float(pos[:, 0].min()), float(pos[:, 1].min())
    span = float(pos[:, 0].max()) - ox
    s = span / round(span / s)
    fx, fy = (pos[:, 0] - ox) / s, (pos[:, 1] - oy) / s
    ix, iy = np.round(fx).astype(np.int64), np.round(fy).astype(np.int64)
    res = max(np.abs(fx - ix).max(), np.abs(fy - iy).max())
    if res > 0.01:
        raise SystemExit("terrain_lod: vertices are not on a regular lattice (residual %.4f of spacing)" % res)
    return s, ox, oy, ix, iy


def _rasterize(L, tri_top, tmesh, vals, nx, ny, cell_owner, keys_out, vals_out, NN):
    """Lattice points covered by each triangle (inclusive) -> (mesh,point) keys with interpolated vals;
    cells whose sample point lies inside -> owner mesh."""
    P = L[tri_top]                         # [T,3,2] int
    V = vals[tri_top]                      # [T,3,k]
    x0 = P[:, :, 0].min(1); y0 = P[:, :, 1].min(1)
    bw = P[:, :, 0].max(1) - x0; bh = P[:, :, 1].max(1) - y0
    B = int(max(bw.max(), bh.max()))
    a, b, c = P[:, 0].astype(np.float64), P[:, 1].astype(np.float64), P[:, 2].astype(np.float64)
    den = (b[:, 1] - c[:, 1]) * (a[:, 0] - c[:, 0]) + (c[:, 0] - b[:, 0]) * (a[:, 1] - c[:, 1])
    for dy in range(B + 1):
        for dx in range(B + 1):
            for cell in (False, True):
                if cell and (dx == B or dy == B):
                    continue
                ox, oy = (dx + 0.3, dy + 0.6) if cell else (dx, dy)
                px, py = x0 + ox, y0 + oy
                ok = (dx <= bw) & (dy <= bh) if not cell else (dx < bw) & (dy < bh)
                w0 = ((b[:, 1] - c[:, 1]) * (px - c[:, 0]) + (c[:, 0] - b[:, 0]) * (py - c[:, 1])) / den
                w1 = ((c[:, 1] - a[:, 1]) * (px - c[:, 0]) + (a[:, 0] - c[:, 0]) * (py - c[:, 1])) / den
                w2 = 1.0 - w0 - w1
                e = 1e-9 if not cell else 0.0
                ok &= (w0 >= -e) & (w1 >= -e) & (w2 >= -e)
                if not ok.any():
                    continue
                if cell:
                    cell_owner[(y0[ok] + dy), (x0[ok] + dx)] = tmesh[ok]
                else:
                    gx, gy = x0[ok] + dx, y0[ok] + dy
                    keys_out.append(tmesh[ok].astype(np.int64) * NN + gy * nx + gx)
                    vals_out.append((V[ok] * np.stack([w0[ok], w1[ok], w2[ok]], 1)[:, :, None]).sum(1))


def _pool(O, s, n_cells):
    """Per s x s cell block: owner if uniform, -2 if mixed (-1 = empty stays -1)."""
    nb = n_cells // s
    blk = O[:nb * s, :nb * s].reshape(nb, s, nb, s)
    mn = blk.min((1, 3)); mx = blk.max((1, 3))
    return np.where(mn == mx, mn, -2)


def decimate(pos, nrm, uv, lmuv, col, tri, first, count, mat, flags, refs, tri_budget, log=print, max_error=None):
    pos = np.asarray(pos, np.float64)
    s, ox, oy, ix, iy = _lattice(pos)
    nx, ny = int(ix.max()) + 1, int(iy.max()) + 1
    NN = nx * ny
    T = pos[tri]
    area = np.cross(T[:, 1, :2] - T[:, 0, :2], T[:, 2, :2] - T[:, 0, :2])
    top = np.abs(area) > 1e-3 * s * s
    tmesh_all = np.zeros(len(tri), np.int64)
    for m, (f, c) in enumerate(zip(first, count)):
        tmesh_all[f:f + c] = m
    L = np.stack([ix, iy], 1)
    vals = np.concatenate([pos[:, 2:3], col[:, 3:4].astype(np.float64) / 255.0, nrm.astype(np.float64)], 1)
    cell_owner = np.full((ny - 1, nx - 1), -1, np.int64)
    keys, kv = [], []
    idx = np.flatnonzero(top)
    for a in range(0, len(idx), 400000):
        sl = idx[a:a + 400000]
        _rasterize(L, tri[sl], tmesh_all[sl], vals, nx, ny, cell_owner, keys, kv, NN)
    keys = np.concatenate(keys); kv = np.concatenate(kv)
    # blend weights depend on the material (its two layers), not on which cell-node mesh holds the quad
    matarr = np.asarray(mat, np.int64)
    keys = matarr[keys // NN] * NN + keys % NN
    keys, first_i = np.unique(keys, return_index=True); kv = kv[first_i]
    mesh_owner = cell_owner
    cell_owner = np.where(mesh_owner >= 0, matarr[np.maximum(mesh_owner, 0)], -1)
    pt = keys % NN
    H = np.full(NN, -np.inf); np.maximum.at(H, pt, kv[:, 0])
    H[np.isinf(H)] = np.nan
    NR = np.zeros((NN, 3)); NR[pt] = kv[:, 2:5]
    log("terrain_lod: lattice %dx%d spacing %.4f, %d (material,point) samples, %d owned cells"
        % (nx, ny, s, len(keys), int((cell_owner >= 0).sum())))

    def alpha(owner, gx, gy):
        inside = (gx >= 0) & (gy >= 0) & (gx < nx) & (gy < ny)
        k = owner.astype(np.int64) * NN + np.clip(gy, 0, ny - 1) * nx + np.clip(gx, 0, nx - 1)
        j = np.searchsorted(keys, k)
        j = np.minimum(j, len(keys) - 1)
        hit = (keys[j] == k) & inside
        return np.where(hit, kv[j, 1], np.nan)

    # owner borders follow the source partition (material blocks, cell-node quads); pad the lattice so
    # they land on power-of-two block edges, else every border costs unit-size triangles
    def align(axis_changes):
        best_p, best = 0, -1
        for p in range(64):
            v = axis_changes + p
            tz = np.zeros(len(v), np.int64)
            for k in range(1, 7):
                tz += (v % (1 << k) == 0)
            sc = int(tz.sum())
            if sc > best:
                best_p, best = p, sc
        return best_p
    co = cell_owner
    chx = np.flatnonzero((co[:, 1:] != co[:, :-1]).any(0)) + 1
    chy = np.flatnonzero((co[1:, :] != co[:-1, :]).any(1)) + 1
    wx = (co[:, 1:] != co[:, :-1]).sum(0); wy = (co[1:, :] != co[:-1, :]).sum(1)
    px = align(np.repeat(chx, wx[chx - 1]))
    py = align(np.repeat(chy, wy[chy - 1]))
    log("terrain_lod: lattice pad x %d y %d (owner borders on power-of-two edges)" % (px, py))

    # Martini grid
    M = 2
    while M + 1 < max(nx + px, ny + py):
        M *= 2
    G = M + 1
    Hg = np.full((G, G), np.nan); Hg[py:py + ny, px:px + nx] = H.reshape(ny, nx)
    Og = np.full((M, M), -1, np.int64); Og[py:py + ny - 1, px:px + nx - 1] = cell_owner
    Ogm = np.full((M, M), -1, np.int64); Ogm[py:py + ny - 1, px:px + nx - 1] = mesh_owner
    ERR = np.zeros((G, G))
    INF = np.inf

    def hz(gx, gy):
        return Hg[gy, gx]

    pooled = {1: Og}
    s_ = 2
    while s_ <= M:
        pooled[s_] = _pool(Og, s_, M)
        s_ *= 2

    def block_owner(s, bx, by):
        P = pooled[s]
        nb = P.shape[0]
        ok = (bx >= 0) & (by >= 0) & (bx < nb) & (by < nb)
        out = np.full(bx.shape, -1, np.int64)
        out[ok] = P[by[ok], bx[ok]]
        return out

    def finish(gx, gy, ax, ay, bx_, by_, own, child):
        h = hz(gx, gy); ha = hz(ax, ay); hb = hz(bx_, by_)
        e = np.abs(h - 0.5 * (ha + hb))
        e = np.where(np.isnan(e), INF, e)
        e = np.where((own == -2) & SPLIT_OWNERS, INF, e)
        e = np.where(own == -1, 0.0, e)          # empty region: never drawn, no need to refine
        u = own >= 0
        if u.any():
            am = alpha(own[u], gx[u] - px, gy[u] - py); aa = alpha(own[u], ax[u] - px, ay[u] - py); ab = alpha(own[u], bx_[u] - px, by_[u] - py)
            ae = np.abs(am - 0.5 * (aa + ab))
            bad = np.isnan(ae) | (ae > ALPHA_TOL)
            eu = e[u]; eu[bad] = INF; e[u] = eu
        if child is not None:
            e = np.maximum(e, child)
        ERR[gy, gx] = e

    # bottom-up: B(2), A(2), B(4), A(4), ..., A(M)
    s = 2
    while s <= M:
        h2 = s // 2
        # B(s): horizontal edges (y % s == 0, x % s == h2) and vertical edges (x % s == 0, y % s == h2)
        for horiz in (True, False):
            if horiz:
                gx, gy = np.meshgrid(np.arange(h2, M, s), np.arange(0, G, s))
            else:
                gx, gy = np.meshgrid(np.arange(0, G, s), np.arange(h2, M, s))
            gx, gy = gx.ravel(), gy.ravel()
            if horiz:
                ax, ay, bx_, by_ = gx - h2, gy, gx + h2, gy
                o1 = block_owner(s, gx // s, gy // s); o2 = block_owner(s, gx // s, gy // s - 1)
            else:
                ax, ay, bx_, by_ = gx, gy - h2, gx, gy + h2
                o1 = block_owner(s, gx // s, gy // s); o2 = block_owner(s, gx // s - 1, gy // s)
            own = np.where(o1 == -1, o2, np.where(o2 == -1, o1, np.where(o1 == o2, o1, -2)))
            child = None
            if s >= 4:
                q = s // 4
                child = np.zeros(len(gx))
                for dx in (-q, q):
                    for dy in (-q, q):
                        cx_, cy_ = gx + dx, gy + dy
                        ok = (cx_ >= 0) & (cy_ >= 0) & (cx_ <= M) & (cy_ <= M)
                        child[ok] = np.maximum(child[ok], ERR[cy_[ok], cx_[ok]])
            finish(gx, gy, ax, ay, bx_, by_, own, child)
        # A(s): block centers; diagonal through the parent block center (top block: (0,0)-(M,M))
        gx, gy = np.meshgrid(np.arange(h2, M, s), np.arange(h2, M, s))
        gx, gy = gx.ravel(), gy.ravel()
        if s == M:
            ax, ay, bx_, by_ = np.zeros_like(gx), np.zeros_like(gy), np.full_like(gx, M), np.full_like(gy, M)
        else:
            Px = (gx // (2 * s)) * (2 * s) + s; Py = (gy // (2 * s)) * (2 * s) + s
            ax, ay, bx_, by_ = Px, Py, 2 * gx - Px, 2 * gy - Py
        own = block_owner(s, gx // s, gy // s)
        child = np.max([ERR[gy, gx - h2], ERR[gy, gx + h2], ERR[gy - h2, gx], ERR[gy + h2, gx]], 0)
        finish(gx, gy, ax, ay, bx_, by_, own, child)
        s *= 2

    def select(E):
        A = np.array([[0, 0], [M, M]]); Bv = np.array([[M, M], [0, 0]]); C = np.array([[M, 0], [0, M]])
        out = []
        while len(A):
            mx_, my_ = (A[:, 0] + Bv[:, 0]) // 2, (A[:, 1] + Bv[:, 1]) // 2
            can = ((A[:, 0] + Bv[:, 0]) % 2 == 0) & ((A[:, 1] + Bv[:, 1]) % 2 == 0)
            sp = can & (ERR[my_, mx_] > E)
            out.append(np.concatenate([A[~sp], Bv[~sp], C[~sp]], 1))
            m = np.stack([mx_[sp], my_[sp]], 1)
            a, b, c = A[sp], Bv[sp], C[sp]
            A = np.concatenate([c, b]); Bv = np.concatenate([a, c]); C = np.concatenate([m, m])
        return np.concatenate(out).reshape(-1, 3, 2)

    def owned(tris):
        cx = tris[:, :, 0].sum(1) / 3.0; cy = tris[:, :, 1].sum(1) / 3.0
        cxi = np.clip(np.floor(cx).astype(np.int64), 0, M - 1); cyi = np.clip(np.floor(cy).astype(np.int64), 0, M - 1)
        own = Ogm[cyi, cxi]
        hv = Hg[tris[:, :, 1], tris[:, :, 0]]
        keep = (own >= 0) & ~np.isnan(hv).any(1)
        return tris[keep], own[keep]

    if max_error is not None:
        t, o = owned(select(max_error))
        if len(t) > tri_budget:
            raise SystemExit("terrain_lod: %d triangles at E=%.1f exceed the budget %d" % (len(t), max_error, tri_budget))
        best = (max_error, t, o)
        lo = hi = max_error
    else:
        lo, hi = 0.0, 4096.0
        t, o = owned(select(hi))
        best = (hi, t, o) if len(t) <= tri_budget else None
    while best is not None and hi - lo > 0.25:
        E = 0.5 * (lo + hi)
        t, o = owned(select(E))
        log("terrain_lod:   E %.2f -> %d triangles" % (E, len(t)))
        if len(t) <= tri_budget:
            best = (E, t, o); hi = E
        else:
            lo = E
    if best is None:
        raise SystemExit("terrain_lod: cannot fit %d triangles: %d at E=%.0f (ownership/alpha splits alone exceed it)" % (tri_budget, len(t), hi))
    E, t, o = best
    log("terrain_lod: max vertical error %.2f u -> %d triangles (budget %d, source %d)" % (E, len(t), tri_budget, len(tri)))

    # rebuild meshes
    order = np.argsort(o, kind="stable"); t = t[order]; o = o[order]
    gxs, gys = t[:, :, 0] - px, t[:, :, 1] - py
    vk = o[:, None] * NN + gys * nx + gxs
    uk, inv = np.unique(vk, return_inverse=True)
    inv = inv.reshape(-1, 3)
    vm = uk // NN; vp = uk % NN; vx, vy = vp % nx, vp // nx
    npos = np.stack([ox + vx * s, oy + vy * s, H[vp]], 1)
    al = alpha(matarr[vm], vx, vy)
    if np.isnan(al).any():
        raise SystemExit("terrain_lod: %d output vertices without a blend weight" % int(np.isnan(al).sum()))
    A = np.c_[ix, iy, np.ones(len(ix))].astype(np.float64)
    cu, *_ = np.linalg.lstsq(A[::37], lmuv[::37, 0], rcond=None); cv, *_ = np.linalg.lstsq(A[::37], lmuv[::37, 1], rcond=None)
    nl = np.stack([np.c_[vx, vy, np.ones(len(vx))] @ cu, np.c_[vx, vy, np.ones(len(vx))] @ cv], 1)
    nn = NR[vp]; ln = np.linalg.norm(nn, axis=1, keepdims=True); nn = np.where(ln > 1e-6, nn / np.maximum(ln, 1e-9), [0, 0, 1])
    ncol = np.concatenate([np.full((len(uk), 3), 255, np.uint8), np.round(np.clip(al, 0, 1) * 255).astype(np.uint8)[:, None]], 1)
    # clockwise from above, like the source and retail ground
    P3 = npos[inv]
    cz = np.cross(P3[:, 1, :2] - P3[:, 0, :2], P3[:, 2, :2] - P3[:, 0, :2])
    flip = cz > 0
    inv[flip] = inv[flip][:, [0, 2, 1]]
    # per-mesh vertex windows: meshes own disjoint vertex sets already (key includes the mesh)
    meshes = np.unique(o)
    nfirst, ncount = [], []
    starts = np.searchsorted(o, meshes); ends = np.searchsorted(o, meshes, side="right")
    for a, b in zip(starts, ends):
        nfirst.append(a); ncount.append(b - a)
    vmax = max(len(np.unique(inv[a:b])) for a, b in zip(starts, ends))
    if vmax > 65535:
        raise SystemExit("terrain_lod: a mesh has %d vertices (> 65535)" % vmax)
    dropped = len(first) - len(meshes)
    log("terrain_lod: %d meshes kept, %d source meshes had no surface left" % (len(meshes), dropped))
    nrefs = None
    if refs is not None:
        # a merged triangle can reach into a neighbour mesh's cells: reference every source mesh's nodes it touches
        cxs = t[:, :, 0].astype(np.float64); cys = t[:, :, 1].astype(np.float64)
        ctr = np.stack([cxs.mean(1), cys.mean(1)], 1)
        samp = [ctr] + [0.9 * np.stack([cxs[:, k], cys[:, k]], 1) + 0.1 * ctr for k in range(3)]
        touch = []
        for sp in samp:
            sx = np.clip(np.floor(sp[:, 0]).astype(np.int64), 0, M - 1); sy = np.clip(np.floor(sp[:, 1]).astype(np.int64), 0, M - 1)
            touch.append(Ogm[sy, sx])
        touch = np.stack(touch, 1)
        nrefs = []
        for mi, (a, b) in zip(meshes, zip(starts, ends)):
            src = set(int(v) for v in np.unique(touch[a:b]) if v >= 0 and matarr[v] == matarr[mi]) | {int(mi)}
            nrefs.append(sorted(set(n for sm in src for n in refs[sm])))
    return (npos.astype(np.float32), nn.astype(np.float32), npos[:, :2].astype(np.float32), nl.astype(np.float32), ncol,
            inv.astype(np.uint32), np.array(nfirst, np.int32), np.array(ncount, np.int32),
            np.asarray(mat)[meshes].astype(np.int32), np.asarray(flags)[meshes].astype(np.uint32), nrefs, E)


def main(argv):
    ap = argparse.ArgumentParser()
    ap.add_argument("inp"); ap.add_argument("out")
    ap.add_argument("--tris", type=int, required=True)
    ap.add_argument("--max-error", type=float, default=None, help="fixed vertical error (u) instead of searching")
    a = ap.parse_args(argv)
    z = np.load(a.inp, allow_pickle=True)
    refs = None
    if "ref_node" in z.files:
        refs = [z["ref_node"][f:f + c].tolist() for f, c in zip(z["ref_first"], z["ref_count"])]
    r = decimate(z["pos"], z["nrm"], z["uv"], z["lmuv"], z["col"], z["tri"], z["mesh_first_tri"], z["mesh_tri_count"],
                 z["mesh_material"], z["mesh_flags"], refs, a.tris, max_error=a.max_error)
    pos, nrm, uv, lmuv, col, tri, first, count, mat, flags, nrefs, E = r
    out = dict(pos=pos, nrm=nrm, uv=uv, lmuv=lmuv, col=col, tri=tri, mesh_first_tri=first, mesh_tri_count=count,
               mesh_material=mat, mesh_flags=flags, mat_names=z["mat_names"])
    if nrefs is not None:
        rf = np.cumsum([0] + [len(x) for x in nrefs[:-1]]).astype(np.int32)
        out.update(ref_first=rf, ref_count=np.array([len(x) for x in nrefs], np.int32),
                   ref_node=np.array([n for x in nrefs for n in x], np.int32))
    np.savez(a.out, **out)


if __name__ == "__main__":
    main(sys.argv[1:])
