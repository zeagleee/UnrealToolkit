"""Editor lifecycle: start with the bridge loaded, wait until usable, stop safely, restart."""
import contextlib
import os
import time

from . import build, logs, procs, state
from .bridge import Bridge, with_bridge_path
from .project import ToolkitError

READY_CHECK = ("{'ready': not unreal.AssetRegistryHelpers.get_asset_registry().is_loading_assets(), "
               "'level': ueh.level(), 'pie': ueh.is_pie()}")


def _say(message):
    print(message, flush=True)


def _bridge_pid(project):
    state, info = Bridge(project).state()
    return info["pid"] if state in ("ok", "busy") else None


def current_level(project):
    """Level open in the running editor, or None (no bridge / unsaved temp level)."""
    if not _bridge_pid(project):
        return None
    with contextlib.suppress(ToolkitError):
        level = Bridge(project).call("ueh.level()", timeout=30, quiet=True).get("result")
        if level and not level.startswith("/Temp/"):
            return level
    return None


def start(project, map_path=None, do_build=True, wait=True, timeout=900, no_throttle=False):
    running = procs.editor_pids(project)
    if running:
        if _bridge_pid(project) in running:
            _say(f"Editor already running (pid {running[0]}) with the bridge."
                 + (" --no-throttle needs a fresh start (`ue.py editor restart --no-throttle`)."
                    if no_throttle and not state.throttle_claimed(project) else ""))
            return running[0]
        raise ToolkitError(f"Editor already running (pid {running}) but the bridge is not up. If it is still starting, "
                           "use `ue.py editor wait`; otherwise close it (`ue.py editor stop --discard` if it has nothing "
                           "to save) and start it again.")
    # A previous session that ended outside the toolkit (closed by hand, crashed) may have left things to undo.
    state.after_editor_exit(project, _say)
    if do_build and build.ubt_build(project) != 0:
        raise ToolkitError("Build failed; not starting the editor.")
    if no_throttle:
        state.disable_throttle(project, _say)
    # The editor runs UnrealBuildTool.exe itself (Live Coding, in-editor compiles), so give it the bundled .NET too.
    env = build.ubt_env(project, dict(os.environ, UE_PYTHONPATH=with_bridge_path(os.environ.get("UE_PYTHONPATH"))))
    with contextlib.suppress(OSError):
        Bridge(project).server_file.unlink()  # only accept a heartbeat from the new session
    cmdline = f'"{project.editor_exe}" "{project.uproject}"' + (f' "{map_path}"' if map_path else "")
    pid = procs.launch_detached(cmdline, env=env, cwd=str(project.root))
    _say(f"Launched editor (pid {pid}){' with map ' + map_path if map_path else ''}.")
    if wait:
        wait_ready(project, pid, timeout)
    return pid


def wait_ready(project, pid, timeout=900):
    bridge = Bridge(project)
    started = time.time()
    next_report = started + 20
    titles = set()
    while True:
        elapsed = time.time() - started
        if not procs.pid_alive(pid):
            crash = logs.latest_crash(project)
            detail = f"\nCrash: {crash.get('ErrorMessage', '')}\n{crash.get('CallStack', '')[:2000]}" if crash else ""
            log = logs.active_log(project)
            errors = [l for l in logs.read_from(log, 0).splitlines() if logs.verbosity(l) in ("Fatal", "Error")][-15:]
            raise ToolkitError(f"Editor exited during startup after {elapsed:.0f}s.{detail}\n" + "\n".join(errors))
        state, info = bridge.state()
        if state == "ok" and info["pid"] == pid:
            with contextlib.suppress(ToolkitError):
                response = bridge.call(READY_CHECK, timeout=30, quiet=True)
                if response["ok"] and response["result"]["ready"]:
                    _say(f"Editor ready after {elapsed:.0f}s (level {response['result']['level']}).")
                    return response["result"]
        for window in procs.windows_of(pid):
            if window["title"] and window["title"] not in titles:
                titles.add(window["title"])
                _say(f"  window opened: {window['title']!r}")
        if elapsed > timeout:
            raise ToolkitError(f"Editor not ready after {timeout}s (bridge state: {state}). "
                               "Check `ue.py windows` / `ue.py screenshot` for a blocking dialog.")
        if time.time() > next_report:
            next_report += 20
            tail = logs.read_from(logs.active_log(project), max(0, logs.size_of(logs.active_log(project)) - 4000))
            last = tail.strip().splitlines()[-1:] or ["(no log yet)"]
            _say(f"  ... {elapsed:.0f}s: {last[0][:160]}")
        time.sleep(1.0)


def stop(project, save=False, discard=False, timeout=180):
    pids = procs.editor_pids(project)
    if not pids:
        _say("Editor is not running.")
        state.after_editor_exit(project, _say)
        return
    bridge_pid = _bridge_pid(project)
    if bridge_pid not in pids:
        if discard:
            kill(project)
            return
        raise ToolkitError("The running editor has no bridge, so unsaved changes can't be checked. Close it by hand, "
                           "or `ue.py editor stop --discard` to force-close it (unsaved changes are lost).")
    bridge = Bridge(project)
    unsaved = bridge.call("ueh.pie_stop() if ueh.is_pie() else None\nueh.dirty()", timeout=60)["result"] or []
    if unsaved and save:
        result = bridge.call("ueh.save()", timeout=900)["result"]
        if result["still_dirty"]:
            raise ToolkitError("Could not save: " + ", ".join(result["still_dirty"]))
        _say(f"Saved {len(result['saved'])} package(s).")
    elif unsaved and discard:
        _say(f"Discarding unsaved changes in {len(unsaved)} package(s).")
        kill(project)
        return
    elif unsaved:
        raise ToolkitError("Unsaved changes in:\n  " + "\n  ".join(unsaved) + "\nRe-run with --save or --discard.")
    bridge.call("unreal.SystemLibrary.quit_editor()", timeout=60)
    if procs.wait_exit(bridge_pid, timeout):
        _say("Editor closed.")
        state.after_editor_exit(project, _say)  # after the exit: the editor writes its settings on the way out
        return
    titles = [w["title"] for w in procs.windows_of(bridge_pid) if w["title"]]
    raise ToolkitError(f"Editor still running after {timeout}s; open windows: {titles}. Use `ue.py kill` to force it.")


def restart(project, save=False, discard=False, do_build=True, map_path=None, timeout=900, no_throttle=False):
    map_path = map_path or current_level(project)
    stop(project, save=save, discard=discard)
    return start(project, map_path=map_path, do_build=do_build, timeout=timeout, no_throttle=no_throttle)


def kill(project):
    victims = procs.project_processes(project)
    for proc in victims:
        procs.kill_tree(proc["pid"])
        _say(f"Killed {proc['name']} (pid {proc['pid']}, {proc['kind']}).")
    for proc in victims:
        procs.wait_exit(proc["pid"], 30)
    if not victims:
        _say("No Unreal processes for this project.")
    state.after_editor_exit(project, _say)
