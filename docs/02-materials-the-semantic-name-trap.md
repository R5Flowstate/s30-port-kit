## 2. Materials: the semantic-name trap

RSX export with name-cache naming writes `$textures` as SYNTHESIZED names; hashes -> GUIDs that do not exist
-> client `Pak file consistency error.` (S21 the engine). mu5: 5,620 wrong refs. Re-export with
**`--texturenames guid`** and copy `$textures` into `assets\material` + `extract\material` (`patch_tex.py`):
```
rsx -nogui -nocachedb -export -exportfullpaths --exporttypes matl --texturenames guid \
    --exportguids batch_NN --exportdir <out> <map>.rpak      # batches <= 200
```
RSX writes an EMPTY json for some materials (mu5: `vent_climatizer_mesh_idle02` rgdp/rgdc). Rebuild from
the v23.2 header (shaderSet @0x58, textureHandles @0x60, depth mats @0x30..0x50, samplers @0x78, features
@0x80, glue @0x84) + the `msnp` snapshot (dxStates = first 0x30 bytes), or leave them out. Copy the `.uber`
too or repak aborts `Failed to open uber asset`. Particle ubers are 240 B in S30; Repak wants 144
(`S21 PTCU/PTCS is 144`): `<REPAK>\tools\particle_uber\convert_particle_uber.py --in-place`
(its exit 1 is the skip count, not a failure).
