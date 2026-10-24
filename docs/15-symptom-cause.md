## 15. Symptom -> cause

| symptom | cause | section |
|---|---|---|
| dedi `Model "...rmdl" not found and "mdl/error.rmdl" couldn't be loaded` | no / incomplete dedi model pak | 5 |
| dedi `Couldn't find asset "datatable/map_zones/zones_<map>.rpak"` (sh_map_zones.nut #173), host dies | zones dtbl only in the client pak | 5, 13 |
| dedi AV rip=0, `r5apex_ds+0x2db20c`, TraceLine on stack | heightfield leaves in BVH | 4 |
| dedi AV in leaf handlers (`+0x2E883F` type 6, `+0x2DACAC` type 8, via `+0x2DB20C`) | header lump lengths stale after hf2tris | 4 |
| dedi AV in a leaf handler, walker nested twice (`+0x2DB020` type-9 prop leaf) | prop collision truncated by old rmdlconv (fa6ad42 fix) | 5 |
| client AV `r5apex_dx12+0x304A99` reading heights, `[NOCLIP-SIM] exception` | client got raw heightfield lumps | 4 |
| client `[FATAL-OBS] ... "Pak file consistency error."` | dangling guid (semantic texture names, S30-common, model->material) | 2, 3 |
| client AV `D3D12Core.dll` read 0xE0 after `pak loaded '<map>.rpak'` | VS/PS linkage -> null PSO | 1 |
| client AV in SignonState(3), then strrchr NULL | lit-bump vertex layout wrong (colour/tangent) | 9 |
| client heap AV `r5apex_dx12+0x571C41`, read FFFF..., PTR_MASK in a register | texture streaming heap class (not RTL) | 10 |
| repak `Failed to open uber asset` / `S21 PTCU/PTCS is 144` | hand-built material without `.uber` / 240-B particle uber | 2 |
| materials bind no textures, everything flat | RSX v15 shds counts = 0 | 1 |
| most props invisible, only some sub-meshes visible | all-zero dxStates (msnp in S30 common) | 7 |
| every lightmapped surface BLACK | 0x69/0x7A missing (t65 1x1) | 10 |
| world dark/flat, no lamp light | 0x62 dropped (type 11) / wrong type | 10 |
| metals / shiny materials black or very dark, worse at night | ambient txtx = cube mean luminance, not the reciprocal | 11 |
| flat lighting, no reflections | no S21 cubemaps_hdr / ambients txtx | 11 |
| players stick / "seesaw" over terrain, traces fine | type-4 edge bits 29-31 = 0 | 4 |
| feet inside ground / stuck on slopes | render terrain != collision (decimated) | 9 |
| map-wide flicker | one material sort per mesh, or 0x78 refs not depth-first | 9 |
| terrain missing in patches / pops with view | mesh not referenced in every cell it touches | 9 |
| `_bm` shows the wrong layer | name order used instead of texture identity | 9 |
| blank minimap, no log | overview wrap or uiia missing | 12 |
| black loadscreen / `missing map pak slot ... _loadscreen.rpak` | loadscreen pak missing / wrong guid / flags | 12 |
| client `SCRIPT COMPILE ERROR: Undefined variable "SetMapSetting_WideBloomAmount"` in `cl_<map>.nut` | the S30 perm pak ships CLIENT scripts as wraps (`scriptsscripts\mp\levels\cl_<map>.nut.client`, `cl_lasermesh.gnut.client`, `mp_rr_<family>_common.nut.client`); a wrap beats the loose file, and S30 scripts call S30-only natives. Natives implemented in client.dll 3f2d3bebb; any NEW S30-only call -> re-run the census | 13 |
| no POI names, no map init, intro camera default | no `tools/levels/<map>.rson` | 13 |
| client `ColorCorrection_LoadAsync ... FS_ASYNC_FILE_INVALID '<map>.raw_hdr'` | loose `materials\correction\<map>.raw_hdr` not deployed (format identical, 143752 B) | R1 |
| foliage waves wrong / trees move like grass | gpu-bone layout / unk_CC bit 0x2 | 8 |
| map/terrain flickers only when far away | world batch cap 0x800 overflows (terrain meshes alternate LOD band / material) | 9 |
| terrain blends look like squares / staircase edges | sharpened alpha at vertices; use s30_pair_alpha (smooth alpha, per-texel sharpening) | 9 |
| ALL generated terrain disappears from the sky / far away, fine up close | S21 world-mesh index buffer (3.1M tris) overflows; client must raise it (ef2865715) | 9 |
| props reflect the wrong cubemap / metals black on props only | converted PS read S30 `cubemapState >> 21`; S21 `cubemapID` is the plain index | 1 |
| terrain layer grey noise (ice, sulfur, soil, dirt_03, mud, lava) | stochastic layer shipped without its LUT | 9 |
| every terrain blend brightened / tinted blue | template detail map overlaid on slot 0 | 9 |
| snow/dirt blends wrong: missing snow, hard slot-0 bias, smeared edges | raw splat weights / template mask+ramp / no height mask | 9 |
| terrain material changes "do nothing", ground blurry, `[STREAM-ABORT] ... s21_opt_stream_stub.bin` spam | top mips in the optional stream, dropped by `[PAK-OPT-DROP]` on the slim deploy | R6 |
| no geyser steam, no lava/snow/steam ambience | map efct not shipped (SKIP) | 13 |
| dedi host shutdown opening a door (`Anim Sequence 'open' not found`) | map aseq/arig never converted | 5 |
| dedi `expects to have a survival preprocess file` | preprocess never generated (SDK write guard) | 13 |
| client `[CONSISTENCY-OBS] pak '<map>_client_perm.rpak' asset <efct> references MISSING guid` + "Pak file consistency error" | effect uses a material the main manifest dropped (bindless particle shaderset) -> rebind, never drop; or efct resolvable lacked the map's own effects | Small map |
| map not offered in the launcher picker | launcher stem filter (`mp_rr_*` only before r5f-launcher 26d29e3) | Small map |
| client script error `ScriptColorCorrection_LoadAsync: No free script colorcorrection slots available` | 32-slot script CC table (client.dll 637fdeeb2 raises to 128) | Small map |
| S30 `<map>_loadscreen.rpak` exports nothing (Ptch/vers only) | stub pak -> own art via make_loadscreen_png + `--from-image` | Small map |
