"""Client for the in-editor agent bridge (editor/agent_bridge.py) and for headless Python runs."""
import json
import os
import subprocess
import sys
import time
import uuid

from . import logs, procs
from .project import EDITOR_PY_DIR, ToolkitError

STALE_HEARTBEAT = 10.0


class Bridge:
    def __init__(self, project):
        self.project = project
        self.dir = project.bridge_dir
        self.inbox = self.dir / "inbox"
        self.outbox = self.dir / "outbox"
        self.server_file = self.dir / "server.json"

    def info(self):
        try:
            return json.loads(self.server_file.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def state(self):
        """('ok'|'busy'|'none'|'dead'|'stopped', info). 'busy' = alive but not ticking lately."""
        info = self.info()
        if not info:
            return "none", None
        if info.get("stopped"):
            return "stopped", info
        if not procs.pid_alive(info["pid"]):
            return "dead", info
        if time.time() - info.get("heartbeat", 0) > STALE_HEARTBEAT:
            return "busy", info
        return "ok", info

    def _unavailable_message(self, state):
        if procs.editor_pids(self.project):
            return ("The editor is running but the agent bridge is not loaded in it (still starting? try "
                    "`ue.py editor wait`).\nOtherwise relaunch it via `ue.py editor stop` + `ue.py editor start` "
                    "(`ue.py bridge install` makes self-launched editors load it too), or paste this into the "
                    "editor's Output Log (Python mode):\n"
                    f"  import sys; sys.path.append(r'{EDITOR_PY_DIR}'); import agent_bridge; agent_bridge.start()")
        return "The editor is not running. Start it with `ue.py editor start` (or use --headless)."

    def call(self, code, timeout=120.0, quiet=False, on_progress=None):
        """Execute Python in the editor. Returns the response dict plus 'log' (lines written meanwhile).

        Scenarios (cells with a top-level yield) run across editor ticks: on_progress(text) gets their new output
        while they run, and the response's 'streamed' says how much of 'stdout' was already handed out. On a
        timeout or Ctrl+C the editor is asked to stop the scenario."""
        state, info = self.state()
        if state not in ("ok", "busy"):
            raise ToolkitError(self._unavailable_message(state))
        if state == "busy" and not quiet:
            print(f"[bridge] editor has not ticked for {time.time() - info['heartbeat']:.0f}s (busy or modal dialog?); "
                  "request queued", file=sys.stderr)
        log_path = logs.active_log(self.project, info)
        log_offset = logs.size_of(log_path)

        request_id = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8]
        self.inbox.mkdir(parents=True, exist_ok=True)
        request_file = self.inbox / f"{request_id}.json"
        tmp = request_file.with_suffix(".tmp")
        tmp.write_text(json.dumps({"id": request_id, "code": code}), encoding="utf-8")
        os.replace(tmp, request_file)

        response_file = self.outbox / f"{request_id}.json"
        progress_file = self.outbox / f"{request_id}.progress"
        streamed = 0
        deadline = time.time() + timeout
        next_alive_check = next_progress_check = time.time() + 0.25
        try:
            while not response_file.exists():
                now = time.time()
                if now > deadline:
                    picked_up = not request_file.exists()
                    if not picked_up:
                        request_file.unlink(missing_ok=True)
                    else:
                        self._cancel(request_id)
                    raise ToolkitError(
                        f"Timed out after {timeout:.0f}s. " +
                        ("A scenario is stopped now; a plain cell still running finishes and its result is discarded."
                         if picked_up else
                         "The editor never picked the request up (busy, or a modal dialog is open: try `ue.py windows` "
                         "and `ue.py screenshot`)."))
                if on_progress and now > next_progress_check:
                    next_progress_check = now + 0.25
                    streamed = self._stream(progress_file, streamed, on_progress)
                if now > next_alive_check:
                    next_alive_check = now + 1.0
                    if not procs.pid_alive(info["pid"]):
                        raise ToolkitError("The editor process exited while the request was pending (crash? see `ue.py crash`).")
                time.sleep(0.03)
        except KeyboardInterrupt:
            self._cancel(request_id)
            raise
        for _ in range(50):  # the file is renamed into place atomically, but may be briefly locked
            try:
                response = json.loads(response_file.read_text(encoding="utf-8"))
                break
            except (OSError, ValueError):
                time.sleep(0.05)
        else:
            raise ToolkitError(f"Could not read response {response_file}")
        response_file.unlink(missing_ok=True)
        response["log"] = logs.read_from(log_path, log_offset).splitlines()
        response["streamed"] = streamed
        return response

    def _cancel(self, request_id):
        """Ask the bridge to stop a running scenario (inbox/<id>.cancel)."""
        try:
            (self.inbox / f"{request_id}.cancel").write_text("", encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _stream(progress_file, streamed, on_progress):
        try:
            text = progress_file.read_text(encoding="utf-8")
        except OSError:
            return streamed
        if len(text) > streamed:
            on_progress(text[streamed:])
            return len(text)
        return streamed


# ---------------------------------------------------------------------------- headless

def run_headless(project, code, timeout=900.0):
    """Run code in a fresh UnrealEditor-Cmd (-run=pythonscript, no rendering). Slow to start, no live editor needed."""
    work = project.out_dir / "headless"
    work.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    request_path, response_path, log_path = (work / f"{stamp}{ext}" for ext in (".request.json", ".response.json", ".log"))
    request_path.write_text(json.dumps({"code": code, "response_path": str(response_path)}), encoding="utf-8")
    runner = EDITOR_PY_DIR / "headless_runner.py"
    # Forward slashes: Unreal's quoted-value parser treats backslashes as escapes ("\UE_5.7" -> U+000E "_5.7").
    cmdline = (f'"{project.editor_cmd_exe}" "{project.uproject}" -run=pythonscript -script="{runner.as_posix()}" '
               f'-unattended -nullrhi -nosplash -nosound -nop4 -abslog="{log_path.as_posix()}"')
    env = dict(os.environ, AGENT_HEADLESS_REQUEST=str(request_path))
    started = time.time()
    try:
        proc = subprocess.run(cmdline, env=env, cwd=str(project.root), timeout=timeout,
                              stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ToolkitError(f"Headless run timed out after {timeout:.0f}s; log: {log_path}")
    lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines() if log_path.exists() else []
    if not response_path.exists():
        problems = [l for l in lines if logs.verbosity(l) in ("Fatal", "Error")][-30:]
        raise ToolkitError(f"Headless run produced no result (exit code {proc.returncode}); log: {log_path}\n" + "\n".join(problems))
    response = json.loads(response_path.read_text(encoding="utf-8"))
    response["log"] = lines
    response["seconds_total"] = round(time.time() - started, 1)
    request_path.unlink(missing_ok=True)
    response_path.unlink(missing_ok=True)
    return response


# ---------------------------------------------------------------------------- per-user auto-load
# UE 5.6 ignores (and deletes) hand-written Saved/Config/*/Engine.ini entries, so editors launched outside
# the toolkit (Epic launcher, double-clicked .uproject) pick the bridge up from a per-user UE_PYTHONPATH.

def with_bridge_path(value):
    """UE_PYTHONPATH value with the bridge folder added (no duplicates)."""
    parts = [p for p in (value or "").split(os.pathsep) if p]
    if os.path.normcase(str(EDITOR_PY_DIR)) not in map(os.path.normcase, parts):
        parts.insert(0, str(EDITOR_PY_DIR))
    return os.pathsep.join(parts)


def _user_env():
    if sys.platform != "win32":
        return None
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment") as key:
            return winreg.QueryValueEx(key, "UE_PYTHONPATH")[0]
    except OSError:
        return None


def _set_user_env(value):
    if sys.platform != "win32":
        raise ToolkitError("`ue.py bridge install` is Windows-only for now. Elsewhere, add "
                           f"{EDITOR_PY_DIR} to UE_PYTHONPATH in your shell profile.")
    import ctypes
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, "Environment", 0, winreg.KEY_SET_VALUE) as key:
        if value:
            winreg.SetValueEx(key, "UE_PYTHONPATH", 0, winreg.REG_EXPAND_SZ, value)
        else:
            winreg.DeleteValue(key, "UE_PYTHONPATH")
    # Tell Explorer & co. so newly launched programs see the change without a re-login.
    result = ctypes.c_ulong()
    ctypes.windll.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "Environment", 0x0002, 5000, ctypes.byref(result))


def is_installed():
    current = _user_env() or ""
    return os.path.normcase(str(EDITOR_PY_DIR)) in map(os.path.normcase, current.split(os.pathsep))


def install():
    if is_installed():
        return False
    _set_user_env(with_bridge_path(_user_env()))
    return True


def uninstall():
    if not is_installed():
        return False
    keep = [p for p in _user_env().split(os.pathsep) if p and os.path.normcase(p) != os.path.normcase(str(EDITOR_PY_DIR))]
    _set_user_env(os.pathsep.join(keep))
    return True
