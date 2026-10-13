#!/usr/bin/env bash
# Run bench trials one after another in one backend repo, polling each run
# until it completes. One lane per repo: lanes in different repos can run in
# parallel, since each repo has its own cache quota and upload limit.
# Usage: DIR=<rendered-repo-dir> [OUT=results/<round>] [LOGS=logs] lane.sh <lane> <spec> ...
#   <workload>:<trial>[:<ref>][@<run-id>]  dispatch a run on <ref> (default main),
#                                         or wait on an existing run
#   purge[:<only-regex>]                  delete the repo's cache entries (purge.sh),
#                                         keeping the installer entry
#   salt:<label>[:<branch>]               commit a new salt and push it (salt.sh)
# Example (one backend's sequence for one workload):
#   DIR=build/nix-github-actions-cache-bench-hestia lane.sh G-hestia purge \
#     babashka:C1 babashka:W1 babashka:W2 salt:S1 babashka:S1 babashka:W3 purge babashka:C2
# Appends "lane backend workload ref trial run_id conclusion sha gha_cache_bytes notes"
# to $OUT/runs.tsv, saves the log as $LOGS/<run-id>.log and appends the run's
# metrics row to $OUT/metrics.tsv.
# DRY_RUN=1 prints what it would do and touches nothing.
set -uo pipefail
die() { echo "$lane: $*" >&2; exit 1; }

D=$(cd "$(dirname "$0")" && pwd)
lane=${1:-}
if [ -z "$lane" ]; then echo "usage: DIR=<repo-dir> lane.sh <lane> <spec> ..." >&2; exit 1; fi
shift
DIR=${DIR:?set DIR to a rendered backend repo (render.py, then publish.sh)}
R=$(cat "$DIR/.bench-repo" 2>/dev/null) || die "no .bench-repo in $DIR"
backend=$(sed -n 's/^      BENCH_BACKEND: //p' "$DIR/.github/workflows/bench.yml")
[ -n "$backend" ] || die "can't read BENCH_BACKEND from $DIR/.github/workflows/bench.yml"
OUT=${OUT:-$D/../results/current}
LOGS=${LOGS:-$D/../logs}
DRY_RUN=${DRY_RUN:-0}
mkdir -p "$OUT" "$LOGS"
RUNS=$OUT/runs.tsv
[ -s "$RUNS" ] || printf 'lane\tbackend\tworkload\tref\ttrial\trun_id\tconclusion\tsha\tgha_cache_bytes\tnotes\n' > "$RUNS"

# `gh run watch` can exit before the run ends, so poll the status instead.
# Gives up after 3 h (the job's own timeout is 90 min, plus queueing).
wait_done() {
  local id=$1 st="" n=0
  while [ "$st" != completed ]; do
    sleep 60
    n=$((n + 1)); [ "$n" -gt 180 ] && die "run $id not completed after 3 h"
    st=$(gh run view "$id" -R "$R" --json status --jq .status 2>/dev/null || echo "")
  done
}

run() { if [ "$DRY_RUN" = 1 ]; then echo "$lane would run: $*"; else "$@"; fi; }

for spec in "$@"; do
  case $spec in
    purge|purge:*)
      only=${spec#purge}; only=${only#:}
      run "$D/purge.sh" "$R" ${only:+--only "$only"} || die "purge failed"
      continue ;;
    salt:*)
      IFS=: read -r _ label branch <<< "$spec"
      run "$D/salt.sh" "$DIR" "$label" ${branch:+--branch "$branch"} --push || die "salt failed"
      continue ;;
  esac
  id=${spec#*@}; [ "$id" = "$spec" ] && id=""
  IFS=: read -r workload trial ref <<< "${spec%@*}"
  ref=${ref:-main}
  [ -n "$workload" ] && [ -n "$trial" ] || die "bad spec $spec"
  if [ "$DRY_RUN" = 1 ]; then
    echo "$lane would dispatch bench.yml on $R@$ref: workload=$workload trial=$trial"
    continue
  fi
  if [ -z "$id" ]; then
    before=$(date -u +%Y-%m-%dT%H:%M:%SZ)
    # workflow_dispatch needs bench.yml on the default branch; --ref picks
    # the branch whose copy runs
    gh workflow run bench.yml -R "$R" --ref "$ref" -f workload="$workload" -f trial="$trial" \
      || die "dispatch failed for $spec"
    for _ in $(seq 30); do
      sleep 10
      id=$(gh run list -R "$R" --workflow bench.yml --branch "$ref" --event workflow_dispatch --limit 5 \
        --json databaseId,createdAt --jq "[.[] | select(.createdAt >= \"$before\")] | last | .databaseId // empty")
      [ -n "$id" ] && break
    done
    [ -n "$id" ] || die "no run id for $spec"
  fi
  echo "$lane started $workload $trial $id https://github.com/$R/actions/runs/$id"
  wait_done "$id"
  read -r concl sha < <(gh run view "$id" -R "$R" --json conclusion,headSha --jq '"\(.conclusion) \(.headSha)"')
  # Cache usage right after the run: the post steps have saved by now
  bytes=$(gh api "repos/$R/actions/cache/usage" --jq .active_caches_size_in_bytes 2>/dev/null || echo "")
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$lane" "$backend" "$workload" "$ref" "$trial" "$id" "$concl" "$sha" "$bytes" "" >> "$RUNS"
  gh run view "$id" -R "$R" --log > "$LOGS/$id.log" 2>/dev/null || echo "$lane warning: no log for $id"
  hdr=--no-header; [ -s "$OUT/metrics.tsv" ] || hdr=""
  REPO=$R "$D/metrics.py" --runs "$RUNS" --logs "$LOGS" $hdr "$id" >> "$OUT/metrics.tsv" \
    || echo "$lane warning: metrics failed for $id"
  echo "$lane done $workload $trial $id $concl"
done
