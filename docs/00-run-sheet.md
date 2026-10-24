
# S30 -> S21 map port (end to end, one shot)

Proven on `mp_rr_desertlands_mu5` (S30 World's Edge, night). Every step below fixes a failure that was
hit in a real port; nothing here is optional.

```
Source     <S30_GAME>\paks\Win64\   (S30 retail; pc_all(.opt).starpak shared)
Workspace  <MAPS>\<map>\
Scratch    <WORK>\<short>_{terrain,dedi,lm2,fix2}\         (large; keep it off the system drive)
Converter  <WORK>\dxil2dxbc\  (run FROM there)
Deploy     <DEPLOY>\  paks\Win64, paks\Win64_server, vpk, platform\scripts
```

`tools/` are the mu5 working copies. **Never edit them for a new map; clone them**:
```
py tools/retarget.py <map> <S21 twin map> <short> <WORK>\<short>_kit --splat-ids 0x<ids guid>
```
It rewrites map stem, twin stem, starpak names, scratch roots and the splat-ID guid, then lists every
line still naming mu5 (comments mostly) -- read that list. The S21 twin is the retail map of the same
family (World's Edge -> `mp_rr_desertlands_hu`, Olympus -> `mp_rr_olympus_mu2`, Storm Point ->
`mp_rr_tropic_island_mu2`, Broken Moon -> `mp_rr_divided_moon_mu1`, Kings Canyon -> `mp_rr_canyonlands_hu`).
It is the oracle for ground materials, foliage twins and baked values. Then check the per-map constants
table (section P) before running.

---

## RUN SHEET (in order; every arrow is a gate)

### R0. Setup
1. `rsx -nogui -nocachedb -decompresspak <S30>\<map>.rpak` -> copy the `.dec.rpak` to `<ws>\inv\`.
   Page data in a decompressed Respawn pak is contiguous (`pak_msw.load` reads it).
2. `rsx --list inv\base.csv --listformat csv <map>.rpak`; same for perm/temp. S30 `(01)` patch paks fail
   to parse next to the base pak; their rows are not applied (mu5: 0.5 MB, harmless).
3. Oracle: `inv\s21_oracle.csv` = `rsx --list` of every pak the S21 client already loads (common, common_mp,
   common_early + (01) patches, common_flowstate, mp_lobby + perm/temp, startup, ui). A guid in the oracle
   is never packed (would stomp common).
4. Twin data into `<scratch terrain>`: `hu\wrap\maps\` = RSX `--exporttypes wrap` of the twin's client
   perm+temp paks; `hudec\<twin>.rpak.dec.rpak`; `s30inv\hu.csv` = `rsx --list` of the twin pak.
   (Directory names stay `hu*` whatever the twin is.)

### R1. Extract
- RSX export of txtr/shdr/shds/matl/uiia/txan/dtbl/wrap with `--exportpak <map>.rpak`. RSX dies 0xC0000409
  after ~1350-3900 materials in one process: export materials in chunks of <= 200
  (`tools/workspace/resume_matl.py`), always `--texturenames guid` (section 2).
- Models: RSX cannot parse mdl_ v20. `tools/workspace/extract_models.py` pulls body + `.vg` (pc_all
  starpak, entry at `starpakOffset & ~0xFFF`, reads sorted by offset) + `.phy` + `.vg_static` into `mdl_in\`;
  `mdl_paths.py` -> `inv\mdl_paths.txt` (names = real paths; csv names are guid stubs).
- BSP: RSX `--exporttypes wrap` of perm + temp -> `bsp\extract` (header v52 + ~78 lumps + 5 `.ent`).
- Loose: copy `<S30>\materials\correction\<map>.raw_hdr` -> `<deploy>\materials\correction\`.
- Datatables: copy the RSX `datatable\0x<GUID>.csv` exports to `<ws>\extract\datatable\` (dedi needs them, R9).
- Minimap: `rsx -export --exporttypes wrap,uiia` -> put `resource/overviews/<map>.txt` and the `[UIIA-RAW]`
  `0x<GUID>.uiia` in `<ws>\extract\minimap\` (R7 packs them).

### R2. Models
```
rmdlconv.exe -nopause -targetversion 17 -v19s30 mdl_in converted\model_v17      (client)
rmdlconv.exe -nopause -v19s30           mdl_in dedi_work\mdl_v54                (dedi)
```
Delete `.vg_static` that are not Oodle first (mu5: 8 animated models; exit code stays 0, the dedi path
then refuses the model). Gate `tools/dedi/mdlbvh.py <dedi mdl tree>` = 0 bad (rmdlconv >= fa6ad42).
Foliage: `fix_gpubone_layout.py` + section 8.

### R3. Shaders (section 1)  -> gates pso_link_gate / vsfmt / bindcheck = 0

### R4. Materials (sections 2, 7, 8)
`patch_tex.py` (guid textures) -> `convert_particle_uber.py --in-place` (240 -> 144 B ubers) ->
`fix_dxstate_from_msnp.py --apply` -> `fix_matl_hdr_from_s30.py --apply` -> foliage `apply_cc.py`.

### R5. Cubemaps (section 11) -- `tools/workspace/build_cubemaps.py` (needs the client BSP from R7.1)
Writes the BC6H cube array, the **reciprocal** ambient txtx and `inv/cubemaps.json`.

### R6. Main pak
`tools/workspace/gen_full_manifest.py` -> `repak <map>.json` (headerFlags 0x20, uncompressed) ->
- client gate: `rsx --depfilepath deps.txt <built>.rpak` alone, every unresolved guid in the oracle = **0 dangling**;
- `tools/gates/tailgate.py <built>.rpak` = 0 over-read (v10 txtr tails vs dims);
- a failed repak leaves a 128-byte rpak + torn starpaks: delete rpak, both starpaks, starmap, rebuild.
In-place fixes after a build (no rebuild): `tools/gates/patch_txtx.py` (ambient txtx), `apply_cc.py`.
- **TRAP: the dev deploy has NO core `pc_all.opt.starpak` -> the SDK logs `[PAK-OPT-DROP] ... force-drop optional set`
  and NO optional stream file opens, including `pc_all_<map>.opt.starpak`.** Any texture whose top mips are
  `optional` renders at its mandatory cap and the engine retries the stub forever (`[STREAM-ABORT] ...
  's21_opt_stream_stub.bin'`, 34,905 lines in one mu5 run). Every texture WE generate (terrain layers, colour,
  masks) ships with no optional mips (`streamLayout` permanent + mandatory only); otherwise material work looks
  like "nothing changed" (it renders the 512 mip).
- **TRAP: Repak stream-reuse.** With `out/<map>.starmap` + starpaks present Repak reuses stream data and may not
  rewrite the starpaks when only `streamLayout` (mandatory/optional split) changed. After changing stream layout,
  delete `out/<map>.rpak`, both starpaks and the starmap first (clean build).

### R7. Client BSP + terrain
1. `tools/workspace/build_wraps.py` (full run once): stages extract, runs bspconv client (v51) + dedi (v47).
   bspconv must be >= `832a309` (lightmaps type 11 -> 9, RTL kept). Log must show
   `[v52-lightmap-validate] PASSTHROUGH`. Gate `tools/gates/lmgate.py <src maps> <client_v51>` = PASS.
2. Copy `bsp\client_v51` -> `bsp\client_v51_prehf` (terrain tools read the original heightfield BVH).
3. `tools/dedi/hf2tris.py` on `client_v51` (client needs the same triangle lumps as the dedi, section 4).
4. Terrain (section 9), in this order:
   `hfgrid.py` -> `terrain_inputs.py` -> `s30layers/llyr_extract.py` (per-llyr nml/gls/ao) ->
   `s30layers/col_bake.py` (per-layer colour, LUT+macro+tint baked) -> `s30layers/layer_heights.py` ->
   `gen_terrain2.py` (matmap.RVT_SINGLE: one material) -> `stage_hu.py` (only UNSTAGES old twin imports) ->
   `s30layers/rvt_shader.py` (terrain PS from the converted S30 blend PS + RVT composite; fxc) ->
   `s30layers/build_rvt.py` (layer arrays PERMANENT, params, PS/shaderset MSW, material, extra_matl) ->
   gates pso_link_gate / vsfmt / bindcheck = 0 -> re-run R6 (clean: delete out/ rpak+starpaks+starmap) ->
   `inject_terrain.py --src client_v51 --out client_v51_terrain --npz terrain_meshes.npz`.
   Gates: `check_bsp.py`, `vcheck.py`, `surfgate.py`, `cellgate.py` (all PASS / 0).
5. `build_wraps.py --assemble-only client_v51_terrain` (adds the minimap pair to perm) ->
   `repak <map>_client_perm.json` and `_temp.json`. Gate: `tools/gates/wraphdr.py`-style check that
   0x61/0x62/0x69 are in perm, 0x53/0x7A in temp, overview wrap + uiia present.
   **Never run build_wraps without `--assemble-only` after step 3** -- it re-runs bspconv and wipes hf2tris + terrain.

### R8. Dedi BSP
`hf2tris.py <dedi_v47 dir> <map> <lumps 0x15/0x16 dir>` (sets edge bits; rewrites header lengths) ->
move `.prehf` files out -> `vpk_tool.py --vpkdir <DEPLOY>/vpk --revpk <revpk>/revpk.exe newmap <map> --maps-dir <dedi_v47> --deploy`.

### R9. Dedi rpak (section 5)
`workspace/map_anims.py <map>` first (animated props; also feeds the client manifest in R6) ->
`closure.py` (0 unresolved) -> `stage.py` (models **and datatables**) -> `repak build_list_<map>_dedi.json`
-> header `RPak` v8, byte7 0x80, zstd `28 b5 2f fd` at 0x80 (the S3 dedi silently ignores an uncompressed rpak).

### R10. Loadscreen (section 12)
`s21-loadscreen-port/build_loadscreen.py <map> --from-pak <S30>\<map>_loadscreen.rpak --guid-name "ui_image/loadscreens/<map>_widescreen"` (the S30 export has no dev name).

### R11. Scripts + registration (section 13)
Level `.rson` + server/client level `.nut` from the twin, map tables, `platform\r5f_map_names.txt`,
playlist maps block, localization; later the engine-generated survival preprocess.

### R11b. Map audio
Run the s21-miles-custom-audio census (rule 23): `_snd.ent` soundNames + scripts + efct vs S30/S21/custom banks;
port the S30-only events with `port_verbatim.py --add-file` (1:1 relocation), verify, install custom.*.
Soundscape `environment` names must exist in `platform/scripts/audio/environments.rson` (mu5: all 28 did).

### R12. Deploy + test
Kill `r5apex*`, `r5apex_ds`, `R5FlowstateLauncher` (by name, never python). Back up every file you replace
to scratch. Copy: `paks\Win64\<map>.rpak` + `pc_all_<stem>.starpak` + `.opt.starpak` + `.starmap`
(only when the main pak was rebuilt), `<map>_client_perm/temp.rpak`, `<map>_loadscreen.rpak`,
`paks\Win64_server\<map>.rpak`, the VPK pair.
DEDI launch args: `+playlist survival +map <map>`; running dedi: `changelevel <map>`.

---
