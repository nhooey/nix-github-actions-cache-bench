"""metrics.py: log parsing, step timing, runs.tsv formats and the TSV row."""

import pathlib
import sys
import tempfile
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import metrics  # noqa: E402

SHA = "0123456789abcdef0123456789abcdef01234567"


def line(job, step, second, text):
    return f"{job}\t{step}\t2026-10-13T10:00:{second:02d}.5000000Z {text}\n"


class ParseTs(unittest.TestCase):
    def test_timestamp_with_fraction_and_bom(self):
        t, text = metrics.parse_ts("﻿2026-10-13T10:00:01.25Z hello")
        self.assertAlmostEqual(t, 1791885601.25)
        self.assertEqual(text, "hello")

    def test_no_timestamp(self):
        self.assertEqual(metrics.parse_ts("plain text"), (None, "plain text"))


class ParseLog(unittest.TestCase):
    LOG = [
        line("bench", "Set up job", 0, "Starting"),
        line("bench", "Set up job", 2, "git log -1 --format=%H"),
        line("bench", "Set up job", 3, SHA),
        line("bench", "Record job start", 4, "CIMETRIC nproc 4"),
        line("bench", "Record job start", 4, "CIMETRIC cpu AMD EPYC 7763"),
        line("bench", "Hestia", 5, "HTTP error 418, throttled"),
        line("bench", "Hestia", 6, "429 Too Many Requests"),
        line("bench", "Build workload", 10, "CIMETRIC build ripgrep 12345"),
        line("bench", "Build workload", 11, "ok 1 nix build .#babashka-test in 400ms"),
        line(
            "bench",
            "Build workload",
            12,
            "not ok 2 nix build .#babashka-with-features-test in 500ms",
        ),
        line(
            "bench",
            "Cache diagnostics",
            20,
            "CIMETRIC\tpath\tripgrep\t/nix/store/x\tsubstituted\t42",
        ),
        line(
            "bench", "Cache diagnostics", 20, "CIMETRIC\tpath\tbb\tbuilt\t/nix/store/y"
        ),
        line(
            "bench", "Cache diagnostics", 20, "CIMETRIC\tclosure_class\tbuilt\t7\t1000"
        ),
        line("bench", "Cache diagnostics", 21, "CIMETRIC store_bytes 6164972326 "),
        line("bench", "Cache diagnostics", 22, "CIMETRIC cache_mode unstable"),
        line("bench", "Post Hestia", 25, "Pushing is disabled."),
        "no tabs here\n",
    ]

    def test_metrics(self):
        _, m = metrics.parse_log(self.LOG)
        self.assertEqual(m["sha"], SHA)
        self.assertEqual((m["nproc"], m["cpu"]), ("4", "AMD EPYC 7763"))
        self.assertEqual(m["throttles"], 2)
        self.assertEqual(
            m["out_ms"],
            ["ripgrep=12345", "babashka-test=400", "babashka-with-features-test=!500"],
        )
        self.assertEqual(m["out_class"], ["ripgrep:substituted", "bb:built"])
        self.assertEqual(m["closure"], {"built": "7"})
        self.assertEqual(m["store_bytes"], "6164972326")
        self.assertEqual(m["cache_mode"], "unstable")
        self.assertTrue(m["no_push"])

    def test_steps_keep_first_and_last_timestamps(self):
        jobs, _ = metrics.parse_log(self.LOG)
        steps = jobs["bench"]
        self.assertEqual(list(steps)[0], "Set up job")
        first, last = steps["Set up job"]
        self.assertAlmostEqual(last - first, 3)

    def test_sha_only_after_the_git_log_line(self):
        _, m = metrics.parse_log([line("bench", "s", 0, SHA)])
        self.assertEqual(m["sha"], "")


class JobsFromLog(unittest.TestCase):
    def test_a_step_lasts_until_the_next_starts(self):
        jobs, _ = metrics.parse_log(
            [
                line("a", "one", 0, "x"),
                line("a", "one", 3, "x"),
                line("a", "two", 10, "x"),
                line("a", "three", 15, "x"),
                line("a", "three", 19, "x"),
                line("b", "only", 30, "x"),
            ]
        )
        out = metrics.jobs_from_log(jobs)
        start, end, durs = out["a"]
        self.assertAlmostEqual(end - start, 19)
        self.assertEqual(durs, {"one": 10, "two": 5, "three": 4})
        self.assertEqual(out["b"][2], {"only": 0})


class ReadRuns(unittest.TestCase):
    def write(self, text):
        d = tempfile.TemporaryDirectory()
        self.addCleanup(d.cleanup)
        p = pathlib.Path(d.name) / "runs.tsv"
        p.write_text(text)
        return p

    def test_missing_file_is_empty(self):
        self.assertEqual(metrics.read_runs(None), {})
        self.assertEqual(metrics.read_runs("/nonexistent/runs.tsv"), {})

    def test_header_format_pads_short_rows(self):
        p = self.write(
            "lane\tbackend\tworkload\ttrial\trun_id\tconclusion\tnotes\n"
            "hestia\thestia\tripgrep\tC1\t11\tsuccess\n"
            "\n"
        )
        runs = metrics.read_runs(p)
        self.assertEqual(list(runs), ["11"])
        self.assertEqual(runs["11"]["backend"], "hestia")
        self.assertEqual(runs["11"]["notes"], "")

    def test_round_1_format_maps_lanes_to_backends(self):
        p = self.write(
            "P-base\texp/ci-base\tB0a\t21\tsuccess\n"
            "C-hestia\texp/ci-hestia\tW1\t22\tfailure\tflake\n"
        )
        runs = metrics.read_runs(p)
        self.assertEqual(runs["21"]["backend"], "none")
        self.assertEqual(runs["21"]["workload"], "clj-nix-ci")
        self.assertEqual(runs["21"]["ref"], "exp/ci-base")
        self.assertEqual(runs["22"]["backend"], "hestia")
        self.assertEqual((runs["22"]["trial"], runs["22"]["notes"]), ("W1", "flake"))


class Row(unittest.TestCase):
    """An offline row, from runs.tsv and a log on disk."""

    def test_offline_row(self):
        with tempfile.TemporaryDirectory() as d:
            (pathlib.Path(d) / "7.log").write_text(
                "".join(
                    [
                        line("bench", "Set up job", 0, "start"),
                        line("bench", "Run Mic92/hestia@v3.1.0", 10, "x"),
                        line("bench", "Build workload", 15, "CIMETRIC build a 1000"),
                        line(
                            "bench",
                            "Post Run Mic92/hestia@v3.1.0",
                            45,
                            "Pushing is disabled.",
                        ),
                        line(
                            "bench",
                            "Cache diagnostics",
                            50,
                            "CIMETRIC\tpath\ta\t/nix/store/a\tbuilt\t1",
                        ),
                        line("bench", "Cache diagnostics", 58, "done"),
                    ]
                )
            )
            runs = {
                "7": {
                    "backend": "hestia",
                    "workload": "w",
                    "trial": "C1",
                    "conclusion": "success",
                    "sha": "abc",
                    "gha_cache_bytes": "123",
                    "notes": "rerun",
                }
            }
            r = dict(zip(metrics.COLS, metrics.row("7", runs, d, "", True)))
        self.assertEqual(r["wall_s"], "58")
        self.assertEqual(r["diag_s"], "8")
        self.assertEqual(r["job_s"], "50")
        self.assertEqual(r["sum_job_s"], "58")
        self.assertEqual(r["cache_setup_s"], "5")
        self.assertEqual(r["cache_post_s"], "5")
        self.assertEqual(r["build_s"], "30")
        self.assertEqual((r["out_ms"], r["out_class"]), ("a=1000", "a:built"))
        self.assertEqual((r["sha"], r["gha_cache_bytes"]), ("abc", "123"))
        self.assertEqual(r["notes"], "rerun; no-push")

    def test_missing_log_dies(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(SystemExit):
                metrics.row("8", {}, d, "", True)


if __name__ == "__main__":
    unittest.main()
