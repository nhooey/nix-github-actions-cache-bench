#!/usr/bin/env python3
"""Render one repo tree per backend: the fixture flake plus a bench workflow.

Usage: scripts/render.py [--out build] [--repo-pattern P] [backend ...]

- Each backend's cache steps come from workflows/backends/<backend>.yml,
  spliced into workflows/template.yml at the #@SECTION@ markers.
- The repo pattern names each rendered tree (and later its GitHub repo).
  It takes {backend}, and optionally {workload}: with {workload}, each
  backend x workload pair gets its own repo, and its own cache quota.
- Writes <out>/<repo-name>/ with flake.nix, flake.lock, knobs.nix, salt
  and .github/workflows/bench.yml. scripts/publish.sh pushes them.
- An existing salt file in <out>/<repo-name>/ is kept (see salt.sh).
Python standard library only; no YAML parser, so the snippets are
spliced as text and must already carry the template's indentation.
"""
import argparse
import json
import pathlib
import shutil
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "workflows" / "template.yml"
BACKENDS = ROOT / "workflows" / "backends"
FIXTURES = ROOT / "fixtures"
FIXTURE_FILES = ["flake.nix", "flake.lock", "knobs.nix", "salt"]
SECTIONS = ["permissions", "env", "steps", "post-steps"]
DEFAULT_PATTERN = "nhooey/nix-github-actions-cache-bench-{backend}"


def die(msg):
    sys.exit(f"render: {msg}")


def parse_backend(path):
    """Split a snippet file into its #@section blocks (leading comments go to 'doc')."""
    parts = {"doc": []}
    cur = "doc"
    for line in path.read_text().splitlines():
        if line.startswith("#@section "):
            cur = line.split(None, 1)[1].strip()
            if cur not in SECTIONS:
                die(f"{path}: unknown section {cur!r} (expected one of {SECTIONS})")
            parts[cur] = []
            continue
        parts[cur].append(line)
    for s in SECTIONS:
        parts.setdefault(s, [])
        # Drop trailing blank lines so splices stay tidy
        while parts[s] and not parts[s][-1].strip():
            parts[s].pop()
    return parts


def workloads():
    """Workload names from the fixture flake (nix eval), in a stable order."""
    try:
        out = subprocess.run(
            ["nix", "eval", "--json", f"{FIXTURES}#workloads", "--apply", "builtins.attrNames"],
            check=True, capture_output=True, text=True).stdout
    except (OSError, subprocess.CalledProcessError) as e:
        die(f"can't list workloads with nix eval: {getattr(e, 'stderr', e)}")
    names = json.loads(out)
    # babashka first: it's the default and the round-1 workload
    return sorted(names, key=lambda n: (n != "babashka", n))


def render(backend, wl, only_workload=None):
    snip = parse_backend(BACKENDS / f"{backend}.yml")
    options = [only_workload] if only_workload else wl
    text = TEMPLATE.read_text()
    text = text.replace("@BACKEND@", backend)
    text = text.replace("@WORKLOAD_OPTIONS@", ", ".join(options))
    text = text.replace("@DEFAULT_WORKLOAD@", options[0])
    lines = []
    for line in text.splitlines():
        marker = line.strip()
        if marker.startswith("#@") and marker.endswith("@"):
            name = marker[2:-1].lower().replace("_", "-")
            name = {"cache-steps": "steps"}.get(name, name)
            if name not in SECTIONS:
                die(f"template marker {marker} has no section")
            lines.extend(snip[name])
            continue
        lines.append(line)
    out = "\n".join(lines) + "\n"
    # An empty permissions/env block would leave `env:` with no keys
    out = out.replace("    env:\n    steps:", "    steps:")
    # Collapse the blank lines an empty splice leaves behind
    while "\n\n\n" in out:
        out = out.replace("\n\n\n", "\n\n")
    if "#@" in out or "@BACKEND@" in out:
        die(f"{backend}: unreplaced marker in the rendered workflow")
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default=str(ROOT / "build"), help="output directory (default: build/)")
    ap.add_argument("--repo-pattern", default=DEFAULT_PATTERN,
                    help=f"repo name pattern, with {{backend}} and optional {{workload}} (default: {DEFAULT_PATTERN})")
    ap.add_argument("backends", nargs="*", help="backends to render (default: all in workflows/backends)")
    a = ap.parse_args()

    known = sorted(p.stem for p in BACKENDS.glob("*.yml"))
    chosen = a.backends or known
    for b in chosen:
        if b not in known:
            die(f"unknown backend {b!r}; known: {', '.join(known)}")
    for f in FIXTURE_FILES:
        if not (FIXTURES / f).exists():
            die(f"fixtures/{f} is missing (run `nix flake lock ./fixtures` for flake.lock)")

    wl = workloads()
    per_workload = "{workload}" in a.repo_pattern
    outdir = pathlib.Path(a.out)
    for b in chosen:
        for w in (wl if per_workload else [None]):
            repo = a.repo_pattern.format(backend=b, workload=w or "")
            dest = outdir / repo.split("/")[-1]
            wf = dest / ".github" / "workflows"
            wf.mkdir(parents=True, exist_ok=True)
            for f in FIXTURE_FILES:
                # Keep an existing salt: scripts/salt.sh commits it in the
                # rendered repo, and resetting it would change every hash
                if f == "salt" and (dest / f).exists():
                    continue
                shutil.copy2(FIXTURES / f, dest / f)
            (wf / "bench.yml").write_text(render(b, wl, w))
            (dest / ".bench-repo").write_text(repo + "\n")
            print(f"{repo}\t{dest}")


if __name__ == "__main__":
    main()
