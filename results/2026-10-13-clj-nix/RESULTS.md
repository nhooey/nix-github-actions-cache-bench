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

## Scorer output (§7), rounds 1 and 2

```
python3 scripts/score.py results/2026-10-13-clj-nix/metrics.tsv \
  --footprint hestia=0.65e9 --footprint cna=3.5e9 --footprint mnc=2.4e9
```

```text
Workload: clj-nix-ci   (minutes; job time minus the diagnostics step)
┌──────────────┬──────┬────────┬────────┬─────────┬──────┬──────┬──────┬──────┬───────┬────────┬───────┬──────────────────────┐
│ Backend      │ Runs │ Cold C │ Warm W │ Miss S1 │ G1   │ G2   │ G3   │ G4   │ E@0.2 │ E@0.32 │ E@0.5 │ Verdict              │
├──────────────┼──────┼────────┼────────┼─────────┼──────┼──────┼──────┼──────┼───────┼────────┼───────┼──────────────────────┤
│ none         │    3 │      – │   22.2 │       – │    – │    – │    – │    – │  22.2 │   22.2 │  22.2 │ baseline (median B0) │
│ cachix       │    7 │   22.5 │    7.8 │    15.7 │ PASS │ PASS │ PASS │ ?    │  14.1 │   13.2 │  11.8 │ pending              │
│ cna          │    6 │   16.0 │    8.2 │    22.6 │ PASS │ PASS │ PASS │ PASS │  19.7 │   18.0 │  15.4 │ eligible             │
│ fallback     │    2 │      – │      – │       – │ PASS │ ?    │ ?    │ ?    │     – │      – │     – │ pending              │
│ hestia       │    6 │   22.2 │    7.0 │    16.7 │ PASS │ PASS │ PASS │ PASS │  14.8 │   13.6 │  11.8 │ eligible             │
│ mnc          │    3 │   25.6 │   19.9 │       – │ PASS │ FAIL │ FAIL │ PASS │ 24.5~ │  23.8~ │ 22.8~ │ ineligible           │
│ split-hestia │    3 │   10.6 │    8.2 │     9.4 │ PASS │ PASS │ PASS │ ?    │   9.2 │    9.0 │   8.8 │ pending              │
│ up88         │    2 │      – │      – │       – │ PASS │ ?    │ ?    │ ?    │     – │      – │     – │ pending              │
└──────────────┴──────┴────────┴────────┴─────────┴──────┴──────┴──────┴──────┴───────┴────────┴───────┴──────────────────────┘
  note: cna C1 (38367459118): built in 61% of the baseline's build time; a fast runner, not the backend (record nproc/cpu)
  note: mnc W2 (38369911823): built in 66% of the baseline's build time; a fast runner, not the backend (record nproc/cpu)
  default (no secret): hestia
  split: wall 0.43 x B0 (<= 0.6), job-minutes 1.05 x B0 (<= 1.3): passes; also needs split+winner S <= 0.6 x the winner's S1
```

- G4's sizes were measured after W2, not after the full sequence.
- Cachix's G4 waits on its size from app.cachix.org (`--footprint cachix=<bytes>`).
- The `~` marks E computed with median(C) in place of a missing S1.
- `fallback` and `up88` are checks rather than backend sequences, so the scorer leaves them pending; see below.

## Round 2

- **S1 and S2** are salted commits (a trailing comment in `src/cljnix/utils.clj`): every clj-nix derivation rebuilt, babashka included. S1 ran on a branch `s1`, which reads the cache saved on `main`, as a PR would.
- **Hestia's miss is cheap (16.7 min):** only babashka rebuilt; jars, dependency caches and the other test outputs came from the cache.
- **cache-nix-action's miss costs a full build (22.6 min):** its key changed with `src/`, so it restored the older snapshot, then saved a new 3.5 GB one.
- **Cachix (13.2) is within 1 min of Hestia (13.6),** so it isn't adopted: it needs an account, a token and a repo variable.
- **Split + Hestia** (E 9.0): S 9.4 ≤ 0.6 × Hestia's S1 (10.0), so the split is adopted.
- **Fallback** (`exp/ci-cachix-fallback`, `CACHIX_CACHE` deleted): the Cachix step was skipped and Hestia ran; cold 18.0 min, warm 7.1 min, babashka substituted in 5.4 s.
- **The real branches** (clj-nix `up/86` to `up/88`, without instrumentation): cold 8.9 min, warm 6.3 min.

**Decision for clj-nix:** pin the e2e templates to clj-nix's nixpkgs with a weekly unstable run, cache with Hestia, and run the babashka tests in their own jobs.
