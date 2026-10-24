#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
map_model_origin.py -- figure out WHERE every model a map needs actually lives.

A map rpak extract alone is NOT a dedi/server closure. Static props (GAME_LUMP
sprp) + entity model keys reference models that often ship in common /
common_mp / etc. This tool:

  1. Collects required model paths from the BSP (sprp name dict + .ent lumps
     + ENTITIES worldspawn lump).
  2. Inventories candidate rpaks via RSX --list (isolated copies so
     patch_master.rpak cannot phantom-row the CSVs).
  3. Classifies each required model:
       MAP_LOCAL     - in the map's own rpak
       CLOSURE       - in always-loaded set (common, common_mp, mp_lobby, startup)
       OTHER_PAK     - found in some other listed pak
       MISSING       - nowhere in the searched set
  4. Emits a CSV + console summary with the ACTION for dedi packaging:
       OK            - already present in --dedi-map-pak (if given) OR only
                       needed on client
       PULL_INTO_MAP - required by BSP, not in map rpak; must be staged into
                       the dedi map pak (even if it lives in source common*,
                       because the dedi's converted common* is often LOSSY)
       UNRESOLVED    - not found in any searched source pak

Always-loaded closure (source-side search order):
  common.rpak, common_mp.rpak, mp_lobby.rpak, startup.rpak

Usage (skulltown example):
  python map_model_origin.py \\
    --maps-dir ".../bsp/dedi_deploy" \\
    --level mp_rr_freedm_skulltown_s27 \\
    --source-paks "<source install>/paks/Win64" \\
    --map-pak-name mp_rr_freedm_skulltown.rpak \\
    --dedi-map-pak "<DEPLOY>/paks/Win64_server/mp_rr_freedm_skulltown_s27.rpak" \\
    --out ".../inv/model_origin_report.csv"

Requires: rsx.exe (Cafe RSX). Python 3.8+.
"""
from __future__ import annotations
import kitpaths as KP

import argparse
import csv
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

DEFAULT_RSX = (KP.RSX + r"\bin\Release\rsx.exe")
# Standing always-loaded set for S21 client+dedi map work.
CLOSURE_PAKS = ("common.rpak", "common_mp.rpak", "mp_lobby.rpak", "startup.rpak")
RMDL_RE = re.compile(rb"mdl/[A-Za-z0-9_./\-]+\.rmdl", re.IGNORECASE)


def log(msg: str) -> None:
    sys.stdout.write(msg + "\n")
    sys.stdout.flush()


def die(msg: str) -> None:
    sys.stderr.write("ERROR: " + msg + "\n")
    sys.exit(1)


# ---------------------------------------------------------------------------
# BSP: required model paths
# ---------------------------------------------------------------------------
def read_bsp_header(bsp_path: Path):
    data = bsp_path.read_bytes()
    if data[:4] not in (b"rBSP", b"PSBr"):
        die(f"not an rBSP: {bsp_path}")
    ver, flags = struct.unpack_from("<HH", data, 4)
    lumps = []
    for i in range(128):
        o = 16 + i * 16
        ofs, ln, lv, un = struct.unpack_from("<iiii", data, o)
        lumps.append((ofs, ln, lv, un))
    return data, ver, flags, lumps


def load_split_lump(maps_dir: Path, level: str, idx: int, header: bytes, lumps) -> bytes | None:
    for hx in (f"{idx:04X}", f"{idx:04x}"):
        p = maps_dir / f"{level}.bsp.{hx}.bsp_lump"
        if p.is_file():
            return p.read_bytes()
    ofs, ln, _, _ = lumps[idx]
    if ln > 0 and ofs > 0 and ofs + ln <= len(header):
        return header[ofs : ofs + ln]
    return None


def models_from_sprp(gl: bytes) -> set[str]:
    """Pull mdl/...rmdl strings out of GAME_LUMP (sprp dict lives here)."""
    if not gl:
        return set()
    # Prefer sprp sub-blob if the gamelump table is intact; else whole lump.
    names: set[str] = set()
    try:
        count = struct.unpack_from("<i", gl, 0)[0]
        off = 4
        sprp = None
        for _ in range(max(0, min(count, 64))):
            lid, _flags, _ver, fileofs, filelen = struct.unpack_from("<IHHII", gl, off)
            off += 16
            fourcc = struct.pack("<I", lid)
            if fourcc in (b"sprp", b"prps") or lid == 0x73707270:
                if 0 <= fileofs < len(gl) and filelen > 0 and fileofs + filelen <= len(gl):
                    sprp = gl[fileofs : fileofs + filelen]
                break
        blob = sprp if sprp is not None else gl
    except Exception:
        blob = gl
    for m in RMDL_RE.findall(blob):
        names.add(m.decode("ascii", "replace").replace("\\", "/").lower())
    return names


def models_from_bytes(blob: bytes | None) -> set[str]:
    if not blob:
        return set()
    return {m.decode("ascii", "replace").replace("\\", "/").lower() for m in RMDL_RE.findall(blob)}


def collect_required_models(maps_dir: Path, level: str) -> tuple[set[str], dict[str, set[str]]]:
    """Return (all_models, sources) where sources[model] = {sprp, ent, entities}."""
    bsp = maps_dir / f"{level}.bsp"
    if not bsp.is_file():
        die(f"missing BSP: {bsp}")
    header, ver, flags, lumps = read_bsp_header(bsp)
    log(f"BSP {bsp.name}: ver={ver} flags={flags}")

    by_src: dict[str, set[str]] = defaultdict(set)
    all_m: set[str] = set()

    gl = load_split_lump(maps_dir, level, 0x23, header, lumps)
    for m in models_from_sprp(gl or b""):
        all_m.add(m)
        by_src[m].add("sprp")

    ent0 = load_split_lump(maps_dir, level, 0x00, header, lumps)
    for m in models_from_bytes(ent0):
        all_m.add(m)
        by_src[m].add("entities")

    for p in maps_dir.glob(f"{level}_*.ent"):
        for m in models_from_bytes(p.read_bytes()):
            all_m.add(m)
            by_src[m].add("ent:" + p.name)

    log(f"required models: {len(all_m)}  (sprp-bearing={sum(1 for s in by_src.values() if 'sprp' in s)})")
    return all_m, by_src


# ---------------------------------------------------------------------------
# RSX inventory
# ---------------------------------------------------------------------------
def rsx_list(rsx: str, pak: Path, out_csv: Path) -> None:
    """List one pak in isolation (copy into out_csv's dir under a unique name)."""
    out_csv.parent.mkdir(parents=True, exist_ok=True)
    iso = out_csv.with_suffix(".rpak")
    shutil.copy2(pak, iso)
    cmd = [rsx, "-nogui", "-nocachedb", "--list", str(out_csv), "--listformat", "csv", str(iso)]
    proc = subprocess.run(cmd, cwd=str(Path(rsx).parent),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    if proc.returncode != 0 or not out_csv.is_file():
        tail = proc.stdout.decode("utf-8", "replace")[-500:]
        die(f"rsx --list failed for {pak.name}:\n{tail}")


def load_mdl_index(csv_path: Path) -> dict[str, dict]:
    """asset_name(lower, /-slash, no leading junk) -> row dict for mdl_ only."""
    idx = {}
    with open(csv_path, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f):
            if (row.get("type") or "").strip() != "mdl_":
                continue
            name = (row.get("asset_name") or "").replace("\\", "/").strip().lower()
            if name.endswith(".rpak"):
                name = name[:-5]
            if not name.startswith("mdl/"):
                # sometimes bare
                if not name:
                    continue
            idx[name] = {
                "guid": (row.get("guid") or "").lower(),
                "pakver": row.get("pakver") or row.get("rsxver") or "",
                "file_name": row.get("file_name") or "",
            }
    return idx


def build_origin_index(
    rsx: str,
    source_paks_dir: Path,
    map_pak_name: str,
    cache_dir: Path,
    extra_paks: list[str],
) -> dict[str, list[tuple[str, dict]]]:
    """path -> [(pak_label, meta), ...] across map + closure + extras."""
    wanted = []
    if map_pak_name:
        wanted.append(("MAP:" + map_pak_name, map_pak_name))
    for c in CLOSURE_PAKS:
        wanted.append(("CLOSURE:" + c, c))
    for e in extra_paks:
        wanted.append(("OTHER:" + Path(e).name, e))

    # path -> list of (label, meta)
    origin: dict[str, list[tuple[str, dict]]] = defaultdict(list)
    for label, name in wanted:
        pak = source_paks_dir / name if not os.path.isabs(name) else Path(name)
        if not pak.is_file():
            log(f"  skip missing pak: {pak}")
            continue
        csv_path = cache_dir / (label.replace(":", "_").replace("/", "_") + ".csv")
        if not csv_path.is_file():
            log(f"  rsx --list {pak.name} -> {csv_path.name}")
            rsx_list(rsx, pak, csv_path)
        else:
            log(f"  cache hit {csv_path.name}")
        idx = load_mdl_index(csv_path)
        log(f"    {label}: {len(idx)} mdl_")
        for path, meta in idx.items():
            origin[path].append((label, meta))
    return origin


def load_dedi_map_paths(rsx: str, dedi_map_pak: Path | None, cache_dir: Path) -> set[str]:
    if not dedi_map_pak or not dedi_map_pak.is_file():
        return set()
    csv_path = cache_dir / "dedi_map_built.csv"
    log(f"  rsx --list dedi map {dedi_map_pak.name}")
    rsx_list(rsx, dedi_map_pak, csv_path)
    return set(load_mdl_index(csv_path).keys())


# ---------------------------------------------------------------------------
# classify
# ---------------------------------------------------------------------------
def classify(path: str, origins: list[tuple[str, dict]], dedi_have: set[str]) -> tuple[str, str, str]:
    """Return (bucket, origin_summary, action)."""
    labels = [o[0] for o in origins]
    if not labels:
        bucket = "MISSING"
        action = "UNRESOLVED"
    elif any(l.startswith("MAP:") for l in labels):
        bucket = "MAP_LOCAL"
        action = "OK" if (not dedi_have or path in dedi_have) else "PULL_INTO_MAP"
    elif any(l.startswith("CLOSURE:") for l in labels):
        bucket = "CLOSURE"
        # dedi common is lossy -- still pull into map pak unless already there
        action = "OK" if path in dedi_have else "PULL_INTO_MAP"
    else:
        bucket = "OTHER_PAK"
        action = "OK" if path in dedi_have else "PULL_INTO_MAP"

    # if dedi map given and already packed, force OK
    if dedi_have and path in dedi_have:
        action = "OK"

    summary = ";".join(labels) if labels else ""
    return bucket, summary, action


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description="Map model origin / dedi closure classifier")
    ap.add_argument("--maps-dir", required=True, help="dir with <level>.bsp + lumps + .ent")
    ap.add_argument("--level", required=True, help="map stem e.g. mp_rr_freedm_skulltown_s27")
    ap.add_argument("--source-paks", required=True, help="dir with source rpaks (source season or S21 Win64)")
    ap.add_argument("--map-pak-name", default=None,
                    help="map rpak filename inside --source-paks (default: <level without _s27>.rpak or <level>.rpak)")
    ap.add_argument("--extra-pak", action="append", default=[],
                    help="extra rpak filename or abs path to search (repeatable)")
    ap.add_argument("--dedi-map-pak", default=None, help="built dedi map rpak to mark OK vs PULL")
    ap.add_argument("--rsx", default=DEFAULT_RSX)
    ap.add_argument("--cache-dir", default=None, help="inventory cache dir (default: maps-dir/../inv/origin_cache)")
    ap.add_argument("--out", default=None, help="CSV report path")
    ap.add_argument("--refresh", action="store_true", help="ignore cache, re-list paks")
    args = ap.parse_args(argv[1:])

    maps_dir = Path(args.maps_dir)
    source_paks = Path(args.source_paks)
    level = args.level
    if not maps_dir.is_dir():
        die(f"maps-dir not found: {maps_dir}")
    if not source_paks.is_dir():
        die(f"source-paks not found: {source_paks}")
    if not os.path.isfile(args.rsx):
        die(f"rsx not found: {args.rsx}")

    map_pak_name = args.map_pak_name
    if not map_pak_name:
        # strip trailing _s27/_s21 style suffix for source map name guess
        base = re.sub(r"_s\d+$", "", level)
        cand = f"{base}.rpak"
        if (source_paks / cand).is_file():
            map_pak_name = cand
        elif (source_paks / f"{level}.rpak").is_file():
            map_pak_name = f"{level}.rpak"
        else:
            die("could not guess --map-pak-name; pass it explicitly")

    cache_dir = Path(args.cache_dir) if args.cache_dir else (maps_dir.parent / "inv" / "origin_cache")
    if args.refresh and cache_dir.is_dir():
        shutil.rmtree(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)

    required, by_src = collect_required_models(maps_dir, level)

    log("Building source origin index (map + common/common_mp/mp_lobby/startup)...")
    origin = build_origin_index(
        args.rsx, source_paks, map_pak_name, cache_dir, args.extra_pak)

    dedi_have = load_dedi_map_paths(
        args.rsx,
        Path(args.dedi_map_pak) if args.dedi_map_pak else None,
        cache_dir,
    )
    if dedi_have:
        log(f"dedi map pak models: {len(dedi_have)}")

    rows = []
    counts = defaultdict(int)
    action_counts = defaultdict(int)
    for path in sorted(required):
        origins = origin.get(path, [])
        bucket, summary, action = classify(path, origins, dedi_have)
        counts[bucket] += 1
        action_counts[action] += 1
        rows.append({
            "model": path,
            "required_by": ",".join(sorted(by_src.get(path, []))),
            "bucket": bucket,
            "source_paks": summary,
            "guid": origins[0][1]["guid"] if origins else "",
            "in_dedi_map_pak": "1" if path in dedi_have else "0",
            "action": action,
        })

    out = Path(args.out) if args.out else (cache_dir / f"{level}_model_origin.csv")
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0].keys()) if rows else
                           ["model", "required_by", "bucket", "source_paks", "guid",
                            "in_dedi_map_pak", "action"])
        w.writeheader()
        w.writerows(rows)

    log("")
    log("=== SUMMARY ===")
    log(f"required: {len(required)}")
    for k in ("MAP_LOCAL", "CLOSURE", "OTHER_PAK", "MISSING"):
        log(f"  {k:12} {counts.get(k, 0)}")
    log("actions:")
    for k in ("OK", "PULL_INTO_MAP", "UNRESOLVED"):
        log(f"  {k:12} {action_counts.get(k, 0)}")
    log(f"report: {out}")

    # loud failures
    pull = [r for r in rows if r["action"] == "PULL_INTO_MAP"]
    miss = [r for r in rows if r["action"] == "UNRESOLVED"]
    if pull:
        log("")
        log(f"PULL_INTO_MAP ({len(pull)}) -- stage these into the dedi map pak:")
        for r in pull[:40]:
            log(f"  {r['model']:60} <- {r['source_paks']}")
        if len(pull) > 40:
            log(f"  ... +{len(pull) - 40} more")
    if miss:
        log("")
        log(f"UNRESOLVED ({len(miss)}) -- not in map+closure (+extras):")
        for r in miss[:40]:
            log(f"  {r['model']:60}  via {r['required_by']}")
        if len(miss) > 40:
            log(f"  ... +{len(miss) - 40} more")

    # exit code: 0 ok, 1 needs pull, 2 unresolved
    if miss:
        return 2
    if pull:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
