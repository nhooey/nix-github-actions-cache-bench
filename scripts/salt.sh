#!/usr/bin/env bash
# Commit a new salt in a rendered backend repo, so the next run misses.
# Usage: salt.sh <repo-dir> <label> [--branch B] [--push]
#   <repo-dir>  a tree from render.py, already a git clone (publish.sh)
#   <label>     the trial, e.g. S1 or S2; written into the salt with a timestamp
#   --branch B  commit on branch B (created from the current HEAD if missing);
#               default: the current branch
#   --push      push the branch to the repo named in <repo-dir>/.bench-repo
# The salt reaches every workload: clj-builder (so all of clj-nix's builds,
# as a src/ commit would), the ripgrep package (not its deps) and each knob
# derivation. Fixed-output fetches (jars, crates) don't change.
set -uo pipefail
die() { echo "salt: $*" >&2; exit 1; }

dir=${1:-}; label=${2:-}
[ -n "$dir" ] && [ -n "$label" ] || die "usage: salt.sh <repo-dir> <label> [--branch B] [--push]"
shift 2
branch=""; push=0
while [ $# -gt 0 ]; do
  case $1 in
    --branch) branch=$2; shift 2 ;;
    --push) push=1; shift ;;
    *) die "unknown argument $1" ;;
  esac
done
[ -d "$dir/.git" ] || die "$dir is not a git clone (run publish.sh first)"
[ -f "$dir/salt" ] || die "$dir has no salt file (not a rendered bench repo?)"

if [ -n "$branch" ]; then
  git -C "$dir" checkout -q "$branch" 2>/dev/null || git -C "$dir" checkout -q -b "$branch" \
    || die "can't check out $branch"
fi
printf '%s-%s\n' "$label" "$(date -u +%Y%m%dT%H%M%SZ)" > "$dir/salt"
git -C "$dir" add salt
git -C "$dir" commit -q -m "Salt $label" || die "commit failed"
echo "salt: $(git -C "$dir" rev-parse --short HEAD) on $(git -C "$dir" branch --show-current): $(cat "$dir/salt")"

if [ "$push" -eq 1 ]; then
  repo=$(cat "$dir/.bench-repo" 2>/dev/null) || die "no .bench-repo in $dir"
  b=$(git -C "$dir" branch --show-current)
  git -C "$dir" push -q "git@github.com:$repo.git" "$b:refs/heads/$b" || die "push to $repo failed"
  echo "salt: pushed $b to $repo"
fi
