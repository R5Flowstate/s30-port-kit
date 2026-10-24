## Small map without terrain (mp_quicklighting_test, 2026-10-21)
Second port; whole run in one pass. What differed from mu5:
- No 0x15/0x16 lumps (bvhscan: no type-10 leaves) -> skip hf2tris, all of section 9, client_v51_prehf. Check this first.
- Copy `qlt_kit/client/{fix_dxstate_from_msnp,fix_matl_hdr_from_s30,patch_tex}.py` into the workspace root (they
  resolve ROOT from their own dir). Give the converter its OWN workdir (copy *.py + census_s21.json/twins.pkl/
  s21all_owner.json/rsx_s21, junction s21all + dxil-spirv) so mu5's blobs/run_all.json stay intact; run without `-I`.
- `build_wraps.minimap()` exits when the map has no overview: make it return [] when neither file exists.
- **RSX fe8c693 (aseq v14) exits 1 while parsing S30 `common.rpak`** (common_mp/early fine). Export common closure
  with `<WORK>\animconv-wt-s30-leftovers\rsx_main_pre_20261020.exe`. mu5's extract/texture already holds
  many S30-common textures (copy by guid).
- The client gate's dangling refs were S30-common SHADERSETS (8) + one PS: export shds/shdr with -exportdependencies
  into `<short>_dedi/matl16`, integrate16, rerun split/run_all/build_msw/fix_shaderset_msw + gates, regenerate.
  The skydome rgdc was the dead-shaderset casualty until its common PS came in.
- **Bindless-only particle shadersets are NOT a drop:** rebind the material to the S21-resident shaderset the
  SAME-GUID S21 material uses, with that pair's S21 blend states (S30 `0xCE7A6E802B08A999` == S21
  `0x1B948D35BB854480`, blendStates[0] 0x70030206 -> 0xF0138086, [7] 0x138286 -> 0, proven on
  flare_hoop01_add_noz_ptcu; tool `<WORK>\qlt_fx\rebind_ptcu.py`). A dropped particle material left its
  effect pointing at a missing guid -> client `[CONSISTENCY-OBS] ... perm ... MISSING guid` + "Pak file consistency error".
- efct `EFCT_RESOLVABLE` must include the map's OWN effect guids (allow list), or every parent->child edge inside the map
  is cut (`droppedChildEdges`). Gate perm: rsx --depfilepath on perm, unresolved vs oracle + main + perm = 0.
- Effects run order: convert ONCE after the main pak is final (its list = built.csv), with resolvable = oracle + built.csv +
  allow; then `tools/efct/refgate.py <ws>` (run in the efct scratch dir) must print bad 0. Main-pak gate:
  `tools/gates/dangle.py <ws>` after `rsx --depfilepath inv/deps.txt out/<map>.rpak` = dangling [].
- **Launcher only listed `mp_rr_*` stems** (and mp_lobby). Fixed in r5f-launcher `26d29e3` (dev exe rebuilt, not yet in a player
  release): a stem named in `r5f_map_names.txt` / `r5f_wip_maps.txt` counts once its `paks/Win64/<map>.rpak` or server VPK is on disk.
  Players on an older launcher still need an `mp_rr_` name or `changelevel <map>` on the dedi.
- **CLIENT script color correction slots were 32** (stock scripts hold 21) -> `ColorCorrection_Register` beyond that = fatal
  "No free script colorcorrection slots available". client.dll `637fdeeb2` grows it to 128 (`[CC-GROW]` boot line); keep a
  map's own grade list well under ~100 anyway.
- Registration tools (all take `<map> "<name>"`, back up first): `tools/registration/register.py` (playlists + launcher
  name), `loc_insert.py` (14 loc tables byte-exact), `catalog_insert.py`, `stage_loc.py` (stages ONLY your lines from HEAD
  when the tables carry other sessions' WIP -- a concurrent commit can still sweep staged lines; check `git show HEAD`).
  Loadscreen art: `tools/workspace/make_loadscreen_png.py "<TITLE>" "<subtitle>" <out.png>` -> build_loadscreen --from-image.
- Particle rebind: `tools/efct/rebind_ptcu.py <ws>` (extend its MAP with any new same-GUID S30->S21 shaderset pair).
- `dedi/stage.py` carries mu5 closure extras (weapon crate, construction mat, editor nodes): delete for other maps.
- S30 `<map>_loadscreen.rpak` can be a stub (Ptch+vers only) -> `build_loadscreen.py --from-image` with own art.
- The map's color_correction (`cinder_hdr`) differs from the S21 file of the same name: S30 grades that differ ship
  under `materials/correction/s30/` and the env.ent (temp pak + dedi VPK) points there; new S30 grades go top-level.
- Registration for a non-BR map: `survival_dev` + `map_editor` maps blocks, r5f_map_names, MAP_DISPLAY_NAMES,
  GAMEMODE_INSTALLED_MAPS, loc line (byte-exact, from HEAD when staging -- the tables carry other sessions' WIP).
