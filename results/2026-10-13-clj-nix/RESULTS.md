# Round 1: clj-nix's CI, 2026-10-13

What ran:
- clj-nix's whole CI job (lint, `nix flake check`, network tests, bats e2e), not the fixture flake.
  - The babashka e2e tests are this repo's `babashka` workload.
  - Job times include about 6–8 min of fixed cost no Nix cache removes.
- Workflow branches `exp/ci-*` in `nhooey/clj-nix`, branched from 6418139, with the e2e templates pinned to clj-nix's locked nixpkgs (6510408).
- One repo per GitHub-cache backend:
  - `nhooey/clj-nix` (no cache, Magic Nix Cache, Cachix, the split)
  - `nhooey/clj-nix-ci-cna`
  - `nhooey/clj-nix-ci-hestia`
- The experiment plan was `PLAN-ci-speed.md` in the clj-nix checkout (untracked; §10 has these results).

## Files

- `runs.tsv`: the lane runner's rows, copied at about 10:50 UTC while round 2 was still running.
  - Columns: lane, branch, trial, run_id, conclusion, notes (no header).
  - The run IDs belong to different repos (see above).
- `metrics.tsv`: `scripts/metrics.py --offline --all` over the saved logs.
  - Step times come from log timestamps; spot checks against the API were within 3 s.
  - `gha_cache_bytes` is empty: round 1 didn't record it per run.
- The raw logs (`<run-id>.log`, 15 MB) are not copied. They were in the clj-nix session's scratchpad, `…/scratchpad/ci-speed/`, which is temporary.

## Headline (from PLAN §10)

Whole-job minutes; for the split, first job start to last job end.

| Variant | Cold (C1) | Warm (W1, W2) | Babashka tests when warm | Cache size | Verdict |
|---|---|---|---|---|---|
| No cache (B0a, B0b) | – | 22.4, 22.2 | 6.8–7.0 + 5.8 min (built) | – | baseline, median 22.3 |
| Hestia | 21.8 | 6.0, 7.0 | 2.7–3.8 + 3.3–4.2 s (substituted) | 0.65 GB, 13 entries | hits; fastest warm |
| Cachix | 22.6 | 7.9, 8.7 | 3.6–4.4 + 4.6–4.8 s (substituted) | on Cachix (5 GB free) | hits |
| cache-nix-action | 15.5 * | 8.3, 8.7 | 1.7–2.3 + 1.9–2.3 s (restored) | 3.5 GB snapshot; 56–69 s restore | hits |
| Magic Nix Cache | 25.7 | 24.4, 15.6 * | rebuilt both times | 2.4 GB, 1,802 entries | ineligible: never uploaded the `bb` outputs |
| Split, no cache (S0a, S0b) | 11.1, 8.0 | – | 6.0–7.0 + 8.0 min, in parallel jobs | – | 0.36–0.50× baseline |
| U (current nixos-unstable) | 23.2 | – | built | – | passes; the weekly unpinned job works |

\* A fast runner: these runs' babashka builds took about 60% of the usual time with nothing cached.

Notes:
- The pin works: C1 and W1 produced the same `bb` outputs, `dfq0i235…-bb` and `yzykqyck…-bb`. The fixture flake's unsalted `babashka` workload evaluates to the same paths.
- Magic Nix Cache uploaded 813 narinfos, none for `bb`, with no throttling in the logs.
- Hestia pushed 1,809 paths (548 MiB) in 5.6 s at the end of its cold run; its setup and post steps take 1–2 s.
- Once babashka is cached, the floor is about 6–8 min: the other e2e tests plus lint, flake checks and network tests.
- Cachix's first C1 (38364822517) pushed nothing because the secret was empty ("Pushing is disabled."). It stays as a cold sample, marked `no-push`.

## Scorer output (§7), on the rows so far

```
python3 scripts/score.py results/2026-10-13-clj-nix/metrics.tsv \
  --footprint hestia=0.65e9 --footprint cna=3.5e9 --footprint mnc=2.4e9
```

```
┌──────────────┬──────┬────────┬────────┬─────────┬──────┬──────┬──────┬──────┬───────┬────────┬───────┬──────────────────────┐
│ Backend      │ Runs │ Cold C │ Warm W │ Miss S1 │ G1   │ G2   │ G3   │ G4   │ E@0.2 │ E@0.32 │ E@0.5 │ Verdict              │
├──────────────┼──────┼────────┼────────┼─────────┼──────┼──────┼──────┼──────┼───────┼────────┼───────┼──────────────────────┤
│ none         │    3 │      – │   22.2 │       – │    – │    – │    – │    – │  22.2 │   22.2 │  22.2 │ baseline (median B0) │
│ cachix       │    6 │   22.6 │    7.8 │    15.7 │ PASS │ PASS │ PASS │ ?    │  14.1 │   13.2 │  11.8 │ pending              │
│ cna          │    5 │   15.4 │    8.2 │    22.6 │ PASS │ PASS │ PASS │ PASS │  19.7 │   18.0 │  15.4 │ eligible             │
│ hestia       │    5 │   21.8 │    7.0 │    16.7 │ PASS │ PASS │ PASS │ PASS │  14.8 │   13.6 │  11.8 │ eligible             │
│ mnc          │    3 │   25.6 │   19.9 │       – │ PASS │ FAIL │ FAIL │ PASS │ 24.5~ │  23.8~ │ 22.8~ │ ineligible           │
│ split-hestia │    3 │   10.6 │    8.2 │     9.4 │ PASS │ PASS │ PASS │ ?    │   9.2 │    9.0 │   8.8 │ pending              │
└──────────────┴──────┴────────┴────────┴─────────┴──────┴──────┴──────┴──────┴───────┴────────┴───────┴──────────────────────┘
  default (no secret): hestia
```

- G4's sizes are PLAN §10's, measured after W2, not after the full sequence.
- Cachix's G4 waits on its size from app.cachix.org (`--footprint cachix=<bytes>`).
- The `~` marks E computed with median(C) in place of a missing S1.
- The S1 rows are early round-2 data: a salt reuses the fetched jars, so S1 is shorter than a cold run.

## Round 2: still running

**Placeholder.** When this was copied, these were still running or not started:
- S1, W3 and C2 for Hestia, Cachix (S2 in place of C2) and cache-nix-action;
- the Cachix fallback check (PLAN §5 phase 3);
- the split with the winning cache (`exp/ci-split-hestia`: C, W and S done, rescore when complete).

To finish: copy the final `runs.tsv`, rerun `metrics.py` on the logs, rerun the scorer with Cachix's size, and replace this section.
