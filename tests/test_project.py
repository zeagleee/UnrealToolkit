"""uetk/project.py: finding the .uproject and the engine (N9)."""
import json
import unittest

import support  # noqa: F401  (sys.path)
from support import TempDir, chdir, env, make_uproject
from uetk import project
from uetk.project import Project, ToolkitError


class FindUprojectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)
        self.uproject = make_uproject(self.tmp.path / "Game", "MyGame")
        self.sub = self.tmp.path / "Game" / "Source" / "MyGame"
        self.sub.mkdir(parents=True)
        no_env = env(UETK_PROJECT=None)
        no_env.__enter__()
        self.addCleanup(no_env.__exit__, None, None, None)

    def test_from_the_project_folder_and_a_subfolder(self):
        for folder in (self.uproject.parent, self.sub):
            with self.subTest(folder=folder), chdir(folder):
                self.assertEqual(project._find_uproject(), self.uproject)

    def test_explicit_file(self):
        self.assertEqual(project._find_uproject(str(self.uproject)), self.uproject)

    def test_explicit_folder(self):
        with chdir(self.tmp.path):
            self.assertEqual(project._find_uproject(str(self.uproject.parent)), self.uproject)

    def test_explicit_relative_path(self):
        with chdir(self.tmp.path):
            self.assertEqual(project._find_uproject("Game"), self.uproject)
            self.assertEqual(project._find_uproject("Game/MyGame.uproject"), self.uproject)

    def test_environment_variable(self):
        with chdir(self.tmp.path), env(UETK_PROJECT=str(self.uproject.parent)):
            self.assertEqual(project._find_uproject(), self.uproject)

    def test_explicit_wins_over_the_environment(self):
        other = make_uproject(self.tmp.path / "Other", "Other")
        with env(UETK_PROJECT=str(self.uproject)):
            self.assertEqual(project._find_uproject(str(other)), other)

    def test_bad_explicit_paths(self):
        with self.assertRaisesRegex(ToolkitError, "Not a .uproject"):
            project._find_uproject(str(self.sub))
        with self.assertRaisesRegex(ToolkitError, "Not a .uproject"):
            project._find_uproject(str(self.tmp.path / "Game" / "Missing.uproject"))
        readme = self.tmp.path / "readme.txt"
        readme.write_text("x", encoding="utf-8")
        with self.assertRaisesRegex(ToolkitError, "Not a .uproject"):
            project._find_uproject(str(readme))
        with env(UETK_PROJECT=str(self.sub)), self.assertRaisesRegex(ToolkitError, "Not a .uproject"):
            project._find_uproject()

    def test_none_found(self):
        empty = self.tmp.path / "Empty"
        empty.mkdir()
        with chdir(empty), self.assertRaisesRegex(ToolkitError, r"No \.uproject in .*--project"):
            project._find_uproject()


class LauncherManifestTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)
        self.manifest = self.tmp.path / "Epic" / "UnrealEngineLauncher" / "LauncherInstalled.dat"
        self.manifest.parent.mkdir(parents=True)

    def test_finds_the_install(self):
        self.manifest.write_text(json.dumps({"InstallationList": [
            {"AppName": "UE_5.7", "InstallLocation": "D:/Epic/UE_5.7"},
            {"AppName": "UE_5.8", "InstallLocation": "D:/Epic/UE_5.8"},
            {"AppName": "Fortnite", "InstallLocation": "D:/Epic/Fortnite"}]}), encoding="utf-8")
        with env(PROGRAMDATA=str(self.tmp.path)):
            self.assertEqual(project._engine_from_launcher_manifest("5.8").as_posix(), "D:/Epic/UE_5.8")
            self.assertIsNone(project._engine_from_launcher_manifest("5.6"))

    def test_missing_or_broken_manifest(self):
        with env(PROGRAMDATA=str(self.tmp.path)):
            self.assertIsNone(project._engine_from_launcher_manifest("5.8"))
            self.manifest.write_text("{not json", encoding="utf-8")
            self.assertIsNone(project._engine_from_launcher_manifest("5.8"))
            self.manifest.write_text("{}", encoding="utf-8")
            self.assertIsNone(project._engine_from_launcher_manifest("5.8"))


class ProjectTest(unittest.TestCase):
    def setUp(self):
        self.tmp = TempDir()
        self.addCleanup(self.tmp.cleanup)

    def test_descriptor_and_paths(self):
        uproject = make_uproject(self.tmp.path / "Game", "MyGame", association="5.7")
        p = Project(str(uproject))
        self.assertEqual((p.name, p.root, p.engine_association), ("MyGame", uproject.parent, "5.7"))
        self.assertEqual(p.bridge_dir, uproject.parent / "Saved" / "AgentBridge")
        self.assertEqual(Project(str(uproject), instance="client1").bridge_dir.name, "AgentBridge-client1")

    def test_utf8_bom_descriptor(self):
        uproject = self.tmp.path / "Bom.uproject"
        uproject.write_bytes(b"\xef\xbb\xbf" + json.dumps({"EngineAssociation": "5.6"}).encode())
        self.assertEqual(Project(str(uproject)).engine_association, "5.6")

    def test_engine_root_from_the_environment(self):
        engine = self.tmp.path / "UE_5.8"
        exe = engine / "Engine" / "Binaries" / "Win64" / "UnrealEditor.exe"
        exe.parent.mkdir(parents=True)
        exe.write_bytes(b"")
        p = Project(str(make_uproject(self.tmp.path / "Game")))
        with env(UETK_ENGINE_ROOT=str(engine)):
            self.assertEqual(p.engine_root, engine.resolve())

    def test_engine_not_found(self):
        p = Project(str(make_uproject(self.tmp.path / "Game", association="9.9-not-installed")))
        with env(UETK_ENGINE_ROOT=None, PROGRAMDATA=str(self.tmp.path)), \
                self.assertRaisesRegex(ToolkitError, "UETK_ENGINE_ROOT"):
            p.engine_root

    def test_targets(self):
        uproject = make_uproject(self.tmp.path / "Game", "MyGame")
        source = uproject.parent / "Source"
        source.mkdir()
        (source / "MyGame.Target.cs").write_text("Type = TargetType.Game;", encoding="utf-8")
        (source / "MyGameEditor.Target.cs").write_text("Type = TargetType.Editor;", encoding="utf-8")
        p = Project(str(uproject))
        self.assertEqual(p.targets, {"MyGame": "Game", "MyGameEditor": "Editor"})
        self.assertEqual((p.editor_target, p.game_target), ("MyGameEditor", "MyGame"))

    def test_blueprint_only_project_has_no_editor_target(self):
        p = Project(str(make_uproject(self.tmp.path / "Game")))
        with self.assertRaisesRegex(ToolkitError, "No Editor target"):
            p.editor_target


if __name__ == "__main__":
    unittest.main()
