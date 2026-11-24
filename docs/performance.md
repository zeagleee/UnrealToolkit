# Performance runs

`ue.py perf run [LABEL]` profiles the running game (PIE, or a `ue.py net` instance with `--instance`). It:

1. runs any `--exec` console commands;
2. waits `--settle` seconds;
3. records the CSV profiler for `--seconds` of game time;
4. summarizes the file: median, p95 and max of frame, game thread, render thread, RHI thread and GPU time, draw calls
   and primitives, plus the game thread's heaviest exclusive categories.

The summary is saved under `Saved/UnrealToolkit/perf/<label>.json`.

```
ue.py perf run base
ue.py perf run no-shadows --exec "r.ShadowQuality 0"     # cvars stay set: put them back afterwards
ue.py perf compare base no-shadows                        # medians side by side with delta and %
ue.py perf list
```

- Measure A and B in the same PIE session, with the editor started `--no-throttle` (or in front). The baseline
  drifts between sessions, and a throttled editor runs at about 3 fps; a run that slow is flagged.
- `--stats Physics Net` adds CSV columns by substring.
- Checked on the template project (2026-11-23): 50% screen percentage took 27% off GPU time, and `r.ShadowQuality 0`
  took 16% off GPU time and 24 draw calls.
- In that background PIE editor, `RenderThreadTime` (and its critical path) was 0 in every frame; the summary says so
  when it happens.
