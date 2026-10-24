"""Mean S30 layer height per RVT layer index -> <terrain scratch>/layer_heights.json (matmap.s30_stack input).
Height = llyr tile texture 1 red; mean over the 128 px mip."""
import kitpaths as KP
import json, os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import tiles, mask_bake

TERR = (KP.WORK + "/mu5_terrain/")
layers = [l.replace("\\", "/") for l in json.load(open(TERR + "layers.json"))]
by = {L["name"]: L for L in tiles.layers().values()}
hm = {i: float(mask_bake.height_levels(by[n])[3].mean()) for i, n in enumerate(layers)}
json.dump(hm, open(TERR + "layer_heights.json", "w"), indent=1)
print(hm)
