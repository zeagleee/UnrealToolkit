"""uetk/plugins.py: which plugins to install, copying them into a project, and status (N1, N8)."""
import shutil
import unittest
from unittest import mock

import support  # noqa: F401  (sys.path)
from support import StubProject, TempDir
from uetk import plugins


class WithDependenciesTest(unittest.TestCase):
    def test_all_when_empty(self):
        self.assertEqual(plugins.with_dependencies(None), ["UETKEditor", "UETKVfx"])
        self.assertEqual(plugins.with_dependencies([]), ["UETKEditor", "UETKVfx"])

    def test_dependencies_come_first(self):
        self.assertEqual(plugins.with_dependencies(["UETKVfx"]), ["UETKEditor", "UETKVfx"])

    def test_single_plugin_without_dependencies(self):
        self.assertEqual(plugins.with_dependencies(["UETKEditor"]), ["UETKEditor"])

    def test_unknown_plugin(self):
        with self.assertRaisesRegex(ValueError, "unknown plugin.*Nope"):
            plugins.with_dependencies(["Nope"])


class InstallTest(unittest.TestCase):
    """Installs from a copy of the toolkit's plugin sources, so tests can change a "toolkit" file."""

    def setUp(self):
        self.tmp = TempDir()
        self.source = self.tmp.path / "toolkit-plugin"
        shutil.copytree(plugins.ROOT, self.source)
        self.project = StubProject(self.tmp.path / "Project")
        self.project.root.mkdir()
        patcher = mock.patch.object(plugins, "ROOT", self.source)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.addCleanup(self.tmp.cleanup)

    def dest(self, name):
        return plugins.plugin_dir(self.project, name)

    def test_status_before_install(self):
        for row in plugins.status(self.project).values():
            self.assertFalse(row["installed"])
            self.assertFalse(row["up_to_date"])
            self.assertFalse(row["built"])

    def test_install_adds_every_file(self):
        changes = plugins.install(self.project)
        self.assertTrue(changes)
        self.assertTrue(all(": added " in c for c in changes), changes)
        self.assertTrue((self.dest("UETKEditor") / "UETKEditor.uplugin").is_file())
        self.assertTrue((self.dest("UETKVfx") / "UETKVfx.uplugin").is_file())
        status = plugins.status(self.project)
        self.assertTrue(all(row["installed"] and row["up_to_date"] for row in status.values()), status)
        self.assertEqual(plugins.install(self.project), [], "a second install changes nothing")

    def test_install_one_brings_its_dependency(self):
        plugins.install(self.project, ["UETKVfx"])
        self.assertTrue((self.dest("UETKEditor") / "UETKEditor.uplugin").is_file())

    def test_install_updates_changed_and_removes_extra_files(self):
        plugins.install(self.project, ["UETKEditor"])
        uplugin = self.dest("UETKEditor") / "UETKEditor.uplugin"
        uplugin.write_text("edited in the project", encoding="utf-8")
        extra = self.dest("UETKEditor") / "Source" / "Old.cpp"
        extra.write_text("// gone from the toolkit", encoding="utf-8")
        changes = plugins.install(self.project, ["UETKEditor"])
        self.assertIn("UETKEditor: updated UETKEditor.uplugin", changes)
        self.assertIn("UETKEditor: removed Source/Old.cpp", changes)
        self.assertFalse(extra.exists())
        self.assertEqual(uplugin.read_bytes(), (self.source / "UETKEditor" / "UETKEditor.uplugin").read_bytes())

    def test_build_folders_are_left_alone(self):
        plugins.install(self.project, ["UETKEditor"])
        binary = self.dest("UETKEditor") / "Binaries" / "Win64" / "UnrealEditor-UETKEditor.dll"
        binary.parent.mkdir(parents=True)
        binary.write_bytes(b"MZ")
        self.assertEqual(plugins.install(self.project, ["UETKEditor"]), [])
        self.assertTrue(binary.exists())
        self.assertTrue(plugins.status(self.project)["UETKEditor"]["built"])

    def test_status_goes_stale_when_a_toolkit_file_changes(self):
        plugins.install(self.project)
        self.assertTrue(plugins.status(self.project)["UETKEditor"]["up_to_date"])
        cpp = next((self.source / "UETKEditor").rglob("*.cpp"))
        cpp.write_text(cpp.read_text(encoding="utf-8") + "\n// newer\n", encoding="utf-8")
        status = plugins.status(self.project)
        self.assertFalse(status["UETKEditor"]["up_to_date"])
        self.assertTrue(status["UETKVfx"]["up_to_date"])


if __name__ == "__main__":
    unittest.main()
