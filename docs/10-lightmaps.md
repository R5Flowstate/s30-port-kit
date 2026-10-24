## 10. Lightmaps (type 11 -> 9, real-time lights mandatory)

- S30 `0x53` (8 B: type u8, compressedType u8, tag u16, w u16, h u16): mu5 = 4 x 1024x992 + 1 x 384x384
  (tag 1 = landscape), all type 11 / compressed 8. Type 11 = plane 0 RGB9E5 shared-exponent HDR + plane 1
  RGBA8 (direction xyz*2-1, w). S21's tables equal S30's for types 0-10 and have no 11; the S21 0x62/sky/RTL
  loaders walk types 1/9/10 only; S21 reads ONLY 0x62 (0x61 compressed is never read).
- **Shader rule (S30 `Mod_LoadLightmapHeaders`):** c_branchFlags bit 15 (RGBM decode) is set iff
  page 0 type is 1 or 4..8; HDR types 9/10/11 are read raw. S21 never sets it; retail S21 PS always
  RGBM-decode (`rgb * (a*3.984375 + 0.015625)`).
- **Ship type 9** (bspconv `832a309`): plane 0 RGBA16F, rgb = RGB9E5 value (lossless), alpha half `0x33E8`
  -> RGBM scale 1.00005; raw readers and RGBM readers both see S30's value. Not type 1 (clamps at 4; mu5
  reaches 39) or type 10 (alpha 1 -> retail PS x4). 0x62 grows 8 -> 12 B/texel.
- **0x69/0x7A are mandatory**: every world PS (retail and converted) computes the lightmap UV as
  `TEXCOORD5 / min(size(t65 RealTimeLightIDs), 1024)` and the VS writes TEXCOORD5 in texels. Without them t65
  is the engine's 1x1 default -> one corner texel per page = black.
  RTL sizes: 0x69 in-rpak 6N bytes (header 6.5N), 0x7A 126 B per page (63 u16 light ids); per-texel id u32
  bits 24-31 = page, 6-bit slots (63 = none) -> `s_globalLights[id+32]`; world lights 0x36 112 B (cap 16352).
- Placement: 0x61/0x62/0x69 in perm (retail flags 0x8 zero-copy; Repak writes 0x4 = heap copy at load,
  works), 0x53/0x7A in temp (retail 0x11). 0x69 data alignment 256.
- Gate `tools/gates/lmgate.py <S30 maps dir> <client_v51>`: raw 0 mismatches, RGBM max rel err < 1e-4,
  plane 1 identical. `tools/gates/hdr.py` dumps 0x53/0x61/0x62/0x69/0x7A sizes.
- The 21:09 crash once blamed on RTL (heap AV `r5apex_dx12+0x571C41`, read FFFF..., PTR_MASK in a register,
  texture streamer the engine freeing its staging buffer) is the texture-streaming heap class; every RTL
  CPU path is bounded.
