## 14. Visibility facts (for terrain-from-far issues)
- Outdoor sky volume = one cell (mu5 cell 23, box z -32..39936) with ~160 portals into the ground cells
  (z -21760..-32); its own tree refs ~190 meshes; ground meshes are drawn through the portal walk.
- Occluder z-fill mesh = lumps 0x75 (verts f3) / 0x76 (u16 idx): vertical cell-boundary walls; drawn
  (`r_zfill_world_occluder`, default 1) unless the camera cell has skyFlags bit 4 (mcell_t 0x6B {numPortals,
  firstPortal, skyFlags}). S21 debug convars: `pvs_frustumCullOnly` (default 0), `pvs_cullBoxes` (default 1).
- mu5 terrain vanishing from the sky was NOT visibility: it was the S21 world triangle budget (section 9);
  fixed by client.dll ef2865715 (`[WORLD-INDEX] world mesh index limit 9437184 -> 28311552`), user-verified.
