"""Render terrain for mp_rr_desertlands_mu5 built from the collision heightfield patches.

Top surface = exactly the collision triangles (same vertices, same diagonals); quads larger than SUBMAX samples are
subdivided on their own planes, so the surface is unchanged. Skirts close the cracks between patches whose border
heights disagree. Every mesh is referenced from each portal cell its quads touch (cell BSP lumps 0x6A/0x01).
Materials and blend weights come from matmap.py (S30 splat layers -> S21 materials named after them).
Output: terrain_meshes.npz (inject_terrain contract + ref_first/ref_count/ref_node)."""
import kitpaths as KP
import json, sys, os, collections, struct
import numpy as np

ROOT = (KP.WORK + "/mu5_terrain/")

import hfpatches, matmap
from scipy import ndimage
N = matmap.N
act, wts = matmap.sample_layers()
RAW = wts
wts = matmap.s30_stack(act, wts)  # S30 RVT layer weighting picks the pair; vertex alpha uses RAW (s30_pair_alpha)
mats, msrc, mside, mpick, mfb, mtot, _ = matmap.choose_s30(act, wts, ~np.isnan(np.load(ROOT + "hf_grid.npy")), None)
_, (fy, fx) = ndimage.distance_transform_edt(mfb, return_indices=True)
mpick = mpick[fy, fx]
used = np.unique(mpick)
remap = np.full(len(mats), -1); remap[used] = np.arange(len(used))
mats = [mats[i] for i in used]; mside = [mside[i] for i in used]; msrc = msrc[used]
BLKMAT = remap[mpick]
TINT = json.load(open(ROOT + "rvt_layer_descs.json"))
for i, (a, b) in enumerate(mside):
    for l in a | b:
        if any(abs(t - 1) > 1e-3 for t in TINT[l]["tint"]):
            print("tinted layer", matmap.TOK[l], TINT[l]["tint"], "in", mats[i])
print("materials", len(mats), "from hu", int((msrc == "H").sum()))
MTOP = []
for s1, s0 in mside:
    A, B = next(iter(s1)), next(iter(s0))
    MTOP.append(A if A == B else matmap.s30_pair_top(act, A, B))
from cellbsp import CellBsp

SUBMAX, TILE, NODE_MIN, CRACK, SKIRT_PAD, MAXV = 4, 256, 2048.0, 0.25, 2.0, 64000
MAPD = hfpatches.MAPD
d = hfpatches.load()
_v = [~np.isnan(d[k_]) for k_ in ("za", "zb", "zc", "zd")]
_live = np.where(d["ttb"] == 0, (_v[0] & _v[3] & _v[2]) | (_v[0] & _v[1] & _v[3]), (_v[0] & _v[1] & _v[2]) | (_v[1] & _v[3] & _v[2]))
d = {k_: (v_[_live] if isinstance(v_, np.ndarray) else v_) for k_, v_ in d.items()}
hf = d["hf"]
sx, sy = hf["scale"][0], hf["scale"][1]
Ox, Oy = hf["origin"][0], hf["origin"][1]
NG = 4097

# ---- final quads: coplanar subdivision of full quads larger than SUBMAX
q = d["q"].astype(np.int64)
full = ~(np.isnan(d["za"]) | np.isnan(d["zb"]) | np.isnan(d["zc"]) | np.isnan(d["zd"]))
nsub = np.where(full & (q > SUBMAX), q // SUBMAX, 1)
rep = nsub * nsub
src = np.repeat(np.arange(len(q)), rep)
k = np.arange(len(src)) - np.repeat(np.cumsum(rep) - rep, rep)
n = nsub[src]
fi, fj = k % n, k // n                    # sub column (x), sub row (y)
s_ = q[src] // n
F = {key: d[key][src] for key in ("ttb", "za", "zb", "zc", "zd", "pid", "px", "py", "lc", "lr", "last_c", "last_r")}
qq = q[src]
gx0 = F["px"] + F["lc"] * qq + fi * s_
gy0 = F["py"] + F["lr"] * qq + fj * s_
NQ = len(src)
print("render quads", NQ, "from", len(q), "collision quads")

ox = F["px"] * sx + Ox; oy = F["py"] * sy + Oy
dx = qq * sx; dy = qq * sy

def interp(u, v, a, b, c, dd, ttb):
    z0 = np.where(u >= v, a + u * (b - a) + v * (dd - b), a + v * (c - a) + u * (dd - c))
    z1 = np.where(u + v <= 1, a + u * (b - a) + v * (c - a), dd + (1 - u) * (c - dd) + (1 - v) * (b - dd))
    z = np.where(ttb == 0, z0, z1)
    z = np.where((u == 0) & (v == 0), a, z); z = np.where((u == 1) & (v == 0), b, z)
    z = np.where((u == 0) & (v == 1), c, z); z = np.where((u == 1) & (v == 1), dd, z)
    return z

# corners A(0,0) B(1,0) C(0,1) D(1,1) of each final quad, in parent (u, v)
CORN = ((0, 0), (1, 0), (0, 1), (1, 1))
cu = [(fi + ci) / n for ci, cj in CORN]; cv = [(fj + cj) / n for ci, cj in CORN]
cu = [np.where(fi + ci == n, 1.0, u) for u, (ci, cj) in zip(cu, CORN)]
cv = [np.where(fj + cj == n, 1.0, v) for v, (ci, cj) in zip(cv, CORN)]
CX = np.stack([ox + (F["lc"] + u) * dx for u in cu], 1)
CY = np.stack([oy + (F["lr"] + v) * dy for v in cv], 1)
CZ = np.stack([interp(u, v, F["za"], F["zb"], F["zc"], F["zd"], F["ttb"]) for u, v in zip(cu, cv)], 1)
CGX = np.stack([gx0 + ci * s_ for ci, cj in CORN], 1); CGY = np.stack([gy0 + cj * s_ for ci, cj in CORN], 1)

# ---- vertex normals: area-weighted collision face normals per grid point (shared across patches)
acc = np.zeros((NG * NG, 3))
pa = np.stack([d["px"] + d["lc"] * q, d["py"] + d["lr"] * q], 1)
P4 = []
for ci, cj, zk in ((0, 0, "za"), (1, 0, "zb"), (0, 1, "zc"), (1, 1, "zd")):
    P4.append(np.stack([(d["px"] * sx + Ox) + (d["lc"] + ci) * q * sx, (d["py"] * sy + Oy) + (d["lr"] + cj) * q * sy, d[zk]], 1))
G4 = [(pa[:, 1] + cj * q) * NG + pa[:, 0] + ci * q for ci, cj in CORN]
ttbp = d["ttb"]
for (i0, i1, i2), sel in (((0, 3, 2), ttbp == 0), ((0, 1, 3), ttbp == 0), ((0, 1, 2), ttbp == 1), ((1, 3, 2), ttbp == 1)):
    fn = np.cross(P4[i1][sel] - P4[i0][sel], P4[i2][sel] - P4[i0][sel])
    ok = np.all(np.isfinite(fn), 1)
    for ii in (i0, i1, i2):
        np.add.at(acc, G4[ii][sel][ok], fn[ok])
nrm_grid = lambda key: acc[key] / np.maximum(np.linalg.norm(acc[key], axis=-1, keepdims=True), 1e-12)
parent_key = [(F["py"] + (F["lr"] + cj) * qq) * NG + F["px"] + (F["lc"] + ci) * qq for ci, cj in CORN]
PN = [nrm_grid(kk) for kk in parent_key]
CN = []
for u, v in zip(cu, cv):
    u_, v_ = u[:, None], v[:, None]
    nn_ = PN[0] * (1 - u_) * (1 - v_) + PN[1] * u_ * (1 - v_) + PN[2] * (1 - u_) * v_ + PN[3] * u_ * v_
    CN.append(nn_ / np.maximum(np.linalg.norm(nn_, axis=1, keepdims=True), 1e-12))
CN = np.stack(CN, 1)
del acc

# ---- crack spread on patch borders: every patch's border heights rasterized per grid sample
bmn = np.full(NG * NG, np.inf); bmx = np.full(NG * NG, -np.inf)
sides = (("za", "zb", d["lr"] == 0, 0, 0, 1, 0), ("zc", "zd", d["last_r"], 0, 1, 1, 1),
         ("za", "zc", d["lc"] == 0, 0, 0, 0, 1), ("zb", "zd", d["last_c"], 1, 0, 1, 1))
for z0k, z1k, sel, c0, r0, c1, r1 in sides:
    sel = sel & ~np.isnan(d[z0k]) & ~np.isnan(d[z1k])
    for t in range(int(q[sel].max()) + 1 if sel.any() else 0):
        m = sel & (t <= q)
        f = t / q[m]
        gx = pa[m, 0] + (c0 + (c1 - c0) * f) * q[m]; gy = pa[m, 1] + (r0 + (r1 - r0) * f) * q[m]
        z = d[z0k][m] * (1 - f) + d[z1k][m] * f
        key = gy.astype(np.int64) * NG + gx.astype(np.int64)
        np.minimum.at(bmn, key, z); np.maximum.at(bmx, key, z)
spread = np.where(np.isfinite(bmn), bmx - bmn, 0.0)
del bmn, bmx
# render-quad sides on a patch border: (corner i, corner j, on-border mask)
QSIDES = ((0, 1, (F["lr"] == 0) & (fj == 0)), (2, 3, F["last_r"] & (fj == n - 1)),
          (0, 2, (F["lc"] == 0) & (fi == 0)), (1, 3, F["last_c"] & (fi == n - 1)))
skirt_depth = []
for i0, i1, sel in QSIDES:
    ok = sel & ~np.isnan(CZ[:, i0]) & ~np.isnan(CZ[:, i1])
    mx_ = np.zeros(NQ)
    for t in range(int(s_.max()) + 1):
        m = ok & (t <= s_)
        f = t / s_[m]
        key = (CGY[m, i0] + (CGY[m, i1] - CGY[m, i0]) * f).astype(np.int64) * NG + (CGX[m, i0] + (CGX[m, i1] - CGX[m, i0]) * f).astype(np.int64)
        mx_[m] = np.maximum(mx_[m], spread[key])
    skirt_depth.append(np.where(ok & (mx_ > CRACK), mx_ + SKIRT_PAD, 0.0))
skirt_depth = np.stack(skirt_depth, 1)
print("skirt edges", int((skirt_depth > 0).sum()), "max depth %.1f" % skirt_depth.max())

# ---- quad boxes -> portal cells (box query, conservative) and primary cell (centre point)
cb = CellBsp(MAPD + "client_v51", "mp_rr_desertlands_mu5.bsp", ".bsp_lump", -1.0)
zmin = np.nanmin(CZ, 1) - skirt_depth.max(1) - 1; zmax = np.nanmax(CZ, 1) + 1
qmn = np.stack([CX.min(1) - 0.5, CY.min(1) - 0.5, zmin], 1); qmx = np.stack([CX.max(1) + 0.5, CY.max(1) + 0.5, zmax], 1)
def box_cells(mn, mx):
    item = np.arange(len(mn)); node = np.zeros(len(mn), np.int64); out_i, out_c = [], []
    while len(item):
        pn = cb.bn[node, 0]; leaf = pn == 0xFFFFFFFF
        out_i.append(item[leaf]); out_c.append(cb.bn[node[leaf], 1])
        item, node = item[~leaf], node[~leaf]
        pl = cb.pl[cb.bn[node, 0]]
        lo = np.where(pl[:, :3] >= 0, mn[item], mx[item]); hi = np.where(pl[:, :3] >= 0, mx[item], mn[item])
        dlo = (lo * pl[:, :3]).sum(1) - pl[:, 3]; dhi = (hi * pl[:, :3]).sum(1) - pl[:, 3]
        ch = cb.bn[node, 1]
        front = dhi >= 0; back = dlo < 0
        item = np.concatenate([item[front], item[back]]); node = np.concatenate([ch[front], ch[back] + 1])
    return np.concatenate(out_i), np.concatenate(out_c)
bi, bc = box_cells(qmn, qmx)
pair = np.unique(bi * 1024 + bc)
bi, bc = pair // 1024, pair % 1024
ctr = np.stack([CX.mean(1), CY.mean(1), np.nanmean(CZ, 1) + 0.5], 1)
prim = cb.cells(ctr)
starts = np.searchsorted(bi, np.arange(NQ)); ends = np.searchsorted(bi, np.arange(NQ), "right")
cellset_str = np.array([",".join(map(str, bc[a:b])) for a, b in zip(starts, ends)])
csu, cs_id = np.unique(cellset_str, return_inverse=True)
print("cell sets", len(csu), "quads in >1 cell", int(((ends - starts) > 1).sum()))
if not np.all([str(p) in s.split(",") for p, s in zip(prim[:100000], cellset_str[:100000])]): sys.exit("primary cell outside the box cell set")

# ---- cell tree (0x77): cell i owns root node i
CT = MAPD + "client_v51/mp_rr_desertlands_mu5.bsp.0077.bsp_lump"
nd = np.fromfile(CT, np.dtype([("mn", "<f4", 3), ("c", "<u4"), ("mx", "<f4", 3), ("r", "<u4")]))
nfade = np.fromfile(CT.replace(".0077.", ".0027."), "<u2")
ncc = (nd["c"] & 255).astype(np.int64); nfc = (nd["c"] >> 8).astype(np.int64)
child_of = np.zeros(len(nd), bool)
for i in np.flatnonzero(ncc): child_of[nfc[i]:nfc[i] + ncc[i]] = True
if not np.array_equal(np.flatnonzero(~child_of), np.arange(cb.ncell)): sys.exit("cell roots are not nodes 0..numCells-1")
def descend(start, mn, mx):
    node = np.asarray(start, np.int64).copy()
    for _ in range(64):
        moved = False
        for j in range(int(ncc.max())):
            act = j < ncc[node]
            if not act.any(): break
            c = np.where(act, nfc[node] + j, 0)
            ext = (nd["mx"][c] - nd["mn"][c])[:, :2].max(1)
            go = act & (ext >= NODE_MIN) & (nfade[c] == 65535) & np.all(nd["mn"][c] <= mn, 1) & np.all(nd["mx"][c] >= mx, 1)
            if go.any(): node[go] = c[go]; moved = True
        if not moved: break
    return node
qnode = descend(prim, qmn, qmx)
node_par = np.full(len(nd), -1, np.int64)
for i in np.flatnonzero(ncc): node_par[nfc[i]:nfc[i] + ncc[i]] = i
node_root = np.arange(len(nd))
for _ in range(64):
    m_ = node_par[node_root] >= 0
    if not m_.any(): break
    node_root[m_] = node_par[node_root[m_]]
if not np.array_equal(node_root[qnode], prim): sys.exit("quad node left its primary cell")

# ---- per-vertex attributes keyed by (patch, grid y, grid x)
vkey = F["pid"].astype(np.int64)[:, None] * (NG * NG) + CGY * NG + CGX
uk, first_idx, vinv = np.unique(vkey.ravel(), return_index=True, return_inverse=True)
vinv = vinv.reshape(NQ, 4)
VP = np.stack([CX.ravel(), CY.ravel(), CZ.ravel()], 1)[first_idx]
VN = CN.reshape(-1, 3)[first_idx]
VGX = CGX.ravel()[first_idx]; VGY = CGY.ravel()[first_idx]
_lmh = open(MAPD + "client_v51/%s.bsp.0053.bsp_lump" % hfpatches.L, "rb").read()
_land = [struct.unpack_from("<HH", _lmh, i + 4) for i in range(0, len(_lmh), 8) if _lmh[i + 2] == 1]   # tag 1 = landscape page
assert len(_land) == 1, "expected one landscape (tag 1) lightmap page, got %d" % len(_land)
LMW, LMH = _land[0]
VLM = np.stack([VGX / N + 0.5 / LMW, VGY / N + 0.5 / LMH], 1)
cx_ = np.minimum(gx0 + s_ // 2, N - 1); cy_ = np.minimum(gy0 + s_ // 2, N - 1)
qmat = BLKMAT[cy_ // matmap.BLOCK, cx_ // matmap.BLOCK]
tile = (gy0 // TILE) * 64 + gx0 // TILE
# render winding (clockwise from above) over the collision diagonal
TRI_T0 = ((0, 2, 3), (0, 3, 1)); TRI_T1 = ((0, 2, 1), (1, 2, 3))

# ---- meshes = (cell set, node, tile, material)
gkey = ((cs_id.astype(np.int64) * len(nd) + qnode) * 4096 + tile) * 64 + qmat
order = np.lexsort((gkey, qmat))
gs = gkey[order]
cut = np.flatnonzero(np.r_[True, gs[1:] != gs[:-1], True])
P, NR, UV, LM, WF, TRI = [], [], [], [], [], []
mfirst, mcount, mmat, mflag, rfirst, rcount, rnode = [], [], [], [], [], [], []
nverts = ntri = 0
def emit(qi, refs):
    global nverts, ntri
    tris = []
    for tset, sel in ((TRI_T0, F["ttb"][qi] == 0), (TRI_T1, F["ttb"][qi] == 1)):
        for t in tset:
            ok = sel & np.all(~np.isnan(CZ[qi][:, list(t)]), 1)
            tris.append(vinv[qi][ok][:, list(t)])
    tris = np.concatenate(tris)
    gv, loc = np.unique(tris, return_inverse=True)
    loc = loc.reshape(-1, 3)
    pos_, nrm_, lm_ = VP[gv], VN[gv], VLM[gv]
    s1_, s0_ = mside[qmat[qi[0]]]
    ys_, xs_ = np.minimum(VGY[gv], N - 1), np.minimum(VGX[gv], N - 1)
    if s1_ == s0_:
        wf_ = np.ones(len(gv))
    else:
        A_, B_ = next(iter(s1_)), next(iter(s0_))
        wa = matmap.layer_weight(act, RAW, A_, ys_, xs_)
        wb = matmap.layer_weight(act, RAW, B_, ys_, xs_)
        wf_ = matmap.s30_pair_alpha(wa, wb, A_, B_, MTOP[qmat[qi[0]]])
    sk_tris, sk_p, sk_n, sk_w, sk_l = [], [], [], [], []
    nv = len(gv)
    for si, (i0, i1, _) in enumerate(QSIDES):
        dep = skirt_depth[qi, si]; m = dep > 0
        if not m.any(): continue
        a = np.searchsorted(gv, vinv[qi][m, i0]); b = np.searchsorted(gv, vinv[qi][m, i1])
        k_ = np.arange(m.sum())
        a2 = nv + 2 * k_; b2 = a2 + 1
        sk_tris.append(np.concatenate([np.stack([a, a2, b2], 1), np.stack([a, b2, b], 1), np.stack([a, b2, a2], 1), np.stack([a, b, b2], 1)]))
        lo_a = pos_[a].copy(); lo_a[:, 2] -= dep[m]; lo_b = pos_[b].copy(); lo_b[:, 2] -= dep[m]
        sk_p.append(np.stack([lo_a, lo_b], 1).reshape(-1, 3)); sk_n.append(np.stack([nrm_[a], nrm_[b]], 1).reshape(-1, 3))
        sk_w.append(np.stack([wf_[a], wf_[b]], 1).ravel()); sk_l.append(np.stack([lm_[a], lm_[b]], 1).reshape(-1, 2))
        nv += 2 * int(m.sum())
    if sk_tris:
        loc = np.concatenate([loc] + sk_tris); pos_ = np.concatenate([pos_] + sk_p); nrm_ = np.concatenate([nrm_] + sk_n)
        wf_ = np.concatenate([wf_] + sk_w); lm_ = np.concatenate([lm_] + sk_l)
    if nv > 65535: return False
    P.append(pos_); NR.append(nrm_); UV.append(pos_[:, :2]); LM.append(lm_); WF.append(wf_); TRI.append(loc + nverts)
    mfirst.append(ntri); mcount.append(len(loc)); mmat.append(int(qmat[qi[0]])); mflag.append(0x100A00)
    rfirst.append(len(rnode)); rcount.append(len(refs)); rnode.extend(refs)
    ntri += len(loc); nverts += nv
    return True
def mesh_refs(qi):
    mn = qmn[qi].min(0); mx = qmx[qi].max(0)
    own = int(qnode[qi[0]]); refs = [own]
    pr = int(node_root[own])
    for c in csu[cs_id[qi[0]]].split(","):
        c = int(c)
        if c == pr: continue
        if np.all(nd["mn"][c] <= mn) and np.all(nd["mx"][c] >= mx):
            refs.append(int(descend([c], mn[None], mx[None])[0]))
        else:
            refs.append(c)
    return refs
for g in range(len(cut) - 1):
    qi_all = order[cut[g]:cut[g + 1]]
    parts = [qi_all]
    while parts:
        qi = parts.pop(0)
        if not emit(qi, mesh_refs(qi)):
            h = len(qi) // 2; parts[:0] = [qi[:h], qi[h:]]
P, NR, UV, LM, WF, TRI = [np.concatenate(x) for x in (P, NR, UV, LM, WF, TRI)]
rcount_ = np.array(rcount)
print("verts", nverts, "tris", len(TRI), "meshes", len(mfirst), "refs/mesh %.3f" % rcount_.mean(), "max refs", rcount_.max())
np.savez(ROOT + "terrain_meshes.npz", pos=np.array(P, np.float32), nrm=np.array(NR, np.float32), uv=np.array(UV, np.float32),
         lmuv=np.array(LM, np.float32), col=np.concatenate([np.full((nverts, 3), 255, np.uint8), np.round(np.clip(WF, 0, 1) * 255).astype(np.uint8)[:, None]], 1),
         w_first=np.array(WF, np.float32), tri=np.array(TRI, np.uint32), mesh_first_tri=np.array(mfirst, np.int32),
         mesh_tri_count=np.array(mcount, np.int32), mesh_material=np.array(mmat, np.int32), mesh_flags=np.array(mflag, np.uint32),
         mat_names=np.array(mats), ref_first=np.array(rfirst, np.int32), ref_count=np.array(rcount, np.int32), ref_node=np.array(rnode, np.int32))
json.dump({"materials": mats, "sides": [[sorted(matmap.TOK[x] for x in S) for S in sd] for sd in mside], "source": list(msrc),
           "top": [int(x) for x in MTOP]},
          open(ROOT + "terrain_materials.json", "w"), indent=1)
