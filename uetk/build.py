"""UnrealBuildTool / automation tests / AutomationTool wrappers that print short, actionable summaries."""
import json
import os
import re
import subprocess
import time

from . import logs
from .project import ToolkitError

LIVE_CODING_BLOCK = "Unable to build while Live Coding is active"
_PROBLEM_RE = re.compile(r"\berror\b|\bwarning\b|^ERROR:|\bLNK\d{4}\b", re.I)
_KEY_LINE_RE = re.compile(r"^(Result:|Total execution time|Target is up to date|BUILD (SUCCESSFUL|FAILED)|AutomationTool exiting)")


def ubt_env(project, base=None):
    """Environment for running UnrealBuildTool.exe directly (copy of `base`, default os.environ).

    Build.bat and RunUAT.bat call GetDotnetPath.bat, which puts the engine's bundled .NET first; launching
    UnrealBuildTool.exe directly skips that, and UE 5.8's UBT (net10.0) then fails with "You must install or
    update .NET" on machines without a system-wide .NET 10. Mirror that script. UE_USE_SYSTEM_DOTNET=1 opts out,
    as it does for the engine's scripts. Also used for editors we launch, since the editor runs UBT directly too."""
    env = dict(os.environ if base is None else base)
    dotnet = project.bundled_dotnet_dir
    if env.get("UE_USE_SYSTEM_DOTNET") == "1" or dotnet is None:
        return env
    env["DOTNET_ROOT"] = str(dotnet)
    env["PATH"] = str(dotnet) + os.pathsep + env.get("PATH", "")
    env["DOTNET_MULTILEVEL_LOOKUP"] = "0"  # ignore system-wide .NET installs
    env["DOTNET_ROLL_FORWARD"] = "LatestMajor"
    return env


def _run_streamed(cmd, log_path, verbose, cwd=None, env=None):
    """Run cmd (list or literal command line), tee output to log_path. Returns (exit_code, lines)."""
    lines = []
    with open(log_path, "w", encoding="utf-8") as log_file:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                                text=True, encoding="utf-8", errors="replace", cwd=cwd, env=env)
        for line in proc.stdout:
            log_file.write(line)
            lines.append(line.rstrip())
            if verbose:
                print(lines[-1], flush=True)
        proc.wait()
    return proc.returncode, lines


def _print_summary(lines, max_problems=60):
    problems, seen = [], set()
    for line in lines:
        if _PROBLEM_RE.search(line) and line not in seen:
            seen.add(line)
            problems.append(line)
    for line in problems[:max_problems]:
        print("  " + line)
    if len(problems) > max_problems:
        print(f"  ... {len(problems) - max_problems} more (see log)")
    for line in lines:
        if _KEY_LINE_RE.match(line.strip()):
            print(line.strip())


def ubt_build(project, target=None, config="Development", platform="Win64", clean=False, verbose=False):
    """Build a target with UnrealBuildTool. Returns the exit code (0 = success)."""
    target = target or project.editor_target
    log_path = project.out_dir / f"build-{target}-{platform}-{config}.log"
    # UBT's own log defaults to the per-user %LOCALAPPDATA%/UnrealBuildTool/Log.txt, which it renames on start; that
    # fails ("IOException: ... being used by another process") while any other UBT (another editor's) has it open.
    ubt_log = project.out_dir / "ubt-log.txt"
    base = [str(project.ubt_exe), target, platform, config, f"-Project={project.uproject}", "-WaitMutex",
            f"-Log={ubt_log}"]
    env = ubt_env(project)
    if clean:
        print(f"Cleaning {target} {platform} {config} ...", flush=True)
        _run_streamed(base + ["-Clean"], log_path, verbose, env=env)
    print(f"Building {target} {platform} {config} ...", flush=True)
    started = time.time()
    code, lines = _run_streamed(base, log_path, verbose, env=env)
    _print_summary(lines)
    if any(LIVE_CODING_BLOCK in l for l in lines):
        print("The editor has Live Coding active. Use `ue.py livecoding` for .cpp-only changes, "
              "or `ue.py build --restart` to close the editor, build, and reopen it.")
    print(f"{'OK' if code == 0 else 'FAILED'}: exit code {code} in {time.time() - started:.0f}s; full log: {log_path}")
    return code


def generate_project_files(project, verbose=False):
    log_path = project.out_dir / "generate-project-files.log"
    cmd = [str(project.ubt_exe), "-projectfiles", f"-project={project.uproject}", "-game", "-rocket", "-progress"]
    code, lines = _run_streamed(cmd, log_path, verbose, env=ubt_env(project))
    _print_summary(lines)
    print(f"{'OK' if code == 0 else 'FAILED'}: exit code {code}; full log: {log_path}")
    return code


def discover_test_prefixes(project):
    """First path segment of every automation test declared in Source (e.g. {'RoboMedic'})."""
    pattern = re.compile(r'IMPLEMENT_\w*AUTOMATION_TEST\s*\(\s*\w+\s*,\s*"([^".]+)')
    prefixes = set()
    for path in (project.root / "Source").rglob("*.cpp"):
        prefixes.update(pattern.findall(path.read_text(encoding="utf-8", errors="replace")))
    return sorted(prefixes)


def run_tests(project, test_filter=None, timeout=1800, verbose=False):
    """Run automation tests in a headless editor and print per-test results. Returns exit code."""
    test_filter = test_filter or "+".join(discover_test_prefixes(project))
    if not test_filter:
        raise ToolkitError("No test filter given and no automation tests found in Source/.")
    report_dir = project.out_dir / "tests" / time.strftime("%Y%m%d-%H%M%S")
    report_dir.mkdir(parents=True, exist_ok=True)
    log_path = report_dir / "test.log"
    # Forward slashes: Unreal's quoted-value parser treats backslashes as escape sequences.
    cmdline = (f'"{project.editor_cmd_exe}" "{project.uproject}" -ExecCmds="Automation RunTests {test_filter};Quit" '
               f'-TestExit="Automation Test Queue Empty" -ReportExportPath="{report_dir.as_posix()}" '
               f'-unattended -nullrhi -nosplash -nosound -nop4 -abslog="{log_path.as_posix()}"')
    print(f"Running tests '{test_filter}' headless (this loads the editor; can take a few minutes) ...", flush=True)
    started = time.time()
    try:
        proc = subprocess.run(cmdline, cwd=str(project.root), timeout=timeout, stdin=subprocess.DEVNULL,
                              stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except subprocess.TimeoutExpired:
        raise ToolkitError(f"Tests timed out after {timeout}s; log: {log_path}")
    index = report_dir / "index.json"
    if not index.exists():
        lines = log_path.read_text(encoding="utf-8", errors="replace").splitlines() if log_path.exists() else []
        if verbose:
            print("\n".join(lines[-80:]))
        errors = [l for l in lines if logs.verbosity(l) in ("Fatal", "Error")][-30:]
        raise ToolkitError(f"No test report produced (exit code {proc.returncode}); log: {log_path}\n" + "\n".join(errors))
    report = json.loads(index.read_text(encoding="utf-8-sig"))
    for test in report.get("tests", []):
        print(f"  [{test.get('state')}] {test.get('fullTestPath')}")
        for entry in test.get("entries", []):
            event = entry.get("event", {})
            if event.get("type") in ("Error", "Warning"):
                print(f"      {event['type']}: {event.get('message')}")
    failed = report.get("failed", 0)
    print(f"{report.get('succeeded', 0)} passed, {report.get('succeededWithWarnings', 0)} passed with warnings, "
          f"{failed} failed, {report.get('notRun', 0)} not run in {time.time() - started:.0f}s; report: {report_dir}")
    if not report.get("tests"):
        print(f"No tests matched '{test_filter}'.")
        return 1
    return 1 if failed else 0


def package(project, config="Development", out_dir=None, verbose=False):
    """Build, cook, stage, pak and archive a Win64 game build with AutomationTool."""
    out_dir = out_dir or (project.out_dir / "Packaged" / config)
    log_path = project.out_dir / f"package-{config}.log"
    args = ["BuildCookRun", f"-project={project.uproject}", "-noP4", "-utf8output", "-platform=Win64",
            f"-clientconfig={config}", f"-target={project.game_target}", "-build", "-cook", "-stage", "-pak",
            "-iostore", "-compressed", "-prereqs", "-archive", f"-archivedirectory={out_dir}"]
    if config.lower() == "shipping":
        args.append("-nodebuginfo")  # no .pdb (~200 MB) in a build meant for players
    # cmd /s strips only the outer quotes, so paths with spaces survive the trip through RunUAT.bat.
    cmdline = f'cmd.exe /d /s /c ""{project.runuat_bat}" {subprocess.list2cmdline(args)}"'
    print(f"Packaging Win64 {config} -> {out_dir} (long-running) ...", flush=True)
    started = time.time()
    code, lines = _run_streamed(cmdline, log_path, verbose)
    _print_summary(lines)
    print(f"{'OK' if code == 0 else 'FAILED'}: exit code {code} in {time.time() - started:.0f}s; full log: {log_path}")
    return code
