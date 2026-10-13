#!/usr/bin/env python3
"""Per-run metrics: one TSV row per run, with the columns of PLAN-ci-speed §6.

Usage:
  metrics.py --runs results/<round>/runs.tsv --logs logs [--offline] [--no-header] [RUN_ID ... | --all]

Sources:
- runs.tsv (from lane.sh) gives backend, workload, trial, sha, cache usage
  and notes. Round-1 files (no header: lane branch trial run_id conclusion
  [notes]) also work; the lane name maps to the backend.
- <logs>/<run-id>.log (`gh run view --log`) gives the CIMETRIC lines, the
  build or bats timings and the throttle count. Fetched with gh if missing
  and REPO is set.
- Step and job times come from the GitHub API (`gh api .../jobs`) when
  REPO is set and --offline isn't; otherwise from the log's timestamps
  (a step lasts from its first line to the next step's first line).

Columns:
  job_s          run time minus the diagnostics step; for several jobs, the
                 wall-clock (first job start to last job end) minus the
                 diagnostics step of the job that ended last
  wall_s, sum_job_s   wall-clock and summed job time (CI minutes), with diagnostics
  cache_setup_s, cache_post_s   the backend's step and its post step, summed over jobs
  build_s        the workload step ("Build workload"; round 1: "End-to-end tests")
  out_ms         attr=ms per built attribute (round 1: the bats babashka timings)
  out_class      attr:class per output path (round 1: the `bb` paths)
  closure_*      closure path counts by class (new template only)
Python standard library only (3.9+).
"""
import argparse
import csv
import datetime as dt
import json
import os
import pathlib
import re
import subprocess
import sys

COLS = ["backend", "workload", "trial", "run_id", "sha", "conclusion",
        "job_s", "wall_s", "sum_job_s", "diag_s", "cache_setup_s", "cache_post_s", "build_s",
        "out_ms", "out_class", "closure_built", "closure_substituted", "closure_restored",
        "throttles", "gha_cache_bytes", "store_bytes", "nproc", "cpu", "notes"]

CACHE_STEP = re.compile(r"hestia|cachix|cache-nix-action|magic-nix-cache|GitHub Actions cache", re.I)
BUILD_STEP = re.compile(r"^(Build workload|End-to-end test)")
DIAG_STEP = "Cache diagnostics"
THROTTLE = re.compile(r"HTTP error 418|429 Too Many|throttled")
BATS = re.compile(r"(not )?ok \d+ nix build \.#(babashka(?:-with-features)?-test) in (\d+)ms")
TS = re.compile(r"^\ufeff?(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?Z ?(.*)$")
# Round-1 lanes: <phase letter>-<variant>; "base" was the no-cache baseline
LEGACY_BACKEND = {"base": "none"}


def die(msg):
    sys.exit(f"metrics: {msg}")


def parse_ts(s):
    m = TS.match(s)
    if not m:
        return None, s
    t = dt.datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    return t + float(m.group(2) or 0), m.group(3)


def read_runs(path):
    """run_id -> dict of what runs.tsv knows about it."""
    runs = {}
    if not path or not pathlib.Path(path).exists():
        return runs
    with open(path, newline="") as f:
        rows = [r for r in csv.reader(f, delimiter="\t") if r and any(c.strip() for c in r)]
    if rows and rows[0][0] == "lane":
        hdr = rows[0]
        for r in rows[1:]:
            d = dict(zip(hdr, r + [""] * (len(hdr) - len(r))))
            runs[d["run_id"]] = d
    else:
        # Round 1: lane branch trial run_id conclusion [notes]
        for r in rows:
            lane, branch, trial, rid, concl = r[:5]
            variant = lane.split("-", 1)[1] if "-" in lane else lane
            runs[rid] = {"lane": lane, "backend": LEGACY_BACKEND.get(variant, variant),
                         "workload": "clj-nix-ci", "ref": branch, "trial": trial, "run_id": rid,
                         "conclusion": concl, "sha": "", "gha_cache_bytes": "",
                         "notes": r[5] if len(r) > 5 else ""}
    return runs


def gh(*args):
    return subprocess.run(["gh", *args], check=True, capture_output=True, text=True).stdout


def parse_log(path):
    """Steps with times, plus every metric the log carries."""
    jobs = {}      # job -> ordered {step: [first_ts, last_ts]}
    m = {"out_ms": [], "out_class": [], "closure": {}, "throttles": 0, "sha": "",
         "store_bytes": "", "nproc": "", "cpu": "", "cache_mode": "", "no_push": False}
    want_sha = False
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            parts = line.rstrip("\n").split("\t", 2)
            if len(parts) < 3:
                continue
            job, step, rest = parts
            t, text = parse_ts(rest)
            if t is not None:
                steps = jobs.setdefault(job, {})
                if step in steps:
                    steps[step][1] = t
                else:
                    steps[step] = [t, t]
            if THROTTLE.search(text):
                m["throttles"] += 1
            if "Pushing is disabled." in text or "Missing a Cachix auth token" in text:
                m["no_push"] = True
            if want_sha and re.fullmatch(r"[0-9a-f]{40}", text.strip()):
                m["sha"], want_sha = text.strip(), False
            if text.endswith("git log -1 --format=%H") or text.endswith("git log -1 --format='%H'"):
                want_sha = True
            b = BATS.search(text)
            if b:
                m["out_ms"].append(f"{b.group(2)}={'!' if b.group(1) else ''}{b.group(3)}")
                continue
            if text.startswith("CIMETRIC\t"):
                c = text.split("\t")
                if c[1] == "path" and len(c) == 6:          # new: attr name class size
                    m["out_class"].append(f"{c[2]}:{c[4]}")
                elif c[1] == "path" and len(c) == 5 and c[2] == "bb":   # round 1
                    m["out_class"].append(f"bb:{c[3]}")
                elif c[1] == "closure_class" and len(c) == 5:
                    m["closure"][c[2]] = c[3]
            elif text.startswith("CIMETRIC "):
                c = text.split(" ", 2)
                key, val = c[1], (c[2] if len(c) > 2 else "")
                if key == "build":
                    attr, ms = val.split()
                    m["out_ms"].append(f"{attr}={ms}")
                elif key in ("store_bytes", "nproc", "cpu", "cache_mode"):
                    m[key] = val.strip()
    return jobs, m


def jobs_from_log(jobs):
    """job -> (start, end, {step: seconds})"""
    out = {}
    for job, steps in jobs.items():
        items = sorted(steps.items(), key=lambda kv: kv[1][0])
        durs = {}
        for i, (name, (first, last)) in enumerate(items):
            end = items[i + 1][1][0] if i + 1 < len(items) else last
            durs[name] = durs.get(name, 0) + (end - first)
        out[job] = (items[0][1][0], items[-1][1][1], durs)
    return out


def jobs_from_api(repo, rid):
    data = json.loads(gh("api", f"repos/{repo}/actions/runs/{rid}/jobs?per_page=100"))
    out = {}

    def t(s):
        return dt.datetime.strptime(s[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=dt.timezone.utc).timestamp()
    for j in data["jobs"]:
        if not (j.get("started_at") and j.get("completed_at")):
            continue
        durs = {}
        for s in j.get("steps", []):
            if s.get("started_at") and s.get("completed_at"):
                durs[s["name"]] = durs.get(s["name"], 0) + t(s["completed_at"]) - t(s["started_at"])
        out[j["name"]] = (t(j["started_at"]), t(j["completed_at"]), durs)
    return out


def fmt(x):
    return "" if x is None or x == "" else str(int(round(x)))


def row(rid, runs, logs, repo, offline):
    info = runs.get(rid, {})
    log = pathlib.Path(logs) / f"{rid}.log"
    if not log.exists() and repo and not offline:
        log.write_text(gh("run", "view", rid, "-R", repo, "--log"))
    if not log.exists():
        die(f"no log for run {rid} at {log} (set REPO to fetch it)")
    steps, m = parse_log(log)
    jobs = jobs_from_api(repo, rid) if (repo and not offline) else jobs_from_log(steps)
    if not jobs:
        die(f"run {rid}: no job timings")
    start = min(j[0] for j in jobs.values())
    end = max(j[1] for j in jobs.values())
    last = max(jobs.values(), key=lambda j: j[1])
    wall = end - start
    diag = last[2].get(DIAG_STEP, 0)

    def total(pred):
        return sum(d for j in jobs.values() for n, d in j[2].items() if pred(n))
    notes = [n for n in [info.get("notes", "")] if n]
    if m["no_push"] and "no-push" not in " ".join(notes):
        notes.append("no-push")
    if m["cache_mode"] and m["cache_mode"] != "default":
        notes.append(f"cache_mode={m['cache_mode']}")
    if len(jobs) > 1:
        notes.append(f"jobs={len(jobs)}")
    concl = info.get("conclusion", "")
    sha = info.get("sha") or m["sha"]
    if repo and not offline and (not concl or not sha):
        r = json.loads(gh("api", f"repos/{repo}/actions/runs/{rid}"))
        concl, sha = concl or (r.get("conclusion") or ""), sha or r.get("head_sha", "")
    vals = {
        "backend": info.get("backend", ""), "workload": info.get("workload", ""),
        "trial": info.get("trial", ""), "run_id": rid, "sha": sha, "conclusion": concl,
        "job_s": fmt(wall - diag), "wall_s": fmt(wall),
        "sum_job_s": fmt(sum(j[1] - j[0] for j in jobs.values())), "diag_s": fmt(diag),
        "cache_setup_s": fmt(total(lambda n: CACHE_STEP.search(n) and not n.startswith("Post "))),
        "cache_post_s": fmt(total(lambda n: CACHE_STEP.search(n) and n.startswith("Post "))),
        "build_s": fmt(total(lambda n: BUILD_STEP.search(n))),
        "out_ms": ",".join(m["out_ms"]), "out_class": ",".join(m["out_class"]),
        "closure_built": m["closure"].get("built", ""),
        "closure_substituted": m["closure"].get("substituted", ""),
        "closure_restored": m["closure"].get("restored", ""),
        "throttles": str(m["throttles"]), "gha_cache_bytes": info.get("gha_cache_bytes", ""),
        "store_bytes": m["store_bytes"], "nproc": m["nproc"], "cpu": m["cpu"],
        "notes": "; ".join(notes),
    }
    return [vals[c] for c in COLS]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--runs", help="runs.tsv from lane.sh (or round 1's)")
    ap.add_argument("--logs", default="logs", help="directory of <run-id>.log files (default: logs)")
    ap.add_argument("--repo", default=os.environ.get("REPO", ""), help="owner/name (default: $REPO)")
    ap.add_argument("--offline", action="store_true", help="use only runs.tsv and the logs")
    ap.add_argument("--no-header", action="store_true")
    ap.add_argument("--all", action="store_true", help="every run in --runs")
    ap.add_argument("run_ids", nargs="*")
    a = ap.parse_args()

    runs = read_runs(a.runs)
    ids = list(runs) if a.all else a.run_ids
    if not ids:
        die("no runs given (pass run ids or --all with --runs)")
    if a.all and a.repo and not a.offline:
        # One runs.tsv can span repos (round 1 did); the API needs the right one
        die("--all with the API needs one repo per run; use --offline, or pass ids with REPO set")
    w = csv.writer(sys.stdout, delimiter="\t", lineterminator="\n")
    if not a.no_header:
        w.writerow(COLS)
    for rid in ids:
        if a.all and not (pathlib.Path(a.logs) / f"{rid}.log").exists():
            print(f"metrics: skipping {rid}: no log", file=sys.stderr)
            continue
        w.writerow(row(rid, runs, a.logs, a.repo, a.offline))


if __name__ == "__main__":
    main()
