"""score.py: trial kinds, the four gates, E(h) and the choices."""

import pathlib
import subprocess
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import score  # noqa: E402


def run(trial, job_s="", **kw):
    """A metrics.tsv row with the columns score.py reads."""
    r = {
        "backend": "x",
        "workload": "w",
        "trial": trial,
        "run_id": f"id-{trial}",
        "job_s": str(job_s),
        "wall_s": "",
        "sum_job_s": "",
        "out_ms": "",
        "out_class": "",
        "conclusion": "success",
        "notes": "",
        "gha_cache_bytes": "",
    }
    r.update(kw)
    return r


class Kind(unittest.TestCase):
    def test_labels(self):
        cases = {
            "U": "unstable",
            "u": "unstable",
            "B0": "base",
            "B0a": "base",
            "C": "cold",
            "C1": "cold",
            "c2": "cold",
            "S2": "cold",
            "W": "warm",
            "W1": "warm",
            "W3": "warm",
            "S1": "miss",
            "S": "miss",
            "S0a": "split",
            "Cx": "other",
            "W1b": "other",
            "": "other",
        }
        for trial, want in cases.items():
            with self.subTest(trial=trial):
                self.assertEqual(score.kind(trial), want)


class OutMs(unittest.TestCase):
    def test_parses_times_and_failures(self):
        r = run("W1", out_ms="a=100,b=!2500,,c=")
        self.assertEqual(
            score.out_ms(r),
            [("a", 100.0, False), ("b", 2500.0, True), ("c", None, False)],
        )

    def test_empty(self):
        self.assertEqual(score.out_ms(run("W1")), [])


class Baseline(unittest.TestCase):
    def test_medians_leave_out_unstable(self):
        rows = [
            run("B0a", 100, out_ms="a=10,b=20"),
            run("B0b", 200, out_ms="a=30,b=30"),
            run("B0c", 300, out_ms="a=50,b=50"),
            run("U", 9999, out_ms="a=99999"),
        ]
        self.assertEqual(score.baseline(rows), (200, 60))

    def test_no_timings(self):
        self.assertEqual(score.baseline([run("B0", 100)]), (100, None))


class GateG1(unittest.TestCase):
    def test_successes_pass(self):
        self.assertEqual(score.gate_g1([run("C1"), run("W1", conclusion="")]), "PASS")

    def test_unexplained_failure_fails(self):
        self.assertEqual(
            score.gate_g1([run("C1"), run("W1", conclusion="failure")]), "FAIL"
        )

    def test_failure_marked_flake_or_not_cache_passes(self):
        for note in ("flake", "runner flake; rerun", "not-cache"):
            with self.subTest(note=note):
                rs = [run("W1", conclusion="failure", notes=note)]
                self.assertEqual(score.gate_g1(rs), "PASS")


class GateG2(unittest.TestCase):
    def w(self, trial="W1", out_ms="a=1000", out_class="a:substituted"):
        return run(trial, out_ms=out_ms, out_class=out_class)

    def test_no_warm_run_is_unknown(self):
        self.assertEqual(score.gate_g2([]), "?")

    def test_substituted_or_restored_and_fast_passes(self):
        rs = [self.w(out_ms="a=59999,b=10", out_class="a:substituted,b:restored")]
        self.assertEqual(score.gate_g2(rs), "PASS")

    def test_a_built_output_fails(self):
        rs = [self.w(out_class="a:substituted,b:built")]
        self.assertEqual(score.gate_g2(rs), "FAIL")

    def test_an_attribute_at_the_limit_fails(self):
        self.assertEqual(score.gate_g2([self.w(out_ms="a=60000")]), "FAIL")

    def test_a_failed_attribute_fails(self):
        self.assertEqual(score.gate_g2([self.w(out_ms="a=!10")]), "FAIL")

    def test_no_timings_or_no_classes_fails(self):
        self.assertEqual(score.gate_g2([self.w(out_ms="")]), "FAIL")
        self.assertEqual(score.gate_g2([self.w(out_class="")]), "FAIL")

    def test_judges_w1_not_the_first_warm_run(self):
        rs = [self.w("W2", out_class="a:built"), self.w("W1")]
        self.assertEqual(score.gate_g2(rs), "PASS")
        rs = [self.w("W2"), self.w("w1", out_class="a:built")]
        self.assertEqual(score.gate_g2(rs), "FAIL")

    def test_without_w1_judges_the_first_warm_run(self):
        rs = [self.w("W", out_class="a:built"), self.w("W2")]
        self.assertEqual(score.gate_g2(rs), "FAIL")


class GateG3(unittest.TestCase):
    def test_unknown_without_a_cold_run(self):
        self.assertEqual(score.gate_g3(None, 100, 100), "?")

    def test_unknown_without_a_miss_when_cold_is_cheap(self):
        self.assertEqual(score.gate_g3(100, None, 100), "?")

    def test_three_minutes_over_passes(self):
        self.assertEqual(score.gate_g3(1180, 1180, 1000), "PASS")

    def test_more_than_three_minutes_over_fails(self):
        self.assertEqual(score.gate_g3(1181, 1000, 1000), "FAIL")
        self.assertEqual(score.gate_g3(1000, 1181, 1000), "FAIL")

    def test_an_expensive_cold_run_fails_even_without_a_miss(self):
        self.assertEqual(score.gate_g3(1181, None, 1000), "FAIL")


class GateG4(unittest.TestCase):
    def test_largest_github_cache_size_against_5_gb(self):
        rs = [run("C1", gha_cache_bytes="1e9"), run("W1", gha_cache_bytes="5e9")]
        self.assertEqual(score.gate_g4("hestia", rs, {}), "PASS")
        rs.append(run("W2", gha_cache_bytes="5000000001"))
        self.assertEqual(score.gate_g4("hestia", rs, {}), "FAIL")

    def test_unknown_without_a_size(self):
        self.assertEqual(score.gate_g4("cna", [run("C1")], {}), "?")

    def test_footprint_overrides_measured_size(self):
        rs = [run("C1", gha_cache_bytes="9e9")]
        self.assertEqual(score.gate_g4("cna", rs, {"cna": 3.5e9}), "PASS")

    def test_cachix_ignores_github_cache_size(self):
        rs = [run("C1", gha_cache_bytes="1e9")]
        self.assertEqual(score.gate_g4("cachix", rs, {}), "?")
        self.assertEqual(score.gate_g4("cachix", rs, {"cachix": 2.5e9}), "PASS")
        self.assertEqual(score.gate_g4("cachix", rs, {"cachix": 2.6e9}), "FAIL")


def eligible_rows(cold, warm, miss=None):
    rs = [
        run("C1", cold, gha_cache_bytes="1e9"),
        run("W1", warm, out_ms="a=1000", out_class="a:substituted"),
    ]
    if miss is not None:
        rs.append(run("S1", miss))
    return rs


class ScoreBackend(unittest.TestCase):
    def test_expected_time_weights_warm_and_miss(self):
        res = score.score_backend(
            "hestia", eligible_rows(1000, 400, 1100), 1000, {}, False
        )
        self.assertEqual(res["gates"], ["PASS"] * 4)
        self.assertEqual((res["verdict"], res["eligible"]), ("eligible", True))
        self.assertEqual(res["mark"], "")
        self.assertAlmostEqual(res["E"][0.2], 0.2 * 400 + 0.8 * 1100)
        self.assertAlmostEqual(res["E"][0.32], 0.32 * 400 + 0.68 * 1100)
        self.assertAlmostEqual(res["E"][0.5], 750)

    def test_cold_stands_in_for_a_missing_miss(self):
        res = score.score_backend("hestia", eligible_rows(1000, 400), 1000, {}, False)
        self.assertEqual(res["mark"], "~")
        self.assertAlmostEqual(res["E"][0.5], 700)
        self.assertEqual((res["verdict"], res["eligible"]), ("pending", False))

    def test_provisional_ranks_pending(self):
        res = score.score_backend("hestia", eligible_rows(1000, 400), 1000, {}, True)
        self.assertEqual((res["verdict"], res["eligible"]), ("pending", True))

    def test_a_failed_gate_is_ineligible_even_provisionally(self):
        rs = eligible_rows(1000, 400, 1100)
        rs[1]["out_class"] = "a:built"
        res = score.score_backend("hestia", rs, 1000, {}, True)
        self.assertEqual(res["gates"][1], "FAIL")
        self.assertEqual((res["verdict"], res["eligible"]), ("ineligible", False))

    def test_no_warm_run_has_no_expected_time(self):
        res = score.score_backend("hestia", [run("C1", 1000)], 1000, {}, True)
        self.assertEqual(res["E"], {h: None for h in score.H})


def result(e32, eligible=True, e20=None, e50=None):
    return {
        "E": {
            0.2: e32 if e20 is None else e20,
            0.32: e32,
            0.5: e32 if e50 is None else e50,
        },
        "eligible": eligible,
    }


class PickDefault(unittest.TestCase):
    def test_lowest_expected_time_wins(self):
        rs = {"cna": result(900), "mnc": result(800), "hestia": result(600)}
        self.assertEqual(score.pick_default(rs, 0.32), ("hestia", ["hestia"]))

    def test_ties_under_a_minute_go_to_the_simplest_setup(self):
        rs = {"hestia": result(600), "mnc": result(659), "cna": result(660)}
        self.assertEqual(score.pick_default(rs, 0.32), ("mnc", ["mnc", "hestia"]))
        rs["cna"] = result(620)
        self.assertEqual(
            score.pick_default(rs, 0.32), ("cna", ["cna", "mnc", "hestia"])
        )

    def test_ineligible_and_non_github_backends_are_skipped(self):
        rs = {
            "cachix": result(100),
            "split-hestia": result(100),
            "hestia": result(100, eligible=False),
            "cna": result(900),
        }
        self.assertEqual(score.pick_default(rs, 0.32), ("cna", ["cna"]))

    def test_none_eligible(self):
        rs = {
            "hestia": result(100, eligible=False),
            "mnc": {"E": {0.32: None}, "eligible": True},
        }
        self.assertIsNone(score.pick_default(rs, 0.32))

    def test_picks_by_the_given_hit_rate(self):
        rs = {"cna": result(600, e20=900), "hestia": result(800, e20=700)}
        self.assertEqual(score.pick_default(rs, 0.32)[0], "cna")
        self.assertEqual(score.pick_default(rs, 0.2)[0], "hestia")


class CachixGain(unittest.TestCase):
    def test_gain_over_the_default(self):
        rs = {"hestia": result(800), "cachix": result(700)}
        self.assertEqual(score.cachix_gain(rs, "hestia"), 100)

    def test_none_unless_cachix_is_eligible_with_a_score(self):
        self.assertIsNone(score.cachix_gain({"hestia": result(800)}, "hestia"))
        rs = {"hestia": result(800), "cachix": result(100, eligible=False)}
        self.assertIsNone(score.cachix_gain(rs, "hestia"))


class SplitCheck(unittest.TestCase):
    def rows(self, wall, jobs):
        return [run("S0a", wall_s=str(wall), sum_job_s=str(jobs))]

    def test_at_the_limits_passes(self):
        self.assertEqual(
            score.split_check(self.rows(600, 1300), 1000), (0.6, 1.3, True)
        )

    def test_over_either_limit_fails(self):
        self.assertFalse(score.split_check(self.rows(601, 1000), 1000)[2])
        self.assertFalse(score.split_check(self.rows(500, 1301), 1000)[2])


class FastRunnerNotes(unittest.TestCase):
    def test_flags_cold_and_warm_runs_under_70_percent(self):
        rs = [
            run("C1", out_ms="a=69", out_class="a:built"),
            run("W2", out_ms="a=70", out_class="a:built"),
            run("S1", out_ms="a=10", out_class="a:built"),
            run("W1", out_ms="a=10", out_class="a:substituted"),
        ]
        notes = score.fast_runner_notes("cna", rs, 100)
        self.assertEqual(len(notes), 1)
        self.assertTrue(notes[0].startswith("cna C1 (id-C1): built in 69% "))

    def test_nothing_without_a_baseline_build_time(self):
        rs = [run("C1", out_ms="a=1", out_class="a:built")]
        self.assertEqual(score.fast_runner_notes("cna", rs, None), [])


class Results(unittest.TestCase):
    """The README's headline: the scorer's default on round 1's rows."""

    METRICS = ROOT / "results" / "2026-10-13-clj-nix" / "metrics.tsv"
    FOOTPRINT = ["hestia=0.65e9", "cna=3.5e9", "mnc=2.4e9", "cachix=1.07e9"]

    def score(self, *args):
        p = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "score.py"),
                str(self.METRICS),
                *args,
            ],
            capture_output=True,
            text=True,
            check=True,
        )
        return p.stdout

    def test_default_is_hestia_and_cachix_not_adopted(self):
        flags = [x for f in self.FOOTPRINT for x in ("--footprint", f)]
        out = self.score(*flags)
        self.assertIn("  default (no secret): hestia\n", out)
        self.assertIn("don't adopt Cachix", out)
        self.assertRegex(out, r"│ mnc +│.*│ ineligible +│")
        self.assertIn("passes; also needs split+winner", out)

    def test_without_footprints_nothing_is_eligible(self):
        self.assertIn("default: none eligible (pass --provisional", self.score())

    def test_bad_footprint_is_refused(self):
        p = subprocess.run(
            [
                sys.executable,
                str(ROOT / "scripts" / "score.py"),
                str(self.METRICS),
                "--footprint",
                "cna",
            ],
            capture_output=True,
            text=True,
        )
        self.assertNotEqual(p.returncode, 0)
        self.assertIn("bad --footprint", p.stderr)


if __name__ == "__main__":
    unittest.main()
