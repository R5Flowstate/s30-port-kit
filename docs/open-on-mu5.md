## Open on mu5 (2026-10-20)
- Terrain blends: two layers per 4x4 block is the floor (third layers + block-edge cuts, blendsim error ~5-11 on
  snow pairs). Next steps if needed: smaller blocks near multi-layer areas, a third-layer decal pass, or per-pair
  edge-aware block choice. No hex stochastic tiling (S30 hides repetition on stochastic layers).
- Sky "dots" = vista `mdl/vistas/desertlands_mu5_se.rmdl` at the sky camera, additive `divided_moon_stars(2)`
  materials on converted shaders; navmesh; survival preprocess (user-run, then wire into the rson); RVT decals
  (road tracks) not converted; 30 map efct without particle materials; `[STREAM-ABORT]` texture reads growing.
