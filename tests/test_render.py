"""render.py: snippet sections and splicing them into the template."""

import pathlib
import sys
import unittest

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import render  # noqa: E402

TEMPLATE = """name: @BACKEND@
on:
  options: [@WORKLOAD_OPTIONS@]
  default: @DEFAULT_WORKLOAD@
jobs:
  bench:
    permissions:
      contents: read
#@PERMISSIONS@
    env:
#@ENV@
    steps:
      - first


#@CACHE_STEPS@

      - build

#@POST_STEPS@
"""


def snip(**sections):
    s = {k: [] for k in render.SECTIONS}
    s.update({k.replace("_", "-"): v for k, v in sections.items()})
    return s


class ParseBackend(unittest.TestCase):
    def test_sections_and_doc(self):
        parts = render.parse_backend(
            "# about\n#@section permissions\n      actions: read\n\n\n#@section steps\n      - uses: x\n",
            "x.yml",
        )
        self.assertEqual(parts["doc"], ["# about"])
        self.assertEqual(parts["permissions"], ["      actions: read"])
        self.assertEqual(parts["steps"], ["      - uses: x"])
        self.assertEqual((parts["env"], parts["post-steps"]), ([], []))

    def test_unknown_section_dies(self):
        with self.assertRaises(SystemExit) as e:
            render.parse_backend("#@section setup\n", "x.yml")
        self.assertIn("x.yml: unknown section 'setup'", str(e.exception))


class Splice(unittest.TestCase):
    def test_fills_markers_and_options(self):
        out = render.splice(
            TEMPLATE,
            snip(
                permissions=["      actions: read"],
                env=["      A: 1"],
                steps=["      - cache"],
            ),
            "hestia",
            ["babashka", "ripgrep"],
        )
        self.assertIn("name: hestia\n", out)
        self.assertIn("  options: [babashka, ripgrep]\n  default: babashka\n", out)
        self.assertIn(
            "      contents: read\n      actions: read\n    env:\n      A: 1\n", out
        )
        self.assertIn("      - first\n\n      - cache\n\n      - build\n", out)

    def test_empty_env_and_blank_runs_collapse(self):
        out = render.splice(TEMPLATE, snip(), "none", ["ripgrep"])
        self.assertNotIn("env:", out)
        self.assertNotIn("\n\n\n", out)
        self.assertIn("    permissions:\n      contents: read\n    steps:\n", out)

    def test_unknown_marker_dies(self):
        with self.assertRaises(SystemExit):
            render.splice("#@SETUP@\n", snip(), "none", ["w"])


class RealBackends(unittest.TestCase):
    """Every snippet in workflows/backends renders against the real template."""

    WL = ["babashka", "knob-slow", "ripgrep"]

    def backends(self):
        names = sorted(p.stem for p in render.BACKENDS.glob("*.yml"))
        self.assertIn("none", names)
        return names

    def test_each_backend_renders_with_its_steps(self):
        for b in self.backends():
            with self.subTest(backend=b):
                out = render.render(b, self.WL)
                for marker in (
                    "#@",
                    "@BACKEND@",
                    "@WORKLOAD_OPTIONS@",
                    "@DEFAULT_WORKLOAD@",
                ):
                    self.assertNotIn(marker, out)
                self.assertIn(f"BENCH_BACKEND: {b}\n", out)
                self.assertIn("options: [babashka, knob-slow, ripgrep]", out)
                self.assertIn("default: babashka\n", out)
                self.assertNotIn("\n\n\n", out)
                path = render.BACKENDS / f"{b}.yml"
                for line in render.parse_backend(path.read_text(), path)["steps"]:
                    self.assertIn(line + "\n", out)

    def test_one_workload_per_repo(self):
        out = render.render("hestia", self.WL, "ripgrep")
        self.assertIn("options: [ripgrep]\n", out)
        self.assertIn("default: ripgrep\n", out)


if __name__ == "__main__":
    unittest.main()
