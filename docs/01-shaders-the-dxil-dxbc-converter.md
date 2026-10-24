## 1. Shaders: the DXIL -> DXBC converter

**What it is.** S21's renderer can only create DXBC (SM5) shaders; every S30 shader is DXIL (SM6), and
bytecode cannot be patched across that gap. The converter decompiles and recompiles each shader entry:

```
S30 DXIL blob
  -> dxil-spirv --use-reflection-names --ssbo-srv   (DXIL -> SPIR-V, keeps names)
  -> SPIRV-Cross --hlsl --shader-model 60           (SPIR-V -> HLSL text)
  -> dxil2s21.py ABI rewrite                         (S30 data layout -> S21's, BY NAME)
  -> fxc /O3 vs_5_0 | ps_5_0                         (HLSL -> S21 DXBC)
```

S30 DXIL keeps full reflection (every cbuffer member name/offset, every resource name), so the rewrite
remaps by NAME, not by guessing offsets:

- cbuffers: `CBufUberStaticSingular`->`CBufUberStatic` b0, `...DynamicSingular`->`CBufUberDynamic` b1,
  `CBufModelInstance` b2, `CBufCommonPerCamera` b3 (S30 1024 B vs S21 880 B, members reordered).
  `c_dof` moves camera -> uber-dynamic; `c_ssaoMode` == S21 `c_separateIndirectDiffuse`.
- Pins (S21 runs these paths itself): `c_pbrFalloffBiasSq`=1.0; exposure/irradiance-volume/debug = 0.
- Cubemap cb6 `CubemapSample_s` -> S21 `g_cubemapSamples` t72: rows 0..63 = `(0,0,0, t72[id])`,
  rows 64..127 = 0, row 128 = `(lum(c_skyColor), 0, ...)` = S30 "mode 0" (row128.y = 0), which is the
  formula retail S21 PS use. The per-cubemap value must be the reciprocal (section 11).
- Structured buffers -> `StructuredBuffer<struct{uint w[N]}>`, word offsets remapped by field name
  (`g_modelInst`, `g_gpuBones` field order differs, lights, foliage).
- `shadowmapSampler` s15 -> s0; `ssaoTexture`/`exposureTexture`/`Bindless3d` removed (gates pinned).
- Quad ops emulated with `ddx_fine/ddy_fine`. Signature semantics restored from location
  (= DXIL register), members declared in DXIL (register, component) order, SV_ last
  (else fxc X4576 / VS-PS packing mismatch).
- **Cubemap index (props)**: S30 `c_modelInst.cubemapState` packs primary<<21 | secondary<<10 (2047 = none) | blend
  weight (10 bits); S21 `cubemapID` (cb2[7].x / g_modelInst w[28]) is the PLAIN index. A field alias reads `>>21` = 0 ->
  every converted prop samples cubemap 0 (black metals). The converter emits `(id << 21) | 0x1FFC00`. World meshes
  use the 6-bit vertex CUBEMAP_ID in both builds (no change).
- Bindless permutations dropped. S30 VS are instanced-only: static entries are synthesized
  (`g_modelInst[i]` -> `CBufModelInstance`); instanced PS synthesized from cb2 PS when the S21 twin has one.
- `c_branchFlags` (per-camera +604): bit 0x400 = ray-traced lights on (0 on S21 -> lightmap RTL lookup runs);
  bit 0x8000 = RGBM lightmap decode, which S21 never sets (section 10).

Run (from `<WORK>\dxil2dxbc`; copies in `tools/dxil2dxbc/`):
```
python split.py <extract\shader> blobs          # DXBC containers out of RSX MSWs
python run_all.py                               # convert every blob + twin gate -> run_all.json (~3.5 min)
python build_msw.py                             # S21 v15 shader MSWs -> extract\shader_s21 (~3 min)
python fix_shaderset_msw.py                     # shaderset MSWs with REAL counts -> extract\shaderset_s21
python tools/client/pso_link_gate.py          # ENGINE pairing: must print bad 0
python tools/client/vsfmt.py                  # VS ISGN vs flags[0] vertex-format mask: bad 0
python tools/client/bindcheck.py              # every bind slot has S21 precedent: 0
```

**DX12 PSO LINKAGE (the client crash after `pak loaded '<map>.rpak'`).** Material load prewarms a PSO per
(VS entry, PS entry) pair; a VS/PS signature mismatch makes `CreateGraphicsPipelineState`
fail -> NULL PSO into `ID3D12PipelineLibrary::StorePipeline` -> AV in `D3D12Core.dll`
(read 0xE0), frame `client.dll Hook_PsoCreateGraphics` (pass-through). D3D11 validation cannot see it.
Engine pairing: one dim tuple (d0 variant, d1 instancing, d2) indexes BOTH shaders, entry =
d0*sc0 + d1*sc1 + d2*sc2 of EACH header; d1 walked only if the VS has sc1. Retail law (3,620 S21 PS):
static slots (d1=0) NEVER read SV_InstanceID; only the (variant1, instanced) VS entry outputs it.
build_msw traps fixed: any live instanced entry => instancing dim; static synthesis never falls back to
instanced bytes; a static entry still carrying ID/COLOR1 is a hard error; S21-owned GUIDs ship OUR
converted bytes too; static skinned VS get COLOR1 zeroed.
Gates (mu5): 0 unmapped fields, 0 fxc errors; twins: binds 277/277, ISGN 276/277, headers 169/173;
shaderset counts 94/94; RSX `-validateshaders` 0 flagged.

S21 v15 header law: envOptScales[0..1] = cumulative strides of [variant, instancing]; sc4 (PS) / sc5 (VS) =
live count; debug half = null entries; bit 54 on the feature word only when bit 50 is set; aliases copy
the source slot's flags.

**TRAP: RSX v15 shaderset reader uses the v12 struct** (counts at +0x10, not +0x18) -> every exported shds
MSW has ZERO counts -> materials bind no textures. Always regenerate from the pak header
(`fix_shaderset_msw.py` / `tools/client/fix_common_shds.py`).

**Unconvertible = bindless-only shaders** (`build_msw`: `no convertible entry`); on mu5 all 14 were
particle materials, dropped by the dead-shaderset rule.
