## 7. Materials header fixes (run before gen_full_manifest; tools/client/)
- `fix_dxstate_from_msnp.py <map .dec> <common_mp .dec> <common .dec> --apply`: S30 materials keep dxStates in
  `msnp`; RSX writes 0 when it lives in S30 common (blend write mask 0 = INVISIBLE prop). Use BASE common paks
  decompressed (`(01)` paks expose no msnp). Adds the RT-set mask bit (0x8 if glueFlags & 0x20000 else 0x4).
  Exit 1 on any non-depth zero-write material.
- `fix_matl_hdr_from_s30.py <map .dec> <S21 twin .dec> <S21 district .dec> --s30 <common...> --apply`:
  unk_C0/unk_EA/unk_E8 from S30 +0x90/+0x98/+0xA4, unk_CC from S21 retail.
