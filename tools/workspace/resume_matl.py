"""Export missing materials in chunks. Bisect a chunk that crashes RSX."""
import kitpaths as KP
import os
import subprocess
import sys

ROOT = (KP.MAPS + r"\mp_rr_desertlands_mu5")
RSX = (KP.RSX + r"\bin\Release\rsx.exe")
SRC = (KP.S30_GAME + r"\paks\Win64\mp_rr_desertlands_mu5.rpak")
GUIDS = os.path.join(ROOT, "inv", "matl_missing_order.txt")
SLICE = (KP.WORK + r"\desertlands_mu5\slice.txt")
LOG = (KP.WORK + r"\desertlands_mu5\slice.log")
BAD = os.path.join(ROOT, "inv", "matl_crash.txt")
CHUNK = 200


def run(guids):
    with open(SLICE, "w") as f:
        f.write("\n".join(guids) + "\n")
    with open(LOG, "w") as log:
        r = subprocess.run(
            [RSX, "-nogui", "-nocachedb", "-export", "-exportfullpaths",
             "--exportguids", SLICE, "--exporttypes", "matl",
             "--exportdir", os.path.join(ROOT, "extract"), SRC],
            stdout=log, stderr=subprocess.STDOUT)
    return r.returncode == 0


def bisect(guids, bad):
    if len(guids) == 1:
        print("  CRASH", guids[0], flush=True)
        bad.append(guids[0])
        return
    mid = len(guids) // 2
    left, right = guids[:mid], guids[mid:]
    print("  bisect %d / %d" % (len(left), len(right)), flush=True)
    if not run(left):
        bisect(left, bad)
    if not run(right):
        bisect(right, bad)


def main():
    guids = [ln.strip() for ln in open(GUIDS) if ln.strip()]
    bad = []
    for i in range(0, len(guids), CHUNK):
        chunk = guids[i:i + CHUNK]
        print("chunk %d-%d" % (i, i + len(chunk)), flush=True)
        if not run(chunk):
            print("  chunk failed, bisecting", flush=True)
            bisect(chunk, bad)
    with open(BAD, "w") as f:
        f.write("\n".join(bad) + ("\n" if bad else ""))
    print("done bad=%d" % len(bad), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
