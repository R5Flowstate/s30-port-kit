# Terrain inject (S21 v51 client BSP)

hu is the format oracle. Geometry is appended; hu meshes are not copied. Source `client_v51` is not modified. Output is a full copy under `client_v51_terrain`. Header stays rBSP v51, flags 1. `fileofs`, `version`, and `uncompLen` are copied. `filelen` is set to the sidecar size for every lump that has a file, except 0x69 (padded RTL length, left alone). Sidecars are lowercase `%04x`.

## Mesh 0x50, 28 bytes `IH8hHI`

| bytes | field |
|---|---|
| 0 u32 | first index into 0x4F |
| 4 u16 | triangle count |
| 6 u16 | first vertex = min index in that slice |
| 8 u16 | last-first = max index - min index |
| 10 u8, 11 u8 | vertex type, cubemap (255 = none). Lit bump is type byte 2 |
| 12 u8[4] | styles, 0 |
| 16 i16[2] | luxel origin |
| 20 u16 | fade, 0 |
| 22 u16 | material sort |
| 24 u32 | flags |

The eight shorts are bytes 6..21. `(flags >> 9) & 3` selects the vertex lump: 1 = 0x49 / 32, 2 = 0x47 / 20, 3 = 0x4A / 24. Retail ground `0x100a00` is lit bump (`0x200`). Luxel is `floor(min lightmap uv * page width/height)` on the lightmap page, clipped to the page. Lightmap uv stays page-normalized 0..1, matching hu. Page 4 of mu5 lump 0x53 is 384x384. Lumps 0x53, 0x61, 0x62, 0x69, 0x7A are not written.

## Lit-bump vertex 0x49, 32 bytes

`u32 posIdx, u32 nrmIdx, float2 albedo uv, i32 -1, float2 lightmap uv, u8[4] colour`. Positions 0x03 and normals 0x1E are appended, so existing indices stay valid.

## Material sort 0x52, 12 bytes `4hI`

`u16 texdata, i16 lightmap header, i16 0, u16 span-1, i32 vertexOffset`. The third short is 0 on every hu sort. Cubemap is the mesh byte, not this short. Index is relative to `vertexOffset`: absolute vertex = `u16 + vo`. The window ends at the next greater `vo` used by the same vertex lump, or at the lump vertex count. `span-1` is not the clamp. New sorts are appended, one per npz mesh (split only if a mesh exceeds 65535 triangles). `vo` is the end of 0x49. Lightmap header is 4.

Texdata 0x02 is 16 bytes: name offset, width, height, flags. Names live in 0x0F, null-terminated, no `material/` prefix. A missing name is appended (2048x2048, flags `0xA00`). `world/moss/icelandic_moss_01_wld_icelandic_dirt_01_wld_bm` is already texdata 243.

## Mesh bounds 0x51, 32 bytes

`origin xyz, radius, extents xyz, yaw`. Written with yaw 0, origin at the AABB center, extents half the size, radius the extent length. The sphere contains the vertices.

## Level info 0x7B, 36 bytes `<5I3fI`

`firstDecal, firstTransparent, firstSky, firstStaticProp, numStaticProps, sun xyz, numEntityModels`. `firstStaticProp = (firstSky + 63) & ~63`. Lump 0x7A is the RTL lightmap page, not level info. Opaque meshes are `[0, firstDecal)`. New meshes are inserted at the old `firstDecal`. All four range starts increase by N, then `firstStaticProp` is recomputed. `numStaticProps` is unchanged and matches the sprp prop count in 0x23.

Model 0x0E is 64 bytes: mins, maxs, `firstMesh i32`, `meshCount i32`, 32-byte tail. The model whose range contains the insert grows `meshCount`. Models with `firstMesh >= insert` (including empty ones) shift `firstMesh` by N. Model 0 bounds grow to cover the new positions.

## Cell tree 0x77 / 0x78 / 0x79

Node is 32 bytes: `float3 mins, u32, float3 maxs, u32`. Each u32 is `count` in the low 8 bits and the index in the upper 24. Word 3 is child count and first child. Word 7 is obj-ref count and ref offset. Count max is 255.

0x78 is u32. Value `< firstSky` is a mesh index. Value `>= firstStaticProp` is a static-prop id (`value - firstStaticProp`). The gap `[firstSky, firstStaticProp)` is empty. Sky and submodel meshes are not in this array. hu leaves 3 meshes below `firstSky` unreferenced; the gate allows that budget.

0x79 is parallel to 0x78, 32 bytes: `mins xyz, 0, maxs xyz, 0`.

0x25 is a prefix of per-node subtree obj-ref totals (u32), shorter than the node count. 0x27 is one u16 per node.

Registration: existing ref slices are kept per node. Each new mesh is attached to the deepest overlapping nodes (a leaf, or an internal node whose children miss it). If nothing overlaps, the nearest leaf is expanded, and its ancestors, then the ref is added there. The bounds row is the mesh AABB clipped to the node. A leaf over 255 refs is split on its longest axis; the two children are appended (child index > parent) and the parent keeps no refs. Ref and bounds arrays are rewritten in node order and the 24-bit offsets updated. Subtree totals are recomputed and the original 0x25 length is rewritten. 0x27 is extended with zeros only when nodes were added and it was already one u16 per old node.

Before that, mesh ids in `[insert, old firstSky)` increase by N. Values `>= old firstStaticProp` increase by `newFirstProp - oldFirstProp`. The same prop-id shift is applied to 0x64. Not shifted: 0x13 (BVH vertex indices), 0x66 (lightprobe index per prop), lightmap bytes.

## CSM 0x63 / 0x64

Same 32-byte node packing. 0x64 values are either `< numShadowMeshes` (exactly the shadow-mesh indices) or inside `[firstStaticProp, firstStaticProp + numStaticProps)`. hu ground meshes do not appear in 0x64, so new world meshes are not inserted there. 0x63 and the 0x26 subtree-total prefix stay as copied when the tree is unchanged. Only prop ids move, and only when `firstStaticProp` moves.

## Gate

`python tools/check_bsp.py <map.bsp>` prints PASS/FAIL for header lengths, level info, indices, mesh bounds, and visibility, and exits 1 on any FAIL. Indices must sit in the sort window and be `< 65536`, with `fv/lv` matching min/max. Header `filelen` must equal the sidecar size (0x69: header `>=` file; missing 0x39 allowed). Meshes below `firstSky` left out of 0x78 must be `<= 3`. Obj refs must be in the mesh or prop id space. 0x25 must match the recomputed prefix. Mesh bounds must contain their vertices (sphere, 2 unit slack).

Both of these passed:

```
python tools/check_bsp.py "<MAPS>\mp_rr_desertlands_mu5\bsp\client_v51_terrain\mp_rr_desertlands_mu5.bsp"
python tools/check_bsp.py "<WORK>\mu5_terrain\hu\wrap\maps\mp_rr_desertlands_hu.bsp"
```

## Command

From `<WORK>\mu5_terrain`:

```
python tools/inject_terrain.py --src "<MAPS>\mp_rr_desertlands_mu5\bsp\client_v51" --out "<MAPS>\mp_rr_desertlands_mu5\bsp\client_v51_terrain" --npz "<WORK>\mu5_terrain\synthetic_terrain.npz" --make-synthetic
```

That wrote four 64-unit quads (centers near 384,256 at z=-12, flags `0x100a00`, the moss material) and inserted 4 meshes at index 37603: 12 obj refs, 0 cell splits, prop-id delta 0, lightmap page 4 at 384x384. Drop `--make-synthetic` to inject `terrain_meshes.npz` instead. A separate 60-mesh run moved `firstStaticProp` by 64, shifted 0x64 prop ids, left 0x63 and 0x26 unchanged, and passed the gate; that copy was deleted.
