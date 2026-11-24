"""Windows process helpers: find this project's Unreal processes, launch detached, wait, kill, list windows.

The module imports on any platform (so `import ue` works on Linux and macOS); the Win32 parts are set up only on
Windows, and the functions that need them say so elsewhere."""
import ctypes
import json
import os
import subprocess
import sys
import time

from .project import ToolkitError

IS_WINDOWS = sys.platform == "win32"

if IS_WINDOWS:
    from ctypes import wintypes

    _k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    _u32 = ctypes.WinDLL("user32", use_last_error=True)

    _k32.OpenProcess.restype = wintypes.HANDLE
    _k32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    _k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    _k32.CloseHandle.argtypes = [wintypes.HANDLE]

    WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    _u32.EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
    _u32.GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
    _u32.IsWindowVisible.argtypes = [wintypes.HWND]
    _u32.IsIconic.argtypes = [wintypes.HWND]
    _u32.GetWindowTextLengthW.argtypes = [wintypes.HWND]
    _u32.GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
    _u32.GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    _u32.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]

    try:  # real pixel sizes for window rects and screenshots on scaled displays
        _u32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        pass


def _windows_only(what):
    if not IS_WINDOWS:
        raise ToolkitError(f"{what} is Windows-only for now (Linux and macOS support is planned; see TODO.md).")

SYNCHRONIZE = 0x00100000
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
WAIT_TIMEOUT = 0x102
ERROR_ACCESS_DENIED = 5

DETACHED_PROCESS = 0x00000008
CREATE_NEW_PROCESS_GROUP = 0x00000200
CREATE_BREAKAWAY_FROM_JOB = 0x01000000

_PS_QUERY = (
    "Get-CimInstance Win32_Process -Filter \"Name LIKE 'UnrealEditor%' OR Name LIKE 'CrashReportClient%'\" "
    "| Select-Object ProcessId,ParentProcessId,Name,CommandLine | ConvertTo-Json -Compress"
)


def pid_alive(pid):
    if not IS_WINDOWS:
        try:
            os.kill(int(pid), 0)
        except PermissionError:
            return True
        except OSError:
            return False
        return True
    handle = _k32.OpenProcess(SYNCHRONIZE | PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid))
    if not handle:
        return ctypes.get_last_error() == ERROR_ACCESS_DENIED
    try:
        return _k32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
    finally:
        _k32.CloseHandle(handle)


def wait_exit(pid, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if not pid_alive(pid):
            return True
        time.sleep(0.5)
    return not pid_alive(pid)


def unreal_processes():
    """All UnrealEditor*/CrashReportClient* processes: [{pid, ppid, name, cmdline}]."""
    _windows_only("Finding Unreal processes")
    out = subprocess.run(["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_QUERY],
                         capture_output=True, text=True, timeout=60).stdout.strip()
    if not out:
        return []
    rows = json.loads(out)
    rows = rows if isinstance(rows, list) else [rows]
    return [{"pid": r["ProcessId"], "ppid": r["ParentProcessId"], "name": r["Name"],
             "cmdline": r.get("CommandLine") or ""} for r in rows]


def project_processes(project):
    """Unreal processes whose command line references this project, tagged with a 'kind'."""
    root = str(project.root).lower().replace("/", "\\") + "\\"
    result = []
    for proc in unreal_processes():
        if root not in proc["cmdline"].lower().replace("/", "\\"):
            continue
        name = proc["name"].lower()
        cmdline = proc["cmdline"].lower()
        if name == "unrealeditor.exe" and (" -game" in cmdline or " -server" in cmdline):
            proc["kind"] = "game"  # a game/server process (ue.py net), not the editor
        elif name == "unrealeditor.exe":
            proc["kind"] = "editor"
        elif name.startswith("crashreportclient"):
            proc["kind"] = "crash-reporter"
        else:
            proc["kind"] = "commandlet" if "-run=" in proc["cmdline"].lower() else "headless"
        result.append(proc)
    return result


def editor_pids(project):
    return [p["pid"] for p in project_processes(project) if p["kind"] == "editor"]


def launch_detached(cmdline, env=None, cwd=None):
    """Start a process that outlives this script (and the agent's shell job). Returns its pid."""
    _windows_only("Launching the editor")
    flags = DETACHED_PROCESS | CREATE_NEW_PROCESS_GROUP
    common = dict(env=env, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                  stderr=subprocess.DEVNULL, close_fds=True)
    try:
        return subprocess.Popen(cmdline, creationflags=flags | CREATE_BREAKAWAY_FROM_JOB, **common).pid
    except OSError:  # the job object may forbid breakaway
        return subprocess.Popen(cmdline, creationflags=flags, **common).pid


def kill_tree(pid):
    _windows_only("Killing processes")
    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], capture_output=True)


def windows_of(pid):
    """Visible top-level windows of a process: [{hwnd, title, rect, minimized}], largest first (none off Windows)."""
    if not IS_WINDOWS:
        return []
    found = []

    def callback(hwnd, _lparam):
        owner = wintypes.DWORD()
        _u32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid and _u32.IsWindowVisible(hwnd):
            rect = wintypes.RECT()
            _u32.GetWindowRect(hwnd, ctypes.byref(rect))
            width, height = rect.right - rect.left, rect.bottom - rect.top
            if width > 1 and height > 1:
                length = _u32.GetWindowTextLengthW(hwnd)
                buf = ctypes.create_unicode_buffer(length + 1)
                _u32.GetWindowTextW(hwnd, buf, length + 1)
                found.append({"hwnd": hwnd, "title": buf.value, "rect": (rect.left, rect.top, width, height),
                              "minimized": bool(_u32.IsIconic(hwnd))})
        return True

    _u32.EnumWindows(WNDENUMPROC(callback), 0)
    found.sort(key=lambda w: w["rect"][2] * w["rect"][3], reverse=True)
    return found


def restore_window(hwnd):
    """Un-minimize without stealing focus."""
    _windows_only("Restoring a window")
    _u32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
