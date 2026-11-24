"""Unreal log helpers: find the active editor log, read increments, classify lines, summarize crashes."""
import re
import xml.etree.ElementTree as ET
from pathlib import Path

LINE_RE = re.compile(r"^(?:\[[^\]]*\]\[\s*\d+\])?(?P<cat>[A-Za-z]\w*): (?:(?P<verb>Fatal|Error|Warning|Display|Log|Verbose|VeryVerbose): )?(?P<msg>.*)$")
COMPILER_PROBLEM_RE = re.compile(r"\b(error|warning) [A-Z]+\d{3,5}\b|: (fatal )?error\b", re.I)


def active_log(project, bridge_info=None):
    """The log the running editor writes to (bridge-reported if known, else the newest session log)."""
    if bridge_info and bridge_info.get("log_file") and Path(bridge_info["log_file"]).exists():
        return Path(bridge_info["log_file"])
    if not project.logs_dir.exists():
        return None
    logs = [p for p in project.logs_dir.glob("*.log")
            if "-backup-" not in p.name and not p.name.startswith("UnrealVersionSelector")]
    return max(logs, key=lambda p: p.stat().st_mtime) if logs else None


def size_of(path):
    try:
        return path.stat().st_size if path else 0
    except OSError:
        return 0


def read_from(path, offset):
    """Text appended to `path` since byte `offset` (whole file if it was truncated/rotated)."""
    if not path:
        return ""
    with open(path, "rb") as f:
        f.seek(0, 2)
        if f.tell() < offset:
            offset = 0
        f.seek(offset)
        return f.read().decode("utf-8", errors="replace")


def verbosity(line):
    m = LINE_RE.match(line)
    if m and m.group("verb"):
        return m.group("verb")
    if COMPILER_PROBLEM_RE.search(line):
        return "Error" if "error" in line.lower() else "Warning"
    return None


def is_problem(line):
    return verbosity(line) in ("Fatal", "Error", "Warning")


def category(line):
    m = LINE_RE.match(line)
    return m.group("cat") if m else None


def latest_crash(project):
    """Summary dict of the newest folder in Saved/Crashes, or None."""
    if not project.crashes_dir.exists():
        return None
    folders = [p for p in project.crashes_dir.iterdir() if p.is_dir()]
    if not folders:
        return None
    folder = max(folders, key=lambda p: p.stat().st_mtime)
    summary = {"folder": str(folder)}
    context = folder / "CrashContext.runtime-xml"
    if context.exists():
        try:
            root = ET.parse(context).getroot()
            for tag in ("CrashType", "ErrorMessage", "CallStack", "TimeOfCrash", "EngineVersion"):
                node = root.find(f".//{tag}")
                if node is not None and node.text:
                    summary[tag] = node.text.strip()
        except ET.ParseError as exc:
            summary["parse_error"] = str(exc)
    logs = sorted(folder.glob("*.log"))
    if logs:
        tail = logs[0].read_text(encoding="utf-8", errors="replace").splitlines()
        summary["log_errors"] = [l for l in tail if verbosity(l) in ("Fatal", "Error")][-25:]
    return summary
