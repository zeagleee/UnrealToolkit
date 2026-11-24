"""`ue.py net`: a multiplayer session as separate game processes, each with its own Python bridge.

A listen (or dedicated) server and N clients run as `UnrealEditor.exe <project> -game` / `-server` processes, headless
(-nullrhi) unless named in `render`. Each is started with `-UETKBridge=<name>`, so `ue.py --instance <name> py ...`
(and exec, wait, render) talks to it, and test scripts drive them with `NetSession`. Separate processes are the real
thing: in-process multi-player PIE can't travel between maps and shares one Python interpreter.

Session state: <Project>/Saved/UnrealToolkit/net/session.json; per-process logs next to it.
"""
import configparser
import json
import os
import re
import time

from . import build, procs, state
from .bridge import Bridge, with_bridge_path
from .project import Project, ToolkitError

SNAPSHOT = "net"  # `start --protect` keeps Saved/SaveGames under this snapshot name


def _dir(project):
    path = project.out_dir / "net"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _session_file(project):
    return _dir(project) / "session.json"


def read_session(project):
    try:
        return json.loads(_session_file(project).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def default_map(project):
    """GameDefaultMap from Config/DefaultEngine.ini, as a package path."""
    ini = project.root / "Config" / "DefaultEngine.ini"
    parser = configparser.ConfigParser(strict=False, interpolation=None)
    parser.optionxform = str
    try:
        parser.read_string(ini.read_text(encoding="utf-8-sig"))
        value = parser.get("/Script/EngineSettings.GameMapsSettings", "GameDefaultMap", fallback="")
    except (OSError, configparser.Error):
        value = ""
    return value.split(".")[0] or None


def _instance(project, name):
    return Project(str(project.uproject), instance=name)


def _wait_bridge(project, name, pid, timeout):
    bridge = Bridge(_instance(project, name))
    started = time.time()
    while time.time() - started < timeout:
        if not procs.pid_alive(pid):
            raise ToolkitError(f"{name} (pid {pid}) exited during startup; see `ue.py net logs --errors`.")
        bridge_state, info = bridge.state()
        if bridge_state == "ok" and info.get("pid") == pid:
            return time.time() - started
        time.sleep(1.0)
    raise ToolkitError(f"{name}: no bridge after {timeout}s (is `ue.py bridge install` or UE_PYTHONPATH in place?)")


def start(project, map_path=None, clients=1, dedicated=False, render=(), port=7777, extra_args="", steam=False,
          protect=False, timeout=300, say=print):
    """Launch the server, then the clients, wait for every bridge and for the clients to join. Returns the session."""
    previous = read_session(project)
    if previous and any(procs.pid_alive(i["pid"]) for i in previous["instances"].values()):
        raise ToolkitError("A net session is already running (`ue.py net status`); `ue.py net stop` first.")
    map_path = map_path or default_map(project)
    if not map_path:
        raise ToolkitError("No map: pass --map /Game/... (the project has no GameDefaultMap).")
    if protect:
        state.snapshot_save(project, state.DEFAULT_PROTECT, SNAPSHOT)
    env = build.ubt_env(project, dict(os.environ, UE_PYTHONPATH=with_bridge_path(os.environ.get("UE_PYTHONPATH"))))
    out = _dir(project)
    names = ["server"] + [f"client{i}" for i in range(1, clients + 1)]
    session = {"map": map_path, "port": port, "dedicated": dedicated, "started": time.strftime("%Y-%m-%d %H:%M:%S"),
               "protect": protect, "instances": {}}
    for index, name in enumerate(names):
        log = out / f"{name}.log"
        if log.exists():
            log.unlink()
        rendered = name in render
        if name == "server":
            target = f'"{map_path}"' if dedicated else f'"{map_path}?listen"'
            mode = "-server" if dedicated else "-game"
            flags = f"-port={port}"
        else:
            target, mode, flags = f"127.0.0.1:{port}", "-game", ""
        view = (f"-windowed -ResX=960 -ResY=540 -WinX={40 + 980 * ((index - 1) % 2)} -WinY={40 + 580 * ((index - 1) // 2)}"
                if rendered else "-nullrhi")
        cmdline = (f'"{project.editor_exe}" "{project.uproject}" {target} {mode} {flags} {view} -nosound -unattended '
                   f'{"" if steam else "-nosteam"} -NoVerifyGC -UETKBridge={name} -abslog="{log.as_posix()}" {extra_args}')
        cmdline = re.sub(r"\s+", " ", cmdline).strip()
        with_role = "dedicated server" if dedicated and name == "server" else ("listen server" if name == "server" else "client")
        # Only accept a heartbeat from this session's process.
        try:
            Bridge(_instance(project, name)).server_file.unlink()
        except OSError:
            pass
        pid = procs.launch_detached(cmdline, env=env, cwd=str(project.root))
        session["instances"][name] = {"pid": pid, "role": with_role, "log": str(log), "rendered": rendered,
                                      "cmdline": cmdline}
        _session_file(project).write_text(json.dumps(session, indent=2), encoding="utf-8")
        say(f"{name}: pid {pid}, {with_role}{', rendering' if rendered else ''}")
        waited = _wait_bridge(project, name, pid, timeout)
        say(f"{name}: bridge up after {waited:.0f}s")
    expected = clients + (0 if dedicated else 1)
    joined = wait_players(project, expected, timeout=min(timeout, 120))
    say(f"server sees {len(joined)} player(s): {', '.join(joined)}")
    return session


def call(project, name, code, timeout=120):
    """Run code in one instance; returns the result (raises ToolkitError with the traceback on failure)."""
    response = Bridge(_instance(project, name)).call(code, timeout=timeout, quiet=True)
    if not response["ok"]:
        raise ToolkitError(f"{name}: {response['error'].strip()}")
    return response["result"]


def wait_players(project, count, timeout=120, name="server"):
    """Block until the server's game state lists `count` players; returns their names."""
    deadline = time.time() + timeout
    players = []
    while time.time() < deadline:
        players = call(project, name, "ueh.net_info()['players']", timeout=30) or []
        if len(players) >= count:
            return players
        time.sleep(1.0)
    raise ToolkitError(f"server sees {len(players)} of {count} players after {timeout}s: {players}")


def status(project):
    """[{name, pid, alive, role, bridge, net}] for the session's instances."""
    session = read_session(project)
    if not session:
        return []
    rows = []
    for name, inst in session["instances"].items():
        alive = procs.pid_alive(inst["pid"])
        row = {"name": name, "pid": inst["pid"], "alive": alive, "role": inst["role"]}
        if alive:
            bridge_state, _ = Bridge(_instance(project, name)).state()
            row["bridge"] = bridge_state
            if bridge_state == "ok":
                try:
                    row["net"] = call(project, name, "ueh.net_info()", timeout=20)
                except ToolkitError as exc:
                    row["net"] = str(exc).splitlines()[-1]
        rows.append(row)
    return rows


def stop(project, say=print):
    session = read_session(project)
    if not session:
        say("No net session.")
        return
    for name, inst in session["instances"].items():
        if procs.pid_alive(inst["pid"]):
            procs.kill_tree(inst["pid"])
            procs.wait_exit(inst["pid"], 30)
            say(f"{name}: stopped (pid {inst['pid']})")
        else:
            say(f"{name}: had already exited")
    if session.get("protect"):
        for line in state.describe(state.snapshot_restore(project, SNAPSHOT)):
            say(f"Restored {line}")
    _session_file(project).unlink()


_LINE_TIME = re.compile(r"^\[(\d{4}\.\d\d\.\d\d-\d\d\.\d\d\.\d\d:\d{3})\]")


def merged_log(project, grep=None, errors=False, tail=200):
    """Every instance's log lines, merged by timestamp and prefixed with the instance name."""
    from . import logs
    session = read_session(project) or {"instances": {}}
    pattern = re.compile(grep, re.I) if grep else None
    merged = []
    for name, inst in session["instances"].items():
        try:
            text = open(inst["log"], encoding="utf-8", errors="replace").read()
        except OSError:
            continue
        last = ""
        for line in text.splitlines():
            match = _LINE_TIME.match(line)
            last = match.group(1) if match else last
            if errors and not logs.is_problem(line):
                continue
            if pattern and not pattern.search(line):
                continue
            merged.append((last, name, line))
    merged.sort(key=lambda item: item[0])
    lines = [f"{name:8} {line}" for _, name, line in merged]
    return lines[-tail:] if tail else lines


class Instance:
    """One game process of the session, for test scripts: py(), exec(), info(), log()."""

    def __init__(self, project, name):
        self.project, self.name = project, name

    def py(self, code, timeout=120):
        return call(self.project, self.name, code, timeout)

    def exec(self, command, timeout=60):
        return call(self.project, self.name, f"ueh.console({command!r})", timeout)

    def info(self):
        return call(self.project, self.name, "ueh.net_info()", 30)

    def wait(self, condition, timeout=60):
        """Block until a Python expression is true in this process (polled once a second)."""
        deadline = time.time() + timeout
        while time.time() < deadline:
            if call(self.project, self.name, f"bool({condition})", 30):
                return True
            time.sleep(1.0)
        raise ToolkitError(f"{self.name}: `{condition}` still false after {timeout}s")

    def __repr__(self):
        return f"<Instance {self.name}>"


class NetSession:
    """The running session, handed to `ue.py net run` scripts as `net`:
        net.server.py("ueh.net_info()"); net.clients[0].exec("SomeCheat 1"); net.wait_players(3)
        net["client2"].wait("ueh.player()[1] is not None"); net.log(grep="Error")"""

    def __init__(self, project):
        session = read_session(project)
        if not session:
            raise ToolkitError("No net session (`ue.py net start` first).")
        self.project, self.session = project, session
        self.instances = {name: Instance(project, name) for name in session["instances"]}
        self.server = self.instances["server"]
        self.clients = [inst for name, inst in self.instances.items() if name != "server"]

    def __getitem__(self, name):
        return self.instances[name]

    def wait_players(self, count, timeout=120):
        return wait_players(self.project, count, timeout)

    def log(self, grep=None, errors=False, tail=200):
        return merged_log(self.project, grep, errors, tail)
