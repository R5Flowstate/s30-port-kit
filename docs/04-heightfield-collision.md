## 4. Heightfield collision (dedi crash + client crash + stuck)

S30 world collision uses **BVH leaf type 10 = heightfield**. S3 dedi leaf dispatch
has NULL at 0, 1, 10, 11 -> first TraceLine into terrain calls address 0
(`AV(execute) rip=0`, frame1 `r5apex_ds+0x2db20c`). The S21 client misreads the patch (+6 = `idx<<1 | diag`)
-> AV `r5apex_dx12+0x304A99`. Fix both with `tools/dedi/hf2tris.py <maps dir> <map> <dir with 0x15/0x16>`:
every patch -> type-4 uncompressed-triangle leaves in a type-3 bundle; verts appended to 0x03, leaves to 0x13,
nodes repointed in 0x12, **header lump lengths rewritten** (stale lengths = garbage leaves = AV in leaf handlers).
- Patch `CollLeafHeightfieldPatch_s` (40 B): coord u16[2], dims u8[2], bit0 triangulate dir, bits1-7 hf idx,
  quadsPerPlane, maxErr, heightDataOffset, heightBias, heightScale, rle u64, surfProp[4] u16. Heightfield
  (0x15, 40 B): origin, scale, dims, count; heights = 0x16 bytes; z = (s-1)*heightScale + heightBias, s=0 hole.
  Triangle order/diagonals match the engine's heightfield patch iterator.
- Type-4 leaf: u16 `surfProp | (nTris-1)<<12`, u16 `base>>10`; per tri u32 `delta:11 | o1:9<<11 | o2:9<<20`
  **| edge-contact bits 29-31**; v0 = base+=delta, v1/v2 = v0+1+o. Rotate so the min index is first.
- **Bits 29-31 must be 7 on every triangle** (`EDGE_ALL`; retail hu 2.34M/2.34M). The S3 hull sweep
  (the engine/the engine) spreads them with `73*(w>>29)` and emits edge/vertex contacts only for set
  bits; 0 = face-only contacts = players stick / "seesaw" walk. Ray traces ignore them (looked fine).
  `tools/terrain/t4edges.py` patches an already-built 0x13 in place.
- World vertex base is +1 (0x0E model 0 int[4]); leaf index is 24-bit (mu5 used 12.5M of 16.7M); only world
  nodes (`< model1.bvhNodeIndex`).
Gates: `bvhscan.py` (types {0,1,3,4,5,6,7,8,9} only), vertices inside node bounds, +Z normals, every external
lump size == header len. mu5: 128,140 patches -> 5.13M tris. Keep originals (`*_prehf`); move `.prehf` files
out of the maps dir before `vpk_tool newmap` (it stages everything).
