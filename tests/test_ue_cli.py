"""ue.py's helpers and argument parsing (N5)."""
import contextlib
import io
import json
import os
import unittest

import support  # noqa: F401  (sys.path)
from support import TempDir, env
import ue


class UndoMsysPathsTest(unittest.TestCase):
    """Git Bash turns /Game/X into <Git install>/Game/X; ue.py puts it back."""

    def setUp(self):
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)
        self.git = self.tmp.path / "Git"
        (self.git / "usr" / "bin").mkdir(parents=True)
        self.root = str(self.git).replace("\\", "/")

    def git_bash(self, **extra):
        values = dict(MSYSTEM="MINGW64", EXEPATH=str(self.git), MSYS_NO_PATHCONV=None)
        values.update(extra)
        return env(**values)

    def test_rewritten_paths_come_back(self):
        with self.git_bash():
            self.assertEqual(ue._undo_msys_paths([f"{self.root}/Game/Maps/Main", "info"]), ["/Game/Maps/Main", "info"])

    def test_option_values(self):
        with self.git_bash():
            self.assertEqual(ue._undo_msys_paths([f"--to={self.root}/Game/Props", "--to", f"{self.root}/Game/X"]),
                             ["--to=/Game/Props", "--to", "/Game/X"])

    def test_case_insensitive_prefix(self):
        with self.git_bash():
            self.assertEqual(ue._undo_msys_paths([f"{self.root.upper()}/Game/X"]), ["/Game/X"])

    def test_existing_paths_under_git_stay(self):
        real = self.git / "usr" / "bin"
        with self.git_bash():
            arg = str(real).replace("\\", "/")
            self.assertEqual(ue._undo_msys_paths([arg]), [arg])

    def test_exepath_may_be_the_bin_folder(self):
        with self.git_bash(EXEPATH=str(self.git / "bin")):
            self.assertEqual(ue._msys_root(), self.root)

    def test_untouched_outside_git_bash(self):
        argv = [f"{self.root}/Game/X"]
        with env(MSYSTEM=None, EXEPATH=str(self.git), MSYS_NO_PATHCONV=None):
            self.assertIsNone(ue._msys_root())
            self.assertEqual(ue._undo_msys_paths(argv), argv)
        with self.git_bash(MSYS_NO_PATHCONV="1"):
            self.assertEqual(ue._undo_msys_paths(argv), argv)

    def test_no_install_folder_found(self):
        with self.git_bash(EXEPATH=str(self.tmp.path / "nowhere")):
            self.assertIsNone(ue._msys_root())


class CollapseRepeatsTest(unittest.TestCase):
    def test_counts_repeats_ignoring_timestamps(self):
        lines = ["[2026.11.24-14.49.05:592][  1]LogTemp: Warning: spam",
                 "[2026.11.24-14.49.05:600][  2]LogTemp: Warning: spam",
                 "LogTemp: other",
                 "[2026.11.24-14.49.06:000][  3]LogTemp: Warning: spam"]
        self.assertEqual(ue._collapse_repeats(lines),
                         ["[2026.11.24-14.49.05:592][  1]LogTemp: Warning: spam  (x3)", "LogTemp: other"])

    def test_single_lines_unchanged(self):
        self.assertEqual(ue._collapse_repeats(["a", "b"]), ["a", "b"])
        self.assertEqual(ue._collapse_repeats([]), [])


class DumpsTest(unittest.TestCase):
    def test_number_arrays_stay_on_one_line(self):
        text = ue._dumps({"location": [1.0, 2.5, -3], "names": ["a", "b"]})
        self.assertIn('"location": [1.0, 2.5, -3]', text)
        self.assertIn('"names": ["a", "b"]', text)
        self.assertEqual(json.loads(text), {"location": [1.0, 2.5, -3], "names": ["a", "b"]})

    def test_nested_structures_stay_indented(self):
        value = {"rows": [{"a": 1}, {"b": [1, 2]}]}
        text = ue._dumps(value)
        self.assertEqual(json.loads(text), value)
        self.assertIn('"b": [1, 2]', text)
        self.assertIn("\n", text)

    def test_long_arrays_are_left_alone(self):
        value = list(range(200))
        self.assertEqual(json.loads(ue._dumps(value)), value)
        self.assertIn("\n", ue._dumps(value))

    def test_non_ascii(self):
        self.assertIn("Ü", ue._dumps({"name": "Über"}))


class ParseParamsTest(unittest.TestCase):
    def test_json_values_and_strings(self):
        self.assertEqual(ue._parse_params(["scale=2", "color=[1,0.4,0.1]", "loop=true", "name=Fire", "path=/Game/X",
                                           "empty=", "eq=a=b"]),
                         {"scale": 2, "color": [1, 0.4, 0.1], "loop": True, "name": "Fire", "path": "/Game/X",
                          "empty": "", "eq": "a=b"})

    def test_none(self):
        self.assertEqual(ue._parse_params(None), {})
        self.assertEqual(ue._parse_params([]), {})


class ParserTest(unittest.TestCase):
    @staticmethod
    def commands(parser):
        sub = next(a for a in parser._actions if a.__class__.__name__ == "_SubParsersAction")
        return sub.choices

    def test_every_command_help_parses(self):
        parser = ue.build_parser()
        commands = self.commands(parser)
        self.assertGreater(len(commands), 30)
        for name in commands:
            with self.subTest(command=name):
                out = io.StringIO()
                with contextlib.redirect_stdout(out), self.assertRaises(SystemExit) as stop:
                    ue.build_parser().parse_args([name, "--help"])
                self.assertEqual(stop.exception.code, 0)
                self.assertIn(f"usage: ue.py {name}", out.getvalue())

    def test_top_level_help(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out), self.assertRaises(SystemExit):
            ue.build_parser().parse_args(["--help"])
        self.assertIn("Build & code", out.getvalue())

    def test_every_command_has_a_function(self):
        for name, sub in self.commands(ue.build_parser()).items():
            with self.subTest(command=name):
                self.assertTrue(callable(sub.get_default("fn")))


class MainTest(unittest.TestCase):
    def test_no_project_is_a_clean_error(self):
        with TempDir() as tmp, support.chdir(tmp), env(UETK_PROJECT=None):
            err = io.StringIO()
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(err):
                code = ue.main(["info"])
        self.assertEqual(code, 2)
        self.assertIn("error: No .uproject", err.getvalue())


if __name__ == "__main__":
    unittest.main()
