"""Agent bridge: lets external tools run Python inside a live Unreal Editor.

Runs inside UnrealEditor. Requests are JSON files dropped into Saved/AgentBridge/inbox by the
`ue.py` client; each one is executed on the game thread from a Slate post-tick callback and the
response is written to Saved/AgentBridge/outbox. File IPC keeps it free of sockets/firewalls.

Scenarios (cells with a top-level `yield`, see scenario.py) keep running across ticks: the response is
written when they end, their output so far goes to outbox/<id>.progress, and inbox/<id>.cancel stops one.
Every namespace also gets the project's own helpers from <Project>/.uetk/helpers.py (reloaded on change).

Started automatically by init_unreal.py when this folder is on the editor's Python path
(UE_PYTHONPATH, or AdditionalPaths installed by `ue.py bridge install`). Manual start from the
editor's Python console:  import agent_bridge; agent_bridge.start()
"""
import ast
import contextlib
import importlib
import importlib.util
import io
import json
import linecache
import os
import re
import sys
import time
import traceback

import unreal

import scenario

POLL_INTERVAL = 0.05
HEARTBEAT_INTERVAL = 1.0
STALE_RESPONSE_SECONDS = 600
MAX_RESULT_ITEMS = 5000
PROGRESS_INTERVAL = 0.25
PROJECT_HELPERS = os.path.join(".uetk", "helpers.py")
PROJECT_MODULE = "uetk_project_helpers"

_server = None


def instance_name():
    """`-UETKBridge=<name>` on the command line (game processes launched by `ue.py net`), else None."""
    match = re.search(r"-uetkbridge=([\w.-]+)", unreal.SystemLibrary.get_command_line(), re.I)
    return match.group(1) if match else None


def _is_dedicated_server():
    return re.search(r"(^|\s)-server(\s|$)", unreal.SystemLibrary.get_command_line(), re.I) is not None


def bridge_dir():
    """Saved/AgentBridge for the editor; Saved/AgentBridge-<name> for a named instance (`ue.py --instance <name>`)."""
    saved = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir())
    name = instance_name()
    return os.path.join(saved, "AgentBridge-" + name if name else "AgentBridge")


def _write_json_atomic(path, data):
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=1)
    os.replace(tmp, path)


# --------------------------------------------------------------------------- value conversion

def to_jsonable(value, depth=0):
    """Convert Unreal/Python values into JSON-friendly data for the client."""
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if depth > 8:
        return repr(value)
    if isinstance(value, unreal.Object):
        return value.get_path_name()
    if isinstance(value, (unreal.Name, unreal.Text)):
        return str(value)
    if isinstance(value, unreal.EnumBase):
        return value.name
    if isinstance(value, unreal.StructBase):
        try:
            return value.export_text()
        except Exception:
            return str(value)
    if isinstance(value, (dict, unreal.Map)):
        return {str(to_jsonable(k, depth + 1)): to_jsonable(v, depth + 1) for k, v in value.items()}
    if isinstance(value, (list, tuple, set, frozenset, unreal.Array, unreal.FixedArray, unreal.Set)):
        items = list(value)
        out = [to_jsonable(v, depth + 1) for v in items[:MAX_RESULT_ITEMS]]
        if len(items) > MAX_RESULT_ITEMS:
            out.append("... %d more items" % (len(items) - MAX_RESULT_ITEMS))
        return out
    if isinstance(value, type):
        return "<class %s>" % value.__name__
    return repr(value)


# --------------------------------------------------------------------------- execution

# Helper modules pre-imported into every namespace and reloaded when their file changes. Support modules come
# first so a reload of `vfx` sees the new `vfx_assets`; only the names in NAMESPACE_MODULES go into the namespace.
HELPER_MODULES = ("ueh", "uprop", "vfx_assets", "vfx", "bpedit", "mat", "inputs", "importer")
NAMESPACE_MODULES = ("ueh", "vfx", "bpedit", "mat", "inputs", "importer")


def new_namespace():
    ns = {"__name__": "__agent__", "unreal": unreal}
    for name in NAMESPACE_MODULES:
        try:
            ns[name] = importlib.import_module(name)
        except Exception:
            unreal.log_warning("AgentBridge: could not import %s helpers:\n%s" % (name, traceback.format_exc()))
    load_project_helpers(ns)
    return ns


def project_helpers_path():
    """<Project>/.uetk/helpers.py if the project has one."""
    root = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_dir())
    path = os.path.normpath(os.path.join(root, PROJECT_HELPERS))
    return path if os.path.isfile(path) else None


def _mtime(path):
    try:
        return os.path.getmtime(path) if path else None
    except OSError:
        return None


def load_project_helpers(namespace):
    """(Re)import the project's .uetk/helpers.py into `namespace`: the module as `proj`, plus its public names
    (its __all__, else the functions and classes it defines). Names from an earlier version are removed first."""
    for name in namespace.pop("__uetk_project_names__", []):
        namespace.pop(name, None)
    namespace.pop("proj", None)
    path = project_helpers_path()
    if not path:
        sys.modules.pop(PROJECT_MODULE, None)
        return None
    module = importlib.util.module_from_spec(importlib.util.spec_from_loader(PROJECT_MODULE, loader=None, origin=path))
    module.__file__ = path
    sys.modules[PROJECT_MODULE] = module
    try:
        # Compiled from source every time: a cached .pyc is trusted when size and mtime-second match, so a quick
        # same-length edit would otherwise load the old code.
        with open(path, "r", encoding="utf-8") as f:
            source = f.read()
        exec(compile(source, path, "exec"), module.__dict__)
    except Exception:
        unreal.log_error("AgentBridge: project helpers %s failed to load:\n%s" % (path, traceback.format_exc()))
        return _mtime(path)
    names = list(getattr(module, "__all__", None) or
                 [n for n, v in vars(module).items()
                  if not n.startswith("_") and getattr(v, "__module__", None) == PROJECT_MODULE])
    for name in names:
        namespace[name] = getattr(module, name)
    namespace["proj"] = module
    namespace["__uetk_project_names__"] = names
    return _mtime(path)


def execute(code, namespace, filename="<agent>"):
    """Run `code` like a REPL cell: statements are exec'd, a trailing expression is returned.

    A cell with a top-level `yield` (or whose trailing expression is a generator) is a scenario (see
    scenario.py): the response then carries the generator under "_generator" for the caller to run."""
    out = io.StringIO()
    result = None
    generator = None
    error = None
    start = time.time()
    linecache.cache[filename] = (len(code), None, code.splitlines(True), filename)
    try:
        tree = ast.parse(code, filename, "exec")
        with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
            if scenario.has_top_level_yield(tree):
                exec(compile(scenario.wrap_scenario(tree), filename, "exec"), namespace)
                generator = namespace.pop(scenario.SCENARIO_FN)()
            else:
                last_expr = None
                if tree.body and isinstance(tree.body[-1], ast.Expr):
                    last_expr = ast.Expression(tree.body.pop().value)
                if tree.body:
                    exec(compile(tree, filename, "exec"), namespace)
                if last_expr is not None:
                    result = eval(compile(last_expr, filename, "eval"), namespace)
                if scenario.is_generator(result):
                    generator, result = result, None
    except BaseException:  # SystemExit/KeyboardInterrupt from user code must not kill the bridge
        error = traceback.format_exc()
        generator = None
    try:
        result_repr = None if result is None else repr(result)
    except Exception as exc:
        result_repr = "<repr failed: %s>" % exc
    response = {
        "ok": error is None,
        "stdout": out.getvalue(),
        "result": to_jsonable(result),
        "result_repr": result_repr,
        "error": error,
        "seconds": round(time.time() - start, 3),
    }
    if generator is not None:
        response["_generator"] = generator
    return response


def _game_seconds():
    """PIE game time for scenarios' plain-number waits (None outside PIE: they use the wall clock)."""
    try:
        import ueh
        return ueh.game_time()
    except Exception:
        return None


# --------------------------------------------------------------------------- server

def _detect_log_file():
    cmdline = unreal.SystemLibrary.get_command_line()
    for token in ("-abslog=", "-ABSLOG="):
        if token in cmdline:
            value = cmdline.split(token, 1)[1]
            value = value[1:].split('"', 1)[0] if value.startswith('"') else value.split(" ", 1)[0]
            return os.path.abspath(value)
    log_dir = unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_log_dir())
    try:
        logs = [os.path.join(log_dir, n) for n in os.listdir(log_dir) if n.endswith(".log") and "-backup-" not in n]
        return max(logs, key=os.path.getmtime) if logs else None
    except OSError:
        return None


class _Server(object):
    def __init__(self):
        self.dir = bridge_dir()
        self.inbox = os.path.join(self.dir, "inbox")
        self.outbox = os.path.join(self.dir, "outbox")
        for d in (self.inbox, self.outbox):
            os.makedirs(d, exist_ok=True)
        for name in os.listdir(self.inbox):  # requests addressed to a previous editor session
            with contextlib.suppress(OSError):
                os.remove(os.path.join(self.inbox, name))
        self.namespace = new_namespace()
        self.helpers_mtime = self._helpers_mtime()
        self.project_helpers_mtime = _mtime(project_helpers_path())
        self.tasks = {}  # request id -> scenario.Task still running
        self.started = time.time()
        self.last_poll = 0.0
        self.last_beat = 0.0
        self.last_cleanup = 0.0
        self.requests_handled = 0
        self.last_error, self.last_error_time = None, 0.0
        self.own_mtime = self._own_mtime()
        unreal.SystemLibrary.execute_console_command(None, "FLUSHLOG")
        self.log_file = _detect_log_file()
        self._start_ticking()
        self.heartbeat()

    def _start_ticking(self):
        """Slate's post-tick in the editor and game processes. A dedicated server runs no Slate, so there the
        UETKEditor plugin's engine-loop ticker drives the bridge."""
        self.ticker, self.tick_handle = None, None
        if _is_dedicated_server():
            ticker = getattr(unreal, "UETKTicker", None)
            if ticker is not None:
                self.ticker = ticker.get()
                self.ticker.on_tick.add_callable(self.tick)
                return
            unreal.log_error("AgentBridge: a dedicated server needs the UETKEditor plugin to tick "
                             "(`ue.py plugins install`); requests won't be answered")
        self.tick_handle = unreal.register_slate_post_tick_callback(self.tick)

    def _stop_ticking(self):
        if self.ticker is not None:
            self.ticker.on_tick.remove_callable(self.tick)
        elif self.tick_handle is not None:
            unreal.unregister_slate_post_tick_callback(self.tick_handle)

    @staticmethod
    def _own_mtime():
        """The bridge's own source files: editing either hot-swaps the bridge."""
        return tuple(_mtime(path) for path in (__file__, scenario.__file__))

    def _helpers_mtime(self):
        """{module name: file mtime} of the loaded helper modules."""
        out = {}
        for name in HELPER_MODULES:
            mod = sys.modules.get(name)
            try:
                out[name] = os.path.getmtime(mod.__file__) if mod else None
            except (OSError, AttributeError):
                out[name] = None
        return out

    def _reload_helpers_if_changed(self):
        mtimes = self._helpers_mtime()
        changed = [n for n in HELPER_MODULES if mtimes[n] is not None and mtimes[n] != self.helpers_mtime.get(n)]
        if changed:
            # Reload from the first changed module on, so modules that import it pick up the new version.
            for name in HELPER_MODULES[HELPER_MODULES.index(changed[0]):]:
                if name not in sys.modules:
                    continue
                try:
                    module = importlib.reload(sys.modules[name])
                    if name in NAMESPACE_MODULES:
                        self.namespace[name] = module
                    unreal.log("AgentBridge: reloaded %s helpers" % name)
                except Exception:
                    unreal.log_error("AgentBridge: %s reload failed:\n%s" % (name, traceback.format_exc()))
            self.helpers_mtime = self._helpers_mtime()
        # A helper module added to the toolkit while this editor ran (or one that failed to import): try again.
        for name in NAMESPACE_MODULES:
            if name not in self.namespace and os.path.isfile(os.path.join(os.path.dirname(__file__), name + ".py")):
                try:
                    self.namespace[name] = importlib.import_module(name)
                    self.helpers_mtime = self._helpers_mtime()
                    unreal.log("AgentBridge: loaded %s helpers" % name)
                except Exception:
                    pass
        project_mtime = _mtime(project_helpers_path())
        if project_mtime != self.project_helpers_mtime:
            self.project_helpers_mtime = load_project_helpers(self.namespace)
            unreal.log("AgentBridge: %s project helpers" % ("loaded" if project_mtime else "removed"))

    def heartbeat(self, stopped=False):
        self.last_beat = time.time()
        _write_json_atomic(os.path.join(self.dir, "server.json"), {
            "pid": os.getpid(),
            "started": self.started,
            "heartbeat": self.last_beat,
            "stopped": stopped,
            "requests_handled": self.requests_handled,
            "project": unreal.Paths.convert_relative_path_to_full(unreal.Paths.get_project_file_path()),
            "engine_version": unreal.SystemLibrary.get_engine_version(),
            "log_file": self.log_file,
            "bridge_file": os.path.abspath(__file__),
            "scenarios_running": sorted(self.tasks),
        })

    def tick(self, delta_seconds):
        try:
            now = time.time()
            if now - self.last_beat >= HEARTBEAT_INTERVAL:
                if self._own_mtime() != self.own_mtime:
                    _reload_self()  # bridge code was edited: hot-swap without restarting the editor
                    return
                self.heartbeat()
            for request_id in list(self.tasks):  # scenarios advance every tick, between the engine's frames
                self.step_task(request_id)
            if now - self.last_poll < POLL_INTERVAL:
                return
            self.last_poll = now
            entries = sorted(os.scandir(self.inbox), key=lambda e: e.name)
            for entry in entries:
                if entry.name.endswith(".cancel"):
                    self.cancel(entry.path)
            for entry in entries:
                if entry.name.endswith(".json"):
                    self.handle(entry.path)
            if now - self.last_cleanup > 60:
                self.last_cleanup = now
                self.cleanup_outbox(now)
        except Exception:
            message = traceback.format_exc()
            if message != self.last_error or time.time() - self.last_error_time > 30:  # don't flood the log at 20 Hz
                self.last_error, self.last_error_time = message, time.time()
                unreal.log_error("AgentBridge tick failed:\n" + message)

    def handle(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                request = json.load(f)
        except (OSError, ValueError):
            return  # partially visible file; retry next poll
        finally:
            with contextlib.suppress(OSError):
                os.remove(path)
        request_id = request.get("id") or os.path.splitext(os.path.basename(path))[0]
        self._reload_helpers_if_changed()
        response = execute(request.get("code", ""), self.namespace, "<agent-%s>" % request_id)
        generator = response.pop("_generator", None)
        if generator is None:
            self.finish(request_id, response)
            return
        task = scenario.Task(generator, request_id, seconds_clock=_game_seconds, to_jsonable=to_jsonable)
        task.out.write(response.get("stdout") or "")
        task.progress_written, task.progress_time = 0, 0.0
        self.tasks[request_id] = task
        self.step_task(request_id)  # up to the first wait right away

    def step_task(self, request_id):
        task = self.tasks.get(request_id)
        if task is None:
            return
        try:
            response = task.step()
        except Exception:
            response = task.cancel("failed in the bridge:\n" + traceback.format_exc())
        if response is None:
            self.write_progress(task)
            return
        self.tasks.pop(request_id, None)
        self.finish(request_id, response)

    def write_progress(self, task):
        """The scenario's output so far, for the client to stream (outbox/<id>.progress)."""
        text = task.stdout
        now = time.time()
        if len(text) == task.progress_written or now - task.progress_time < PROGRESS_INTERVAL:
            return
        task.progress_written, task.progress_time = len(text), now
        path = os.path.join(self.outbox, task.id + ".progress")
        with contextlib.suppress(OSError):
            with open(path + ".tmp", "w", encoding="utf-8") as f:
                f.write(text)
            os.replace(path + ".tmp", path)

    def cancel(self, path):
        """inbox/<id>.cancel: the client gave up waiting (timeout, Ctrl+C); stop that scenario."""
        request_id = os.path.splitext(os.path.basename(path))[0]
        with contextlib.suppress(OSError):
            os.remove(path)
        task = self.tasks.pop(request_id, None)
        if task is not None:
            unreal.log_warning("AgentBridge: scenario %s cancelled by the client" % request_id)
            self.finish(request_id, task.cancel())

    def finish(self, request_id, response):
        response["id"] = request_id
        self.requests_handled += 1
        unreal.SystemLibrary.execute_console_command(None, "FLUSHLOG")  # so the client sees this call's log lines
        _write_json_atomic(os.path.join(self.outbox, request_id + ".json"), response)
        with contextlib.suppress(OSError):
            os.remove(os.path.join(self.outbox, request_id + ".progress"))

    def cleanup_outbox(self, now):
        for entry in os.scandir(self.outbox):
            with contextlib.suppress(OSError):
                if now - entry.stat().st_mtime > STALE_RESPONSE_SECONDS:
                    os.remove(entry.path)

    def stop(self, final=True):
        with contextlib.suppress(Exception):
            self._stop_ticking()
        reason = "stopped: the bridge shut down" if final else "stopped: the bridge reloaded (its source changed)"
        for request_id in list(self.tasks):  # answer waiting clients instead of leaving them to time out
            with contextlib.suppress(Exception):
                self.finish(request_id, self.tasks.pop(request_id).cancel(reason))
        if final:
            with contextlib.suppress(Exception):
                self.heartbeat(stopped=True)


def _should_skip():
    if os.environ.get("AGENT_BRIDGE", "1") == "0":
        return "AGENT_BRIDGE=0"
    if instance_name():
        return None  # a named instance (ue.py net) has its own folder, so -nullrhi is fine
    cmdline = unreal.SystemLibrary.get_command_line().lower()
    for flag in ("-run=", "-nullrhi", "-unattended"):
        if flag in cmdline:
            return "command line has " + flag  # commandlets / headless test runs must not take over the bridge
    return None


def start():
    global _server
    if _server is not None:
        return _server
    reason = _should_skip()
    if reason:
        unreal.log("AgentBridge: not starting (%s)" % reason)
        return None
    _server = _Server()
    with contextlib.suppress(Exception):
        unreal.register_python_shutdown_callback(stop)
    unreal.log("AgentBridge: listening on %s" % _server.dir)
    return _server


def stop():
    global _server
    if _server is not None:
        _server.stop()
        _server = None


def _reload_self():
    """Hot-swap the bridge after its source changed. If the new code fails (a file saved mid-edit, say), put the old
    server back and try again at the next change, so a half-finished edit can't leave the editor without a bridge."""
    global _server
    old = _server
    if old is not None:
        old.stop(final=False)
    try:
        importlib.reload(scenario)
        module = importlib.reload(sys.modules[__name__])  # re-runs this file: its `_server` starts as None
        if module.start() is None:
            raise RuntimeError("the reloaded bridge declined to start")
        unreal.log("AgentBridge: reloaded after source change")
    except Exception:
        unreal.log_error("AgentBridge: reload failed; keeping the previous bridge until the next change:\n"
                         + traceback.format_exc())
        if old is not None:
            module = sys.modules[__name__]
            module._server = old
            old.own_mtime = old._own_mtime()  # retry only when the files change again
            old._start_ticking()
            old.heartbeat()
