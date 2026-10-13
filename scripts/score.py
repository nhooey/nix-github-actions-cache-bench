#!/usr/bin/env python3
"""Apply PLAN-ci-speed §7: the four gates, the expected time E, and the choices.

Usage:
  score.py METRICS.tsv [...] [--workload W] [--baseline none] [--footprint BACKEND=BYTES ...]
           [--provisional]

Trials (by label, from metrics.py's `trial` column):
  baseline   every run of the baseline backend except U (unstable mode)
  cold C     C1, C2, C, and S2 (Cachix's second cold run is a salt, not a purge)
  warm W     W1, W2, W3, W; W1 (else the first W) is the convergence run
  miss S     S1 (else S)
Gates (each PASS, FAIL or ? when its runs are missing):
  G1  no cache-caused failure: a failed run counts unless its notes say
      "flake" or "not-cache" (one rerun allowed to rule out a flake)
  G2  converges: on W1 every output was substituted or restored and every
      timed attribute took under 60 s
  G3  a miss is cheap: median(C) - median(B0) <= 3 min and S1 - median(B0) <= 3 min
  G4  footprint: GitHub cache <= 5 GB (half of 10 GB), or Cachix <= 2.5 GB
      (half of the free 5 GB). From gha_cache_bytes, or --footprint.
Score: E(h) = h*median(W) + (1-h)*S1, at h = 0.2, 0.32 (PLAN §2) and 0.5.
  Without an S1 run, median(C) stands in for S1, marked "~".
Choices: the no-secret default is the eligible GitHub-cache backend (mnc,
  cna, hestia) with the lowest E(0.32); differences under 1 min are ties and
  go to the simpler setup (cna, then mnc, then hestia). Cachix is adopted
  only if eligible and E(cachix) <= E(default) - 1 min. The job split is
  adopted if S0's wall <= 0.6 x median(B0) and its job-minutes <= 1.3 x B0's.
Python standard library only (3.9+).
"""
import argparse
import csv
import statistics
import sys

H = [0.2, 0.32, 0.5]
GHA_BACKENDS = ["cna", "mnc", "hestia"]     # tie order: simplest setup first
GHA_LIMIT = 5e9
CACHIX_LIMIT = 2.5e9
GATE_S = 180
CONVERGE_MS = 60000
TIE_S = 60


def die(msg):
    sys.exit(f"score: {msg}")


def num(s):
    try:
        return float(s)
    except (TypeError, ValueError):
        return None


def med(xs):
    xs = [x for x in xs if x is not None]
    return statistics.median(xs) if xs else None


def kind(trial):
    t = trial.upper()
    if t == "U":
        return "unstable"
    if t.startswith("B0"):
        return "base"
    if t in ("C", "S2") or (t.startswith("C") and t[1:].isdigit()):
        return "cold"
    if t == "W" or (t.startswith("W") and t[1:].isdigit()):
        return "warm"
    if t in ("S1", "S"):
        return "miss"
    if t.startswith("S0"):
        return "split"
    return "other"


def out_ms(r):
    """[(attr, ms, failed)] from 'attr=ms,attr=!ms'"""
    res = []
    for item in filter(None, r["out_ms"].split(",")):
        attr, _, v = item.partition("=")
        res.append((attr, num(v.lstrip("!")), v.startswith("!")))
    return res


def mins(s, mark=""):
    return "–" if s is None else f"{s / 60:.1f}{mark}"


def table(headers, rows):
    """Box-drawn table; numeric-looking cells right-aligned."""
    w = [max(len(str(x)) for x in col) for col in zip(headers, *rows)]

    def line(l, m, r):
        return l + m.join("─" * (n + 2) for n in w) + r

    def cells(row):
        out = []
        for x, n in zip(row, w):
            x = str(x)
            right = x[:1].isdigit() or x[:1] in "–-"
            out.append(" " + (x.rjust(n) if right else x.ljust(n)) + " ")
        return "│" + "│".join(out) + "│"
    lines = [line("┌", "┬", "┐"), cells(headers), line("├", "┼", "┤")]
    lines += [cells(r) for r in rows]
    lines.append(line("└", "┴", "┘"))
    return "\n".join(lines)


def score_workload(rows, workload, base_name, footprint, provisional):
    by = {}
    for r in rows:
        by.setdefault(r["backend"], []).append(r)
    if base_name not in by:
        print(f"[{workload}] no '{base_name}' runs: no baseline, skipping")
        return
    b0 = med([num(r["job_s"]) for r in by[base_name] if kind(r["trial"]) != "unstable"])
    built_ms = med([sum(ms for _, ms, _ in out_ms(r) if ms) for r in by[base_name]
                    if out_ms(r) and kind(r["trial"]) != "unstable"])

    results, notes = {}, []
    hdr = ["Backend", "Runs", "Cold C", "Warm W", "Miss S1", "G1", "G2", "G3", "G4"] + \
          [f"E@{h}" for h in H] + ["Verdict"]
    out = []
    for name in sorted(by, key=lambda n: (n != base_name, n)):
        rs = by[name]
        if name == base_name:
            e = {h: b0 for h in H}
            results[name] = {"E": e, "eligible": True}
            out.append([name, len(rs), "–", mins(b0), "–", "–", "–", "–", "–"] +
                       [mins(b0)] * 3 + ["baseline (median B0)"])
            continue
        if all(kind(r["trial"]) == "split" for r in rs):
            continue    # the plain split is judged by its own rule below
        cold = [num(r["job_s"]) for r in rs if kind(r["trial"]) == "cold"]
        warm_rows = [r for r in rs if kind(r["trial"]) == "warm"]
        miss = [num(r["job_s"]) for r in rs if kind(r["trial"]) == "miss"]
        c, w, s = med(cold), med([num(r["job_s"]) for r in warm_rows]), (miss[0] if miss else None)

        # Runner variance: a cold or warm run that built its outputs much
        # faster than the baseline did. Not S1: a salt reuses the fetched
        # dependencies, so its builds are legitimately shorter.
        for r in rs:
            if kind(r["trial"]) not in ("cold", "warm"):
                continue
            o = out_ms(r)
            if built_ms and o and "built" in r["out_class"] and sum(ms or 0 for _, ms, _ in o) < 0.7 * built_ms:
                notes.append(f"{name} {r['trial']} ({r['run_id']}): built in "
                             f"{sum(ms or 0 for _, ms, _ in o) / built_ms:.0%} of the baseline's build time; "
                             "a fast runner, not the backend (record nproc/cpu)")

        # G1: failures not marked as flakes
        bad = [r for r in rs if r["conclusion"] not in ("success", "")
               and not any(k in r["notes"] for k in ("flake", "not-cache"))]
        g1 = "FAIL" if bad else "PASS"

        # G2: convergence on W1
        w1 = next((r for r in warm_rows if r["trial"].upper() == "W1"), warm_rows[0] if warm_rows else None)
        if w1 is None:
            g2 = "?"
        else:
            classes = [x.split(":", 1)[1] for x in filter(None, w1["out_class"].split(","))]
            fast = all(ms is not None and ms < CONVERGE_MS and not failed for _, ms, failed in out_ms(w1))
            hit = classes and all(k in ("substituted", "restored") for k in classes)
            g2 = "PASS" if (fast and hit and out_ms(w1)) else "FAIL"

        # G3: a miss costs at most 3 min more than the baseline
        if c is None:
            g3 = "?"
        elif c - b0 > GATE_S or (s is not None and s - b0 > GATE_S):
            g3 = "FAIL"
        else:
            g3 = "PASS" if s is not None else "?"

        # G4: footprint
        limit = CACHIX_LIMIT if name.startswith("cachix") else GHA_LIMIT
        fp = footprint.get(name)
        if fp is None:
            fp = max([num(r["gha_cache_bytes"]) for r in rs if num(r["gha_cache_bytes"]) is not None] or [None],
                     key=lambda x: x or 0)
            if name.startswith("cachix"):
                fp = footprint.get(name)    # GitHub usage says nothing about Cachix
        g4 = "?" if fp is None else ("PASS" if fp <= limit else "FAIL")

        gates = [g1, g2, g3, g4]
        s_used, mark = (s, "") if s is not None else (c, "~")
        e = {h: (h * w + (1 - h) * s_used) if (w is not None and s_used is not None) else None for h in H}
        if "FAIL" in gates:
            verdict, ok = "ineligible", False
        elif "?" in gates:
            verdict, ok = "pending", provisional
        else:
            verdict, ok = "eligible", True
        results[name] = {"E": e, "eligible": ok}
        out.append([name, len(rs), mins(c), mins(w), mins(s), *gates] +
                   [mins(e[h], mark) for h in H] + [verdict])

    print(f"Workload: {workload}   (minutes; job time minus the diagnostics step)")
    print(table(hdr, out))
    for n in notes:
        print(f"  note: {n}")

    # The no-secret default
    def pick(h):
        cands = [(results[n]["E"][h], n) for n in GHA_BACKENDS
                 if n in results and results[n]["eligible"] and results[n]["E"][h] is not None]
        if not cands:
            return None
        best = min(e for e, _ in cands)
        ties = [n for e, n in cands if e - best < TIE_S]
        return sorted(ties, key=GHA_BACKENDS.index)[0], ties
    choice = pick(0.32)
    if choice is None:
        print("  default: none eligible" + ("" if provisional else " (pass --provisional to rank pending ones)"))
    else:
        d, ties = choice
        tie = f" (tie within 1 min: {', '.join(ties)}; simplest setup wins)" if len(ties) > 1 else ""
        print(f"  default (no secret): {d}{tie}")
        for h in (0.2, 0.5):
            other = pick(h)
            if other and other[0] != d:
                print(f"  note: at h = {h} the default would be {other[0]}")
        cx = results.get("cachix")
        if cx and cx["eligible"] and cx["E"][0.32] is not None:
            gain = results[d]["E"][0.32] - cx["E"][0.32]
            verdict = "adopt Cachix with the fallback" if gain >= TIE_S else "don't adopt Cachix"
            print(f"  cachix: E(cachix) is {gain / 60:+.1f} min better than {d}: {verdict}")

    # The job split, on its own
    split = [r for r in by.get("split", []) if kind(r["trial"]) == "split"]
    if split:
        wall = med([num(r["wall_s"]) for r in split])
        jobs = med([num(r["sum_job_s"]) for r in split])
        ok = wall <= 0.6 * b0 and jobs <= 1.3 * b0
        print(f"  split: wall {wall / b0:.2f} x B0 (<= 0.6), job-minutes {jobs / b0:.2f} x B0 (<= 1.3): "
              + ("passes" if ok else "fails") + "; also needs split+winner S <= 0.6 x the winner's S1")
    print()


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("metrics", nargs="+", help="metrics TSV files from metrics.py")
    ap.add_argument("--workload", help="score only this workload")
    ap.add_argument("--baseline", default="none", help="baseline backend (default: none)")
    ap.add_argument("--footprint", action="append", default=[], metavar="BACKEND=BYTES",
                    help="cache size after the sequence, e.g. cachix=1.2e9 (read from app.cachix.org)")
    ap.add_argument("--provisional", action="store_true",
                    help="rank backends whose gates are still '?' as if they passed")
    a = ap.parse_args()

    footprint = {}
    for f in a.footprint:
        k, _, v = f.partition("=")
        if num(v) is None:
            die(f"bad --footprint {f!r} (want BACKEND=BYTES)")
        footprint[k] = num(v)
    rows = []
    for path in a.metrics:
        with open(path, newline="") as f:
            rd = csv.DictReader(f, delimiter="\t")
            need = {"backend", "workload", "trial", "job_s", "out_ms", "out_class", "conclusion", "notes"}
            if not need <= set(rd.fieldnames or []):
                die(f"{path}: missing columns {sorted(need - set(rd.fieldnames or []))}")
            rows += list(rd)
    workloads = sorted({r["workload"] for r in rows})
    if a.workload:
        workloads = [w for w in workloads if w == a.workload] or die(f"no rows for workload {a.workload}")
    for w in workloads:
        score_workload([r for r in rows if r["workload"] == w], w, a.baseline, footprint, a.provisional)


if __name__ == "__main__":
    main()
