# Runbook for a supervising agent

The scripts do the deterministic parts: render, publish, dispatch, poll, purge, salt, extract, score. You supervise: check each run's diagnostics, judge failures, decide reruns, and write up the results.

## 0. Ground rules

- Free, non-self-hosted backends only. Never add Garnix (discontinued) or FlakeHub Cache (paid).
- One lane per repo at a time: `lane.sh` matches runs to dispatches by branch and time, and backends in one repo would share a cache quota.
- Stage explicit paths only; never `git add -A`, `--all`, `.` or `commit -a`.
- On macOS, the user's PATH has GNU sed. The scripts avoid `sed -i`; keep it that way.
- Stop only processes you started, by PID. No pattern-based `pkill`.

## 1. Setup that needs a human

Ask for these; the agent can't or mustn't do them:

1. **Create one public, empty repo per backend** (the classifier blocks `gh repo create`):
   - `nhooey/nix-gha-cache-bench-none`
   - `nhooey/nix-gha-cache-bench-hestia`
   - `nhooey/nix-gha-cache-bench-cachix`
   - `nhooey/nix-gha-cache-bench-cna`
   - `nhooey/nix-gha-cache-bench-mnc`
   - Public repos get free Actions minutes on standard runners.
   - For one repo per backend × workload, render with `--repo-pattern 'nhooey/nix-gha-cache-bench-{backend}-{workload}'` and create those instead.
2. **Cachix** (for the `cachix` repo only):
   1. Sign in at https://app.cachix.org as `nhooey`.
   2. Create a **public** cache, e.g. `nhooey-gha-bench`, with signing managed by Cachix.
   3. Generate an auth token for that cache with **write** permission.
   4. `gh variable set CACHIX_CACHE -R nhooey/nix-gha-cache-bench-cachix --body nhooey-gha-bench`
   5. `gh secret set CACHIX_AUTH_TOKEN -R nhooey/nix-gha-cache-bench-cachix`, pasting the token at the prompt, never on the command line.
3. **Keep the clj-nix pin alive:** `fixtures/flake.nix` pins `github:nhooey/clj-nix/641813992fc6…`, a commit on the fork's force-pushed integration branch. Ask the human to push a tag to keep it, e.g. `git push origin 6418139:refs/tags/bench/round1-base`.
4. **Approve pushes:** `publish.sh --push` and `lane.sh` (via `salt.sh --push`) push to the bench repos. Confirm that you're allowed to before the first run.

## 2. Prepare

```sh
nix flake lock ./fixtures          # only if inputs changed; commit the lock
scripts/render.py                  # -> build/nix-gha-cache-bench-<backend>/
scripts/publish.sh build/nix-gha-cache-bench-*        # commit locally, review
scripts/publish.sh --push build/nix-gha-cache-bench-* # after the human created the repos
```

Before the first dispatch, check:
- each repo has `.github/workflows/bench.yml` on `main` (dispatch needs it on the default branch);
- `gh variable list -R …-cachix` shows `CACHIX_CACHE`, and `gh secret list` shows `CACHIX_AUTH_TOKEN`;
- `gh api repos/<repo>/actions/cache/usage` reports 0 entries, or only the installer entry.

## 3. Run order and lanes

**Sequence per backend and workload** (6 runs):

| Trial | Run | Measures |
|---|---|---|
| C1 | first run on an empty cache | cold cost plus save/upload |
| W1 | same commit | convergence in one run |
| W2 | same commit | warm variance |
| S1 | salted commit | a real miss: restore, rebuild, save |
| W3 | S1's commit again | warm after a miss |
| C2 | after a purge (Cachix: salt S2) | second cold sample |

**The baseline (`none`):** 2 or 3 runs per workload are enough (B0a, B0b); there's nothing to warm.

**Lanes:** run one lane per repo, all repos in parallel. Within a repo, run workloads one after another and purge between them. A lane for one backend and one workload:

```sh
DIR=build/nix-gha-cache-bench-hestia OUT=results/<date>-fixtures \
  scripts/lane.sh G-hestia purge \
    babashka:C1 babashka:W1 babashka:W2 \
    salt:S1 babashka:S1 babashka:W3 \
    purge babashka:C2
```

- Run it in the background and save its output (`… > lane-hestia.out 2>&1`). Report the run URLs it prints.
- `DRY_RUN=1` prints the plan first.
- **Cachix:** its cache can't be purged, so replace `purge babashka:C2` with `salt:S2 babashka:S2`.
- **Salt branch:** `salt:S1` commits on the current branch (`main`). `salt:S1:s1` with `babashka:S1:s1` puts the salt on a branch `s1`, as a PR would. A non-default branch reads only its own caches and the default branch's, so `main`'s warm cache is visible there and the reverse isn't.
- **Workload order:** `babashka` first (the round-1 comparison), then `ripgrep` and the three knobs.
- **Early stops:**
  - **Magic Nix Cache:** if W1 and W2 both rebuild (outputs `built`, or each over 60 s), mark it "doesn't converge" and skip S1, W3 and C2. In round 1 it never uploaded the babashka outputs.
  - **Any backend:** if a cache-caused failure repeats after one rerun, mark it ineligible and stop its sequence.
- Budget: a babashka sequence is about 6 × 15–25 min. Knob and ripgrep sequences are shorter.

## 4. Check every run

After each run, read its metrics row (`$OUT/metrics.tsv`) and, if anything looks off, the log (`logs/<run-id>.log`):
- `out_class`: the outputs on W runs should all be `substituted` or `restored`.
- `cache_mode` in `notes` (Cachix only): `push` expected. `pull-only` means the token is missing: the run is a cold sample, not a C1 that seeds the cache; rerun after fixing the secret. `fallback` means the variable is missing.
- `no-push` in `notes`: Cachix logged "Pushing is disabled." Same problem as above.
- `throttles`: lines with 418, 429 or "throttled". Above 0 means the backend hit GitHub's rate limit.
- `nproc` and `cpu`: note the runner. Round 1 saw runners that built babashka in about 60% of the usual time; `score.py` flags them.
- `store_bytes` and `closure_*`: sanity checks for snapshot sizes and how much of the closure the backend served.

## 5. Failures: cache-caused or flake

**Cache-caused** (counts against gate 1):
- the cache step or its post step fails (restore, save, push, purge);
- the build fails on a corrupt or partial path, or a hash mismatch for a substituted path;
- the job runs out of disk after a restore (`CIMETRIC root_disk`), or hits the 90-min timeout in a post step that's still uploading.

**Not cache-caused** (rerun once, mark `flake` or `not-cache` in the `notes` column of `runs.tsv`):
- runner provisioning or "lost communication with the server";
- network failures fetching from cache.nixos.org, GitHub or Maven/crates.io before the cache is involved;
- a GitHub incident (check https://www.githubstatus.com).

**Rule:** one rerun. If the same failure repeats, it's cache-caused unless the log shows otherwise. Write the reason in `notes`; the scorer ignores failed rows only when the notes say `flake` or `not-cache`.

## 6. GitHub limits

- 10 GB of cache per repo. Above it, the oldest entries are evicted (or billed if the limit was raised; don't raise it).
- 200 cache uploads per minute and 1,500 downloads per minute, per repo. Magic Nix Cache's one-entry-per-path design is the one this hurts.
- Entries unused for 7 days are evicted: a sequence must not pause for a week between runs.
- A run on a non-default branch reads only its own branch's caches and the default branch's. A PR reads its base branch's.
- `workflow_dispatch` only works for a workflow file on the default branch; `--ref` picks which branch's copy runs.
- Fork-PR runs get no secrets and no variables. The Cachix workflow then takes the fallback step.

## 7. Purge rules

- Purge with `scripts/purge.sh <repo>`, never `gh cache delete --all`: that manages about 1 entry/s, which takes most of an hour for Magic Nix Cache's thousands of entries. `purge.sh` deletes by id, 8 at a time.
- Always keep the Determinate Nix installer entry (`determinatesystem-nix-installer*`, about 47 MiB); `purge.sh` keeps it by default.
- Purge before each backend's C1 and C2, and between workloads in the same repo.
- To delete only one backend's entries, filter keys:
  - Hestia: `--only '^(pack-|m3#)'`
  - cache-nix-action: `--only '^nix-Linux-'`
- After a purge, check `gh api repos/<repo>/actions/cache/usage`: only the installer entry should remain.
- Cachix: don't delete paths. Its second cold run is a salt (S2).

## 8. Gotchas

- `gh run watch` can exit before the run ends. `lane.sh` polls `gh run view --json status` instead.
- `secrets` can't be used in a step's `if:`; use `vars` or `env`. The Cachix workflow puts both in job `env`.
- An empty Cachix `authToken` means pull-only: the action runs and logs "Pushing is disabled." without failing. Round 1's first Cachix C1 pushed nothing this way.
- An empty Cachix `name` fails the action, so the step is guarded by `env.CACHIX_CACHE != ''`.
- Hestia's cache keys are `pack-*` and `m3#*`. For long-lived use it needs a daily GC workflow; the bench purges instead.
- Magic Nix Cache never uploaded the babashka outputs in round 1 (813 narinfos, none for `bb`, no throttling logged), so it never converged.
- cache-nix-action GCs the store before saving. The workload's out-links in `$RUNNER_TEMP` are GC roots, so the outputs survive; without roots they'd be deleted. Tune `gc-max-store-size-linux` from C1's `store_bytes` plus 20%.
- Flakes only see tracked files: `salt.sh` commits the salt; an uncommitted salt does nothing.
- `render.py` keeps a rendered repo's existing `salt`, so re-rendering doesn't undo a salt.
- `nix path-info --json` without `--json-format` warns on new Nix; the diagnostics handle both the array and object forms.
- On macOS the user's PATH has GNU sed.
- The fixture's babashka uses the fork's clj-nix (babashka 1.13.226 plus the features fix). Upstream clj-nix's babashka 1.12 doesn't build on GraalVM ≥ 25.1.

## 9. Write up the results

1. Make `results/<date>-<name>/` (the lane's `OUT`). It holds `runs.tsv` and `metrics.tsv`. Don't commit the raw logs; say where they are.
2. Mark flakes in `runs.tsv`'s `notes`, then regenerate:
   `scripts/metrics.py --runs results/<…>/runs.tsv --logs logs --offline --all > results/<…>/metrics.tsv`
3. Score each workload, with Cachix's size from app.cachix.org after its last salt:
   `scripts/score.py results/<…>/metrics.tsv --footprint cachix=<bytes>`
4. Write `RESULTS.md`, in short lists:
   - what ran: repos, the fixture commit, dates;
   - one table per workload: cold, warm, S1, outputs' class, cache size, verdict;
   - the scorer's table and choices, and whether the winner changes at h = 0.2 or 0.5;
   - runner notes (fast runners, `nproc`/`cpu`), flakes and reruns;
   - what's still missing.
5. Update the headline in `README.md` if the verdict changed.
6. Commit with explicit paths.
