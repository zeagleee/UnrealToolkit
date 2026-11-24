"""`ue.py perf`: frame-time measurements of the running game from Unreal's CSV profiler.

`run` profiles PIE (or a `ue.py net` instance) for a few seconds of game time and summarizes the per-frame CSV
(<Project>/Saved/Profiling/CSV): median / p95 / max of the frame, game, render and GPU times and draw calls, plus
the game thread's heaviest exclusive categories. Summaries are saved under Saved/UnrealToolkit/perf/<label>.json,
so `compare` can A/B two runs. Measure A and B in the same PIE session: the baseline drifts between sessions.
"""
import csv
import json
import time
from pathlib import Path

HEADLINE = ("FrameTime", "GameThreadTime", "RenderThreadTime", "RHIThreadTime", "GPUTime", "RHI/DrawCalls",
            "RHI/PrimitivesDrawn")
SCENARIO = """
import glob, os
csv_dir = os.path.join(unreal.Paths.convert_relative_path_to_full(unreal.Paths.project_saved_dir()), 'Profiling', 'CSV')
before = set(glob.glob(os.path.join(csv_dir, '*.csv')))
for _cmd in {execs!r}:
    ueh.console(_cmd, pie=True)
yield ueh.wait({settle!r})
ueh.console('csvprofile start', pie=True)
yield ueh.wait({seconds!r})
ueh.console('csvprofile stop', pie=True)
_new = yield ueh.until(lambda: sorted(set(glob.glob(os.path.join(csv_dir, '*.csv'))) - before, key=os.path.getmtime) or None,
                       timeout=60, message='the CSV profiler to write its file')
_path = _new[-1]
_size = [-1]
def _settled():
    size = os.path.getsize(_path)
    done, _size[0] = size == _size[0] and size > 0, size
    return done
yield ueh.until(_settled, timeout=60, interval=0.5, message='the CSV file to finish')
_path
"""


def out_dir(project):
    path = project.out_dir / "perf"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _stats(values):
    values = sorted(values)
    n = len(values)
    if not n:
        return None
    pick = lambda q: values[min(n - 1, int(round(q * (n - 1))))]
    return {"median": pick(0.5), "p95": pick(0.95), "max": values[-1], "min": values[0]}


def parse_csv(path, skip_frames=5):
    """{column: [values]} from a CSV profile.

    The profiler adds columns while it records (render thread, RHI, stats that start mid-capture). Each row holds the
    columns known when it was written, new columns are appended at the end, and the full header is written again as the
    last "EVENTS" row ([HasHeaderRowAtEnd] 1). So every row is a prefix of that final header, and parsing by the first
    row (or skipping rows of another length) loses most frames and every late column. The first few frames carry the
    start-up hitch and are dropped."""
    with open(path, newline="", encoding="utf-8", errors="replace") as f:
        rows = list(csv.reader(f))
    if not rows:
        return {}
    header = next((row for row in reversed(rows) if row and row[0] == "EVENTS"), rows[0])
    data = [row for row in rows[1:] if row and row[0] != "EVENTS" and not row[0].startswith("[") and len(row) <= len(header)]
    columns = {name: [] for name in header if name and name != "EVENTS"}
    for row in data[skip_frames:]:
        for name, cell in zip(header, row):
            if name in columns:
                try:
                    columns[name].append(float(cell))
                except ValueError:
                    pass
    return {name: values for name, values in columns.items() if values}


def summarize(path, label, extra_stats=(), notes=None):
    columns = parse_csv(path)
    summary = {"label": label, "csv": str(path), "frames": len(columns.get("FrameTime", [])),
               "taken": time.strftime("%Y-%m-%d %H:%M:%S"), "notes": notes or {}, "stats": {}, "game_thread": {}}
    for name in list(HEADLINE) + [c for c in columns if any(x.lower() in c.lower() for x in extra_stats)]:
        if name in columns:
            summary["stats"][name] = _stats(columns[name])
    exclusive = {name.split("/", 2)[-1]: _stats(values)["median"]
                 for name, values in columns.items() if name.startswith("Exclusive/GameThread/")}
    summary["game_thread"] = dict(sorted(exclusive.items(), key=lambda kv: -kv[1])[:12])
    frame = summary["stats"].get("FrameTime")
    if frame and frame["median"] > 150:
        summary["warning"] = ("frames take %.0f ms: the editor is probably throttled in the background "
                              "(`ue.py editor start --no-throttle`, or bring it to the front)" % frame["median"])
    render = summary["stats"].get("RenderThreadTime")
    if render and render["max"] == 0:
        summary.setdefault("notes", {})["render_thread"] = ("RenderThreadTime is 0 in every frame of this capture "
                                                            "(seen in a background PIE editor, 2026-11-23); RHIThreadTime and GPUTime still apply")
    return summary


def save(project, summary):
    path = out_dir(project) / ("%s.json" % summary["label"])
    path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    return path


def load(project, label):
    path = Path(label) if label.endswith(".json") else out_dir(project) / ("%s.json" % label)
    return json.loads(path.read_text(encoding="utf-8"))


def format_summary(summary):
    lines = ["%s: %d frames  (%s)" % (summary["label"], summary["frames"], summary["csv"])]
    lines.append("  %-22s %9s %9s %9s" % ("", "median", "p95", "max"))
    for name, s in summary["stats"].items():
        lines.append("  %-22s %9.2f %9.2f %9.2f" % (name, s["median"], s["p95"], s["max"]))
    if summary["game_thread"]:
        lines.append("  game thread, heaviest (median ms):")
        for name, value in summary["game_thread"].items():
            lines.append("    %-40s %7.2f" % (name, value))
    if summary.get("warning"):
        lines.append("  warning: " + summary["warning"])
    if summary.get("notes", {}).get("render_thread"):
        lines.append("  note: " + summary["notes"]["render_thread"])
    return "\n".join(lines)


def format_compare(a, b):
    lines = ["%-22s %10s %10s %9s %7s" % ("median", a["label"][:10], b["label"][:10], "delta", "%")]
    for name in a["stats"]:
        if name not in b["stats"]:
            continue
        x, y = a["stats"][name]["median"], b["stats"][name]["median"]
        pct = (y - x) / x * 100 if x else 0.0
        lines.append("%-22s %10.2f %10.2f %+9.2f %+6.1f%%" % (name, x, y, y - x, pct))
    return "\n".join(lines)
