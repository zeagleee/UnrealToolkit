# UnrealToolkit

Command-line control of Unreal Engine for any Unreal project, made for AI coding agents (and scripts). Build,
run, test, inspect and edit a project without anyone clicking in the editor: Python in the live editor, offscreen
renders, multiplayer test sessions, performance runs, Blueprint graphs, materials, Enhanced Input, Niagara and
imports. Standard-library Python on your side; a Python bridge and two optional C++ editor plugins on Unreal's side.

**Status:** Windows, Unreal Engine 5.6–5.8, Python 3 (tested with 3.13). Used daily on real projects; expect rough
edges. MIT licensed.

## Quickstart

1. Clone this repo anywhere (say `C:/Tools/UnrealToolkit`). Nothing to install besides Python 3 (3.10 or newer).
   Or install it as a `ue` command: `pipx install git+https://github.com/zeagleee/UnrealToolkit`, then use `ue info`
   and so on wherever this page says `ue.py`.
2. From a folder that holds a `.uproject` (or pass `--project <path>` from anywhere):
   ```
   python C:/Tools/UnrealToolkit/ue.py info                  # the project, its engine, editor and bridge state
   python C:/Tools/UnrealToolkit/ue.py editor start          # builds, then starts the editor with the bridge
   python C:/Tools/UnrealToolkit/ue.py py "ueh.level()"      # Python in the running editor
   python C:/Tools/UnrealToolkit/ue.py plugins install       # optional C++ plugins: renders, Blueprint editing,
   python C:/Tools/UnrealToolkit/ue.py build --restart       #   material reading, Niagara, import helpers
   ```
3. For editors you start yourself (launcher, double-clicked `.uproject`), run `ue.py bridge install` once. Read
   [Security](#security) first.
4. Tell your agent about it, e.g. in the project's `CLAUDE.md` or `AGENTS.md`: "Drive Unreal with
   `python C:/Tools/UnrealToolkit/ue.py`; its commands are in C:/Tools/UnrealToolkit/README.md."
   [docs/agents.md](docs/agents.md) has a fuller block to paste, and a Claude Code skill.

The engine is found from the project's `EngineAssociation` (registry / Epic launcher manifest); override with
`UETK_ENGINE_ROOT`. `ue.py -h` and `ue.py <command> -h` list every option. Below, `ue.py` means
`python <toolkit folder>/ue.py`.

## Security

The bridge runs Python: a request file written into `<Project>/Saved/AgentBridge/inbox/` is executed in the editor,
with the editor's rights (your files, network, project). It's meant for your own agent on your own machine.

- Anything that can write into a bridged project's `Saved` folder can run code in that editor. Don't use it on a
  project folder that other people or untrusted programs can write to.
- `ue.py editor start` loads the bridge into the editor it starts. `ue.py bridge install` sets a per-user
  `UE_PYTHONPATH`, so every editor you start, in any project, loads it; `ue.py bridge uninstall` removes it.
- Game processes started by `ue.py net` get a bridge each, the same way.
- An agent driving the editor can change, save and delete assets. Keep the project in version control.

## Commands

| Need | Command |
|---|---|
| Status of engine/editor/bridge | `ue.py info`, `ue.py editor status` |
| Compile (editor closed) | `ue.py build` (`--target game`, `--config Shipping`, `--clean`, `-v`) |
| Compile with editor open, .cpp-only change | `ue.py livecoding` |
| Compile after header/UPROPERTY/UFUNCTION/new-class changes | `ue.py build --restart` (add `--save`/`--discard` if assets are dirty) |
| Start / stop the editor | `ue.py editor start [--map /Game/...]` (builds first), `ue.py editor stop [--save\|--discard]`, `ue.py editor wait` |
| Run Python in the live editor | `ue.py py "expr"`, `ue.py py -f script.py` or `ue.py py - <<'EOF' ... EOF` (last expression is printed as JSON) |
| A script that waits for the game | `ue.py py -f scenario.py`: a top-level `yield ueh.wait(5)` / `yield ueh.until(...)` makes it a **scenario** (below) |
| Block until something is true | `ue.py wait "<expr>" [--timeout 60]`, `ue.py wait --game-seconds 5` (exit code 1 on timeout) |
| Same without a running editor | `ue.py py --headless ...` (seconds, no rendering; no scenarios) |
| Console command | `ue.py exec "stat fps"`; in the running game: `ue.py exec --pie <cheat>` |
| Play-In-Editor | `ue.py pie start\|stop\|status`; `pie start --protect [paths]` snapshots `Saved/SaveGames` (or the paths) and `pie stop` puts them back |
| Full speed in the background | `ue.py editor start --no-throttle` (restored when the editor exits) |
| Put files back after a test | `ue.py snapshot save [paths]`, `snapshot restore`, `snapshot status` (what changed since) |
| See the editor / game | `ue.py screenshot` (then view the PNG), `ue.py windows` lists dialogs |
| See a spot, actor, asset or widget without the editor in front | `ue.py render view\|pie\|actor\|asset\|widget` (offscreen PNG; [Offscreen renders](docs/renders.md)) |
| Install/update the toolkit's editor plugins in a project | `ue.py plugins install`, then `ue.py build --restart`; `ue.py plugins status` |
| Logs & crashes | `ue.py log --errors`, `ue.py log --grep <regex>`, `ue.py crash` |
| Automation tests | `ue.py test [filter]` (headless; default = every test prefix declared in Source/) |
| Edit Blueprint graphs (nodes, wires, literals, variables, functions) | `bpedit` in `ue.py py`; `ue.py bp graph PATH` (nodes with ids), `ue.py bp compile PATH` ([Editing Blueprints](docs/editing-blueprints.md)) |
| Read every Blueprint as text (reviews) | `ue.py bpdump [/Game/Sub/Folder ...]` → `Saved/UnrealToolkit/bpdump/txt/` ([Reviewing Blueprints](docs/reviewing-blueprints.md)) |
| Save dirty assets | `ue.py save [paths...]` |
| Regenerate VS solution / package | `ue.py genproject`, `ue.py package --config Shipping` (Shipping skips the ~200 MB `.pdb`: `-nodebuginfo`) |
| New C++ project from an engine template, no editor UI | `python scripts/new_from_template.py <engine_root> TP_FirstPerson <dest> <Name> 5.8` (follows the template's `TemplateDefs.ini`: renames, content packs, class redirects). Then set the renderer settings before the first editor launch |
| Frame times / A-B a change | `ue.py perf run base`, `perf run b --exec "r.ShadowQuality 0"`, `perf compare base b` ([Performance runs](docs/performance.md)) |
| Real key/mouse input in PIE or a net instance (triggers and modifiers run) | `ue.py press R --hold 0.6`, `ue.py press E`, `ue.py mouse 5 -3 --for 0.5`; `yield from ueh.press(...)` in scenarios |
| Multiplayer test: server + clients as real processes | `ue.py net start --clients 2 [--dedicated] [--render client1]`, `ue.py --instance client1 py ...`, `net run test.py`, `net logs`, `net stop` ([Multiplayer sessions](docs/multiplayer.md)) |
| Particle effects (Niagara) | `ue.py vfx create /Game/VFX/NS_Fire --preset fire --preview`, `vfx preview`, `vfx describe` ([VFX](docs/vfx.md)) |
| Materials: build from expressions, read one back, instances, functions | `ue.py mat create /Game/M_X --spec m.json --preview`, `mat describe`, `mat instance MI_X --parent M_X --set Roughness=0.3` ([Materials](docs/materials.md)) |
| Enhanced Input: read or build actions and mapping contexts, map keys, contexts on Blueprints and the running player | `ue.py input dump`, `input create --spec i.json`, `input map IMC_X IA_Y LeftShift`, `input use BP_Controller IMC_X`, `input active` ([Enhanced Input](docs/input.md)) |
| Menus and HUD in the running game; Widget Blueprints from a spec | `ue.py ui tree`, `ui click "Save game"`, `ui type PlayerName Ada`, `ui wait Paused`, `ui create WBP_X --spec m.json` ([Menus and HUD](docs/ui.md)) |
| Import meshes, animations, textures and sounds (folders too) with presets; reimport what changed | `ue.py import D:/Art --to /Game/Art`, `import --status /Game/Art`, `import --reimport /Game/Art` ([Importing](docs/importing.md)) |
| Check the project before a commit or in CI (exit code 1 on errors) | `ue.py validate [/Game/Folder]`, `--checks all`, `--json`, `--fix-redirectors` ([Validating the project](docs/validation.md)) |
| Hung editor | `ue.py kill` (loses unsaved work; check `ueh.dirty()` first) |
| Bridge in self-launched editors | `ue.py bridge install` / `uninstall` (per-user `UE_PYTHONPATH`) |

## In-editor helpers (`ueh`)

In `ue.py py`, `unreal` and `ueh` (`editor/ueh.py`) are pre-imported; `ueh.help()` lists everything:

- assets: `find`, `info`, `deps`, `refs`, `duplicate`, `rename`, `delete`, `fix_redirectors`, `refresh_registry`, `import_files`, `create_bp`, `create_asset`
- properties: `props`, `get`, `set`, `export_text` (Blueprint paths resolve to the CDO; `export_text` shows every UPROPERTY incl. BP variables)
- Blueprints: `compile_bp`, `compile_bps`, `reparent_bp`; data tables: `dt_json`, `dt_fill`
- level: `level`, `open_level`, `save_level`, `actors`, `actor`, `spawn`, `move`, `destroy`, `select`, `components`, `camera`, `set_camera`, `focus`
  (`spawn`, `actors`, `actor` and `destroy` take `world='game'` for the running game: [Spawning into the running game](docs/spawning.md))
- PIE: `pie_start`, `pie_stop`, `is_pie`, `pie_world`, `player`, `console`; saving: `dirty`, `save`, `save_object`

Variables persist between `py` calls within one editor session, in one shared namespace: a quick `sub = ...` in a
scratch call replaces a function of that name a loaded test script defined, and the script then fails mid-run. Use
distinctive names in throwaway calls. Edits to `ueh.py`, `agent_bridge.py`, `scenario.py`
and the other helper modules take effect on the next call (hot reload, no editor restart). A helper module added to
the toolkit while an editor runs is loaded on its next call.

## Scenarios: scripts that wait for the game

A plain `py` call runs on the game thread, so the game is frozen until it returns. "Do X, wait, check Y"
used to mean several calls with `sleep`s in between and guessed timings. A cell with a **top-level `yield`**
runs as a scenario instead: the bridge resumes it on later ticks while the game keeps running, and the
client waits for the end, printing the scenario's output as it comes.

```python
ueh.console("RMStartDay 1", pie=True)
yield ueh.wait(5)                                        # 5 s of PIE game time (wall clock outside PIE); `yield 5` too
box = yield ueh.until(lambda: find_box(), timeout=30)    # resumes when truthy and returns the value
yield ueh.frames(2)                                      # 2 editor ticks; a bare `yield` is 1
{"credits": state()["credits"]}                          # the trailing expression is the result
```

- Variables the scenario assigns stay in the session like a plain cell's. `until` raises `TimeoutError` inside the
  scenario after `timeout` wall-clock seconds (catch it to carry on); other exceptions end it with a traceback.
- Give the call a budget: `ue.py py -f s.py --timeout 300`. On a timeout or Ctrl+C the editor stops the scenario.
  Other `py` calls still answer while one runs (peek at state from a second terminal).
- Game time pauses with the game (pause menu, breakpoints): a `wait` then waits too. Use `wait_real` for wall time.
- Waits are checked in a Slate post-tick callback, once per editor frame.

## Project helpers

`<Project>/.uetk/helpers.py`, if it exists, is loaded into every `py` call: the module as `proj`, plus its
public names (its `__all__`, or the functions and classes it defines) directly. It reloads when the file changes,
and `ueh.help()` lists it. Put a project's recurring snippets there (game-state readers, cheat wrappers, the
steps of a test) instead of retyping them each session. It is compiled from source on every load, so quick
same-size edits are never served from a stale `.pyc`.

## Guides

One page per feature, each ending with what it was checked on and what tripped up the first version:

- [Materials](docs/materials.md)
- [Enhanced Input](docs/input.md)
- [Menus and HUD (UMG)](docs/ui.md)
- [Spawning into the running game](docs/spawning.md)
- [Importing](docs/importing.md)
- [Validating the project](docs/validation.md)
- [VFX (Niagara)](docs/vfx.md)
- [Multiplayer sessions](docs/multiplayer.md)
- [Performance runs](docs/performance.md)
- [Editing Blueprints](docs/editing-blueprints.md)
- [Reviewing Blueprints](docs/reviewing-blueprints.md)
- [Offscreen renders](docs/renders.md)

Reference pages: [every command and its options](docs/commands.md) and [every editor-side helper](docs/helpers.md)
(both generated by `scripts/gen_docs.py`), and [instructions to give your agent](docs/agents.md). General Unreal
Engine knowledge is in [docs/unreal-notes.md](docs/unreal-notes.md).

## Editor plugins

Some things Python can't reach, so the toolkit ships C++ editor plugins (`plugin/`). `ue.py plugins install` copies
all of them into the project's `Plugins/` folder (source only), and `ue.py build --restart` compiles them. Run it
again after a toolkit update: a `.cpp`-only change can go in with `ue.py livecoding`, a header change needs
`build --restart`. `ue.py plugins status` shows each one as installed, built, or loaded.

- **UETKEditor:** offscreen renders (`ue.py render`), Blueprint graph editing (`bpedit`), reading material
  graphs and compile errors (`mat`), UMG widget trees (`ui`), import helpers (collision, LOD groups), and Data
  Validation, map check and redirector fix-up results (`validate`). It turns on the engine's Data Validation plugin
  (on by default anyway). It also holds the shared capture code (`FUETKCapture`, `FUETKContactSheet` in
  `UETKCapture.h`).
- **UETKVfx:** Niagara (`ue.py vfx`). It needs UETKEditor, and `vfx install` brings it along.
- **Live Coding blocks other builds on the same engine.** While one editor has Live Coding active, UBT refuses to build
  *another* project on that engine install ("Unable to build while Live Coding is active"). Close that editor first.

## Leaving the machine as it was

- **Save games:** any PIE run can overwrite them. `ue.py pie start --protect` snapshots `Saved/SaveGames` (pass
  other project paths to protect those); `pie stop`, `editor stop` and `kill` mirror them back exactly (new files
  removed). If PIE ended another way, the next `pie stop`, `pie start --protect` or `editor start` restores it.
  `ue.py snapshot status` shows what changed since the snapshot.
- **Background throttling:** `editor start --no-throttle` (also `restart`) sets `bThrottleCPUWhenNotForeground=False`
  in the per-user `EditorSettings.ini` before launch and puts the user's value back after the editor exits (the editor
  rewrites that file on the way out, so it can't be done earlier). `build --restart` and `editor restart` keep it.
  It is shared by every project on the same engine version, so it's restored only when the last editor that asked for
  it has closed. State: `%LOCALAPPDATA%/UnrealToolkit/throttle-<version>.json`.

## Rules of thumb

- Nothing is saved unless you pass `save=True` or run `ue.py save`. Check `ueh.dirty()` before stopping or killing the editor; never discard someone's unsaved work without asking.
- Prefer `editor stop` over `kill`: caches are written on a graceful exit, so the next start is much faster.
- Log lines emitted during a `py` call are shown under `--- log ---` (repeats collapsed); Blueprint compile errors appear there.
- `ueh.actors()` returns at most 500 actors by default; filter with `cls=` / `name=`.
- Paths passed on Unreal command lines must use forward slashes (`\U...` is parsed as an escape).
- **UBT's shared log:** by default every UnrealBuildTool run renames `%LOCALAPPDATA%/UnrealBuildTool/Log.txt` on start,
  and fails in about 3 s ("IOException: ... being used by another process") while another UBT has it open (another
  editor's). `ue.py build` passes `-Log=<Project>/Saved/UnrealToolkit/ubt-log.txt` so builds don't collide (2026-11-22).
- UBT, `genproject` and toolkit-launched editors run with the engine's bundled .NET (`Engine/Binaries/ThirdParty/DotNet/<ver>/<arch>`),
  the same setup `Build.bat` does through `GetDotnetPath.bat`. UE 5.8's UBT needs .NET 10, which usually isn't installed system-wide.
  Set `UE_USE_SYSTEM_DOTNET=1` to use the system .NET instead.
- **A dialog at startup stops the editor** until someone answers it: a "Low Drive Space" warning (UE 5.7 shows one
  when C: is nearly full), "restore packages?" after a crash. `ue.py editor start` names it after about 45 s, when the
  log has stopped too; the toolkit never sends keys or clicks to answer it.
- **Blueprint-only projects:** `ue.py editor start` fails with "No Editor target in Source/*.Target.cs". Launch `Engine/Binaries/Win64/UnrealEditor.exe <uproject> <map>` yourself, then `ue.py editor wait`. The bridge comes up if the per-user `UE_PYTHONPATH` is set.
- **Screenshots of a spot in the level:** `ueh.set_camera` moves the camera, but an unfocused editor doesn't redraw, so `ue.py screenshot` shows the old view. `editor_set_viewport_realtime(True)` also logs a handled ensure.
  Use `ue.py render view --at x,y,z --look x,y,z` instead ([Offscreen renders](docs/renders.md)); it meters dark interiors to mid-gray, so the temporary light and hand-set exposure the old recipe needed aren't.
- **`ueh` defines its own `set()` helper,** which hides Python's built-in `set` inside `ueh.py`. Use a dict there.
- **Struct arrays come back as copies** (e.g. a static mesh's `static_materials`). Change element `i`, write it back with `arr[i] = s`, then `set_editor_property` the whole array. `for s in arr: s.set_editor_property(...)` silently does nothing.
- **A texture with `srgb` off** needs `SAMPLERTYPE_LINEAR_COLOR` on its texture-sample expression. Otherwise the material fails to compile and falls back to the default material.
- **Background throttling** (`bThrottleCPUWhenNotForeground`, about 3 fps when unfocused) can't be changed from Python or `set`: use `ue.py editor start --no-throttle` (see [Leaving the machine as it was](#leaving-the-machine-as-it-was)).
- **Never send synthetic key presses** (`keybd_event`, `SendKeys`) to test input. Windows won't hand the foreground to the editor from a background process, so the keys land in the user's terminal.
  - `inject_input_vector_for_action` tests the code paths. It skips the mapping-level triggers (Tap, Hold).
  - It skips the mapping's modifiers too: the injected vector is the action value (no Negate, no Mouse2D 0.07 sensitivity).
  - **To run the triggers and modifiers too, force a real key:**
    - From the command line: `ue.py press R --hold 0.6`, `ue.py press E` (a 0.08 s tap), `ue.py mouse 5 -3 --for 0.5`
      (a per-frame Mouse XY delta).
    - In scenarios: `yield from ueh.press('R', 0.6)`, `yield from ueh.mouse(5, -3, 0.5)`; `ueh.key_down`/`key_up`/
      `release_all` for manual control.
    - All of them work in a `ue.py net` instance with `--instance`.
    - Under the hood are Enhanced Input's cheat commands `Input.+key <Key> [value]` / `Input.-key`. They feed the key
      each frame through the active mappings, so Tap/Hold triggers, chords and modifiers run, and the OS keyboard is
      never touched.
    - Holds are timed in game time, or in wall time while the game is paused: a key can pause it mid-hold (P opening a
      pause menu), and game time stops then. The key is released even if the scenario is cancelled.
    - Checked in the template project (2026-11-23): forced Mouse2D turned the camera through IA_Look, a held SpaceBar
      jumped 86 cm, and a tap of P opened the pause menu.
  - **Forced keys reach gameplay input, not a focused UMG widget.** With a menu open in UI-only input mode, the menu
    handles keys itself as Slate key events, so pressing P again doesn't close it. Drive menus with `ue.py ui`
    (see [Menus and HUD](docs/ui.md)).
  - **Pick the subsystem by world.** After a PIE restart, the newest `EnhancedInputLocalPlayerSubsystem` can be a leftover, or another PIE player's. Use the one whose `get_world()` is the PIE world.
- **`PlayNumberOfClients` persists** in the project's `Saved/Config/WindowsEditor/EditorPerProjectUserSettings.ini`. A test that set it to 2 and didn't set it back leaves every later PIE running two players. Check it before trusting single-player results.
- **Scripted PIE waits counted in frames** are short at full frame rate (70 frames < 1 s at 100+ fps). Wait generously for anything timed.
- **Python odds and ends:**
  - `SystemLibrary.line_trace_single` returns `None` on a miss. On a hit, `hit.to_tuple()[4]` is the location.
  - `unreal.Key()` takes no arguments: `k = unreal.Key(); k.set_editor_property('key_name', 'F')`.
  - A `ChildActorComponent`'s actor is `get_editor_property('child_actor')`. On a Blueprint template, set the class with `set_child_actor_class(cls)`.
  - A player state's id is `ps.get_editor_property('player_id')` (no `get_player_id`). Destroying an actor is `destroy_actor()`. The game state is `GameplayStatics.get_game_state(world)`.
  - **`SystemLibrary.execute_console_command(None, 'NetEmulation.Off')` with no PIE running crashes the editor** (`HandleInvalidWorldContext`, a `check`). Pass a live PIE world, and turn emulation off before stopping PIE.
  - **In multi-player PIE, actors spawned at runtime have different names in each world** (level-placed ones keep theirs). Pair copies by what they're attached to or owned by.
- **Adding components to a Blueprint from Python:** `SubobjectDataSubsystem` (an engine subsystem).
  - The calls: `k2_gather_subobject_data_for_blueprint(bp)`; `add_new_subobject(AddNewSubobjectParams(parent_handle=..., new_class=..., blueprint_context=bp))`; `rename_subobject(h, unreal.Text(name))`.
  - The template object: `SubobjectDataBlueprintFunctionLibrary.get_object(get_data(h))`.
  - Actor components hang under the Blueprint's own handle (the first one). A new Blueprint's CDO has no `root_component`, so take the first scene component's handle.
  - `get_variable_name` can repeat children in the list; match by name.
  - Native (C++) components: set them on the CDO's component (`cdo.get_editor_property('Name').set_editor_property(...)`), then compile and save.
- **Not exposed to Python:**
  - `SubsystemBlueprintLibrary`: iterate `unreal.ObjectIterator(unreal.EnhancedInputLocalPlayerSubsystem)` instead.
  - `GameplayStatics.begin_deferred_actor_spawn_from_class`: `ueh.spawn(..., world='game')` spawns into PIE through the
    plugin instead ([Spawning into the running game](docs/spawning.md)).
  - Some settings only take their C++ name: `set_editor_property('PlayNumberOfClients', 2)` on `Default__LevelEditorPlaySettings` works, the snake_case name fails.
- **Git Bash rewrites arguments that start with `/`:** `/Game/X` arrives as `C:/Program Files/Git/Game/X`. `ue.py`
  puts such paths back (an argument under Git's install folder that doesn't exist there), so `/Game/...` arguments
  work as typed. Other programs still need `MSYS_NO_PATHCONV=1`.

More Unreal Engine knowledge that isn't about the toolkit (measuring performance, scene captures, rendering
cost and scalability, networking, online sessions, project setup, packaging, importing from Blender) is in
[docs/unreal-notes.md](docs/unreal-notes.md).

## Layout

| Path | Runs in | Purpose |
|---|---|---|
| `ue.py` | system Python | CLI entry point |
| `uetk/project.py` | system Python | finds the `.uproject`, its engine install, targets, paths |
| `uetk/build.py` | system Python | UnrealBuildTool builds, project files, headless automation tests, BuildCookRun packaging |
| `uetk/editor.py` | system Python | start (with bridge) / wait / stop (dirty-check, save or discard) / restart / kill |
| `uetk/bridge.py` | system Python | bridge client, headless `-run=pythonscript` runner, optional per-user auto-load |
| `uetk/procs.py`, `uetk/winshot.py` | system Python | process & window discovery, PrintWindow screenshots to PNG |
| `uetk/logs.py` | system Python | active log, incremental reads, problem filtering, crash summaries |
| `editor/agent_bridge.py` | UnrealEditor | executes requests from `<Project>/Saved/AgentBridge/inbox` on the game thread; runs scenarios; loads project helpers |
| `editor/scenario.py` | UnrealEditor (no `unreal` import) | scenario cells: rewriting a top-level `yield` cell, waits, stepping |
| `editor/ueh.py` | UnrealEditor | helper library pre-imported as `ueh` |
| `uetk/state.py` | system Python | snapshots (`snapshot`, `pie --protect`) and the `--no-throttle` setting |
| `editor/init_unreal.py` | UnrealEditor | starts the bridge when this folder is on `UE_PYTHONPATH` |
| `editor/headless_runner.py` | UnrealEditor-Cmd | entry script for `ue.py py --headless` |
| `plugin/UETKVfx` | UnrealEditor (C++ plugin, copied into projects) | Niagara authoring, stack dumps, offscreen previews (`unreal.UETKVfxLibrary`) |
| `editor/bpedit.py` | UnrealEditor | Blueprint graph editing on top of `unreal.UETKBlueprintLibrary` |
| `editor/mat.py` | UnrealEditor | materials from expression specs, reading them back (`unreal.UETKMaterialLibrary`), instances, functions |
| `editor/inputs.py` | UnrealEditor | Enhanced Input actions, mapping contexts, triggers and modifiers as specs; contexts on Blueprints and in the running game |
| `editor/uprop.py` | UnrealEditor | property plumbing shared by `mat`, `inputs` and `importer`: loose enum names, structs from lists, asset paths, PIE-safe loading |
| `editor/ui.py` | UnrealEditor or a game process | UMG: the widgets on screen, clicking and typing, Widget Blueprints from a spec (`unreal.UETKWidgetLibrary`) |
| `editor/importer.py` | UnrealEditor (or headless) | importing files and folders with presets, the source record in each asset, reimport of changed sources |
| `editor/vfx.py`, `vfx_assets.py`, `vfx_presets.py` | UnrealEditor (presets: any Python) | VFX spec builder, generated particle materials/textures, presets |
| `editor/validate.py` | UnrealEditor (or headless) | `ue.py validate`: Data Validation, Blueprint compiles, references, redirectors, textures, map check, unused assets, naming |
| `plugin/UETKEditor` | UnrealEditor (C++ plugin, copied into projects) | offscreen renders (`unreal.UETKEditorLibrary`, `FUETKCapture` for other plugins), spawning into the running game (`unreal.UETKGameLibrary`), Blueprint graphs, material graphs and compile results (`unreal.UETKMaterialLibrary`), validation (`unreal.UETKValidateLibrary`) |
| `uetk/perf.py` | system Python | `ue.py perf`: CSV profiler runs, parsing (header at the end), summaries, A/B |
| `uetk/net.py` | system Python | `ue.py net`: game processes with their own bridges, session state, merged logs, `NetSession` for test scripts |
| `uetk/plugins.py` | system Python | `ue.py plugins install/status`: copies the toolkit's plugins into a project; warns when a project's copy is out of date |
| `uetk/bpdump.py` | system Python | `ue.py bpdump`: condenses exported Blueprints (T3D) into readable text |
| `scripts/` | system Python | `new_from_template.py` (projects from engine templates), `gen_docs.py` (the generated reference pages), `net_smoke_test.py` |
| `tests/` | system Python | the engine-free tests: `python -m unittest discover -s tests` |
| `docs/` | | feature guides, the generated command and helper references, agent instructions, Unreal Engine notes |
| `skills/unreal-toolkit/` | | a Claude Code skill with the agent instructions |
| `pyproject.toml` | | packaging: `pipx install` gives a `ue` command |

## How the bridge works

`ue.py editor start` launches `UnrealEditor.exe` with `UE_PYTHONPATH` pointing at `editor/`, so Unreal's
Python plugin runs `editor/init_unreal.py` at startup. That registers a Slate post-tick callback which
polls `<Project>/Saved/AgentBridge/inbox/*.json`, runs the code like a REPL cell (statements, then the
trailing expression is returned), captures stdout and tracebacks, flushes the log, and writes the
response to `outbox/`. The client also returns the log lines written during the call. Plain files instead
of sockets mean there is no firewall or port setup, and nothing in the project's config changes.

Editors launched by hand (Epic launcher, double-clicked `.uproject`) only get the bridge after
`ue.py bridge install`. A hand-written `Saved/Config/WindowsEditor/Engine.ini` entry does not work in UE 5.6:
the editor deletes it.

Outputs (build logs, test reports, screenshots, headless logs) go to `<Project>/Saved/UnrealToolkit/`.

## Contributing

Pull requests are welcome, and many tasks don't need Unreal Engine installed: the
[good first issues](https://github.com/zeagleee/UnrealToolkit/labels/good%20first%20issue) need only Python 3 and git.
[CONTRIBUTING.md](CONTRIBUTING.md) says how to take one and run the tests. Questions and ideas go to
[Discussions](https://github.com/zeagleee/UnrealToolkit/discussions).
