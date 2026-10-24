## 3. Client closure: S30-common assets

`gen_full_manifest.py` (`tools/workspace/`): junctions `assets\{texture,shader,shaderset,texture_anim,
datatable,txtx}` -> `extract\...`, `mdl` -> `converted\model_v17\mdl`; model `_path` from `inv\mdl_paths.txt`;
skips `vers Ptch efct msnp asqd rson uiia rmap wrap cube llyr aseq arig stgs stlt` + oracle guids; starpaks
`paks\Win64\pc_all_<stem>.starpak` / `.opt.starpak`. It also packs converted shaders/shadersets not in the
oracle, textures referenced by packed materials (`txtr_common`, tails synthesized), `inv\extra_matl.txt`
materials (twin terrain materials land here via stage_hu), applies the **dead-shaderset rule** (shds whose
VS/PS is neither converted nor resident -> dropped with its materials), and exits non-zero on any
unresolved texture ref. Keeps `assets\material` when >= 5600 jsons are already there.

Pull S30-common closure (deps come along):
```
rsx -nogui -nocachedb -export -exportfullpaths -exportdependencies --texturenames guid \
    --exportguids need.txt --exportdir <x> "<S30>\common.rpak" "<S30>\common_mp.rpak" \
    "<S30>\common_early.rpak" <map>.rpak
```
then `tools/client/integrate16.py`. `common_early` holds some shaders (mu5 `0x0101F5D7376B1A9C`).

**Client gate:** `rsx -nogui -nocachedb --depfilepath deps.txt <built>.rpak` (pak ALONE): every `X*`
(unresolved) entry must be in the oracle -> **0 dangling**. Model -> material refs only show up here.
