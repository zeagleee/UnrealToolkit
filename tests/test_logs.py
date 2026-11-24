"""uetk/logs.py: reading verbosity and category off Unreal log lines (N4)."""
import unittest

import support  # noqa: F401  (sys.path)
from support import TempDir
from uetk import logs

STAMP = "[2026.11.24-14.49.05:592][  0]"


class VerbosityTest(unittest.TestCase):
    def test_timestamped_lines(self):
        cases = {
            f"{STAMP}LogWindows: Error: Assertion failed": "Error",
            f"{STAMP}LogTemp: Warning: something odd": "Warning",
            f"{STAMP}LogOutputDevice: Fatal: crashed": "Fatal",
            f"{STAMP}LogInit: Display: Engine is initialized": "Display",
            f"{STAMP}LogPython: Log: hello": "Log",
            f"[2026.11.24-14.49.05:592][123]LogNet: Verbose: tick": "Verbose",
        }
        for line, expected in cases.items():
            with self.subTest(line=line):
                self.assertEqual(logs.verbosity(line), expected)

    def test_lines_without_a_timestamp(self):
        self.assertEqual(logs.verbosity("LogTemp: Error: no stamp"), "Error")
        self.assertEqual(logs.verbosity("LogTemp: Warning: no stamp"), "Warning")
        self.assertEqual(logs.verbosity("LogTemp: Display: no stamp"), "Display")

    def test_plain_log_line_has_no_verbosity(self):
        self.assertIsNone(logs.verbosity(f"{STAMP}LogTemp: just a message"))
        self.assertIsNone(logs.verbosity("LogTemp: just a message"))
        self.assertIsNone(logs.verbosity(""))
        self.assertIsNone(logs.verbosity("not a log line at all"))

    def test_verbosity_word_is_only_read_in_its_place(self):
        self.assertIsNone(logs.verbosity(f"{STAMP}LogTemp: the Error: here is part of the message"))
        self.assertEqual(logs.verbosity(f"{STAMP}LogInit: Display: 0 errors, 0 warnings"), "Display")

    def test_compiler_problems(self):
        self.assertEqual(logs.verbosity(r"D:\Proj\Source\A.cpp(12): error C2065: 'x': undeclared identifier"), "Error")
        self.assertEqual(logs.verbosity(r"D:\Proj\Source\A.cpp(30): warning C4996: deprecated"), "Warning")
        self.assertEqual(logs.verbosity("LINK : fatal error LNK1104: cannot open file"), "Error")


class IsProblemTest(unittest.TestCase):
    def test_problems(self):
        for verb in ("Fatal", "Error", "Warning"):
            with self.subTest(verb=verb):
                self.assertTrue(logs.is_problem(f"{STAMP}LogTemp: {verb}: x"))

    def test_not_problems(self):
        for line in (f"{STAMP}LogTemp: Display: x", f"{STAMP}LogTemp: Log: x", f"{STAMP}LogTemp: x", "",
                     "LogTemp: Verbose: x"):
            with self.subTest(line=line):
                self.assertFalse(logs.is_problem(line))


class CategoryTest(unittest.TestCase):
    def test_category(self):
        self.assertEqual(logs.category(f"{STAMP}LogWindows: Error: x"), "LogWindows")
        self.assertEqual(logs.category(f"{STAMP}LogPython: hello"), "LogPython")
        self.assertEqual(logs.category("LogTemp: Warning: no stamp"), "LogTemp")

    def test_no_category(self):
        self.assertIsNone(logs.category("free text"))
        self.assertIsNone(logs.category(""))
        self.assertIsNone(logs.category(r"D:\A.cpp(1): error C1: x"))


class ReadFromTest(unittest.TestCase):
    def test_increments_and_rotation(self):
        with TempDir() as tmp:
            log = tmp / "Editor.log"
            log.write_bytes(b"first\n")
            offset = logs.size_of(log)
            with open(log, "ab") as f:
                f.write(b"second\n")
            self.assertEqual(logs.read_from(log, offset), "second\n")
            log.write_bytes(b"new\n")  # truncated: read from the start
            self.assertEqual(logs.read_from(log, offset), "new\n")
            self.assertEqual(logs.size_of(tmp / "missing.log"), 0)
            self.assertEqual(logs.read_from(None, 0), "")


if __name__ == "__main__":
    unittest.main()
