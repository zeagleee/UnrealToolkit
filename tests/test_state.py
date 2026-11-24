"""uetk/state.py: folder snapshots and ini editing (N7)."""
import unittest

import support  # noqa: F401  (sys.path)
from support import StubProject, TempDir
from uetk import state
from uetk.project import ToolkitError


class SnapshotTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)
        self.project = StubProject(self.tmp.path / "Proj")
        self.saves = self.project.root / "Saved" / "SaveGames"
        self.saves.mkdir(parents=True)
        (self.saves / "slot0.sav").write_bytes(b"slot zero")
        (self.saves / "sub").mkdir()
        (self.saves / "sub" / "deep.sav").write_bytes(b"deep")
        self.config = self.project.root / "Config" / "DefaultGame.ini"
        self.config.parent.mkdir()
        self.config.write_text("[A]\nx=1\n", encoding="utf-8")

    def test_save_writes_a_manifest(self):
        manifest = state.snapshot_save(self.project, ["Saved/SaveGames", "Config/DefaultGame.ini", "Saved/Missing"])
        self.assertTrue(manifest["pending"])
        self.assertEqual([(e["path"], e["kind"], e["files"]) for e in manifest["entries"]],
                         [("Saved/SaveGames", "dir", 2), ("Config/DefaultGame.ini", "file", 1),
                          ("Saved/Missing", "missing", 0)])
        self.assertEqual(state.read_manifest(self.project), manifest)

    def test_diff(self):
        state.snapshot_save(self.project, ["Saved/SaveGames", "Config/DefaultGame.ini"])
        self.assertEqual(state.snapshot_diff(self.project), {
            "Saved/SaveGames": {"changed": [], "added": [], "removed": []},
            "Config/DefaultGame.ini": {"changed": [], "added": [], "removed": []}})
        (self.saves / "slot0.sav").write_bytes(b"overwritten")
        (self.saves / "slot1.sav").write_bytes(b"new")
        (self.saves / "sub" / "deep.sav").unlink()
        self.config.write_text("[A]\nx=2\n", encoding="utf-8")
        self.assertEqual(state.snapshot_diff(self.project), {
            "Saved/SaveGames": {"changed": ["slot0.sav"], "added": ["slot1.sav"], "removed": ["sub/deep.sav"]},
            "Config/DefaultGame.ini": {"changed": ["Config/DefaultGame.ini"], "added": [], "removed": []}})

    def test_restore_mirrors_back_exactly(self):
        state.snapshot_save(self.project, ["Saved/SaveGames", "Config/DefaultGame.ini", "Saved/Missing"])
        (self.saves / "slot0.sav").write_bytes(b"overwritten")
        (self.saves / "new" / "nested").mkdir(parents=True)
        (self.saves / "new" / "nested" / "x.sav").write_bytes(b"x")
        (self.saves / "sub" / "deep.sav").unlink()
        self.config.write_text("changed", encoding="utf-8")
        missing = self.project.root / "Saved" / "Missing"
        missing.mkdir()
        (missing / "made.txt").write_text("made later", encoding="utf-8")

        report = state.snapshot_restore(self.project)
        self.assertEqual(report["Saved/SaveGames"]["changed"], ["slot0.sav"])
        self.assertEqual((self.saves / "slot0.sav").read_bytes(), b"slot zero")
        self.assertEqual((self.saves / "sub" / "deep.sav").read_bytes(), b"deep")
        self.assertFalse((self.saves / "new").exists(), "folders created meanwhile are removed")
        self.assertEqual(self.config.read_text(encoding="utf-8"), "[A]\nx=1\n")
        self.assertFalse(missing.exists(), "a path that didn't exist at save time is removed")
        manifest = state.read_manifest(self.project)
        self.assertFalse(manifest["pending"])
        self.assertIn("restored", manifest)
        self.assertEqual(state.snapshot_diff(self.project)["Saved/SaveGames"],
                         {"changed": [], "added": [], "removed": []})

    def test_named_snapshots_are_separate(self):
        state.snapshot_save(self.project, ["Config/DefaultGame.ini"], name="cfg")
        self.assertIsNone(state.read_manifest(self.project))
        self.assertEqual(state.read_manifest(self.project, "cfg")["entries"][0]["path"], "Config/DefaultGame.ini")

    def test_refusals(self):
        with self.assertRaisesRegex(ToolkitError, "at least one"):
            state.snapshot_save(self.project, [])
        with self.assertRaisesRegex(ToolkitError, "not inside the project"):
            state.snapshot_save(self.project, ["../elsewhere"])
        with self.assertRaisesRegex(ToolkitError, "whole project"):
            state.snapshot_save(self.project, ["."])
        with self.assertRaisesRegex(ToolkitError, "No snapshot"):
            state.snapshot_diff(self.project, "nope")
        with self.assertRaisesRegex(ToolkitError, "No snapshot"):
            state.snapshot_restore(self.project, "nope")

    def test_restore_pending_protect(self):
        self.assertFalse(state.restore_pending_protect(self.project, say=lambda _: None))
        state.snapshot_save(self.project, ["Saved/SaveGames"])
        (self.saves / "slot0.sav").write_bytes(b"overwritten")
        said = []
        self.assertTrue(state.restore_pending_protect(self.project, say=said.append))
        self.assertEqual(said, ["Restored Saved/SaveGames: 1 changed (changed: slot0.sav)"])
        self.assertFalse(state.restore_pending_protect(self.project, say=said.append), "only once")

    def test_describe(self):
        self.assertEqual(state.describe({"A": {"changed": [], "added": [], "removed": []}}), ["A: unchanged"])
        many = [f"f{i}" for i in range(8)]
        line = state.describe({"B": {"changed": many, "added": ["n"], "removed": []}})[0]
        self.assertEqual(line, "B: 8 changed, 1 added (changed: f0, f1, f2, f3, f4, f5 ...; added: n)")


class IniTest(unittest.TestCase):
    SECTION = "/Script/UnrealEd.EditorPerformanceSettings"
    KEY = "bThrottleCPUWhenNotForeground"

    def setUp(self):
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)
        self.ini = self.tmp.path / "EditorSettings.ini"

    def write(self, text, encoding="utf-8", newline="\n", bom=b""):
        self.ini.write_bytes(bom + text.replace("\n", newline).encode(encoding))

    def test_get(self):
        self.write(f"[Other]\n{self.KEY}=Nope\n\n[{self.SECTION}]\nA=1\n{self.KEY} = True\n")
        self.assertEqual(state.ini_get(self.ini, self.SECTION, self.KEY), "True")
        self.assertEqual(state.ini_get(self.ini, "Other", self.KEY), "Nope")
        self.assertIsNone(state.ini_get(self.ini, self.SECTION, "Missing"))
        self.assertIsNone(state.ini_get(self.ini, "NoSection", self.KEY))
        self.assertIsNone(state.ini_get(self.tmp.path / "absent.ini", self.SECTION, self.KEY))

    def test_set_existing_key(self):
        self.write(f"[{self.SECTION}]\n{self.KEY}=True\nOther=1\n")
        self.assertFalse(state.ini_set(self.ini, self.SECTION, self.KEY, "False"))
        self.assertEqual(self.ini.read_text(encoding="utf-8"), f"[{self.SECTION}]\n{self.KEY}=False\nOther=1\n")

    def test_set_new_key_goes_before_trailing_blank_lines(self):
        self.write(f"[{self.SECTION}]\nOther=1\n\n[Next]\nB=2\n")
        state.ini_set(self.ini, self.SECTION, self.KEY, "False")
        self.assertEqual(self.ini.read_text(encoding="utf-8"),
                         f"[{self.SECTION}]\nOther=1\n{self.KEY}=False\n\n[Next]\nB=2\n")

    def test_set_and_remove_new_section_round_trip(self):
        original = "[First]\nA=1\n"
        self.write(original)
        added_separator = state.ini_set(self.ini, self.SECTION, self.KEY, "False")
        self.assertTrue(added_separator)
        self.assertEqual(self.ini.read_text(encoding="utf-8"), f"[First]\nA=1\n\n[{self.SECTION}]\n{self.KEY}=False\n")
        state.ini_remove(self.ini, self.SECTION, self.KEY, drop_empty_section=True, drop_separator=added_separator)
        self.assertEqual(self.ini.read_text(encoding="utf-8"), original)

    def test_remove_keeps_a_section_with_other_keys(self):
        self.write(f"[{self.SECTION}]\n{self.KEY}=False\nOther=1\n")
        state.ini_remove(self.ini, self.SECTION, self.KEY, drop_empty_section=True)
        self.assertEqual(self.ini.read_text(encoding="utf-8"), f"[{self.SECTION}]\nOther=1\n")

    def test_remove_missing_key_leaves_the_file(self):
        self.write("[A]\nx=1\r\n")
        before = self.ini.read_bytes()
        state.ini_remove(self.ini, self.SECTION, self.KEY)
        self.assertEqual(self.ini.read_bytes(), before)

    def test_set_creates_the_file(self):
        path = self.tmp.path / "new" / "Editor.ini"
        state.ini_set(path, self.SECTION, self.KEY, "False")
        self.assertEqual(path.read_text(encoding="utf-8"), f"[{self.SECTION}]\n{self.KEY}=False\n")

    def test_utf16_with_crlf_is_kept(self):
        self.write(f"[{self.SECTION}]\r\nOther=ä\r\n", encoding="utf-16-le", bom=b"\xff\xfe")
        state.ini_set(self.ini, self.SECTION, self.KEY, "False")
        raw = self.ini.read_bytes()
        self.assertTrue(raw.startswith(b"\xff\xfe"), raw[:4])
        text = raw[2:].decode("utf-16-le")
        self.assertEqual(text, f"[{self.SECTION}]\r\nOther=ä\r\n{self.KEY}=False\r\n")
        self.assertEqual(state.ini_get(self.ini, self.SECTION, "Other"), "ä")

    def test_utf8_bom_and_crlf_are_kept(self):
        self.write(f"[{self.SECTION}]\nOther=1\n", newline="\r\n", bom=b"\xef\xbb\xbf")
        state.ini_set(self.ini, self.SECTION, self.KEY, "False")
        state.ini_remove(self.ini, self.SECTION, "Other")
        raw = self.ini.read_bytes()
        self.assertEqual(raw, b"\xef\xbb\xbf" + f"[{self.SECTION}]\r\n{self.KEY}=False\r\n".encode("utf-8"))

    def test_plain_utf8_lf_is_kept(self):
        self.write("[A]\nx=1\n")
        state.ini_set(self.ini, "A", "y", "2")
        self.assertEqual(self.ini.read_bytes(), b"[A]\nx=1\ny=2\n")


if __name__ == "__main__":
    unittest.main()
