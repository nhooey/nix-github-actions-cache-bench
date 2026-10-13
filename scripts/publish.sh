#!/usr/bin/env bash
# Commit and push rendered backend repos (from render.py) to GitHub.
# Usage: publish.sh [--push] <repo-dir> ...
#   without --push: commit locally and print what would be pushed
#   --push:         also push main to the repo named in <repo-dir>/.bench-repo
# STUB BOUNDARY: this never creates repos. A human creates each one first
# (public, empty), because the agent's classifier blocks `gh repo create`:
#   gh repo create <owner/name> --public
# The repo must be public: Actions minutes are then free on standard runners.
set -uo pipefail
die() { echo "publish: $*" >&2; exit 1; }

push=0
[ "${1:-}" = "--push" ] && { push=1; shift; }
[ $# -gt 0 ] || die "usage: publish.sh [--push] <repo-dir> ..."

for dir in "$@"; do
  repo=$(cat "$dir/.bench-repo" 2>/dev/null) || die "$dir has no .bench-repo (render it first)"
  if [ ! -d "$dir/.git" ]; then
    git -C "$dir" init -q -b main || die "git init failed in $dir"
    # .bench-repo is the controller's note of where this tree goes
    echo .bench-repo >> "$dir/.git/info/exclude"
  fi
  # Explicit paths only
  git -C "$dir" add flake.nix flake.lock knobs.nix salt .github/workflows/bench.yml
  if git -C "$dir" diff --cached --quiet; then
    echo "publish: $repo: nothing new to commit"
  else
    git -C "$dir" commit -q -m "Render bench workflow and fixtures" || die "commit failed in $dir"
    echo "publish: $repo: committed $(git -C "$dir" rev-parse --short HEAD)"
  fi
  if [ "$push" -eq 0 ]; then
    echo "publish: $repo: not pushed (pass --push)"
    continue
  fi
  gh repo view "$repo" --json name > /dev/null 2>&1 \
    || die "$repo doesn't exist or isn't visible; a human must create it (gh repo create $repo --public)"
  git -C "$dir" push -q "git@github.com:$repo.git" main:refs/heads/main || die "push to $repo failed"
  echo "publish: $repo: pushed main"
done
