"""Put things back after a test session.

* Folder snapshots: PIE runs often overwrite save games (or any file under Saved/). `snapshot_save` copies
  folders/files away, `snapshot_restore` mirrors them back exactly (files created meanwhile are removed).
  `pie start --protect` takes the "protect" snapshot; `pie stop`, `editor stop` and `kill` restore it.
* Background throttling: an unfocused editor runs at a few fps, which makes scripted play slow. The setting
  lives per user and per engine version in EditorSettings.ini; `editor start --no-throttle` turns it off and
  the toolkit puts the user's value back once the last editor that asked for it has exited.
"""
import hashlib
import json
import os
import shutil
import time
from pathlib import Path

from .project import ToolkitError

PROTECT = "protect"
DEFAULT_PROTECT = ["Saved/SaveGames"]


# ---------------------------------------------------------------------------- snapshots

def _snapshot_dir(project, name):
    return project.out_dir / "snapshots" / name


def _manifest_path(project, name):
    return _snapshot_dir(project, name) / "manifest.json"


def read_manifest(project, name=PROTECT):
    try:
        return json.loads(_manifest_path(project, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _write_manifest(project, name, manifest):
    _manifest_path(project, name).write_text(json.dumps(manifest, indent=1), encoding="utf-8")


def _inside_project(project, rel):
    target = (project.root / rel).resolve()
    root = project.root.resolve()
    if target != root and root not in target.parents:
        raise ToolkitError(f"{rel}: not inside the project folder {root}")
    if target == root:
        raise ToolkitError("Refusing to snapshot the whole project folder; name the folders to keep (e.g. Saved/SaveGames).")
    return target


def _files(path):
    """{relative posix path: sha1} of a file or of every file under a folder."""
    path = Path(path)
    if path.is_file():
        return {"": _sha1(path)}
    if not path.is_dir():
        return {}
    return {p.relative_to(path).as_posix(): _sha1(p) for p in sorted(path.rglob("*")) if p.is_file()}


def _sha1(path):
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def snapshot_save(project, rel_paths, name=PROTECT):
    """Copy the given project-relative files/folders away. Returns the manifest."""
    if not rel_paths:
        raise ToolkitError("Name at least one file or folder to snapshot.")
    folder = _snapshot_dir(project, name)
    if folder.exists():
        shutil.rmtree(folder)
    (folder / "data").mkdir(parents=True)
    manifest = {"name": name, "created": time.strftime("%Y-%m-%d %H:%M:%S"), "pending": True, "entries": []}
    for index, rel in enumerate(rel_paths):
        source = _inside_project(project, rel)
        stored = folder / "data" / str(index)
        if source.is_dir():
            shutil.copytree(source, stored)
            kind = "dir"
        elif source.is_file():
            shutil.copy2(source, stored)
            kind = "file"
        else:
            kind = "missing"
        manifest["entries"].append({"path": Path(rel).as_posix(), "kind": kind, "files": len(_files(stored)) if kind != "missing" else 0})
    _write_manifest(project, name, manifest)
    return manifest


def snapshot_diff(project, name=PROTECT):
    """What changed since the snapshot: {path: {'changed': [...], 'added': [...], 'removed': [...]}}."""
    manifest = read_manifest(project, name)
    if not manifest:
        raise ToolkitError(f"No snapshot named {name!r}.")
    folder = _snapshot_dir(project, name)
    report = {}
    for index, entry in enumerate(manifest["entries"]):
        before = _files(folder / "data" / str(index)) if entry["kind"] != "missing" else {}
        after = _files(project.root / entry["path"])
        report[entry["path"]] = {
            "changed": sorted(k or entry["path"] for k in before.keys() & after.keys() if before[k] != after[k]),
            "added": sorted(k or entry["path"] for k in after.keys() - before.keys()),
            "removed": sorted(k or entry["path"] for k in before.keys() - after.keys()),
        }
    return report


def snapshot_restore(project, name=PROTECT):
    """Mirror the snapshot back. Returns the diff that was undone. The snapshot is kept (marked restored)."""
    manifest = read_manifest(project, name)
    if not manifest:
        raise ToolkitError(f"No snapshot named {name!r}.")
    report = snapshot_diff(project, name)
    folder = _snapshot_dir(project, name)
    for index, entry in enumerate(manifest["entries"]):
        target = _inside_project(project, entry["path"])
        stored = folder / "data" / str(index)
        if entry["kind"] == "dir":
            target.mkdir(parents=True, exist_ok=True)
            wanted = _files(stored)
            for rel in _files(target).keys() - wanted.keys():
                (target / rel).unlink()
            for rel, digest in wanted.items():
                destination = target / rel
                if not destination.is_file() or _sha1(destination) != digest:
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(stored / rel, destination)
            for sub in sorted((p for p in target.rglob("*") if p.is_dir()), key=lambda p: len(p.parts), reverse=True):
                if not any(sub.iterdir()) and not (stored / sub.relative_to(target)).is_dir():
                    sub.rmdir()
        elif entry["kind"] == "file":
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(stored, target)
        elif target.is_dir():
            shutil.rmtree(target)
        elif target.exists():
            target.unlink()
    manifest["pending"] = False
    manifest["restored"] = time.strftime("%Y-%m-%d %H:%M:%S")
    _write_manifest(project, name, manifest)
    return report


def describe(report):
    """One line per path: 'Saved/SaveGames: 2 changed, 1 added' / 'unchanged'."""
    lines = []
    for path, d in report.items():
        parts = [f"{len(d[k])} {k}" for k in ("changed", "added", "removed") if d[k]]
        detail = "; ".join(f"{k}: {', '.join(d[k][:6])}{' ...' if len(d[k]) > 6 else ''}" for k in ("changed", "added", "removed") if d[k])
        lines.append(f"{path}: {', '.join(parts) or 'unchanged'}" + (f" ({detail})" if detail else ""))
    return lines


def restore_pending_protect(project, say=print):
    """Restore the 'protect' snapshot if a session left it pending. Returns True if it restored."""
    manifest = read_manifest(project, PROTECT)
    if not manifest or not manifest.get("pending"):
        return False
    report = snapshot_restore(project, PROTECT)
    for line in describe(report):
        say(f"Restored {line}")
    return True


# ---------------------------------------------------------------------------- background throttling

THROTTLE_SECTION = "/Script/UnrealEd.EditorPerformanceSettings"
THROTTLE_KEY = "bThrottleCPUWhenNotForeground"


def editor_settings_ini(project):
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
    return Path(local) / "UnrealEngine" / project.engine_major_minor / "Saved/Config/WindowsEditor/EditorSettings.ini"


def _throttle_state_path(project):
    local = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData/Local")
    return Path(local) / "UnrealToolkit" / f"throttle-{project.engine_major_minor}.json"


def _read_ini(path):
    raw = path.read_bytes() if path.exists() else b""
    if raw.startswith(b"\xff\xfe") or raw.startswith(b"\xfe\xff"):
        encoding = "utf-16"
    elif raw.startswith(b"\xef\xbb\xbf"):
        encoding = "utf-8-sig"
    else:
        encoding = "utf-8"
    text = raw.decode(encoding, errors="replace")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text.splitlines(), encoding, newline


def _write_ini(path, lines, encoding, newline):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes((newline.join(lines) + newline).encode(encoding))


def _find(lines, section, key):
    """(section header index or None, key line index or None, end of section index)."""
    header, key_line, end = None, None, len(lines)
    for i, line in enumerate(lines):
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            if header is not None:
                end = i
                break
            if stripped[1:-1] == section:
                header = i
        elif header is not None and key_line is None and stripped.split("=", 1)[0].strip() == key and "=" in stripped:
            key_line = i
    return header, key_line, end


def ini_get(path, section, key):
    lines, _, _ = _read_ini(path)
    header, key_line, _ = _find(lines, section, key)
    return lines[key_line].split("=", 1)[1].strip() if key_line is not None else None


def ini_set(path, section, key, value):
    """Set key=value in [section], adding what is missing. Returns whether it added a blank separator line before a
    new section (ini_remove needs that to take out exactly what was added)."""
    lines, encoding, newline = _read_ini(path)
    header, key_line, end = _find(lines, section, key)
    added_separator = False
    if key_line is not None:
        lines[key_line] = f"{key}={value}"
    elif header is not None:
        insert = end
        while insert > header + 1 and not lines[insert - 1].strip():  # before the blank lines that end the section
            insert -= 1
        lines.insert(insert, f"{key}={value}")
    else:
        if lines and lines[-1].strip():
            lines.append("")
            added_separator = True
        lines += [f"[{section}]", f"{key}={value}"]
    _write_ini(path, lines, encoding, newline)
    return added_separator


def ini_remove(path, section, key, drop_empty_section=False, drop_separator=False):
    """Remove key from [section]; with drop_empty_section also the then-empty section, and with drop_separator the blank
    line ini_set put before it."""
    lines, encoding, newline = _read_ini(path)
    header, key_line, end = _find(lines, section, key)
    if key_line is None:
        return
    del lines[key_line]
    end -= 1
    if drop_empty_section and header is not None and not any(l.strip() for l in lines[header + 1:end]):
        del lines[header:end]
        if drop_separator and header > 0 and not lines[header - 1].strip():
            del lines[header - 1]
    _write_ini(path, lines, encoding, newline)


def disable_throttle(project, say=print):
    """Before launching the editor: background throttling off, remembering the user's value."""
    ini = editor_settings_ini(project)
    state_path = _throttle_state_path(project)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        lines, _, _ = _read_ini(ini)
        header, key_line, _ = _find(lines, THROTTLE_SECTION, THROTTLE_KEY)
        state = {"ini": str(ini), "previous": ini_get(ini, THROTTLE_SECTION, THROTTLE_KEY),
                 "section_existed": header is not None, "projects": []}
    project_key = str(project.uproject)
    if project_key not in state["projects"]:
        state["projects"].append(project_key)
    added_separator = ini_set(ini, THROTTLE_SECTION, THROTTLE_KEY, "False")
    state["added_separator"] = state.get("added_separator") or added_separator
    state_path.parent.mkdir(parents=True, exist_ok=True)
    state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
    say(f"Background throttling off for this session ({ini}); the toolkit restores it when the editor exits.")


def restore_throttle(project, say=print):
    """After this project's editor exited: drop its claim; the last one out puts the user's value back."""
    state_path = _throttle_state_path(project)
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    project_key = str(project.uproject)
    if project_key not in state["projects"]:
        return False
    state["projects"].remove(project_key)
    if state["projects"]:
        state_path.write_text(json.dumps(state, indent=1), encoding="utf-8")
        return False
    ini = Path(state["ini"])
    if state["previous"] is None:
        ini_remove(ini, THROTTLE_SECTION, THROTTLE_KEY, drop_empty_section=not state["section_existed"],
                   drop_separator=state.get("added_separator", False))
    else:
        ini_set(ini, THROTTLE_SECTION, THROTTLE_KEY, state["previous"])
    state_path.unlink()
    say(f"Background throttling restored to {'the default' if state['previous'] is None else state['previous']}.")
    return True


def throttle_claimed(project):
    try:
        state = json.loads(_throttle_state_path(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return str(project.uproject) in state.get("projects", [])


def after_editor_exit(project, say=print):
    """Everything a closed editor leaves to undo: a pending PIE protect snapshot, the throttle setting."""
    restore_pending_protect(project, say)
    restore_throttle(project, say)
