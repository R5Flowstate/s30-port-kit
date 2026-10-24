# AGENTS.md

Setup, requirements and rules for this repo -- for people and coding agents alike.

## What this is

A toolkit plus format notes for porting an S30 Apex map to the R5Flowstate S21 build: the client
rpak/BSP and the dedicated-server BSP/VPK + rpak. The docs are the spec; the tools are the World's Edge
(`mp_rr_desertlands_mu5`) working copies.

## Read before acting

- `docs/00-run-sheet.md` -- the port in order (R0..R12). Follow it in order; do not skip steps.
- `docs/15-symptom-cause.md` -- map a crash/visual symptom to its cause before writing any fix.
- `docs/per-map-constants.md` -- values that change per map; check them after retargeting.
- The numbered `docs/NN-*.md` file for the subsystem you are touching (shaders, materials, terrain, ...).

## Rules

1. **Never edit `tools/` for a specific map.** Clone with
   `py tools/retarget.py <map> <S21 twin> <short> <out dir> --splat-ids 0x<guid>`, review the leftover
   lines it prints, and work in the clone. Fixes that apply to every map go back into `tools/`.
2. **Every step has a gate; a step is not done until its gate passes** (0 dangling, 0 over-read, PASS).
   Gates live in `tools/gates/` and next to the step's tools. Never build a pak to discover what is
   missing -- run the closure/gate first.
3. **Paths come from `kitpaths`.** Every tool does `import kitpaths as KP` and reads roots such as
   `KP.WORK`, `KP.MAPS`, `KP.DEPLOY`, `KP.S30_GAME`, `KP.RSX`, `KP.REPAK`. Never hardcode a disk path
   in a tool; add a root to `kit.example.toml` instead. `kit.local.toml` is the user's and is gitignored.
4. **Shared helpers go in `lib/`** (`lib/shader/` for DXBC/MSW). Importing `kitpaths` puts every
   `lib/` and `tools/` folder on `sys.path`, so tools import siblings by bare module name. Do not add
   `sys.path` hacks.
5. **Scratch output goes under `KP.WORK`**, never inside the repo.
6. **Never commit game assets** (paks, starpaks, textures, models, shader blobs, extracted CSVs). Only
   code, docs and small hand-written tables belong here.
7. **The S21 twin map is the oracle** for ground materials, foliage and baked values: match it, do not
   invent constants. Values come from the source data, never borrowed from an unrelated asset.
8. A GUID already present in the S21 client's loaded paks is never repacked (it would overwrite the
   shared asset). The oracle list from R0 is how you check.

## Changing the kit

- New symptom found -> add a row to `docs/15-symptom-cause.md` pointing at the section that explains it.
- New format fact -> the matching `docs/NN-*.md`, stated as what is true, not how it was found.
- New tool -> the right `tools/` folder, reading paths via `KP`, with a gate or a self-check that exits
  non-zero on failure.
- Keep comments short and factual. No machine paths, no personal notes.

## Layout

| Path | What |
|---|---|
| `docs/` | Run sheet, per-subsystem format facts, symptom -> cause table, `INDEX.md` |
| `tools/dxil2dxbc/` | DXIL -> DXBC shader converter (S30 shaders to the S21 shader model), twin/link gates |
| `tools/terrain/` | Heightfield -> lit-bump world meshes, RVT layer bake (`s30layers/`), cell gates |
| `tools/client/` | Material header / dxstate / shaderset fixers, cubemaps, foliage wind |
| `tools/dedi/` | Heightfield -> collision triangles, BVH checks, dedi closure + staging |
| `tools/workspace/` | Extract, pak manifest, BSP wraps, anims, loadscreen |
| `tools/gates/` | Offline gates (lightmaps, texture tails, material coverage, static props, occlusion, ...) |
| `tools/efct/` | Particle rebinds |
| `tools/registration/` | Map registration + localization in the S21 platform scripts |
| `tools/retarget.py` | Clone the tools for another map |
| `lib/` | Shared helpers; `lib/shader/` is the DXBC/MSW library (cbuffer remap, pak shader reader) |
| `data/mu5/` | World's Edge terrain layer and material tables (a worked example) |

## Requirements

- Windows, Python 3.11+.
- An S30 Apex Legends install (the source paks) and an R5Flowstate S21 install (the target).
- These tools, built from source; point the matching `kit.local.toml` roots at the checkouts:

| Tool | Repo | Used for |
|---|---|---|
| RSX | [R5Flowstate/rsx](https://github.com/R5Flowstate/rsx) | Extracting / decompressing source paks |
| RePak | [R5Flowstate/RePak](https://github.com/R5Flowstate/RePak) | Building client and dedi rpaks |
| bspconv | [R5Flowstate/bspconv](https://github.com/R5Flowstate/bspconv) | BSP version conversion (client + dedi) |
| rmdlconv | [R5Flowstate/rmdlconv](https://github.com/R5Flowstate/rmdlconv) | Model version conversion |
| R5-AnimConv | [R5Flowstate/R5-AnimConv](https://github.com/R5Flowstate/R5-AnimConv) | Animation conversion |
| revpk | [R5Flowstate/s21-r5sdk](https://github.com/R5Flowstate/s21-r5sdk) | Dedi map VPKs |
| texconv | [microsoft/DirectXTex](https://github.com/microsoft/DirectXTex) | Texture encoding |

## Setup / sanity check

```
py -m pip install -e .
copy kit.example.toml kit.local.toml   # then fill in the roots
py -c "import kitpaths, pak_msw, cb_remap, string_to_guid"
```

## Contributing

Ported a map, found a new crash class, or fixed a tool? Open a PR. A new symptom belongs in
`docs/15-symptom-cause.md` with the section that explains it.
