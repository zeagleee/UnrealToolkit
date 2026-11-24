"""ue.py - drive Unreal Engine for any project from the command line (no manual steps).

  python <toolkit folder>/ue.py [--project PATH] <command> [options]
  The project is the nearest *.uproject above the current folder unless --project is given.

Build & code   build, genproject, livecoding, test, package
Editor         editor start|stop|restart|status, kill, windows, screenshot
In-editor      py (Python in the live editor or --headless; scenarios with `yield` waits), wait, exec, pie, save
Input          press KEY [--hold s], mouse DX DY [--for s] (Enhanced Input forcing: triggers and modifiers run)
Performance    perf run [LABEL] [--exec CMD], perf compare A B, perf list (CSV profiler medians of the running game)
State          snapshot save|restore|status (pie start --protect, editor start --no-throttle restore themselves)
Diagnostics    info, log, crash, bridge install|uninstall|status
Review         bpdump (every Blueprint as readable text: variables, defaults, widget tree, event flows)
Blueprints     bp graphs|graph|compile (node ids and links for editing with `bpedit` in `ue.py py`)
VFX            vfx install|status|presets|create|preview|describe|place|modules (Niagara via the UETKVfx plugin)
Render         render view|pie|actor|asset|widget (offscreen PNGs via the UETKEditor plugin)
Plugins        plugins install|status (the toolkit's C++ editor plugins, copied into the project and built with it)
Multiplayer    net start|status|stop|logs|run (server + clients as game processes; --instance NAME targets one)
"""
import argparse
import json
import re
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from uetk import build, editor, logs, procs, state  # noqa: E402
from uetk.bridge import Bridge, install, is_installed, run_headless, uninstall  # noqa: E402
from uetk.project import Project, ToolkitError  # noqa: E402


def _print_response(resp, log_mode):
    stdout = (resp.get("stdout") or "")[resp.get("streamed", 0):]  # scenario output already shown while it ran
    if stdout.strip():
        print(stdout.rstrip())
    if resp["ok"]:
        result = resp.get("result")
        if result is not None:
            print(result if isinstance(result, str) else _dumps(result))
    else:
        print(resp["error"].rstrip(), file=sys.stderr)
    lines = resp.get("log", [])
    if log_mode == "problems":
        lines = [l for l in lines if logs.is_problem(l) or logs.category(l) == "LogPython"]
    elif log_mode == "errors":
        lines = [l for l in lines if logs.verbosity(l) in ("Fatal", "Error")]
    elif log_mode == "none":
        lines = []
    lines = _collapse_repeats([l for l in lines if "FLUSHLOG" not in l])
    if lines:
        print("--- log ---")
        if len(lines) > MAX_LOG_LINES:
            print(f"(showing last {MAX_LOG_LINES} of {len(lines)} distinct lines; `ue.py log` has everything)")
        print("\n".join(lines[-MAX_LOG_LINES:]))
    return 0 if resp["ok"] else 1


MAX_LOG_LINES = 60
_TIMESTAMP_RE = re.compile(r"^\[[^\]]*\]\[\s*\d+\]")
_FLAT_ARRAY_RE = re.compile(r"\[\s+([^\[\]{}]*?)\s+\]")


def _dumps(value):
    """Indented JSON, but arrays of scalars (vectors, name lists) stay on one line."""
    def flatten(m):
        inner = m.group(1)
        return "[" + ", ".join(part.strip() for part in inner.split(",\n")) + "]" if len(inner) < 400 else m.group(0)
    return _FLAT_ARRAY_RE.sub(flatten, json.dumps(value, indent=2, ensure_ascii=False))


def _collapse_repeats(lines):
    """Per-frame editor spam: keep the first occurrence of each message (timestamp ignored), with a count."""
    counts, order = {}, []
    for line in lines:
        key = _TIMESTAMP_RE.sub("", line)
        if key not in counts:
            counts[key] = [line, 0]
            order.append(key)
        counts[key][1] += 1
    return [counts[k][0] + (f"  (x{counts[k][1]})" if counts[k][1] > 1 else "") for k in order]


def _read_code(args):
    if args.file:
        return Path(args.file).read_text(encoding="utf-8")
    if args.code and args.code != "-":
        return args.code
    if args.code == "-" or not sys.stdin.isatty():
        return sys.stdin.read()
    raise ToolkitError("No code given (pass CODE, -f FILE, or pipe code on stdin).")


# ---------------------------------------------------------------------------- commands

def cmd_info(p, args):
    print(f"Project : {p.name}  ({p.uproject})")
    print(f"Engine  : {p.engine_version} at {p.engine_root}  [EngineAssociation {p.engine_association!r}]")
    print(f"Targets : " + ", ".join(f"{n} ({t})" for n, t in p.targets.items()))
    rows = procs.project_processes(p)
    print("Process : " + (", ".join(f"{r['kind']} pid {r['pid']}" for r in rows) or "no editor running"))
    bridge_state, info = Bridge(p).state()
    extra = f" (pid {info['pid']}, {info.get('requests_handled', 0)} requests handled)" if bridge_state in ("ok", "busy") else ""
    print(f"Bridge  : {bridge_state}{extra}; auto-load for self-launched editors: {'installed' if is_installed() else 'not installed'}")
    print(f"Log     : {logs.active_log(p, info)}")
    return 0


def cmd_build(p, args):
    target = {"editor": p.editor_target, "game": p.game_target}.get(args.target, args.target)
    restarted_level = None
    keep_no_throttle = state.throttle_claimed(p)
    if target == p.editor_target and procs.editor_pids(p):
        if not args.restart:
            raise ToolkitError("The editor is running (Live Coding blocks editor builds). Use --restart "
                               "(add --save/--discard if there are unsaved changes) or `ue.py livecoding`.")
        restarted_level = editor.current_level(p) or ""
        editor.stop(p, save=args.save, discard=args.discard)
    code = build.ubt_build(p, target, args.config, args.platform, args.clean, args.verbose)
    if restarted_level is not None:
        if code == 0:
            editor.start(p, map_path=restarted_level or None, do_build=False, no_throttle=keep_no_throttle)
        else:
            print("Build failed; editor left closed. Fix the errors and run `ue.py editor start`.")
    return code


def cmd_genproject(p, args):
    return build.generate_project_files(p, args.verbose)


def cmd_livecoding(p, args):
    resp = Bridge(p).call("unreal.SystemLibrary.execute_console_command(ueh.editor_world(), 'LiveCoding.CompileSync')",
                          timeout=args.timeout)
    lines = [l for l in resp["log"] if "LiveCoding" in l or logs.is_problem(l)]
    print("\n".join(lines) or "(no Live Coding output in the log)")
    text = "\n".join(lines)
    if "Live coding succeeded" in text:
        print("OK: no code changes detected" if "no code changes" in text else "OK: patch applied")
        return 0
    print("FAILED (header/reflection changes need `ue.py build --restart`)")
    return 1


def cmd_test(p, args):
    return build.run_tests(p, args.filter, args.timeout, args.verbose)


def cmd_package(p, args):
    return build.package(p, args.config, args.out, args.verbose)


def cmd_editor(p, args):
    if args.action == "start":
        editor.start(p, map_path=args.map, do_build=not args.no_build, wait=not args.no_wait, timeout=args.timeout,
                     no_throttle=args.no_throttle)
    elif args.action == "stop":
        editor.stop(p, save=args.save, discard=args.discard, timeout=args.timeout)
    elif args.action == "restart":
        editor.restart(p, save=args.save, discard=args.discard, do_build=not args.no_build, map_path=args.map,
                       timeout=args.timeout, no_throttle=args.no_throttle or state.throttle_claimed(p))
    elif args.action == "wait":
        editor.wait_ready(p, _main_editor_pid(p), args.timeout)
    else:
        bridge_state, info = Bridge(p).state()
        pids = procs.editor_pids(p)
        print(f"editor: {'running pid ' + ', '.join(map(str, pids)) if pids else 'not running'}; bridge: {bridge_state}")
        if bridge_state == "ok":
            resp = Bridge(p).call(editor.READY_CHECK + " | {'dirty': ueh.dirty()}", timeout=30)
            print(json.dumps(resp["result"], indent=2))
    return 0


def cmd_kill(p, args):
    editor.kill(p)
    return 0


def cmd_py(p, args):
    code = _read_code(args)
    if args.headless:
        if procs.editor_pids(p) and not args.force:
            raise ToolkitError("The editor is running; a headless run could fight it over asset files. "
                               "Use the live editor (drop --headless) or pass --force.")
        resp = run_headless(p, code, timeout=args.timeout)
        log_mode = args.log or "errors"
    else:
        on_progress = None if args.json else (lambda text: print(text, end="", flush=True))
        resp = Bridge(p).call(code, timeout=args.timeout, on_progress=on_progress)
        log_mode = args.log or "problems"
    if args.json:
        print(json.dumps(resp, indent=2, ensure_ascii=False))
        return 0 if resp["ok"] else 1
    return _print_response(resp, log_mode)


BPDUMP_DEFAULT_SKIP = ("/Game/__ExternalActors__", "/Game/__ExternalObjects__", "/Game/Developers",
                       "/Game/Collections", "/Game/Characters/Mannequins", "/Game/LevelPrototyping",
                       "/Game/StarterContent")


def cmd_bpdump(p, args):
    from uetk import bpdump
    out = Path(args.out).resolve() if args.out else p.out_dir / "bpdump"
    raw, txt = out / "raw", out / "txt"
    for folder in (raw, txt):  # clear only our own outputs, so a reused --out folder keeps anything else
        if folder.is_dir():
            for f in folder.iterdir():
                if f.is_file() and f.suffix in (".t3d", ".json", ".txt"):
                    f.unlink()
    raw.mkdir(parents=True, exist_ok=True)
    roots = args.paths or ["/Game"]
    skip = list(BPDUMP_DEFAULT_SKIP) + list(args.skip or [])
    code = f"ueh.export_blueprints({raw.as_posix()!r}, roots={roots!r}, skip={skip!r})"
    live = bool(procs.editor_pids(p)) and not args.headless
    print(f"Exporting from {'the running editor' if live else 'a headless editor'} ...", flush=True)
    resp = Bridge(p).call(code, timeout=args.timeout) if live else run_headless(p, code, timeout=args.timeout)
    if not resp["ok"]:
        return _print_response(resp, "errors")
    report = resp.get("result") or {}
    index = bpdump.condense_dir(str(raw), str(txt))
    if args.verbose:
        print("\n".join(index))
    print(f"{report.get('blueprints', 0)} Blueprints, {report.get('levels', 0)} level Blueprints, "
          f"{report.get('data', 0)} data assets -> {txt}  (index: _index.txt; raw T3D in {raw})")
    failed = [line for line in index if "FAILED" in line]
    for pkg, err in report.get("errors", []):
        print(f"export error: {pkg}: {err}", file=sys.stderr)
    for line in failed:
        print(f"condense error: {line.strip()}", file=sys.stderr)
    return 1 if failed or report.get("errors") else 0


def _py_args(**kwargs):
    return ", ".join(f"{k}={v!r}" for k, v in kwargs.items() if v is not None)


def _parse_params(items):
    """--set key=value pairs; values parsed as JSON when they can be (numbers, lists, true/false)."""
    params = {}
    for item in items or []:
        key, _, raw = item.partition("=")
        try:
            params[key] = json.loads(raw)
        except ValueError:
            params[key] = raw
    return params


def _install_plugins(p, names):
    from uetk import plugins
    try:
        changes = plugins.install(p, names)
    except ValueError as exc:
        raise ToolkitError(str(exc))
    print("\n".join(changes) if changes else "Already up to date: " + ", ".join(plugins.with_dependencies(names)))
    if not (p.root / "Source").is_dir():
        print("note: this project has no Source/ folder (Blueprint-only). The editor asks to build the plugins on "
              "launch, which needs Visual Studio; adding any C++ class to the project also works.")
    if changes:
        cpp_only = all(c.endswith(".cpp") for c in changes)
        print("Next: `ue.py build --restart` (editor open) or `ue.py build` (closed)"
              + ("; only .cpp files changed, so `ue.py livecoding` also works" if cpp_only else ""))
    return 0


def _plugin_status(p):
    from uetk import plugins
    info = plugins.status(p)
    bridge_state, _ = Bridge(p).state()
    loaded = {}
    if bridge_state == "ok":
        code = "{n: hasattr(unreal, n + 'Library') for n in %r}" % (list(info),)
        loaded = Bridge(p).call(code, timeout=30, quiet=True).get("result") or {}
    for name, row in info.items():
        state = "built" if row["built"] else ("installed, not built" if row["installed"] else "not installed")
        if row["installed"] and not row["up_to_date"]:
            state += ", out of date (`ue.py plugins install`)"
        if name in loaded:
            state += ", loaded" if loaded[name] else ", not loaded in the running editor"
        print(f"{name:11} {state:40} {plugins.PURPOSE[name]}")
    return 0


def cmd_render(p, args):
    def vec(text):
        return [float(v) for v in text.split(",")] if text else None

    common = dict(out=args.out, exposure=args.exposure,
                  size=[int(v) for v in args.size.lower().split("x")] if args.size else None)
    orbit = dict(yaws=vec(args.yaws), pitch=args.pitch, zoom=args.zoom, fov=args.fov, columns=args.columns)
    post = [m for m in args.post.split(",") if m] if args.post else None
    if args.kind == "view":
        if args.target:
            raise ToolkitError("`render view` takes --at/--look/--rot, not a target (it renders the open level)")
        call = "ueh.render_view(%s)" % _py_args(location=vec(args.at), rotation=vec(args.rot), look_at=vec(args.look),
                                                 fov=args.fov, post=post, **common)
    elif args.kind == "pie":
        call = "ueh.render_pie(%s)" % _py_args(player_index=args.player, post=post, **common)
    else:
        if not args.target:
            raise ToolkitError(f"`render {args.kind}` needs a target: " + {
                "actor": "an actor label", "asset": "an asset path", "widget": "a Widget Blueprint path or class"}[args.kind])
        if args.kind == "actor":
            call = "ueh.render_actor(%r, %s)" % (args.target, _py_args(isolate=args.isolate or None, post=post, **orbit,
                                                                       **common))
        elif args.kind == "asset":
            call = "ueh.render_asset(%r, %s)" % (args.target, _py_args(
                floor=False if args.no_floor else None, environment=False if args.no_env else None, mesh=args.mesh,
                post=post, **orbit, **common))
        else:
            call = "ueh.render_widget(%r, %s)" % (args.target, _py_args(pie=args.pie or None, out=args.out,
                                                                        size=common["size"]))
    return _print_response(Bridge(p).call(call, timeout=args.timeout), "problems")


def cmd_net(p, args):
    from uetk import net
    if args.action == "start":
        render = [r for r in (args.render or "").split(",") if r]
        net.start(p, map_path=args.map, clients=args.clients, dedicated=args.dedicated, render=render, port=args.port,
                  extra_args=args.args or "", steam=args.steam, protect=args.protect, timeout=args.timeout)
        print("Talk to them with `ue.py --instance server py ...` (client1, client2, ...); `ue.py net stop` ends it.")
        return 0
    if args.action == "status":
        rows = net.status(p)
        if not rows:
            print("No net session.")
        for row in rows:
            info = row.get("net")
            detail = (f"{info['role']} on {info['map']}, players {info['players']}, t={info['time']}s"
                      if isinstance(info, dict) else (info or ""))
            print(f"{row['name']:8} pid {row['pid']:<6} {'alive' if row['alive'] else 'EXITED':6} "
                  f"bridge {row.get('bridge', '-'):6} {detail}")
        return 0
    if args.action == "stop":
        net.stop(p)
        return 0
    if args.action == "logs":
        print("\n".join(net.merged_log(p, args.grep, args.errors, args.tail)))
        return 0
    if args.action == "run":
        if not args.script:
            raise ToolkitError("`net run` needs a script: plain Python with `net` (NetSession) in scope")
        session = net.NetSession(p)
        code = Path(args.script).read_text(encoding="utf-8")
        scope = {"__name__": "__net__", "net": session, "project": p}
        exec(compile(code, args.script, "exec"), scope)
        return 0
    raise ToolkitError(f"unknown net action {args.action}")


def cmd_perf(p, args):
    from uetk import perf
    if args.action == "list":
        for path in sorted(perf.out_dir(p).glob("*.json"), key=lambda f: f.stat().st_mtime):
            s = json.loads(path.read_text(encoding="utf-8"))
            frame = s["stats"].get("FrameTime", {}).get("median")
            print(f"{s['label']:24} {s['taken']}  {s['frames']:5d} frames  frame {frame if frame is None else round(frame, 2)} ms")
        return 0
    if args.action == "compare":
        if len(args.labels) != 2:
            raise ToolkitError("`perf compare` takes two labels (or .json paths)")
        print(perf.format_compare(perf.load(p, args.labels[0]), perf.load(p, args.labels[1])))
        return 0
    label = (args.labels[0] if args.labels else None) or time.strftime("run-%H%M%S")
    code = perf.SCENARIO.format(execs=list(args.exec or []), settle=args.settle, seconds=args.seconds)
    resp = Bridge(p).call(code, timeout=(args.settle + args.seconds) * 6 + 120)
    if not resp["ok"]:
        return _print_response(resp, "errors")
    summary = perf.summarize(resp["result"], label, args.stats or (),
                             notes={"exec": args.exec or [], "seconds": args.seconds, "instance": p.instance})
    path = perf.save(p, summary)
    print(perf.format_summary(summary))
    print(f"saved {path}")
    return 0


def cmd_press(p, args):
    value = f", value={args.value!r}" if args.value is not None else ""
    code = f"yield from ueh.press({args.key!r}, {args.hold!r}{value})\n'{args.key} held {args.hold}s'"
    return _print_response(Bridge(p).call(code, timeout=args.hold * 4 + 60), "problems")


def cmd_mouse(p, args):
    code = f"yield from ueh.mouse({args.dx!r}, {args.dy!r}, {args.seconds!r})\n'mouse ({args.dx}, {args.dy}) per frame for {args.seconds}s'"
    return _print_response(Bridge(p).call(code, timeout=args.seconds * 4 + 60), "problems")


def cmd_bp(p, args):
    if args.action == "graphs":
        code = f"bpedit.graphs({args.path!r})"
    elif args.action == "graph":
        code = (f"bpedit._call('describe_graph', bpedit.blueprint({args.path!r}), {args.graph or ''!r})" if args.json
                else f"bpedit.graph({args.path!r}, {args.graph or ''!r}).text()")
    else:
        code = f"bpedit.compile({args.path!r}, raise_on_error=False)"
    return _print_response(Bridge(p).call(code, timeout=args.timeout), "problems")


def cmd_plugins(p, args):
    if args.action == "install":
        return _install_plugins(p, args.names)
    return _plugin_status(p)


def cmd_vfx(p, args):
    if args.action == "install":
        return _install_plugins(p, ["UETKVfx"])
    if args.action == "status":
        return _plugin_status(p)
    if args.action == "presets":
        sys.path.insert(0, str(Path(__file__).resolve().parent / "editor"))
        import vfx_presets  # pure Python: no editor needed
        for name, info in vfx_presets.catalog().items():
            print(f"{name:12} {info['description']}")
            print(f"{'':12} params: {', '.join(f'{k}={v!r}' for k, v in info['params'].items())}")
        return 0
    if args.action == "modules":
        return _print_response(Bridge(p).call(f"vfx.module_catalog({args.target!r})", timeout=args.timeout), "problems")
    if not args.target:
        raise ToolkitError(f"`vfx {args.action}` needs a system path, e.g. /Game/VFX/NS_Fire")
    target = args.target
    preview_kwargs = dict(times=[float(t) for t in args.times.split(",")] if args.times else None, out=args.out,
                          size=args.size, yaw=args.yaw, pitch=args.pitch, distance=args.distance,
                          environment=args.env or None, floor=args.floor or None, exposure=args.exposure,
                          columns=args.columns, zoom=args.zoom,
                          background=None if not args.background else
                          ("black" if args.background == "black" else [float(v) for v in args.background.split(",")]))
    if args.action == "create":
        options = {k: v for k, v in preview_kwargs.items() if v is not None}
        if args.spec:
            spec = json.loads(Path(args.spec).read_text(encoding="utf-8"))
            code = f"vfx.build({spec!r}, {target!r})"
            if args.preview:
                code = f"_r = {code}\n_r['preview'] = vfx.preview({target!r}, **{options!r})\n_r"
        elif args.preset:
            code = (f"vfx.create({target!r}, {args.preset!r}, preview_after={args.preview}, "
                    f"preview_options={options!r}, **{_parse_params(args.set)!r})")
        else:
            raise ToolkitError("give --preset NAME (see `ue.py vfx presets`) or --spec FILE.json")
    elif args.action == "preview":
        code = f"vfx.preview({target!r}, {_py_args(**preview_kwargs)})"
    elif args.action == "describe":
        code = f"vfx.describe({target!r}, text={not args.json}, hidden={args.hidden})"
    elif args.action == "place":
        at = [float(v) for v in args.at.split(",")] if args.at else [0.0, 0.0, 0.0]
        code = f"vfx.place({target!r}, {at!r}, label={args.label!r})"
    elif args.action == "modules":
        code = f"vfx.module_catalog({target!r})"
    else:
        raise ToolkitError(f"unknown vfx action {args.action}")
    return _print_response(Bridge(p).call(code, timeout=args.timeout), "problems")


def cmd_mat(p, args):
    target = args.target
    size = [int(v) for v in args.size.lower().split("x")] if args.size else None
    preview = _py_args(mesh=args.mesh, out=args.out, size=size, pitch=args.pitch, zoom=args.zoom,
                       exposure=args.exposure, yaws=[float(v) for v in args.yaws.split(",")] if args.yaws else None)
    if args.action == "nodes":
        code = f"mat.nodes({target!r})"
    elif not target:
        raise ToolkitError(f"`mat {args.action}` needs an asset path, e.g. /Game/Materials/M_Rock")
    elif args.action == "create":
        if not args.spec:
            raise ToolkitError("give --spec FILE.json (or --spec - to read stdin); the format is in editor/mat.py")
        text = sys.stdin.read() if args.spec == "-" else Path(args.spec).read_text(encoding="utf-8")
        code = f"mat.build({json.loads(text)!r}, {target!r}, save_asset={not args.no_save})"
    elif args.action == "instance":
        code = (f"mat.instance({target!r}, parent={args.parent!r}, params={_parse_params(args.set)!r}, "
                f"save_asset={not args.no_save}, reset={args.reset})")
    elif args.action == "describe":
        code = f"mat.describe({target!r}, text={not args.json})"
    elif args.action == "params":
        code = f"mat.params({target!r})"
    elif args.action == "compile":
        code = f"mat.compile({target!r})"
    elif args.action == "preview":
        code = f"mat.preview({target!r}, {preview})"
    else:
        raise ToolkitError(f"unknown mat action {args.action}")
    if args.preview and args.action in ("create", "instance"):
        code = f"_r = {code}\n_r['preview'] = mat.preview({target!r}, {preview})\n_r"
    return _print_response(Bridge(p).call(code, timeout=args.timeout), "problems")


def cmd_input(p, args):
    a = list(args.args or [])

    def need(n, usage):
        if len(a) < n:
            raise ToolkitError(f"usage: ue.py input {args.action} {usage}")

    def items(values):
        out = []
        for v in values or []:
            try:
                out.append(json.loads(v))
            except ValueError:
                out.append(v)
        return out

    save = not args.no_save
    if args.action == "dump":
        code = f"inputs.describe({(a[0] if a else None)!r}, text={not args.json}, root={args.root!r})"
    elif args.action == "create":
        if not args.spec:
            raise ToolkitError("give --spec FILE.json (or --spec - to read stdin); the format is in editor/inputs.py")
        text = sys.stdin.read() if args.spec == "-" else Path(args.spec).read_text(encoding="utf-8")
        code = f"inputs.build({json.loads(text)!r}, save_asset={save})"
    elif args.action == "map":
        need(3, "CONTEXT ACTION KEY [--trigger T] [--modifier M]")
        code = (f"inputs.map_key({a[0]!r}, {a[1]!r}, {a[2]!r}, triggers={items(args.trigger)!r}, "
                f"modifiers={items(args.modifier)!r}, save_asset={save})")
    elif args.action == "unmap":
        need(1, "CONTEXT [--action ACTION] [--key KEY]")
        code = f"inputs.unmap({a[0]!r}, action={args.unmap_action!r}, key={args.key!r}, save_asset={save})"
    elif args.action == "use":
        need(2, "BLUEPRINT CONTEXT [--prop NAME]")
        code = f"inputs.use_context({a[0]!r}, {a[1]!r}, prop={args.prop!r}, save_asset={save}, remove={args.remove})"
    elif args.action == "keys":
        code = f"inputs.keys({(a[0] if a else None)!r})"
    elif args.action == "active":
        code = f"inputs.active(player={args.player})"
    elif args.action in ("add", "remove"):
        need(1, "CONTEXT")
        code = (f"inputs.add_context({a[0]!r}, priority={args.priority}, player={args.player})" if args.action == "add"
                else f"inputs.remove_context({a[0]!r}, player={args.player})")
    else:
        raise ToolkitError(f"unknown input action {args.action}")
    return _print_response(Bridge(p).call(code, timeout=args.timeout), "problems")


def cmd_import(p, args):
    from uetk import procs
    options = _parse_params(args.set)
    for key in ("kind", "skeleton", "texture", "name"):
        if getattr(args, key):
            options[key] = getattr(args, key)
    rules = []
    for item in args.rule or []:
        pattern, _, rest = item.partition(":")
        rules.append(dict(_parse_params([kv for kv in rest.split(",") if kv]), match=pattern))
    if args.reimport:
        code = f"importer.reimport({(args.paths[0] if args.paths else '/Game')!r}, all={args.all})"
    elif args.status:
        code = f"importer.sources({(args.paths[0] if args.paths else '/Game')!r})"
    else:
        if not args.paths or not args.to:
            raise ToolkitError("usage: ue.py import FILE_OR_FOLDER... --to /Game/Folder (or --reimport / --status)")
        files = [str(Path(f).resolve()) for f in args.paths]
        code = (f"importer.run({files!r}, {args.to!r}, rules={rules!r}, save={not args.no_save}, flat={args.flat}, "
                f"**{options!r})")
    if not args.json:
        code = f"importer.report({code})"
    live = bool(procs.editor_pids(p)) and not args.headless
    if not live:
        print("No editor running: importing headless ...", flush=True)
    resp = Bridge(p).call(code, timeout=args.timeout) if live else run_headless(p, code, timeout=args.timeout)
    return _print_response(resp, "problems")


def cmd_exec(p, args):
    code = f"ueh.console({args.command!r}, pie={args.pie})"
    return _print_response(Bridge(p).call(code, timeout=args.timeout), "all")


def cmd_wait(p, args):
    if args.game_seconds is not None:
        code = f"(yield ueh.wait({args.game_seconds!r}))"
        what = f"{args.game_seconds:g} s of game time"
        budget = args.game_seconds * 4 + 30  # game time can run slower than the wall clock
    elif args.condition:
        code = (f"(yield ueh.until(lambda: ({args.condition}), timeout={args.timeout!r}, message={args.condition!r}, "
                f"interval={args.interval!r}))")
        what = args.condition
        budget = args.timeout + 30
    else:
        raise ToolkitError('Give a Python condition (e.g. "ueh.is_pie()") or --game-seconds N.')
    started = time.time()
    resp = Bridge(p).call(code, timeout=budget)
    if not resp["ok"]:
        last = resp["error"].strip().splitlines()[-1]
        print(last if "TimeoutError" in last else resp["error"].rstrip(), file=sys.stderr)
        return 1
    print(f"{what}: done after {time.time() - started:.1f}s -> {resp['result_repr']}")
    return 0


def cmd_snapshot(p, args):
    if args.action == "save":
        manifest = state.snapshot_save(p, args.paths or state.DEFAULT_PROTECT, args.name)
        for entry in manifest["entries"]:
            print(f"Saved {entry['path']} ({entry['kind']}, {entry['files']} file(s)) as snapshot {args.name!r}.")
    elif args.action == "restore":
        for line in state.describe(state.snapshot_restore(p, args.name)):
            print(f"Restored {line}")
    else:
        manifest = state.read_manifest(p, args.name)
        if not manifest:
            print(f"No snapshot named {args.name!r}.")
            return 0
        print(f"Snapshot {args.name!r} from {manifest['created']}, "
              f"{'waiting to be restored' if manifest.get('pending') else 'restored ' + manifest.get('restored', '')}:")
        for line in state.describe(state.snapshot_diff(p, args.name)):
            print(f"  {line}")
    return 0


def cmd_save(p, args):
    code = "ueh.save(" + ", ".join(repr(a) for a in args.paths) + ")"
    return _print_response(Bridge(p).call(code, timeout=900), "problems")


def cmd_pie(p, args):
    bridge = Bridge(p)
    if args.action == "status":
        return _print_response(bridge.call("{'pie': ueh.is_pie(), 'world': ueh.pie_world()}"), "none")
    want = args.action == "start"
    if want and args.protect is not None:
        state.restore_pending_protect(p)  # an earlier protected session that ended without `pie stop`
        manifest = state.snapshot_save(p, args.protect or state.DEFAULT_PROTECT, state.PROTECT)
        print("Protected (restored by `ue.py pie stop`, `editor stop` or `kill`): "
              + ", ".join(e["path"] for e in manifest["entries"]))
    elif want and (state.read_manifest(p) or {}).get("pending"):
        print("note: a protected session is still pending; `ue.py pie stop` restores it", file=sys.stderr)
    code = f"ueh.pie_start(simulate={args.simulate})" if want else "ueh.pie_stop()"
    bridge.call(code)
    deadline = time.time() + args.timeout
    while time.time() < deadline:
        if bridge.call("ueh.is_pie()", quiet=True)["result"] == want:
            print(f"PIE {'running' if want else 'stopped'}.")
            if not want:
                time.sleep(1.0)  # let the game's end-of-play saves land before putting the files back
                state.restore_pending_protect(p)
            return 0
        time.sleep(0.5)
    raise ToolkitError(f"PIE did not {'start' if want else 'stop'} within {args.timeout}s.")


def cmd_log(p, args):
    path = Path(args.file) if args.file else logs.active_log(p, Bridge(p).info())
    if not path or not path.exists():
        raise ToolkitError("No log file found.")
    lines = logs.read_from(path, 0).splitlines()
    if args.errors:
        lines = [l for l in lines if logs.is_problem(l)]
    if args.grep:
        import re
        pattern = re.compile(args.grep, re.I)
        lines = [l for l in lines if pattern.search(l)]
    print(f"# {path}")
    print("\n".join(lines[-args.tail:] if args.tail else lines))
    return 0


def cmd_crash(p, args):
    crash = logs.latest_crash(p)
    if not crash:
        print("No crash reports in Saved/Crashes.")
        return 0
    for key, value in crash.items():
        value = "\n    ".join(value) if isinstance(value, list) else value
        print(f"{key}: {value}")
    return 0


def _main_editor_pid(p):
    pids = procs.editor_pids(p)
    if not pids:
        raise ToolkitError("Editor is not running.")
    return pids[0]


def cmd_windows(p, args):
    for proc in procs.project_processes(p):
        print(f"{proc['kind']} pid {proc['pid']} ({proc['name']})")
        for w in procs.windows_of(proc["pid"]):
            x, y, width, height = w["rect"]
            print(f"   {w['title']!r:60} {width}x{height} at ({x},{y}){' MINIMIZED' if w['minimized'] else ''}")
    return 0


def cmd_screenshot(p, args):
    from uetk import winshot
    pid = _main_editor_pid(p)
    windows = procs.windows_of(pid)
    if not windows:
        raise ToolkitError("The editor has no visible windows.")
    targets = windows if args.all else windows[:1]
    out_dir = p.out_dir / "screenshots"
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    for i, w in enumerate(targets):
        if w["minimized"]:
            if not args.restore:
                print(f"skipped minimized window {w['title']!r} (use --restore)")
                continue
            procs.restore_window(w["hwnd"])
            time.sleep(0.5)
        out = Path(args.out) if args.out and len(targets) == 1 else out_dir / f"{stamp}-{i}.png"
        width, height = winshot.capture(w["hwnd"], str(out), args.max_width)
        print(f"{out}  ({width}x{height}, window {w['title']!r})")
    return 0


def cmd_bridge(p, args):
    if args.action == "install":
        print("Installed: per-user UE_PYTHONPATH now includes the bridge folder." if install() else "Already installed.",
              "Applies to editors launched from now on (restart the Epic Games Launcher if it is open).")
    elif args.action == "uninstall":
        print("Uninstalled (removed from per-user UE_PYTHONPATH)." if uninstall() else "Was not installed.")
    else:
        bridge_state, info = Bridge(p).state()
        print(f"state: {bridge_state}; auto-load installed: {is_installed()}")
        if info:
            print(json.dumps(info, indent=2))
    return 0


# ---------------------------------------------------------------------------- argument parsing

def build_parser():
    parser = argparse.ArgumentParser(prog="ue.py", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("-p", "--project", help=".uproject file or its folder (default: found from the current folder)")
    parser.add_argument("-i", "--instance", help="talk to a game process started by `ue.py net` (server, client1, ...) "
                                                 "instead of the editor: py, exec, wait, render, log")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("info", help="project, engine, editor and bridge status").set_defaults(fn=cmd_info)

    s = sub.add_parser("build", help="compile with UnrealBuildTool")
    s.add_argument("--target", default="editor", help="editor | game | <TargetName> (default: editor)")
    s.add_argument("--config", default="Development", help="Debug | DebugGame | Development | Shipping")
    s.add_argument("--platform", default="Win64")
    s.add_argument("--clean", action="store_true", help="clean before building")
    s.add_argument("--restart", action="store_true", help="close the running editor, build, reopen it on the same level")
    s.add_argument("--save", action="store_true", help="with --restart: save unsaved assets first")
    s.add_argument("--discard", action="store_true", help="with --restart: drop unsaved changes")
    s.add_argument("-v", "--verbose", action="store_true", help="stream full UBT output")
    s.set_defaults(fn=cmd_build)

    s = sub.add_parser("genproject", help="regenerate Visual Studio project files")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_genproject)

    s = sub.add_parser("livecoding", help="Live Coding compile in the running editor (.cpp-only changes)")
    s.add_argument("--timeout", type=float, default=600)
    s.set_defaults(fn=cmd_livecoding)

    s = sub.add_parser("test", help="run automation tests headless")
    s.add_argument("filter", nargs="?", help="test path prefix(es), '+'-separated (default: all project tests)")
    s.add_argument("--timeout", type=float, default=1800)
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_test)

    s = sub.add_parser("package", help="BuildCookRun a Win64 game build")
    s.add_argument("--config", default="Development")
    s.add_argument("--out", help="archive directory (default Saved/UnrealToolkit/Packaged/<config>)")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_package)

    s = sub.add_parser("editor", help="start/stop/restart/wait/status of the editor")
    s.add_argument("action", choices=["start", "stop", "restart", "wait", "status"],
                   help="wait = block until a launching editor is usable")
    s.add_argument("--map", help="level to open, e.g. /Game/Warehouse/Levels/Showcase")
    s.add_argument("--no-build", action="store_true", help="skip the pre-launch build")
    s.add_argument("--no-wait", action="store_true", help="return right after launching")
    s.add_argument("--no-throttle", action="store_true",
                   help="start/restart: keep full speed when the editor is in the background (restored on stop)")
    s.add_argument("--save", action="store_true", help="stop: save unsaved assets first")
    s.add_argument("--discard", action="store_true", help="stop: force-close, dropping unsaved changes")
    s.add_argument("--timeout", type=float, default=900)
    s.set_defaults(fn=cmd_editor)

    sub.add_parser("kill", help="force-kill every Unreal process of this project").set_defaults(fn=cmd_kill)

    s = sub.add_parser("py", help="run Python in the editor (ueh helpers pre-imported); a top-level `yield` makes it a "
                                  "scenario that waits for the game (yield ueh.wait(5) / ueh.until(...))")
    s.add_argument("code", nargs="?", help="code, or '-' for stdin")
    s.add_argument("-f", "--file", help="run a .py file")
    s.add_argument("--headless", action="store_true", help="run in a fresh UnrealEditor-Cmd instead of the live editor")
    s.add_argument("--force", action="store_true", help="allow --headless while the editor is running")
    s.add_argument("--timeout", type=float, default=300)
    s.add_argument("--log", choices=["problems", "errors", "all", "none"], help="which log lines to show")
    s.add_argument("--json", action="store_true", help="print the raw response")
    s.set_defaults(fn=cmd_py)

    s = sub.add_parser("bpdump", help="export every Blueprint (and data asset) and condense it to readable text "
                                      "for review: variables, overridden defaults, widget tree, event flows")
    s.add_argument("paths", nargs="*", help="content roots to scan (default: /Game)")
    s.add_argument("--out", help="output folder (default: Saved/UnrealToolkit/bpdump); writes raw/ and txt/ inside")
    s.add_argument("--skip", nargs="+", help="more package-path prefixes to leave out (template folders are skipped "
                                            "by default: Mannequins, LevelPrototyping, StarterContent)")
    s.add_argument("--headless", action="store_true", help="export in a fresh UnrealEditor-Cmd even if the editor runs")
    s.add_argument("--timeout", type=float, default=1800)
    s.add_argument("-v", "--verbose", action="store_true", help="print the index of converted assets")
    s.set_defaults(fn=cmd_bpdump)

    s = sub.add_parser("render", help="offscreen PNG of a camera view, PIE camera, actor, asset or UMG widget "
                                      "(UETKEditor plugin; the editor needn't be in front and nothing changes)")
    s.add_argument("kind", choices=["view", "pie", "actor", "asset", "widget"],
                   help="view = the open level from the viewport camera or --at/--look; pie = a player's camera; "
                        "actor = a level actor from several angles; asset = mesh/material/Blueprint/texture/widget "
                        "in a preview scene; widget = a UMG widget at --size")
    s.add_argument("target", nargs="?", help="actor label, asset path, or widget Blueprint path / class")
    s.add_argument("--at", help="view: camera location x,y,z (default: the viewport camera)")
    s.add_argument("--look", help="view: point the camera at x,y,z")
    s.add_argument("--rot", help="view: camera rotation pitch,yaw,roll")
    s.add_argument("--size", help="WxH pixels per frame (view 1600x900, actor/asset 800x600, widget 1920x1080)")
    s.add_argument("--fov", type=float, help="horizontal field of view")
    s.add_argument("--exposure", type=float, help="fixed exposure compensation in stops (default: meter to mid-gray)")
    s.add_argument("--yaws", help="actor/asset: camera yaws, e.g. 0,90,180,270 (relative to the subject's facing)")
    s.add_argument("--pitch", type=float, help="actor/asset: camera pitch (default -20)")
    s.add_argument("--zoom", type=float, help="actor/asset: >1 moves closer than the fit")
    s.add_argument("--columns", type=int, help="frames per row")
    s.add_argument("--isolate", action="store_true", help="actor: draw only the actor")
    s.add_argument("--no-floor", action="store_true", help="asset: hide the preview floor")
    s.add_argument("--no-env", action="store_true", help="asset: hide the preview sky")
    s.add_argument("--pie", action="store_true", help="widget: the live instance on screen in PIE")
    s.add_argument("--player", type=int, help="pie: player index (default 0)")
    s.add_argument("--post", help="post-process material path(s), comma-separated, applied to the render")
    s.add_argument("--mesh", help="asset: the static mesh a surface material is drawn on (default a sphere)")
    s.add_argument("--out", help="PNG path (default Saved/UnrealToolkit/renders/<name>.png)")
    s.add_argument("--timeout", type=float, default=600)
    s.set_defaults(fn=cmd_render)

    s = sub.add_parser("net", help="multiplayer session as separate game processes (listen/dedicated server + "
                                   "clients), each with its own bridge: `ue.py --instance client1 py ...`")
    s.add_argument("action", choices=["start", "status", "stop", "logs", "run"],
                   help="run = execute a test script (system Python) with `net` in scope (see uetk/net.py)")
    s.add_argument("script", nargs="?", help="run: the script")
    s.add_argument("--map", help="start: map (default: the project's GameDefaultMap)")
    s.add_argument("--clients", type=int, default=1, help="start: number of clients (default 1)")
    s.add_argument("--dedicated", action="store_true", help="start: dedicated server instead of a listen server")
    s.add_argument("--render", help="start: instances that open a window instead of -nullrhi, e.g. client1")
    s.add_argument("--port", type=int, default=7777)
    s.add_argument("--args", help="start: extra command-line arguments for every process")
    s.add_argument("--steam", action="store_true", help="start: leave Steam on (default -nosteam: LAN/IP sessions)")
    s.add_argument("--protect", action="store_true", help="start: snapshot Saved/SaveGames; stop restores it")
    s.add_argument("--grep", help="logs: regex filter")
    s.add_argument("--errors", action="store_true", help="logs: warnings and errors only")
    s.add_argument("--tail", type=int, default=200, help="logs: last N merged lines (0 = all)")
    s.add_argument("--timeout", type=float, default=300)
    s.set_defaults(fn=cmd_net)

    s = sub.add_parser("perf", help="frame times of the running game (PIE or --instance) from the CSV profiler: "
                                    "run [LABEL], compare A B, list")
    s.add_argument("action", choices=["run", "compare", "list"])
    s.add_argument("labels", nargs="*", help="run: a label for the saved summary; compare: two labels")
    s.add_argument("--seconds", type=float, default=5.0, help="run: seconds of game time to profile (default 5)")
    s.add_argument("--settle", type=float, default=2.0, help="run: seconds to wait after the --exec commands first")
    s.add_argument("--exec", action="append", help="run: console command before measuring (repeatable), e.g. "
                                                     "'r.ScreenPercentage 75'. Cvars stay set until changed back")
    s.add_argument("--stats", nargs="+", help="run: more CSV columns to report (substring match), e.g. Physics Net")
    s.set_defaults(fn=cmd_perf)

    s = sub.add_parser("press", help="press a key in the running game through Enhanced Input's mappings (Hold/Tap "
                                     "triggers and modifiers run; the OS keyboard isn't touched)")
    s.add_argument("key", help="key name: E, R, SpaceBar, LeftShift, LeftMouseButton, Gamepad_FaceButton_Bottom...")
    s.add_argument("--hold", type=float, default=0.08, help="seconds of game time to hold it (default 0.08 = a tap)")
    s.add_argument("--value", help="axis value instead of pressed, e.g. 0.5 or 'X=1 Y=0'")
    s.set_defaults(fn=cmd_press)

    s = sub.add_parser("mouse", help="move the mouse in the running game: (dx, dy) per frame through the mappings")
    s.add_argument("dx", type=float)
    s.add_argument("dy", type=float)
    s.add_argument("--for", dest="seconds", type=float, default=0.25, help="seconds of game time (default 0.25)")
    s.set_defaults(fn=cmd_mouse)

    s = sub.add_parser("bp", help="Blueprint graphs for editing (UETKEditor plugin): graphs, a graph's nodes with ids "
                                  "and links, compile with the compiler's messages; edit with `bpedit` in `ue.py py`")
    s.add_argument("action", choices=["graphs", "graph", "compile"])
    s.add_argument("path", help="Blueprint path, e.g. /Game/BP_Door")
    s.add_argument("graph", nargs="?", help="graph: graph name (default: the event graph)")
    s.add_argument("--json", action="store_true", help="graph: raw JSON")
    s.add_argument("--timeout", type=float, default=300)
    s.set_defaults(fn=cmd_bp)

    s = sub.add_parser("plugins", help="the toolkit's editor plugins (UETKEditor, UETKVfx): install = copy them into "
                                       "Plugins/ (then build), status = installed/built/loaded")
    s.add_argument("action", choices=["install", "status"])
    s.add_argument("names", nargs="*", help="install: only these (dependencies come along); default all")
    s.set_defaults(fn=cmd_plugins)

    s = sub.add_parser("vfx", help="Niagara VFX: install the UETKVfx plugin, build systems from presets or JSON specs, "
                                   "preview them offscreen as a PNG contact sheet, describe a system's stack")
    s.add_argument("action", choices=["install", "status", "presets", "create", "preview", "describe", "place", "modules"],
                   help="install = copy the plugin into Plugins/ (then build); create = preset/spec -> system; "
                        "preview = PNG of the system at several ages; modules = list module scripts (TARGET filters)")
    s.add_argument("target", nargs="?", help="system path, e.g. /Game/VFX/NS_Fire (modules: a name filter)")
    s.add_argument("--preset", help="create: preset name (`ue.py vfx presets`)")
    s.add_argument("--set", nargs="+", metavar="KEY=VALUE", help="create: preset parameters, e.g. color=[1,0.4,0.1] scale=2")
    s.add_argument("--spec", help="create: JSON spec file (format in editor/vfx.py)")
    s.add_argument("--preview", action="store_true", help="create: also render a preview")
    s.add_argument("--times", help="preview: comma-separated ages in seconds (default 0.1,0.4,0.8,1.5)")
    s.add_argument("--out", help="preview: PNG path (default Saved/UnrealToolkit/vfx/<name>.png)")
    s.add_argument("--size", type=int, help="preview: pixels per frame (default 320)")
    s.add_argument("--columns", type=int, help="preview: frames per row")
    s.add_argument("--yaw", type=float, help="preview: camera yaw (0 = looking along +X)")
    s.add_argument("--pitch", type=float, help="preview: camera pitch (default -15)")
    s.add_argument("--distance", type=float, help="preview: camera distance (default: fit the bounds)")
    s.add_argument("--exposure", type=float, help="preview: exposure bias in stops")
    s.add_argument("--zoom", type=float, help="preview: >1 moves closer than the auto-fit (which covers every time)")
    s.add_argument("--background", help="preview: 'black' or r,g,b linear (default: gray behind smoke-like materials)")
    s.add_argument("--env", action="store_true", help="preview: show the preview sky instead of black")
    s.add_argument("--floor", action="store_true", help="preview: show the preview floor")
    s.add_argument("--json", action="store_true", help="describe: raw JSON")
    s.add_argument("--hidden", action="store_true", help="describe: include unset/hidden inputs")
    s.add_argument("--at", help="place: x,y,z in the open level")
    s.add_argument("--label", help="place: actor label")
    s.add_argument("--timeout", type=float, default=900)
    s.set_defaults(fn=cmd_vfx)

    s = sub.add_parser("mat", help="materials: build one from a JSON spec of expressions, read one back as that "
                                   "spec, make instances, list parameters, compile errors, previews (editor/mat.py)")
    s.add_argument("action", choices=["create", "instance", "describe", "params", "compile", "preview", "nodes"],
                   help="create = spec -> material or material function (rebuilds in place); instance = create/update "
                        "a material instance; describe = the material as spec text; nodes = expression classes and "
                        "their pins (TARGET filters)")
    s.add_argument("target", nargs="?", help="asset path, e.g. /Game/Materials/M_Rock (nodes: a name filter)")
    s.add_argument("--spec", help="create: JSON spec file, or - for stdin (format: editor/mat.py)")
    s.add_argument("--parent", help="instance: parent material or instance")
    s.add_argument("--set", nargs="+", metavar="NAME=VALUE",
                   help='instance: parameter values, e.g. Roughness=0.3 "Tint=[1,0,0]" Albedo=/Game/T_X UseDetail=false')
    s.add_argument("--reset", action="store_true", help="instance: clear every override first")
    s.add_argument("--no-save", action="store_true", help="create/instance: don't save the asset")
    s.add_argument("--preview", action="store_true", help="create/instance: also render a preview")
    s.add_argument("--mesh", help="preview: sphere (default), plane, cube, cylinder, cone or a static mesh path")
    s.add_argument("--size", help="preview: WxH pixels per frame")
    s.add_argument("--yaws", help="preview: camera yaws, e.g. 0,90")
    s.add_argument("--pitch", type=float, help="preview: camera pitch")
    s.add_argument("--zoom", type=float, help="preview: >1 moves closer")
    s.add_argument("--exposure", type=float, help="preview: fixed exposure in stops")
    s.add_argument("--out", help="preview: PNG path")
    s.add_argument("--json", action="store_true", help="describe: the spec as JSON instead of text")
    s.add_argument("--timeout", type=float, default=900)
    s.set_defaults(fn=cmd_mat)

    s = sub.add_parser("input", help="Enhanced Input: read every mapping context and action as a spec, build them "
                                     "from one, map/unmap keys, put contexts on Blueprints or the running player "
                                     "(editor/inputs.py)")
    s.add_argument("action", choices=["dump", "create", "map", "unmap", "use", "keys", "active", "add", "remove"],
                   help="dump [PATH] = the project's input (or one context/action) as spec text; create --spec; "
                        "map CONTEXT ACTION KEY; unmap CONTEXT --action/--key; use BLUEPRINT CONTEXT = add it to the "
                        "Blueprint's default contexts; keys [FILTER]; active/add/remove CONTEXT = the running game")
    s.add_argument("args", nargs="*", help="positional arguments of the action (see above)")
    s.add_argument("--spec", help="create: JSON spec file, or - for stdin (format: editor/inputs.py)")
    s.add_argument("--trigger", action="append",
                   help='map: a trigger, repeatable: hold, or {"hold": {"hold_time_threshold": 0.5}} as JSON')
    s.add_argument("--modifier", action="append", help="map: a modifier, repeatable: negate, swizzle, dead_zone...")
    s.add_argument("--action", dest="unmap_action", help="unmap: only this action's mappings")
    s.add_argument("--key", help="unmap: only this key's mappings")
    s.add_argument("--prop", help="use: the Blueprint property, when it has several context properties")
    s.add_argument("--remove", action="store_true", help="use: take the context out of the Blueprint instead")
    s.add_argument("--priority", type=int, default=0, help="add: context priority")
    s.add_argument("--player", type=int, default=0, help="active/add/remove: local player index")
    s.add_argument("--root", default="/Game", help="dump: only assets under this folder")
    s.add_argument("--json", action="store_true", help="dump: the spec as JSON instead of text")
    s.add_argument("--no-save", action="store_true", help="create/map/unmap/use: don't save")
    s.add_argument("--timeout", type=float, default=300)
    s.set_defaults(fn=cmd_input)

    s = sub.add_parser("import", help="import meshes, animations, textures and sounds (files or whole folders) with "
                                      "presets; reimport the ones whose source changed (editor/importer.py)")
    s.add_argument("paths", nargs="*", help="files and folders to import (folders recursively); with --reimport or "
                                            "--status: an asset or content folder (default /Game)")
    s.add_argument("--to", help="destination content folder, e.g. /Game/Props (subfolders are mirrored)")
    s.add_argument("--kind", choices=["static", "skeletal", "animation"], help="what the mesh files are (default: "
                   "from each file's content and name)")
    s.add_argument("--skeleton", help="skeletal meshes: share this skeleton; animations: the skeleton to use "
                                      "(default: the skeletal mesh imported in the same run)")
    s.add_argument("--texture", help="texture preset for every texture: color, normal, masks, grayscale, linear, hdr, "
                                     "ui (default: from each name's suffix)")
    s.add_argument("--name", help="asset name (one file)")
    s.add_argument("--set", nargs="+", metavar="KEY=VALUE", help="options: materials=false collision=box "
                   "lod_group=SmallProp scale=100 looping=true sound_class=/Game/... (see editor/importer.py)")
    s.add_argument("--rule", action="append", metavar="GLOB:KEY=VALUE,...",
                   help="options for matching file names, repeatable: \"*_UI_*.png:texture=ui\"")
    s.add_argument("--flat", action="store_true", help="don't mirror the source folders' subfolders")
    s.add_argument("--reimport", action="store_true", help="reimport assets whose source file changed (--all: every one)")
    s.add_argument("--all", action="store_true", help="--reimport: unchanged ones too")
    s.add_argument("--status", action="store_true", help="list imported assets, their source files and whether they changed")
    s.add_argument("--headless", action="store_true", help="import headless even when an editor is running")
    s.add_argument("--no-save", action="store_true", help="don't save the imported assets")
    s.add_argument("--json", action="store_true", help="the raw records instead of the summary")
    s.add_argument("--timeout", type=float, default=1800)
    s.set_defaults(fn=cmd_import)

    s = sub.add_parser("exec", help="run a console command in the editor (or PIE with --pie)")
    s.add_argument("command")
    s.add_argument("--pie", action="store_true")
    s.add_argument("--timeout", type=float, default=120)
    s.set_defaults(fn=cmd_exec)

    s = sub.add_parser("wait", help="block until a Python condition is true in the editor, or N s of game time pass")
    s.add_argument("condition", nargs="?", help='expression checked every tick, e.g. "ueh.is_pie()"')
    s.add_argument("--game-seconds", type=float, help="wait this much PIE game time instead")
    s.add_argument("--timeout", type=float, default=60, help="give up after this many seconds (exit code 1)")
    s.add_argument("--interval", type=float, default=0, help="check every N seconds instead of every tick")
    s.set_defaults(fn=cmd_wait)

    s = sub.add_parser("snapshot", help="copy files/folders away and put them back exactly (save games, configs)")
    s.add_argument("action", choices=["save", "restore", "status"])
    s.add_argument("paths", nargs="*", help="save: project-relative paths (default Saved/SaveGames)")
    s.add_argument("--name", default=state.PROTECT, help="snapshot name (default 'protect', the one PIE uses)")
    s.set_defaults(fn=cmd_snapshot)

    s = sub.add_parser("save", help="save the given assets, or every dirty package")
    s.add_argument("paths", nargs="*")
    s.set_defaults(fn=cmd_save)

    s = sub.add_parser("pie", help="Play-In-Editor control")
    s.add_argument("action", choices=["start", "stop", "status"])
    s.add_argument("--simulate", action="store_true")
    s.add_argument("--protect", nargs="*", metavar="PATH",
                   help="start: snapshot these project paths (default Saved/SaveGames); stop puts them back")
    s.add_argument("--timeout", type=float, default=120)
    s.set_defaults(fn=cmd_pie)

    s = sub.add_parser("log", help="show the active editor log")
    s.add_argument("--tail", type=int, default=60, help="last N lines (0 = all)")
    s.add_argument("--errors", action="store_true", help="only warnings/errors")
    s.add_argument("--grep", help="regex filter")
    s.add_argument("--file", help="read a specific log file")
    s.set_defaults(fn=cmd_log)

    sub.add_parser("crash", help="summarize the newest crash report").set_defaults(fn=cmd_crash)
    sub.add_parser("windows", help="list windows of this project's Unreal processes").set_defaults(fn=cmd_windows)

    s = sub.add_parser("screenshot", help="capture the editor window to PNG")
    s.add_argument("--out", help="output .png path")
    s.add_argument("--all", action="store_true", help="capture every visible editor window (dialogs too)")
    s.add_argument("--max-width", type=int, default=1600, help="downscale to this width (0 = native)")
    s.add_argument("--restore", action="store_true", help="un-minimize minimized windows first")
    s.set_defaults(fn=cmd_screenshot)

    s = sub.add_parser("bridge", help="status of the in-editor bridge; install = also load it in self-launched editors")
    s.add_argument("action", choices=["install", "uninstall", "status"])
    s.set_defaults(fn=cmd_bridge)
    return parser


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    args = build_parser().parse_args(argv)
    try:
        return args.fn(Project(args.project, instance=args.instance), args) or 0
    except ToolkitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
