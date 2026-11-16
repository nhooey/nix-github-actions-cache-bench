#!/usr/bin/env python3
"""The bench's command-line tool: render, publish, run, purge, salt, measure
and score the backend repos.

Usage: bench <command> [options]   (`bench <command> --help` for each)

Commands:
  render    render one repo tree per backend into build/
  publish   commit the rendered trees, and with --push push them
  lane      run one repo's trials in order, polling each run
  salt      commit a new salt in a rendered repo, so the next run misses
  purge     empty a repo's GitHub cache, keeping the installer entry
  metrics   one TSV row per run, from runs.tsv and the logs
  score     the gates, the expected time E and the choices
  tests     run the scripts' unit tests

Exit status: 0 on success, 1 on failure, 2 on bad usage.

render, metrics and score are the Python modules beside this file; lane,
publish, salt and purge run the shell scripts beside it with the options
given here. Python standard library only.
"""

import argparse
import os
import pathlib
import subprocess
import sys
from typing import NamedTuple

SCRIPTS = pathlib.Path(__file__).resolve().parent
ROOT = SCRIPTS.parent

# Commands handled by a Python module's own main(argv, prog), so their
# --help is the module's.
MODULE_COMMANDS = {
    "render": "render one repo tree per backend into build/",
    "metrics": "one TSV row per run, from runs.tsv and the logs",
    "score": "the gates, the expected time E and the choices",
}


class Invocation(NamedTuple):
    """A command to run: its argv, the environment entries it adds, and its
    working directory (None: the caller's, so relative paths given on the
    command line resolve as typed)."""

    argv: list
    env: dict
    cwd: object = None


def _formatter(prog):
    return argparse.RawDescriptionHelpFormatter(prog, max_help_position=30)


def build_parser():
    """The parser for every command but the module commands, which only
    appear here for the command list in `bench --help`."""
    ap = argparse.ArgumentParser(
        prog="bench",
        description=__doc__.split("\n\n")[0],
        epilog="Example: bench render && bench publish",
        formatter_class=_formatter,
    )
    sub = ap.add_subparsers(dest="command", metavar="<command>", required=True)

    def command(name, summary, description, example):
        return sub.add_parser(
            name,
            help=summary,
            description=description,
            epilog=f"Example: {example}",
            formatter_class=_formatter,
        )

    for name, summary in MODULE_COMMANDS.items():
        sub.add_parser(name, help=summary, add_help=False)

    p = command(
        "publish",
        "commit the rendered trees, and with --push push them",
        "Commit each rendered backend repo (from `bench render`) on its main\n"
        "branch and, with --push, push it to the repo named in its .bench-repo.\n"
        "It never creates repos: a human creates each one first, public and empty.",
        "bench publish --push build/nix-github-actions-cache-bench-hestia",
    )
    p.add_argument("--push", action="store_true", help="also push main to GitHub")
    p.add_argument(
        "dirs",
        nargs="*",
        metavar="DIR",
        help="rendered repo trees (default: every tree in build/ with a .bench-repo)",
    )

    p = command(
        "lane",
        "run one repo's trials in order, polling each run",
        "Run bench trials one after another in one backend repo, polling each\n"
        "run until it completes; one lane per repo. Appends to OUT/runs.tsv and\n"
        "OUT/metrics.tsv and saves each log as LOGS/<run-id>.log.\n\n"
        "Specs:\n"
        "  <workload>:<trial>[:<ref>][@<run-id>]  dispatch a run on <ref> (default\n"
        "                                         main), or wait on an existing run\n"
        "  purge[:<only-regex>]                   empty the repo's cache (bench purge)\n"
        "  salt:<label>[:<branch>]                commit and push a salt (bench salt)",
        "bench lane --dir build/nix-github-actions-cache-bench-hestia "
        "--out results/2026-11-16-fixtures G-hestia purge babashka:C1 babashka:W1",
    )
    p.add_argument(
        "--dir",
        required=True,
        help="the rendered backend repo (bench render, then bench publish)",
    )
    p.add_argument(
        "--out", help="the round's results directory (default: results/current)"
    )
    p.add_argument("--logs", help="where run logs go (default: logs)")
    p.add_argument(
        "--dry-run", action="store_true", help="print the plan and touch nothing"
    )
    p.add_argument("lane", help="the lane's name, written into runs.tsv")
    p.add_argument("specs", nargs="+", metavar="SPEC", help="the steps, in order")

    p = command(
        "salt",
        "commit a new salt in a rendered repo, so the next run misses",
        "Commit a new salt in a rendered backend repo, already a git clone\n"
        "(bench publish). The salt reaches every workload's own code but not\n"
        "its fixed-output fetches.",
        "bench salt build/nix-github-actions-cache-bench-hestia S1 --push",
    )
    p.add_argument("dir", metavar="DIR", help="the rendered repo tree")
    p.add_argument("label", help="the trial, e.g. S1 or S2, written into the salt")
    p.add_argument(
        "--branch",
        help="commit on this branch, created from HEAD if missing (default: current)",
    )
    p.add_argument(
        "--push", action="store_true", help="push the branch to the repo in .bench-repo"
    )

    p = command(
        "purge",
        "empty a repo's GitHub cache, keeping the installer entry",
        "Delete a repo's GitHub Actions cache entries by id, 8 at a time, until\n"
        "only the kept ones are left. Use it instead of `gh cache delete --all`,\n"
        "which takes about 1 s per entry.",
        "bench purge nhooey/nix-github-actions-cache-bench-hestia --only '^(pack-|m3#)'",
    )
    p.add_argument("repo", metavar="OWNER/NAME", help="the GitHub repo")
    p.add_argument("--only", metavar="REGEX", help="delete only keys matching REGEX")
    p.add_argument(
        "--keep",
        metavar="REGEX",
        help="never delete keys matching REGEX (default: '^determinatesystem-nix-installer')",
    )
    p.add_argument(
        "--dry-run", action="store_true", help="count what would go, delete nothing"
    )

    p = command(
        "tests",
        "run the scripts' unit tests",
        "Run the unit tests in tests/ with the standard library's unittest, as\n"
        "`nix flake check` does. They need no network, gh or nix.",
        "bench tests --verbose",
    )
    p.add_argument("--verbose", action="store_true", help="list each test")
    return ap


def rendered_dirs(build):
    """Every tree under `build` that `bench render` wrote, sorted."""
    if not build.is_dir():
        return []
    return sorted(str(d) for d in build.iterdir() if (d / ".bench-repo").is_file())


def plan(a, default_dirs=()):
    """The Invocation for parsed arguments `a` of a shell-backed command.
    `default_dirs` are publish's trees when none are given."""
    if a.command == "publish":
        dirs = a.dirs or list(default_dirs)
        if not dirs:
            raise ValueError("no rendered trees in build/ (run bench render first)")
        argv = [str(SCRIPTS / "publish.sh"), *(["--push"] if a.push else []), *dirs]
        return Invocation(argv, {})
    if a.command == "lane":
        env = {"DIR": a.dir}
        if a.out:
            env["OUT"] = a.out
        if a.logs:
            env["LOGS"] = a.logs
        if a.dry_run:
            env["DRY_RUN"] = "1"
        return Invocation([str(SCRIPTS / "lane.sh"), a.lane, *a.specs], env)
    if a.command == "salt":
        argv = [str(SCRIPTS / "salt.sh"), a.dir, a.label]
        if a.branch:
            argv += ["--branch", a.branch]
        if a.push:
            argv.append("--push")
        return Invocation(argv, {})
    if a.command == "purge":
        argv = [str(SCRIPTS / "purge.sh"), a.repo]
        if a.only:
            argv += ["--only", a.only]
        if a.keep:
            argv += ["--keep", a.keep]
        if a.dry_run:
            argv.append("--dry-run")
        return Invocation(argv, {})
    if a.command == "tests":
        argv = [sys.executable, "-m", "unittest", "discover", "-s", "tests"]
        if a.verbose:
            argv.append("-v")
        return Invocation(argv, {"PYTHONDONTWRITEBYTECODE": "1"}, ROOT)
    raise ValueError(f"no plan for command {a.command!r}")


def run_module(name, argv):
    """Run scripts/<name>.py's main with `argv`; it exits on error."""
    module = __import__(name)
    module.main(argv, prog=f"bench {name}")
    return 0


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if argv and argv[0] in MODULE_COMMANDS:
        sys.path.insert(0, str(SCRIPTS))
        return run_module(argv[0], argv[1:])
    ap = build_parser()
    a = ap.parse_args(argv)
    if a.command in MODULE_COMMANDS:
        # Reached only with options before the command, e.g. `bench -x score`
        ap.error(f"put {a.command}'s options after it")
    defaults = rendered_dirs(ROOT / "build") if a.command == "publish" else ()
    try:
        inv = plan(a, defaults)
    except ValueError as e:
        print(f"bench: {e}", file=sys.stderr)
        return 1
    return subprocess.run(
        inv.argv, env={**os.environ, **inv.env}, cwd=inv.cwd
    ).returncode


if __name__ == "__main__":
    sys.exit(main())
