#!/usr/bin/env bash
# Delete a repo's GitHub Actions cache entries, keeping the Nix installer's.
# Usage: purge.sh <owner/name> [--only REGEX] [--keep REGEX] [--dry-run]
#   --only REGEX   delete only keys matching REGEX (e.g. '^(pack-|m3#)' for Hestia)
#   --keep REGEX   never delete keys matching REGEX
#                  (default: '^determinatesystem-nix-installer', about 47 MiB,
#                  which every backend's run would otherwise re-download)
#   --dry-run      list what would go, delete nothing
# `gh cache delete --all` takes about 1 s per entry, far too slow for MNC's
# thousands of entries, so this deletes by id, 8 at a time, and repeats until
# only kept entries are left (deletes can lag behind the listing).
set -uo pipefail
die() { echo "purge: $*" >&2; exit 1; }

R=${1:-}
[ -n "$R" ] || die "usage: purge.sh <owner/name> [--only REGEX] [--keep REGEX] [--dry-run]"
shift
only='.'
keep='^determinatesystem-nix-installer'
dry=0
while [ $# -gt 0 ]; do
  case $1 in
    --only) only=$2; shift 2 ;;
    --keep) keep=$2; shift 2 ;;
    --dry-run) dry=1; shift ;;
    *) die "unknown argument $1" ;;
  esac
done
command -v gh > /dev/null || die "gh not found"

# Ids of entries to delete: match --only, not --keep
targets() {
  gh cache list -R "$R" --limit 10000 --json id,key \
    --jq "[.[] | select((.key | test(\"$only\")) and (.key | test(\"$keep\") | not)) | .id] | .[]"
}

for pass in 1 2 3; do
  ids=$(targets) || die "can't list caches of $R"
  n=$(printf '%s' "$ids" | grep -c . || true)
  [ "$n" -eq 0 ] && break
  if [ "$dry" -eq 1 ]; then
    echo "purge: would delete $n entries from $R"
    exit 0
  fi
  echo "purge: pass $pass, deleting $n entries from $R"
  # A failed delete (already gone, or a lagging listing) is retried next pass
  printf '%s\n' "$ids" | xargs -P 8 -I{} gh cache delete {} -R "$R" > /dev/null 2>&1
done

left=$(targets | grep -c . || true)
usage=$(gh api "repos/$R/actions/cache/usage" --jq '"\(.active_caches_count) entries, \(.active_caches_size_in_bytes) bytes"')
echo "purge: $R now has $usage ($left purgeable left)"
[ "$left" -eq 0 ] || die "$left entries survived 3 passes; rerun or check the token's actions:write scope"
