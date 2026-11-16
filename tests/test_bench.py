"""bench.py: argument parsing, the plan for each shell-backed command, and
dispatch to the Python modules."""

import pathlib
import subprocess
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import bench  # noqa: E402

SCRIPTS = ROOT / "scripts"


def plan(*argv, default_dirs=()):
    return bench.plan(bench.build_parser().parse_args(list(argv)), default_dirs)


def bench_cli(*argv):
    return subprocess.run(
        [sys.executable, str(SCRIPTS / "bench.py"), *argv],
        capture_output=True,
        text=True,
        env={"PYTHONDONTWRITEBYTECODE": "1"},
    )


class Help(unittest.TestCase):
    def test_top_level_lists_every_command(self):
        out = bench_cli("--help")
        self.assertEqual(out.returncode, 0)
        for name in (
            "render",
            "publish",
            "lane",
            "salt",
            "purge",
            "metrics",
            "score",
            "tests",
        ):
            self.assertIn(f"    {name} ", out.stdout)

    def test_module_commands_show_their_own_options_as_bench(self):
        for name, option in (
            ("render", "--repo-pattern"),
            ("metrics", "--offline"),
            ("score", "--footprint"),
        ):
            out = bench_cli(name, "--help")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn(f"usage: bench {name}", out.stdout)
            self.assertIn(option, out.stdout)

    def test_shell_commands_list_their_options(self):
        for name, options in (
            ("publish", ["--push"]),
            ("lane", ["--dir", "--out", "--logs", "--dry-run"]),
            ("salt", ["--branch", "--push"]),
            ("purge", ["--only", "--keep", "--dry-run"]),
            ("tests", ["--verbose"]),
        ):
            out = bench_cli(name, "--help")
            self.assertEqual(out.returncode, 0, out.stderr)
            self.assertIn(f"usage: bench {name}", out.stdout)
            for o in options:
                self.assertIn(o, out.stdout)

    def test_bad_usage_exits_2(self):
        self.assertEqual(bench_cli().returncode, 2)
        self.assertEqual(bench_cli("nope").returncode, 2)
        self.assertEqual(bench_cli("lane", "G", "babashka:C1").returncode, 2)


class Plan(unittest.TestCase):
    def test_publish_named_dirs(self):
        inv = plan("publish", "--push", "build/a", "build/b")
        self.assertEqual(
            inv.argv, [str(SCRIPTS / "publish.sh"), "--push", "build/a", "build/b"]
        )
        self.assertEqual(inv.env, {})
        self.assertIsNone(inv.cwd)

    def test_publish_defaults_to_rendered_trees(self):
        inv = plan("publish", default_dirs=["build/x"])
        self.assertEqual(inv.argv, [str(SCRIPTS / "publish.sh"), "build/x"])

    def test_publish_without_trees_fails(self):
        with self.assertRaises(ValueError):
            plan("publish")

    def test_lane_passes_options_as_its_environment(self):
        inv = plan(
            "lane",
            "--dir",
            "build/h",
            "--out",
            "results/r",
            "--logs",
            "l",
            "--dry-run",
            "G-hestia",
            "purge",
            "babashka:C1",
        )
        self.assertEqual(
            inv.argv, [str(SCRIPTS / "lane.sh"), "G-hestia", "purge", "babashka:C1"]
        )
        self.assertEqual(
            inv.env,
            {"DIR": "build/h", "OUT": "results/r", "LOGS": "l", "DRY_RUN": "1"},
        )

    def test_lane_leaves_unset_options_to_the_script(self):
        inv = plan("lane", "--dir", "build/h", "G", "babashka:W1")
        self.assertEqual(inv.env, {"DIR": "build/h"})

    def test_salt(self):
        self.assertEqual(
            plan("salt", "build/h", "S1").argv,
            [str(SCRIPTS / "salt.sh"), "build/h", "S1"],
        )
        self.assertEqual(
            plan("salt", "build/h", "S1", "--branch", "s1", "--push").argv,
            [str(SCRIPTS / "salt.sh"), "build/h", "S1", "--branch", "s1", "--push"],
        )

    def test_purge(self):
        self.assertEqual(plan("purge", "o/r").argv, [str(SCRIPTS / "purge.sh"), "o/r"])
        self.assertEqual(
            plan("purge", "o/r", "--only", "^p", "--keep", "^k", "--dry-run").argv,
            [
                str(SCRIPTS / "purge.sh"),
                "o/r",
                "--only",
                "^p",
                "--keep",
                "^k",
                "--dry-run",
            ],
        )

    def test_tests_run_from_the_root(self):
        inv = plan("tests", "--verbose")
        self.assertEqual(
            inv.argv[1:], ["-m", "unittest", "discover", "-s", "tests", "-v"]
        )
        self.assertEqual(inv.cwd, bench.ROOT)


class RenderedDirs(unittest.TestCase):
    def test_only_trees_with_a_bench_repo_sorted(self):
        with tempfile.TemporaryDirectory() as tmp:
            build = pathlib.Path(tmp)
            for name in ("b", "a"):
                (build / name).mkdir()
                (build / name / ".bench-repo").write_text("o/r\n")
            (build / "stray").mkdir()
            self.assertEqual(
                bench.rendered_dirs(build), [str(build / "a"), str(build / "b")]
            )

    def test_missing_build_dir(self):
        self.assertEqual(bench.rendered_dirs(pathlib.Path("/nonexistent/build")), [])


if __name__ == "__main__":
    unittest.main()
