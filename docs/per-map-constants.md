## P. Per-map constants and checks (after retarget)
| what | where | how |
|---|---|---|
| splat-ID texture guid | matmap.py, oracle.py | `terrain_inputs.py` prints it (`--splat-ids` in retarget) |
| heightfield dims/origin/spacing | matmap `N`, surfgate `Ox/Oy/SX`, sideoracle | `hfgrid.py` prints the 0x15 record; spacing = extent/(dims-1) |
| splat -> sample mapping | matmap `(arange(N)*4096)//4095` | splat ID texture is dims/4 per side; recheck if dims differ |
| layer count | layers.json | matmap uses len(LAYERS) |
| landscape lightmap page | 0x53 tag 1 | gen_terrain2 + inject_terrain find it |
| S21 twin | retarget arg | must be a retail S21 map of the same family |
| sky colour | light_environment `_ambient` | build_cubemaps reads `<map>_env.ent` |
| dedi model extras | stage.py | weapon crate / construction mat / editor nodes lines are mu5 closure results; re-run closure.py |
| scratch dirs | retarget | `<WORK>\<short>_*` |
