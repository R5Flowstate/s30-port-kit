## 12. Minimap + loadscreen
- Minimap = `resource/overviews/<map>.txt` wrap (pos_x/pos_y/scale + assets minimap overviews\<map>) + uiia
  `ui_image/overviews/<map>.rpak` (guid = StringToGuid of that path). Both go in the PERM wrap pak;
  `build_wraps.minimap()` adds them from `extract/minimap/` on every assemble. Missing either = blank minimap,
  no log line. RSX `[UIIA-RAW]` export = Repak container (1 reloc, streamedPages 0 on mu5).
- Loadscreen: S30 `<map>_loadscreen.rpak` (1 uiia, 1920x1080, fully resident). Port with
  `s21-loadscreen-port` Section A + `--guid-name "ui_image/loadscreens/<map>_widescreen"` (guid
  0x9D55D011F37D107B on mu5). Pak flags 0x20, uncompressed.
