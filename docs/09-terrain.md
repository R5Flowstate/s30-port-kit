## 9. Terrain (S30 heightmap renderer + RVT -> S21 lit-bump world meshes)

S30 draws terrain with a heightmap renderer + runtime virtual texture; S21 has neither, so the ground must be
generated as BSP world meshes. Tools: `tools/terrain/`.

Inputs, structurally (no per-map offsets):
- `hfgrid.py` -> `hf_grid.npy` (float32 [NY,NX], NaN = hole) from `client_v51_prehf` BVH patches.
- `terrain_inputs.py <map .dec> <extract/texture> <scratch>` -> `layers.json` (llyr names in splat order),
  `rvt_layer_descs.json` (tint/flags/ramp per layer; desc stride 64), `splat_w.npy`; prints the splat-ID guid.
  rmap +0x60 -> RVT desc: +0x00 weights txtr (4096^2 BC5), +0x08 IDs txtr (1024^2 RGBA32_UINT), +0x80 layer
  count, +0x10 descs, +0xC8 llyr guids; llyr +0x08 -> S21 material path. (Weights decode matches mu5's within
  +-1/255 BC5 rounding.) The S30 heightmap grid == collision heightfield grid (mu5 4096^2, 22.222 u, origin -45500).
- `twin_ground_mats.py` -> `terrain_mats.txt` (tie ordering only).
Splat: ID texel = 4x4 weight texels; `.w` = 4-slot palette (0xFF unused); `.x/.y` 6 x 5-bit, `.z` 4 x 5-bit
masks (texel k = (y&3)*4+(x&3), word k//6, shift (k%6)*5). Weights: 1 active R=255; 2 -> R,G; 3 -> R,G,1-R-G.

**GROUND = ONE S30 RVT MATERIAL (current, USER-VERIFIED 2026-10-20: "the perfect blending").** Two-layer blend materials can NOT match S30: S30 mixes up
to 3-4 layers per texel; any per-block pair scheme leaves square cuts wherever 3+ layers meet (blendsim image shows
it; the mean error hides it). Instead the terrain is drawn with ONE material whose pixel shader IS the S30 RVT
composite:
- `s30layers/rvt_shader.py`: takes the converted S30 two-layer world blend PS (0x08C913B24CC7DFD6 entries 0/1, so
  lighting, lightmaps, cubemaps, shadows and the G-buffer writes stay the converted code), forces the slot blend to 0
  and overrides the slot-0 surface terms (albedo, normal pair, gloss, ao, spec) with `RvtComposite(world xy)`:
  splat decode (ids 1024^2 uint4 per 4x4 texel block + weights 4096^2 BC5, the S30 textures themselves), 2x2 bilinear,
  up to 8 layers, S30 stacking sat(((w-mid)+fade(w)*h)*ramp+w)*rem in the NEAREST texel's palette order, normalise,
  weighted layer samples. Old material textures are moved to t100+ (dead -> stripped). Bindings t0..t7 only.
- `s30layers/build_rvt.py`: 28-slice 2048 Texture2DArrays (albedo BC1 sRGB from col_bake, normal BC5, gloss BC4,
  ao BC4, height BC4), params RGBA32F 32x2 (ramp, mid | spec), new PS MSW (entries 0/1 replaced), new shaderset
  (numPixelShaderTextures 8), material `world/terrain_s30/<stem>_rvt` (+colpass) and inv/extra_matl.txt.
  **TRAP: Repak does not stream texture arrays** -- it packs every mip in the rpak. An array json that lists
  mandatory/optional mips makes the header claim streamed mips the starpak never got: the engine reads garbage
  (mu5: dirt rendered RED, `[STREAM-ABORT] tex=<array guid> 2048x2048 ... 0:7340032` every frame). Arrays are
  `permanent` x 12 (rpak 448 -> 916 MB).
  S21 retail PS bind Texture2DArray in material slots (8 PS, albedoTexture t0) -> supported; retail max slot t22.
- `matmap.RVT_SINGLE` (choose_rvt): one material for every block; mu5 6279 terrain meshes, 75 sorts, worst-case
  terrain batches 208 (pairs were 931-1452 -> flicker). Gates: pso_link/vsfmt/bindcheck 0, check_bsp/vcheck/surf/cell.
- Not yet in the shader: S30 hex stochastic tiling (3 hashed samples, exponent = CBufRuntimeVirtualTextureStatic
  m0[2].w, value not read yet) and the per-texel stochastic LUT on the hex blend (baked LUT is exact for 1 sample).
Pair-material path below (choose_s30 pairs, s30_pair_alpha, mask_bake, synth_s30_mats) is kept for reference.
Materials -- S30 PARTS ONLY (`matmap.choose_s30` + `s30layers/`). NEVER ship twin (hu) materials, textures or
S21 retail shader bytes on the ground (user rule 2026-10-20: retail is the oracle, never a donor):
- S30 has NO per-layer materials for most layers: the RVT renders from the `llyr` assets themselves. Each llyr
  carries its textures in the starpak at its asset starpak offset: 3 (or 6 when +0x48 size2 != 0) 128 px BC7
  low-mip textures (8 levels, 0x5570 B each), then Oodle tile streams (stream 0 at base + n*0x5570; tile comp
  offsets u32[85] @ llyr+0x38, total size u32 @ +0x30; stream 1 offsets @ +0x50, size @ +0x48). Tile raw =
  0x30000 = three 256x256 BC7 textures; 85 tiles = mip0 8x8 + mip1 4x4 + mip2 2x2 + mip3 1, row-major, no
  borders -> 2048 px layer. Channels (correlated 0.92-0.996 vs S30's own dirt_01 maps): t0 rgb albedo (LINEAR),
  t0.a normal X, t1.a normal Y, t1.b gloss, t2 rgb spec (linear), t2.a = ao^2.2. Stream 1 = macro normal set (unused).
  Oodle: `oo2core_8_win64.dll` (Repak-Clean bin), OodleLZ_Decompress with exact raw size 0x30000.
- Tiling: RVT samples a layer at the virtual-texture density (rvt worldSize 91000 / 728000 texels = 8/unit) ->
  2048 px repeats every 256 units (== S30 dirt_01 material uv 1/256). RVT desc +12/+13 floats are a macro
  scale, NOT the tiling. Tint = RVT desc +0 float3 (layers 5/6 share one llyr with different tints).
- **ALBEDO IS NOT THE RAW SAMPLE** (composite CS 0xAD1DC52FC16EB15E, `materialDesc` = the 64 B RVT desc):
  flags & 1 = stochastic: the stored albedo is GAUSSIANIZED; real colour = per-channel LUT[byte(s)] from llyr +0x10
  (256 dwords, R low byte, G >>8, B >>16; llyr +0x18 = per-mip means for hex-tile variance restore). flags & 2 =
  overlay with macro (desc +8, 0.5 = neutral), else 2*macro*s; then * tint (desc +0). Shipping raw = grey noise
  on ice/sulfur/soil/dirt_03/mud/lava (user saw 'blends wrong'). `col_bake.py` bakes this per LAYER INDEX
  (layers 5/6 share a llyr) into BC1 sRGB 2048; dirt_01 bake mean == S30 dirt_01 material mean (103/86/76).
  Uber tints are then 1. llyr +0x60 floats are only approx albedo; desc macro is 0.5 on all mu5 layers.
- `llyr_extract.py`: per llyr col BC7 UNORM (raw blocks re-tiled; linear data == sRGB-decoded value), nml BC5,
  gls BC4, ao BC4 (gamma like the S30 map), 12 mips, guid = hash(texture/world/terrain_s30/<layer>_<kind>.rpak).
- **Pair choice and vertex alpha use S30-STACKED weights, never the raw splat weights.** `matmap.s30_stack`
  runs the RVT weighting (below) at every sample with h = layer mean height (`layer_heights.json`); gen_terrain2
  feeds it to `choose_s30` and to the vertex alpha. Raw weights pick the wrong pair: S30 stacking lets top palette
  layers (snow, moss, cobble) win at low raw weight, so a raw-weight pair drew mostly dirt where S30 shows snow
  (blendsim: mu5_l00_l18 error 25 -> 5, oracle-pair floor 4.8). `choose_s30`: per 4x4 block the two strongest
  stacked layers (second only if >= 2%), pair (min, max); pairs under 0.03% of map area merge into the
  best-covering kept pair. mu5: 83 materials, 21425 meshes, 6.74M tris (same as before).
- `synth_s30_mats.py`: clone of an S30 converted two-layer blend (`desertlands_snow_04_wld_to_snow_03_wld_bm`,
  shaderset 0x5295C528B0A52A77): slot1 side t1/t3/t5/t7 + uv1 (uber @0) + L1 tint @404 / spec @432 = layer A;
  slot0 side t0/t2/t4/t6 + uv2 (@24) + L0 tint @356 / spec @388 = layer B;   colpass clone too; uv = scale 1/256, translate (45500,-45500).
  **TRAP: the S30 blend PS OVERLAYS slot-0 albedo with the detail map (t8, uv3)** -- the template's
  is the ice macro map (mean 148/163/178) -> every blend tinted blue/brightened. Slot 8 = flat 128 RGBA8 4x4
  (`neutral_detail`, exact no-op: 128/255-0.00196 = 0.5). **Blend = sat(a*(1+k) + k*(mask-1)), k = uber @140**: the
  template mask means 0.21, so any k > 0 biases hard to slot 0.
- **Height blend (S30 texelIDsAndWeights CS 0xAD828A9C520451B9)**: layers stacked from the top palette slot down,
  w' = sat(((w - mid) + fade(w)*h)*ramp + w) * remaining (fade = 1-(1-4w)^4 for w<0.25; mid desc+6, ramp desc+5);
  ALL slots run the formula (slot 0 too), then weights are normalised; splat weights are bilinear per texel.
  height h = llyr tile texture 1 RED (stones bright, mean ~0.5; `materialHeightUnstreamed.x`). The stacking goes
  into the vertex alpha (s30_stack); the per-texel height breakup is the material: `mask_bake.py` writes per pair
  mask = 0.5 + hA - hB (BC4 1024) into slot 10, synth sets k = 1 (blendsim sweep: k barely matters once alpha is
  stacked; 1 best). Remaining error vs S30 = third layers per block (S30 stacks up to 4 per texel) and straight
  block-edge cuts where the neighbouring pair lacks a layer -- the two-layer-per-block floor.
- **CURRENT MODEL (per-texel, blendsim error 0.2-5 vs S30):** vertex alpha must stay SMOOTH -- sharpening at the
  vertices (stacked alpha) makes every edge a staircase along the 22 u triangle grid ("looks like squares"). Vertex
  alpha = `matmap.s30_pair_alpha(raw wA, raw wB, top)`: reproduces sat(w(1+r) + r(fade(w)*h - mid)) of the TOP layer
  (higher palette slot, `s30_pair_top`, saved as terrain_materials.json "top") with fade folded in via the layer mean
  height (fade kills the height term at low weight -- without it moss bleeds over dirt). Mask = h(top) if top is slot 1
  else 1 - h(top); k = ramp(top). Pair CHOICE still from stacked weights over the CLOSED block (own samples + shared
  edge samples, `CLOSED_BLOCKS`) so a layer on a shared border is in both neighbours' pairs.
- `blendsim.py` (offline A/B, no game): renders a 32x32-sample patch around a material with the exact S30 RVT
  weighting (reference), our materials, and the oracle pair (S30 weights restricted to our pair); writes
  `ab_<material>.png` (left S30, right ours) and mean sRGB error. Use it before every terrain-material change.
Legacy twin path (matmap.choose + texsides.py), kept for reference only:
- Catalogue = world `_wldc` materials in the map pak (R) or the twin pak (H) whose NAME carries layer names.
  Only materials with a `_wldc` variant draw on BSP meshes.
- `_bm` blend: vertex colour byte 3 = blend; **slot 1 (albedo2Texture, t1) = what alpha 255 shows; NAME order
  is not slot order** -> sides come from texture identity (`texsides.py`: albedo image = top-mip hash, labels
  seeded by single-layer materials, propagated through blend materials). Some S30 materials are mis-named
  (mu5 `snow_04_to_snow_03_bm` slot 0 is the ice image) -- identity handles it.
- Per 4x4 block the material with max own-layer coverage; twin (H) materials only when they gain >= 5% and
  keep >= 0.05% of map area. mu5: 95.35% of splat weight drawn by its own layer.
- The twin `_bm` PS are type-9 aliases -> shipped as parent bytes; their textures come from the twin with
  verbatim S21 tails (`texture_s21`). Source HD mips from the OLD root s21-full (<DEPLOY> lacks
  `.opt.starpak`). `stage_hu.py` stages them (and unstages the previous set).
- Twin `_bm` PS are retail S21 shaders (RGBM lightmap decode); converted S30 PS read lightmaps raw -> both on
  the landscape page, which is why lightmaps must be type 9 (section 10).
- RVT tint: desc +0x10, stride 64; mu5 tint 1.0 on 23/28 layers, no tinted layer drawn (gen_terrain2 warns).
  llyr +0x60 floats are approx albedo, NOT tint. All layers sample in WORLD XY (uv = world x,y).

Geometry (`gen_terrain2.py` -> `terrain_meshes.npz` + `terrain_materials.json`):
- Surface = EXACTLY the hf2tris collision triangles (same vertices/diagonals; `hfpatches.py`), quads > 4
  samples subdivided on their own planes, crack skirts only where patch borders disagree. A decimated
  surface leaves feet inside the ground (mu5 old gen: max 1722 u off). Spacing = extent/(dims-1), not /dims.
- Winding: retail ground is clockwise from above (cross.z < 0, 99.9%).
- Lightmap uv = sample/N + 0.5/landscape page dims (page = 0x53 entry with tag 1, found at run time).
- Lit-bump vertex 0x49 (32 B): pos u32, nrm u32, uv f2, **colour u8[4] @+16**, lightmap uv f2, **tangent u32
  @+28** (index into 0x1E, bit 31 = bitangent sign). Colour at +28 -> loader reads a ~2^31
  tangent -> AV at SignonState(3), then NULL world model (gate `vcheck.py`).

Culling/visibility (`inject_terrain.py`, `cellbsp.py`, `references/inject_notes.md`):
- **Material sorts shared per material** (u16 vertex window); one sort per mesh = map-wide flicker
  (mu5: 4538 sorts vs retail 581).
- Cull walks **per-portal-cell trees**: cell i = 0x77 root node i; point -> cell via cell BSP 0x6A over planes
  0x01 (d = n.p - w, d<0 -> child+1). Reference a mesh in **every cell its geometry touches** (retail ~1.1
  refs/mesh); refs in the first containing box only = 20% of terrain in unreferenced cells. Gate `cellgate.py`
  (retail 0.02%).
- 0x77 node refs (0x78) must be laid out **depth-first** (own refs, then each child subtree in index order;
  empty subtree -> offset 0): a fully visible node marks its 0x25 total as ONE run. Index-order packing lit
  other subtrees' objects (map-wide flicker). check_bsp gate "subtree refs are not one run".
- 0x27 per-node u16 fade distance: ground only on never-fade (65535) nodes. 0x25 = per-node ref totals.
  Level info field 3 = firstPropObjID read verbatim (injector shifts prop ids).
- 0x51 = dmeshbounds_t {origin, radius, extents, tanYaw}; tanYaw 0 = plain AABB (only dlight/sphere cull read it).
- **S21 WORLD TRIANGLE BUDGET (terrain vanishes from the sky).** S21 builds one per-frame index buffer for all
  visible world meshes: 0x900000 u16 indices = 3,145,728 tris (alloc `mov r9d,1200000h` x3,
  check `cmp eax,900000h`). Meshes are walked in mesh-index
  order and the first one that does not fit ENDS the walk; terrain is inserted after the map's meshes, so when
  enough is in view (high camera) every terrain mesh after the cut is dropped. Later engines have the same knob
  as `-increase_index_limit` (0x1B00000). Retail hu: 3.03M world tris total (fits). mu5: 2.48M own + 6.74M
  terrain = 9.22M. Portal/cell refs are NOT the cause (pvs sim: hu and mu5 alike, ground cells reachable).
  Gate: `check_bsp.py` "world triangle budget" (`--world-tri-budget N` when the client buffer is raised).
  `terrain_lod.py` re-triangulates the npz (RTIN, crack-free, material borders exact, alpha-linear check,
  binary-searched error or `--max-error`), but its floor is set by material fragmentation: per-4x4-block
  materials (44% of blocks border another material) -> >= 4.7M tris at any error. Fit the stock budget only
  with coherent material regions (>= 16x16 samples), or raise the client buffer.
- **S21 WORLD BATCH CAP (distant terrain/map flicker):** the same builder holds 0x800 batches per frame
  (`worldBatches[2048]`, "Exceeded PVS_MAX_WORLD_MESH_BATCHES";
  S30 identical -- NOT raisable). A batch starts whenever consecutive visible meshes differ in the tag
  {shaderLOD (distance band from the engine), materialSortID, flags & 0x40, dlightBits}; past the cap the walk stops
  and which meshes drop changes per frame. S30 fits only because its terrain is not world meshes. `inject_terrain.py`
  orders terrain meshes by material then Morton (z-order) so LOD bands form long runs (mu5 worst-case terrain batches
  1452 -> 931). Keep terrain mesh count and material count low.
Gates: `check_bsp.py <bsp>` PASS (header lengths, level info, indices, mesh bounds, visibility, world triangle budget), `vcheck.py`,
`surfgate.py <dir> <bsp name> <mesh lo> <mesh hi> <hf v0> <hf v1>` max |dz| <= 2 u (mu5 0.0019),
`cellgate.py <dir> <bsp name> .bsp_lump <lo> <hi>`.
