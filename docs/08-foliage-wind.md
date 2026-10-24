## 8. Foliage wind
- **gpu-bone records stay in S30 order** in converted v17 models; retail foliage_prepass_cs wants
  `{packed@0,pivot@4,quat@16}`: `tools/client/foliage/fix_gpubone_layout.py <pak> <converted\model_v17\mdl> --apply`;
  missed tiny-pivot models -> `foliage/pair_gpubones.py` (twin diff vs S21 district/tropic/olympus/divided_moon;
  hu has NO wind models) + `force_rot.py`. Gate: every S21-twinned model byte-identical.
- **matl `unk_CC` bit 0x2 = "VS binds g_foliageInstances"** (0 mixed over 847 retail VS). Derive per material
  from its shaderset's VS (converted `lift/<VS>_*.hlsl` mentions `g_foliageInstances`), exact-GUID retail value
  otherwise; `apply_cc.py` patches pak + bakes json. mu5: 551 materials.
