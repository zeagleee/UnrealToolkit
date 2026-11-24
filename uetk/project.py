"""Locate the .uproject, the engine install it is associated with, and the paths derived from them."""
import json
import os
import re
import sys
from functools import cached_property
from pathlib import Path

PACKAGE_DIR = Path(__file__).resolve().parent
TOOLKIT_DIR = PACKAGE_DIR.parent


def toolkit_path(name):
    """A folder the toolkit ships beside uetk/ (editor, plugin, scripts). In a clone it is <toolkit>/<name>; in an
    installed package (pyproject.toml) it is uetk/_<name>."""
    installed = PACKAGE_DIR / f"_{name}"
    return installed if installed.is_dir() else TOOLKIT_DIR / name


EDITOR_PY_DIR = toolkit_path("editor")


class ToolkitError(Exception):
    """Expected failure; ue.py prints the message without a traceback."""


def _find_uproject(explicit=None):
    """--project / UETK_PROJECT (a .uproject or its folder), else the nearest .uproject above the cwd."""
    given = explicit or os.environ.get("UETK_PROJECT")
    if given:
        path = Path(given).resolve()
        hits = sorted(path.glob("*.uproject")) if path.is_dir() else [path] if path.suffix.lower() == ".uproject" and path.exists() else []
        if not hits:
            raise ToolkitError(f"Not a .uproject file or a folder containing one: {given}")
        return hits[0]
    cwd = Path.cwd().resolve()
    for folder in (cwd, *cwd.parents):
        hits = sorted(folder.glob("*.uproject"))
        if hits:
            return hits[0]
    raise ToolkitError(f"No .uproject in {cwd} or its parents. Run from inside an Unreal project or pass --project <path>.")


def _engine_from_registry(association):
    # Launcher installs register under HKLM; source builds (GUID associations) under HKCU\...\Builds.
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\EpicGames\Unreal Engine\{association}") as key:
            return Path(winreg.QueryValueEx(key, "InstalledDirectory")[0])
    except OSError:
        pass
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"SOFTWARE\Epic Games\Unreal Engine\Builds") as key:
            return Path(winreg.QueryValueEx(key, association)[0])
    except OSError:
        return None


def _engine_from_launcher_manifest(association):
    manifest = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "Epic/UnrealEngineLauncher/LauncherInstalled.dat"
    try:
        data = json.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for item in data.get("InstallationList", []):
        if item.get("AppName") == f"UE_{association}":
            return Path(item["InstallLocation"])
    return None


class Project:
    def __init__(self, uproject=None, instance=None):
        self.instance = instance  # a named game process's bridge (ue.py net); None = the editor
        self.uproject = _find_uproject(uproject)
        self.root = self.uproject.parent
        self.name = self.uproject.stem
        self.descriptor = json.loads(self.uproject.read_text(encoding="utf-8-sig"))
        self.engine_association = self.descriptor.get("EngineAssociation", "")

    # ------------------------------------------------------------------ engine

    @cached_property
    def engine_root(self):
        """Folder that contains Engine/ (e.g. D:/Epic Games/Games/UE_5.6)."""
        candidates = []
        if os.environ.get("UETK_ENGINE_ROOT"):
            candidates.append(Path(os.environ["UETK_ENGINE_ROOT"]))
        if self.engine_association:
            candidates += [_engine_from_registry(self.engine_association),
                           _engine_from_launcher_manifest(self.engine_association)]
        candidates += list(self.root.parents)  # project nested inside an engine tree
        for candidate in candidates:
            if candidate and (candidate / "Engine/Binaries/Win64/UnrealEditor.exe").exists():
                return candidate.resolve()
        raise ToolkitError(f"Cannot find engine for EngineAssociation '{self.engine_association}'. "
                           "Set UETK_ENGINE_ROOT to the folder that contains Engine/.")

    @property
    def engine_dir(self):
        return self.engine_root / "Engine"

    @cached_property
    def engine_version(self):
        try:
            v = json.loads((self.engine_dir / "Build/Build.version").read_text(encoding="utf-8-sig"))
            return f"{v['MajorVersion']}.{v['MinorVersion']}.{v['PatchVersion']} (CL {v.get('Changelist', '?')})"
        except (OSError, ValueError, KeyError):
            return "unknown"

    @cached_property
    def engine_major_minor(self):
        """'5.6': the folder name of per-user engine settings (%LOCALAPPDATA%/UnrealEngine/<this>)."""
        try:
            v = json.loads((self.engine_dir / "Build/Build.version").read_text(encoding="utf-8-sig"))
            return f"{v['MajorVersion']}.{v['MinorVersion']}"
        except (OSError, ValueError, KeyError):
            match = re.match(r"(\d+\.\d+)", self.engine_association or "")
            if match:
                return match.group(1)
            raise ToolkitError("Cannot tell the engine's major.minor version (Engine/Build/Build.version unreadable).")

    @property
    def editor_exe(self):
        return self.engine_dir / "Binaries/Win64/UnrealEditor.exe"

    @property
    def editor_cmd_exe(self):
        return self.engine_dir / "Binaries/Win64/UnrealEditor-Cmd.exe"

    @property
    def ubt_exe(self):
        return self.engine_dir / "Binaries/DotNET/UnrealBuildTool/UnrealBuildTool.exe"

    @cached_property
    def bundled_dotnet_dir(self):
        """The engine's own .NET SDK (Engine/Binaries/ThirdParty/DotNet/<version>/<arch>), or None.

        UBT targets the .NET release Epic bundles (10.0 in UE 5.8), which is often not installed system-wide.
        The version comes from GetDotnetPath.bat, the script Build.bat/RunUAT.bat use; if that script cannot
        be read, the newest bundled version is used."""
        arch = "win-arm64" if (os.environ.get("PROCESSOR_ARCHITECTURE") == "ARM64"
                               or os.environ.get("ProgramFiles(Arm)")) else "win-x64"
        base = self.engine_dir / "Binaries/ThirdParty/DotNet"
        try:
            bat = (self.engine_dir / "Build/BatchFiles/GetDotnetPath.bat").read_text(encoding="utf-8", errors="replace")
            match = re.search(r"^\s*set\s+UE_DOTNET_VERSION=(\S+)", bat, re.I | re.M)
            if match and (base / match.group(1) / arch / "dotnet.exe").exists():
                return base / match.group(1) / arch
        except OSError:
            pass
        found = [exe.parent for exe in base.glob(f"*/{arch}/dotnet.exe")]
        found.sort(key=lambda d: [int(p) if p.isdigit() else 0 for p in d.parent.name.split(".")])
        return found[-1] if found else None

    @property
    def runuat_bat(self):
        return self.engine_dir / "Build/BatchFiles/RunUAT.bat"

    # ------------------------------------------------------------------ targets

    @cached_property
    def targets(self):
        """{target_name: TargetType} parsed from Source/*.Target.cs."""
        found = {}
        for path in sorted((self.root / "Source").glob("*.Target.cs")):
            match = re.search(r"Type\s*=\s*TargetType\.(\w+)", path.read_text(encoding="utf-8-sig"))
            found[path.name[:-len(".Target.cs")]] = match.group(1) if match else "Unknown"
        return found

    def target_of_type(self, target_type):
        for name, kind in self.targets.items():
            if kind == target_type:
                return name
        raise ToolkitError(f"No {target_type} target in Source/*.Target.cs")

    @property
    def editor_target(self):
        return self.target_of_type("Editor")

    @property
    def game_target(self):
        return self.target_of_type("Game")

    # ------------------------------------------------------------------ project folders

    @property
    def saved_dir(self):
        return self.root / "Saved"

    @property
    def logs_dir(self):
        return self.saved_dir / "Logs"

    @property
    def crashes_dir(self):
        return self.saved_dir / "Crashes"

    @property
    def bridge_dir(self):
        """Saved/AgentBridge (the editor), or Saved/AgentBridge-<instance> for a game process (`--instance`)."""
        return self.saved_dir / ("AgentBridge-" + self.instance if self.instance else "AgentBridge")

    @property
    def out_dir(self):
        """Scratch output of the toolkit itself (build logs, test reports, screenshots)."""
        path = self.saved_dir / "UnrealToolkit"
        path.mkdir(parents=True, exist_ok=True)
        return path
