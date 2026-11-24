"""System-Python side of the toolkit's editor plugins (`ue.py plugins`, `ue.py vfx install`).

Each plugin's source lives in the toolkit (plugin/<Name>). `install` copies it into <Project>/Plugins/<Name>
(source only; the project's build compiles it), with the plugins it depends on, and updates changed files on
later runs.
"""
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent / "plugin"
PLUGINS = ("UETKEditor", "UETKVfx")              # install order
DEPENDS = {"UETKVfx": ("UETKEditor",)}
PURPOSE = {
    "UETKEditor": "offscreen renders of level views, actors, assets and widgets (ue.py render)",
    "UETKVfx": "Niagara authoring, stack dumps and previews (ue.py vfx)",
}
_BUILD_DIRS = ("Binaries", "Intermediate")


def _source_files(root):
    if not root.is_dir():
        return []
    return sorted(p.relative_to(root) for p in root.rglob("*")
                  if p.is_file() and not set(p.relative_to(root).parts) & set(_BUILD_DIRS))


def plugin_dir(project, name):
    return project.root / "Plugins" / name


def dll_path(project, name):
    return plugin_dir(project, name) / "Binaries" / "Win64" / f"UnrealEditor-{name}.dll"


def with_dependencies(names):
    """The plugins to install for `names` (all when empty), dependencies first, in install order."""
    wanted = set(names or PLUGINS)
    unknown = wanted - set(PLUGINS)
    if unknown:
        raise ValueError(f"unknown plugin(s): {', '.join(sorted(unknown))} (toolkit plugins: {', '.join(PLUGINS)})")
    for name in list(wanted):
        wanted.update(DEPENDS.get(name, ()))
    return [n for n in PLUGINS if n in wanted]


def install(project, names=None):
    """Copy new or changed files of the plugins (and their dependencies) into the project; remove files the toolkit
    no longer has. Returns the changes as '<Plugin>: updated <file>' lines (empty = already up to date)."""
    changes = []
    for name in with_dependencies(names):
        source, dest = ROOT / name, plugin_dir(project, name)
        for rel in _source_files(source):
            src, dst = source / rel, dest / rel
            existed = dst.exists()
            if not existed or src.read_bytes() != dst.read_bytes():
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst)
                changes.append(f"{name}: {'updated' if existed else 'added'} {rel.as_posix()}")
        wanted = set(_source_files(source))
        for rel in _source_files(dest):
            if rel not in wanted:
                (dest / rel).unlink()
                changes.append(f"{name}: removed {rel.as_posix()}")
    return changes


def status(project):
    """{plugin: {installed, up_to_date, built, path}} for every toolkit plugin."""
    out = {}
    for name in PLUGINS:
        source, dest = ROOT / name, plugin_dir(project, name)
        installed = (dest / f"{name}.uplugin").exists()
        stale = [rel for rel in _source_files(source)
                 if not (dest / rel).exists() or (source / rel).read_bytes() != (dest / rel).read_bytes()]
        dll = dll_path(project, name)
        newest_source = max(((dest / rel).stat().st_mtime for rel in _source_files(dest)), default=0)
        out[name] = {
            "installed": installed,
            "up_to_date": installed and not stale,
            "built": dll.exists() and dll.stat().st_mtime >= newest_source,
            "path": str(dest),
        }
    return out


def stale_warnings(project):
    """One line per plugin installed in the project whose files differ from the toolkit's copy. A stale copy otherwise
    shows up as a missing function deep inside a call. Never raises: an unreadable folder gives no warning."""
    try:
        info = status(project)
    except OSError:
        return []
    return [f"{name} in this project is older than the toolkit's: run `ue.py plugins install`, then `ue.py build`"
            for name, row in info.items() if row["installed"] and not row["up_to_date"]]
