import kitpaths as KP
import sys, numpy as np

import matmap as MM
G = np.load(MM.ROOT + "hf_grid.npy"); valid = ~np.isnan(G)
act, wts = MM.sample_layers()
names, src, side, pick, fb, tot, cat = MM.choose(act, wts, valid, None)
nb = MM.N // MM.BLOCK
LW = np.stack([(MM.layer_weight(act, wts, l) * valid).reshape(nb, MM.BLOCK, nb, MM.BLOCK).sum((1, 3)) for l in range(28)], -1)
for want in sys.argv[1:]:
    i = names.index(want); m = (pick == i) & ~fb
    lw = LW[m].sum(0); own = sum(lw[l] for l in side[i][0] | side[i][1])
    print(want, "blocks", int(m.sum()), "own %.1f%%" % (100 * own / lw.sum()))
    for l in np.argsort(-lw)[:6]: print("   %5.1f%% %s%s" % (100 * lw[l] / lw.sum(), MM.TOK[l], "  (own)" if l in side[i][0] | side[i][1] else ""))
