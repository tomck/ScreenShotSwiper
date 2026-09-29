#!/usr/bin/env python3
"""
Hash every file in inventory.json (SHA-256 for exact dupes, dHash for
near-dupes), then cluster into groups and pick one representative per
group. Cloud-only placeholders get downloaded automatically as a side
effect of reading their bytes to hash them -- each file's hash runs in
its own subprocess with a timeout so a slow/stuck download can't hang
the whole batch (same pattern as the NAS sync script).

Resumable: already-hashed paths in hashes.jsonl are skipped on rerun.

Writes:
  hashes.jsonl     -- one JSON line per file: path, sha256, dhash, width, height (or error)
  dedup_report.csv -- rel grouping: path, group_id, dup_type, is_representative
"""
import csv
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
PYTHON = str(HERE / "venv" / "bin" / "python3")
WORKER = str(HERE / "hash_worker.py")
INVENTORY = HERE / "inventory.json"
HASHES_OUT = HERE / "hashes.jsonl"
REPORT_OUT = HERE / "dedup_report.csv"
TIMEOUT = 90
DHASH_DISTANCE_THRESHOLD = 6  # hamming distance; dhash is 64 bits


def hash_one(path, timeout=TIMEOUT):
    proc = subprocess.Popen([PYTHON, WORKER, path], stdout=subprocess.PIPE,
                             stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        stdout, stderr = proc.communicate(timeout=timeout)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.communicate()
        return {"error": f"timed out after {timeout}s"}
    if proc.returncode != 0:
        return {"error": (stderr or f"exit {proc.returncode}").strip().splitlines()[-1] if stderr else f"exit {proc.returncode}"}
    try:
        return json.loads(stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        return {"error": f"bad worker output: {stdout!r} {stderr!r}"}


def load_done():
    done = {}
    if HASHES_OUT.exists():
        with open(HASHES_OUT) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                done[rec["path"]] = rec
    return done


def main():
    with open(INVENTORY) as f:
        items = json.load(f)

    done = load_done()
    print(f"{len(done)}/{len(items)} already hashed (resuming).", flush=True)

    out = open(HASHES_OUT, "a")
    start = time.time()
    n_new = 0
    for i, item in enumerate(items, 1):
        path = item["path"]
        if path in done:
            continue
        result = hash_one(path)
        result["path"] = path
        out.write(json.dumps(result) + "\n")
        out.flush()
        n_new += 1
        status = "ERROR: " + result["error"] if "error" in result else "ok"
        if n_new % 25 == 0 or "error" in result:
            elapsed = time.time() - start
            print(f"[{i}/{len(items)}] ({elapsed:.0f}s elapsed) {status}: {path}", flush=True)
    out.close()
    print(f"\nHashing done. {n_new} newly hashed this run.", flush=True)

    # ---- clustering ----
    records = load_done()
    valid = {p: r for p, r in records.items() if "error" not in r}
    errors = {p: r for p, r in records.items() if "error" in r}
    print(f"{len(valid)} hashed successfully, {len(errors)} failed (see hashes.jsonl for errors).", flush=True)

    # group by exact sha256 first
    by_sha = {}
    for path, r in valid.items():
        by_sha.setdefault(r["sha256"], []).append(path)

    # one representative per sha256 group (largest file wins -- usually least re-compressed)
    inv_by_path = {it["path"]: it for it in items}

    def file_size(p):
        return inv_by_path.get(p, {}).get("size", 0)

    exact_groups = []  # list of (representative, [members])
    for sha, paths in by_sha.items():
        rep = max(paths, key=file_size)
        exact_groups.append((rep, paths))

    # near-dup clustering across group representatives, via dhash hamming distance
    def hamming(a, b):
        return bin(int(a, 16) ^ int(b, 16)).count("1")

    reps = [g[0] for g in exact_groups]
    rep_dhash = {p: valid[p]["dhash"] for p in reps}
    parent = {p: p for p in reps}

    def find(p):
        while parent[p] != p:
            parent[p] = parent[parent[p]]
            p = parent[p]
        return p

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(reps)):
        for j in range(i + 1, len(reps)):
            if hamming(rep_dhash[reps[i]], rep_dhash[reps[j]]) <= DHASH_DISTANCE_THRESHOLD:
                union(reps[i], reps[j])

    near_clusters = {}
    for p in reps:
        near_clusters.setdefault(find(p), []).append(p)

    # write report: every file gets a row
    with open(REPORT_OUT, "w", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(["path", "group_id", "dup_type", "is_representative", "representative_path", "error"])
        group_id = 0
        for cluster_root, cluster_reps in near_clusters.items():
            group_id += 1
            # pick overall representative of the near-dup cluster: largest pixel area
            def pixel_area(p):
                it = inv_by_path.get(p, {})
                return (it.get("width") or 0) * (it.get("height") or 0)
            overall_rep = max(cluster_reps, key=pixel_area)
            for rep in cluster_reps:
                members = next(m for r, m in exact_groups if r == rep)
                dup_type = "near" if len(cluster_reps) > 1 else ("exact" if len(members) > 1 else "unique")
                for m in members:
                    is_rep = (m == overall_rep)
                    row_type = "exact_dup" if (m != rep) else dup_type
                    writer.writerow([m, group_id, row_type, is_rep, overall_rep, ""])
        for path, r in errors.items():
            writer.writerow([path, "", "ERROR", False, "", r["error"]])

    n_reps = sum(1 for g in near_clusters.values() for _ in [None]) + 0
    total_files = len(valid) + len(errors)
    total_groups = len(near_clusters)
    print(f"\n{total_files} files -> {total_groups} groups. Report written to {REPORT_OUT}", flush=True)


if __name__ == "__main__":
    main()
