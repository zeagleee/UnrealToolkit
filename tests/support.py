"""Shared test helpers: puts the toolkit on sys.path, loads editor/ modules that don't import `unreal`, and makes
throwaway project folders."""
import contextlib
import importlib.util
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def load_editor_module(name):
    """Import editor/<name>.py by path (putting editor/ on sys.path would let its modules shadow others)."""
    spec = importlib.util.spec_from_file_location(f"uetk_test_editor_{name}", ROOT / "editor" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class TempDir:
    """A temporary folder as a Path, removed afterwards (use with `with` or in setUp/tearDown)."""

    def __init__(self):
        self.path = Path(tempfile.mkdtemp(prefix="uetk-test-")).resolve()

    def cleanup(self):
        shutil.rmtree(self.path, ignore_errors=True)

    def __enter__(self):
        return self.path

    def __exit__(self, *exc):
        self.cleanup()


class StubProject:
    """What uetk's helpers need from a Project, without an engine: root, uproject and out_dir."""

    def __init__(self, root, name="Demo"):
        self.root = Path(root)
        self.name = name
        self.uproject = self.root / f"{name}.uproject"
        self.instance = None

    @property
    def out_dir(self):
        path = self.root / "Saved" / "UnrealToolkit"
        path.mkdir(parents=True, exist_ok=True)
        return path


def make_uproject(folder, name="Demo", association="5.8"):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{name}.uproject"
    path.write_text(json.dumps({"FileVersion": 3, "EngineAssociation": association}), encoding="utf-8")
    return path


@contextlib.contextmanager
def chdir(path):
    old = os.getcwd()
    os.chdir(path)
    try:
        yield
    finally:
        os.chdir(old)


@contextlib.contextmanager
def env(**values):
    """Set (str) or remove (None) environment variables for the duration."""
    old = {k: os.environ.get(k) for k in values}
    try:
        for key, value in values.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        yield
    finally:
        for key, value in old.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
