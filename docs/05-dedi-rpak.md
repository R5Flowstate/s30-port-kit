## 5. Dedi rpak

Without `paks\Win64_server\<map>.rpak` the dedi Host_Errors `Model "mdl/..." not found and "mdl/error.rmdl"
couldn't be loaded`; without the map datatables the server script errors
`[SERVER] Couldn't find asset "datatable/map_zones/zones_<map>.rpak"` (sh_map_zones.nut #173) and the host dies.
- `closure.py`: BSP-required models (dedi v47 BSP) vs dedi-loaded paks (common, common_early, common_mp,
  mp_lobby, root_lgnd_skins, common_flowstate, sdk_s30_dedi) vs mdl_v54. Pack = mdl_v54 minus loaded guids.
  Missing ones from S30 `common_mp` (decompress a COPY, extract, rmdlconv) or another map's dedi copy
  (`s21-to-s3-server\<map>\mdl\...`); `editor_effect_node_256/512/1024` reuse `_128`.
- `stage.py` writes `s21-to-s3-server\<map>\` + json + `build_list_<map>_dedi.json`; it adds every
  `<ws>\extract\datatable\0x<GUID>.csv` as `{"_type":"dtbl","_path":"datatable/0x<GUID>.rpak","$guid":...}`
  (RSX csv, type row last; retail twin dedi paks carry the same dtbl).
- `repak build_list_<map>_dedi.json`, check zstd header; re-run closure with the built pak -> 0 unresolved.
- **Animations (`workspace/map_anims.py <map> [--extra-dec <S30 pak .dec.rpak>]`, run before stage.py and
  gen_full_manifest.py)**. Without them every animated prop has only its ref pose and the dedi Host_Errors when a
  script plays a clip (`Anim_PlayWithRefPoint: Anim Sequence 'open' not found for model
  'mdl/door/door_256x256x8_elevatorstyle02_animated.rmdl'` -> `Shutdown host game`, opening any survival door).
  S30 map paks ship aseq **v14** (= v13 header/seqdesc/animdesc, compact 20 B events; RSX >= `fe8c693` loads it,
  older RSX silently skipped every map seq) + arig v7 + asqd v1. **asqd is dumped by pak LAYOUT** (next blob in
  the page): RSX's bone-walk sizing truncates S30 clips (door open 66 B of 5276 = frozen door) and skips clips owned
  by mdl_ v20 models it cannot parse. Model deps come from each mdl_ asset's uses list; self-rigged models get a
  .rson so R5-AnimConv uses the S30 model as rig. `-i 30 -o 3` -> dedi_work/anim_s3 (stage.py adds
  `$animrigs`/`$sequences` + arig entries), `-o 21` -> extract/anim_s21 (gen_full_manifest junctions animrig/animseq,
  same deps). mu5: 2 arig + 18 aseq (doors x2, loot_bin_02, prowler hatch, beacon flag, cooling fan); one asqd
  (prowler close_idle) lives in S30 common.rpak -> decompress a COPY and pass it with --extra-dec. Gates: converted
  door/loot-bin clips equal hu's S21-sourced conversions to +-4 B; dedi guid set = old + 18 aseq + 2 arig.
