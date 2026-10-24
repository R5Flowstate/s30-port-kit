## 11. Cubemaps + ambient reciprocals (`build_cubemaps.py`)

- S30 `cube` v2 per BSP sample (`cubemap/maps/<map>/cubemap_<i>.rpak`): +0x10 u16 w/h, +0x14 index, +0x18 cmp
  size, +0x1C size, +0x20 comp (3 = Oodle), +0x24 = the cube's MEAN LUMINANCE; payload BC6H_UF16, 9 mips,
  mip-major, 6 faces/mip -> S21 one BC6H cube array `texture/maps/<map>/cubemaps_hdr.rpak` (6 x samples).
- **World PS scale every reflection by `sat((|lmDir|*lum(c_skyColor) + lum(lightmap)) * g_cubemapSamples[id])`**;
  t72 comes from txtx `texture_extension/maps/<map>/cubemaps_hdr/cubemap_ambients.rpak` (BSP 0x2B is all-zero
  on every map). It must hold the RECIPROCAL `1/max(0.001, |v|*lum(sky) + ambient)` of the light probe nearest
  the cubemap origin: probe = 0x68 refs (20 B: pos f3, probe index u32, u16, u16) -> 0x65 probes (44 B: int16
  SH R/G/B x4 (DC in .w) + int16[12..14] light vector, /8192); ambient = 0.2126 R.w + 0.7152 G.w + 0.0722 B.w;
  sky = lum(sRGB^2.2 of light_environment `_ambient` rgb) * brightness/255. That is exactly what S30 rebuilds
  each frame (the engine, cube manager +0x1028 sky colour); retail S21 bakes the same (hu 1.1..82,
  cap 1000). Shipping cube +0x24 instead made metals/shiny materials black (30-5000x too dark).
- The map pak's own txtx (16 x RGBA8 on WE) is copied verbatim. Writes `inv/cubemaps.json` (needed by the
  manifest). In-place fix for a built pak: `tools/gates/patch_txtx.py`. Validation tool: `gates/probefit.py`.
