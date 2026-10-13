# nix-github-actions-cache-bench

Benchmarks Nix binary-cache backends on GitHub Actions' free hosted runners.

## What it measures

For each backend, on the same pinned workloads:
- **cold:** the first run on an empty cache, including the backend's save or upload cost;
- **warm:** the same commit again, to see whether the cache converges in one run, and how much it varies;
- **miss:** a salted commit that rebuilds the workload's own code but not its fetched dependencies, as a real source change would;
- **footprint:** GitHub cache usage, or Cachix usage, against the free quota;
- **per output:** whether each output was `restored` (from a store snapshot), `substituted` (from a binary cache) or `built`.

Backends (free and not self-hosted only):

| Name | Action | How it stores |
|---|---|---|
| `none` | – | baseline: build everything |
| `hestia` | `Mic92/hestia@v3.1.0`, `upstream-cache-filter: true` | chunks packed into a few GitHub cache entries |
| `cachix` | `cachix/cachix-action@v17`, with a Hestia fallback when no Cachix is configured | Cachix (5 GB free for open source) |
| `cna` | `nix-community/cache-nix-action@v7` | one `/nix` snapshot per key |
| `mnc` | `DeterminateSystems/magic-nix-cache-action@v15` | one GitHub cache entry per store path |

Not included: Garnix (discontinued), FlakeHub Cache (paid), self-hosted caches (Attic, niks3) and self-hosted runners.

Workloads (`fixtures/flake.nix`; nixpkgs and every source pinned):

| Workload | What it builds | Stresses |
|---|---|---|
| `babashka` | `babashka-test` and `babashka-with-features-test` via clj-nix, as in clj-nix's e2e tests | two large GraalVM native builds over about 2,300 Maven jar paths; matches round 1 |
| `ripgrep` | ripgrep 15.2.0 via crane, deps as their own derivation | a moderate Rust compile over about 25 crate paths |
| `knob-many-small` | 1,000 paths × 4 KiB | per-path cost and upload rate limits |
| `knob-few-large` | 4 paths × 256 MiB, incompressible | per-byte cost |
| `knob-slow` | 4 paths × 1 MiB, 300 s build each | build-time saving alone |

Each backend runs in **its own GitHub repo**: GitHub's cache quota (10 GB) and upload limit (200/min) are per repo, so backends sharing a repo would disturb each other and couldn't run in parallel.

## Round-1 headline (2026-10-13, clj-nix's whole CI job)

Median no-cache job: 22.3 min.

| Backend | Cold | Warm | Verdict |
|---|---|---|---|
| Hestia | 21.8 | 6.0, 7.0 | hits; fastest warm; 0.65 GB |
| Cachix | 22.6 | 7.9, 8.7 | hits |
| cache-nix-action | 15.5 (fast runner) | 8.3, 8.7 | hits; 3.5 GB snapshot, about 1 min restore |
| Magic Nix Cache | 25.7 | 24.4, 15.6 | ineligible: never uploaded the babashka outputs |

- Once babashka is cached, clj-nix's CI floor is about 6–8 min of work no Nix cache removes.
- The scorer's no-secret default on round 1's rows is Hestia.
- Details, caveats and round 2's status: [`results/2026-10-13-clj-nix/RESULTS.md`](results/2026-10-13-clj-nix/RESULTS.md).

## Layout

- `fixtures/`: the workload flake (`flake.nix`, `flake.lock`, `knobs.nix`) and its `salt` file.
- `workflows/template.yml` and `workflows/backends/<backend>.yml`: the bench job and each backend's cache steps.
- `scripts/`:
  - `render.py`: renders one repo tree per backend into `build/`;
  - `publish.sh`: commits and pushes the rendered trees (it never creates repos);
  - `lane.sh`: runs one repo's trial sequence;
  - `salt.sh`: commits a new salt;
  - `purge.sh`: empties a repo's GitHub cache, keeping the installer entry;
  - `metrics.py`: one TSV row per run;
  - `score.py`: the gates and the expected-time score.
- `results/<date>-<name>/`: `runs.tsv`, `metrics.tsv` and `RESULTS.md` per round.
- `AGENTS.md`: the runbook for rerunning the bench.

## How to read the results

- **Times** are job minutes minus the diagnostics step. `job_s` in `metrics.tsv` is that number in seconds.
- **Trials:** C1/C2 cold, W1–W3 warm, S1 a salted miss, W3 the warm run after it. Cachix's second cold run is a salt (S2), since its cache can't be purged.
- **`out_class`** says how each output arrived. A warm run with `built` outputs means the backend didn't converge.
- **Gates** (all four must pass):
  1. no cache-caused failures;
  2. W1 converges: every output `substituted` or `restored`, each in under 60 s;
  3. a miss costs at most 3 min more than no cache, cold or salted;
  4. the footprint is at most half the free quota.
- **E = h·median(W) + (1−h)·S1** is the expected job time when a fraction h of commits leave the cache valid. Round 1 measured h ≈ 0.32 for clj-nix; the scorer also shows 0.2 and 0.5.
- **Differences under 1 min are ties.** Runner speed varies: some runners build in about 60% of the usual time. Check `nproc` and `cpu` in `metrics.tsv` before believing a gap.
