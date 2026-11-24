# UnrealToolkit

Command-line control of Unreal Engine for any Unreal project, made for AI coding agents (and scripts). Build,
run, test, inspect and edit a project without anyone clicking in the editor: Python in the live editor, offscreen
renders, multiplayer test sessions, performance runs, Blueprint graphs, materials, Enhanced Input, Niagara and
imports. Standard-library Python on your side; a Python bridge and two optional C++ editor plugins on Unreal's side.

**Status:** Windows, Unreal Engine 5.6–5.8, Python 3 (tested with 3.13). Used daily on real projects; expect rough
edges. MIT licensed.

## Quickstart

1. Clone this repo anywhere (say `C:/Tools/UnrealToolkit`). Nothing to install besides Python 3.
2. From a folder that holds a `.uproject` (or pass `--project <path>` from anywhere):
   ```
   python C:/Tools/UnrealToolkit/ue.py info                  # the project, its engine, editor and bridge state
   python C:/Tools/UnrealToolkit/ue.py editor start          # builds, then starts the editor with the bridge
   python C:/Tools/UnrealToolkit/ue.py py "ueh.level()"      # Python in the running editor
   python C:/Tools/UnrealToolkit/ue.py plugins install       # optional C++ plugins: renders, Blueprint editing,
   python C:/Tools/UnrealToolkit/ue.py build --restart       #   material reading, Niagara, import helpers
   ```
3. For editors you start yourself (launcher, double-clicked `.uproject`), run `ue.py bridge install` once. Read
   "Security" first.
4. Tell your agent about it, e.g. in the project's `CLAUDE.md` or `AGENTS.md`: "Drive Unreal with
   `python C:/Tools/UnrealToolkit/ue.py`; its commands are in C:/Tools/UnrealToolkit/README.md."

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
| See a spot, actor, asset or widget without the editor in front | `ue.py render view\|pie\|actor\|asset\|widget` (offscreen PNG; below, "Offscreen renders") |
| Install/update the toolkit's editor plugins in a project | `ue.py plugins install`, then `ue.py build --restart`; `ue.py plugins status` |
| Logs & crashes | `ue.py log --errors`, `ue.py log --grep <regex>`, `ue.py crash` |
| Automation tests | `ue.py test [filter]` (headless; default = every test prefix declared in Source/) |
| Edit Blueprint graphs (nodes, wires, literals, variables, functions) | `bpedit` in `ue.py py`; `ue.py bp graph PATH` (nodes with ids), `ue.py bp compile PATH` (below, "Editing Blueprints") |
| Read every Blueprint as text (reviews) | `ue.py bpdump [/Game/Sub/Folder ...]` → `Saved/UnrealToolkit/bpdump/txt/` (below, "Reviewing Blueprints") |
| Save dirty assets | `ue.py save [paths...]` |
| Regenerate VS solution / package | `ue.py genproject`, `ue.py package --config Shipping` (Shipping skips the ~200 MB `.pdb`: `-nodebuginfo`) |
| New C++ project from an engine template, no editor UI | `python scripts/new_from_template.py <engine_root> TP_FirstPerson <dest> <Name> 5.8` (follows the template's `TemplateDefs.ini`: renames, content packs, class redirects). Then set the renderer settings before the first editor launch |
| Frame times / A-B a change | `ue.py perf run base`, `perf run b --exec "r.ShadowQuality 0"`, `perf compare base b` (below, "Performance runs") |
| Real key/mouse input in PIE or a net instance (triggers and modifiers run) | `ue.py press R --hold 0.6`, `ue.py press E`, `ue.py mouse 5 -3 --for 0.5`; `yield from ueh.press(...)` in scenarios |
| Multiplayer test: server + clients as real processes | `ue.py net start --clients 2 [--dedicated] [--render client1]`, `ue.py --instance client1 py ...`, `net run test.py`, `net logs`, `net stop` (below, "Multiplayer sessions") |
| Particle effects (Niagara) | `ue.py vfx create /Game/VFX/NS_Fire --preset fire --preview`, `vfx preview`, `vfx describe` (below, "VFX") |
| Materials: build from expressions, read one back, instances, functions | `ue.py mat create /Game/M_X --spec m.json --preview`, `mat describe`, `mat instance MI_X --parent M_X --set Roughness=0.3` (below, "Materials") |
| Enhanced Input: read or build actions and mapping contexts, map keys, contexts on Blueprints and the running player | `ue.py input dump`, `input create --spec i.json`, `input map IMC_X IA_Y LeftShift`, `input use BP_Controller IMC_X`, `input active` (below, "Enhanced Input") |
| Menus and HUD in the running game; Widget Blueprints from a spec | `ue.py ui tree`, `ui click "Save game"`, `ui type PlayerName Ada`, `ui wait Paused`, `ui create WBP_X --spec m.json` (below, "Menus and HUD") |
| Import meshes, animations, textures and sounds (folders too) with presets; reimport what changed | `ue.py import D:/Art --to /Game/Art`, `import --status /Game/Art`, `import --reimport /Game/Art` (below, "Importing") |
| Check the project before a commit or in CI (exit code 1 on errors) | `ue.py validate [/Game/Folder]`, `--checks all`, `--json`, `--fix-redirectors` (below, "Validating the project") |
| Hung editor | `ue.py kill` (loses unsaved work; check `ueh.dirty()` first) |
| Bridge in self-launched editors | `ue.py bridge install` / `uninstall` (per-user `UE_PYTHONPATH`) |

## In-editor helpers (`ueh`)

In `ue.py py`, `unreal` and `ueh` (`editor/ueh.py`) are pre-imported; `ueh.help()` lists everything:

- assets: `find`, `info`, `deps`, `refs`, `duplicate`, `rename`, `delete`, `fix_redirectors`, `refresh_registry`, `import_files`, `create_bp`, `create_asset`
- properties: `props`, `get`, `set`, `export_text` (Blueprint paths resolve to the CDO; `export_text` shows every UPROPERTY incl. BP variables)
- Blueprints: `compile_bp`, `compile_bps`, `reparent_bp`; data tables: `dt_json`, `dt_fill`
- level: `level`, `open_level`, `save_level`, `actors`, `actor`, `spawn`, `move`, `destroy`, `select`, `components`, `camera`, `set_camera`, `focus`
  (`spawn`, `actors`, `actor` and `destroy` take `world='game'` for the running game: below, "Spawning into the running game")
- PIE: `pie_start`, `pie_stop`, `is_pie`, `pie_world`, `player`, `console`; saving: `dirty`, `save`, `save_object`

Variables persist between `py` calls within one editor session. Edits to `ueh.py`, `agent_bridge.py`, `scenario.py`
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

## Reviewing Blueprints

`ue.py bpdump` makes Blueprints readable without opening the editor UI. It exports every Blueprint, widget, animation Blueprint, level Blueprint, data asset, struct, enum and data table under the given roots (default `/Game`). It then condenses each one into a text file.

- **Where the export runs:** in the running editor when there is one, otherwise headless. Expect about a minute for 50 Blueprints.
- **What it changes:** it loads, and so compiles, every Blueprint, and saves nothing.
- **What it skips by default:** the template folders (Mannequins, LevelPrototyping, StarterContent) and the External Actors/Objects folders. Add more with `--skip /Game/SomePack`.
- **Not yet tested:** level Blueprint export. The project it was first used on has no level Blueprints, so the lookup (`<map>.<map>:PersistentLevel.<map>`) hasn't been tried on a map that has one.

**What each Blueprint's `.txt` holds** (the format is documented at the top of `uetk/bpdump.py`):
- the parent class
- variables, with replication flags and defaults
- components added in the Blueprint
- **only the class defaults that differ from the parent class**
- for widgets, the nested widget tree and property bindings
- every graph as pseudo-code:
  - one block per event or function
  - `nN:` statements, with branch outputs as `[Pin]:`
  - pure nodes inlined as expressions
  - "Unreachable exec nodes" lists disconnected logic

Start with `_index.txt`.

- **Raw files:** the raw T3D sits in `bpdump/raw/` for checking a surprising result. Nodes are `Begin Object Name="K2Node_..."` blocks, and links are `LinkedTo=(NodeName PinId,...)`.
- **Pin ids repeat across copy-pasted nodes.** When you trace links by hand, resolve them by node name first.

Live equivalent: `ueh.export_blueprints(out_dir, roots, skip)` writes the raw files, and `python uetk/bpdump.py <raw> <out>` condenses them. Its first use, on a multiplayer game, found a disconnected match-end graph and a bot Blueprint that the game never spawned. A C++-only review can't see either.

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

## Offscreen renders

`ue.py render` writes PNGs to `Saved/UnrealToolkit/renders/` (view them to check work). They work with the editor in
the background or minimized, don't move the viewport camera, and don't change the level. The same calls are in
`ue.py py` as `ueh.render_view`, `render_pie`, `render_actor`, `render_asset` and `render_widget`.

| What | Command | Notes |
|---|---|---|
| A spot in the open level | `render view` (viewport camera), `--at x,y,z --look x,y,z`, or `--rot p,y,r` | 1600x900, FOV 90 |
| What a PIE player sees | `render pie [--player N]` | The camera's location, FOV and first-person settings. The player's own body is hidden. No HUD |
| A placed actor | `render actor LABEL [--yaws 0,90] [--isolate]` | Four 3/4 views fitted to its bounds; `--isolate` drops the surroundings |
| An asset | `render asset /Game/... [--mesh /Engine/BasicShapes/Plane]` | Static or skeletal mesh, material (a surface material on a sphere or `--mesh`; a post-process material over a sphere and a cube), Blueprint actor, texture (its image), widget Blueprint; Niagara goes to `vfx preview`. A lit preview scene like the asset editors' |
| A UMG widget | `render widget WBP_or_Class [--size 1920x1080] [--pie]` | The project's DPI scale. `--pie` draws the live instance on screen, with its real state; without it a fresh instance is made in the editor level, so game-driven parts are at their defaults |

- **Post-process materials:** `--post /Game/PP_Outline[,/Game/PP_Grade]` applies them to `view`, `pie`, `actor` and
  `asset` renders (`post=` in `ueh.render_*`).
- **Exposure:** each render is metered to mid-gray (a geometric mean, so a bright sky doesn't darken everything).
  Multi-view renders share one exposure so frames compare. `--exposure N` fixes it instead.
- **Before it renders, it waits for** pending loads, texture streaming, mesh and texture builds, and the shaders the
  editor compiles on demand. A `warning` in the result means something was still compiling; render again.
- **What tripped up the first version (2026-11-23):**
  - The preview scene's sky light captures its cubemap only when the world ticks. Untouched, shadow sides were
    black, so `USkyLightComponent::UpdateSkyCaptureContents(World)` runs first.
  - A static mesh loaded for the first time in a fresh copy of a project drew nothing: its render data is built
    asynchronously. Fixed by `FAssetCompilingManager::Get().FinishAllCompilation()`.
  - A first-person character's camera rendered the inside of its own body: the "owner no see" body has no owner to
    hide from in a capture. `render pie` passes those components as hidden.
  - A capture with no view state can't use eye adaptation, so exposure is metered by rendering, measuring, and
    rendering again.

## Multiplayer sessions

`ue.py net start` runs a listen server (or `--dedicated` server) and N clients as separate game processes. Each is
`UnrealEditor.exe <project> -game|-server`, headless (`-nullrhi`, `-nosound`, `-nosteam`) unless named in `--render`.
Each gets its own bridge (`-UETKBridge=<name>`), so any command can target one with `--instance`:

```
ue.py net start --map /Game/Maps/Lvl_Arena --clients 2      # waits for every bridge and for the clients to join
ue.py --instance client1 py "ueh.net_info()"               # {role, map, players, time}; exec, wait and render work too
ue.py --instance client2 exec "MyCheat 1"
ue.py net status                                            # role, map, players and game time of each process
ue.py net logs --errors                                     # every process's log merged by timestamp, name-prefixed
ue.py net run my_test.py                                    # a test script with `net` in scope; scripts/net_smoke_test.py
ue.py net stop                                              # kills them all; --protect on start restores SaveGames
```

- **Why separate processes:** in-process multi-player PIE can't change maps (README, "Online sessions"), and its players
  share one Python interpreter. These are real client/server processes, connected over IP on `127.0.0.1:<port>`.
- **In a game process, `ueh` runs against the game world.** `ueh.game_world()`, `player()`, `console()`, scenario
  waits and `render_pie` all work there, and `ueh.is_editor()` is False. Editor-only helpers (level editing, assets)
  don't apply.
- **`--render client1`** gives that client a 960x540 window, so `ue.py --instance client1 render pie` shows what it
  sees. Keep the rest headless: several rendering game processes beside an editor exhausted a 16 GB machine
  (README, "Online sessions").
- **Steam:** off by default (`-nosteam`: Null subsystem, direct IP). `--steam` leaves it on, but two processes on one
  PC can't both be the same Steam user.
- **Dedicated servers run no Slate**, which is what Python's tick hooks (and so the bridge) use. There the bridge ticks
  from the UETKEditor plugin's engine-loop ticker (`unreal.UETKTicker`), so install the plugins first.
- `procs` now tells these `game` processes apart from the editor, so `editor stop`/`start` leave them alone;
  `ue.py kill` still kills everything of the project.
- **Changing `agent_bridge.py` while editors run:** each bridge hot-reloads when its source changes. A reload of a
  half-finished edit once left an editor without a bridge (a `NameError`). It now keeps the old bridge and retries at
  the next change.

## VFX (Niagara particle systems)

Python can't build Niagara systems on its own: almost nothing in Niagara's editor is exposed to scripting. The
**UETKVfx** editor plugin (`plugin/UETKVfx`, C++) drives the Niagara editor's own stack view models headlessly,
so static switches, rapid-iteration parameters, dynamic inputs and edit conditions behave as if someone clicked
them. `editor/vfx.py` (pre-imported as `vfx` in `ue.py py`) adds a spec builder, presets, shared materials, and
offscreen previews.

- **Setup, once per project:** `ue.py plugins install` (or `ue.py vfx install`, which installs UETKVfx and the
  UETKEditor it needs), then `ue.py build --restart`. See "Editor plugins" above.
- **Engine versions:** everything, previews included, was tested on UE 5.8. On 5.7 and 5.6 the plugin builds, and
  building, compiling and describing presets works headless (`ue.py py --headless`); previews there are untried.
  Headless works on any version except previews, which need a rendering editor.
- **Presets:** `ue.py vfx presets` lists them: sparks, explosion, impact, muzzle_flash, confetti, fire, smoke,
  magic, heal, summon, fountain, rain, snow, dust, fireflies and orbit_trail.
  `ue.py vfx create /Game/VFX/NS_Boom --preset explosion --set scale=2 "color=[0.3,0.6,1]" --preview` builds,
  compiles and saves one, then renders a preview.
- **Your own effects:** a spec (a dict or a JSON file, `vfx create PATH --spec file.json`) lists emitters by intent:
  spawn, lifetime, color, size, shape, velocity, forces, over-life curves and renderers. The full format is in
  `editor/vfx.py`'s docstring, and every preset in `editor/vfx_presets.py` is a working example.
  `vfx_presets.make('fire')` returns a preset's spec to edit before `vfx.build(spec, path)`.
- **Anything the spec doesn't cover:** the same calls the spec uses are public, e.g.
  `vfx.add_module(s, 'Sparks', 'particle_update', 'vortex', before='Solve Forces and Velocity')` and
  `vfx.set_input(s, 'Sparks', 'particle_spawn', 'Initialize Particle', 'Lifetime', {'random': [1, 2]})`.
  Values can be constants, `{'random': [a, b]}`, `{'curve': [[t, v], ...]}`, `{'link': 'Particles.NormalizedAge'}`,
  `{'expression': ...}`, `{'dynamic': path, 'inputs': {...}}` or `{'object': path}`. Any module script works
  (`vfx.module_catalog('noise')` lists them), and so does any emitter asset as a template.
- **Look at it:** `ue.py vfx preview PATH` simulates the system in an offscreen preview scene at several ages. It writes one
  PNG contact sheet to `Saved/UnrealToolkit/vfx/<name>.png`; view it to judge the look. The open level is never touched.
  - Every frame has the same camera, fitted to the bounds over all times. Use `--zoom 2` when far-flung debris makes the
    fit loose, and `--yaw`/`--pitch` to change the angle.
  - The backdrop is black for additive effects and mid-gray when an emitter uses a smoke-like (non-additive) material.
    `--background black` or `--background 0.5,0.5,0.5` overrides it.
  - The result lists the particle count per emitter at each time. `n=0` everywhere means nothing spawned; GPU
    emitters' counts arrive a frame or more late.
- **Read it back:** `ue.py vfx describe PATH` prints the stack: emitters with their non-default properties, modules with
  their set inputs, renderers, and stack issues (`--hidden` includes every unset input, `--json` gives the raw data).
  It works on any Niagara system, not just ones made here.
- **Put it in the level:** `ue.py vfx place PATH --at 0,0,100` (marks the level dirty; not saved).
- **Shared art:** `/Game/VFX/Shared` holds the materials the specs name ('glow', 'smoke', 'ribbon', 'mesh', 'flat',
  'shockwave') and their textures (a soft dot, a 4x4 smoke flipbook, a ring), all generated on first use from
  procedural noise. Any material path works in their place.

What tripped up the first version (UE 5.8, 2026-11-22):
- **Input names nest and contain slashes.** The stack lists inputs under the switch that reveals them
  (`Lifetime Mode/Lifetime`) and under dynamic inputs (`Lifetime/Minimum`), and names like `Box / Plane Mode` contain
  ` / `. Inputs are matched on the whole normalized path: an exact match, or a unique trailing match (`Lifetime`).
- **The Minimal template has no Solve Forces and Velocity**, so velocity and forces do nothing until it's added. The
  spec builder adds it, and puts forces before it.
- **A random colour range links R, G and B by default** (Color Channel Mode "Link RGB / Link A"), so a range from
  dark to white gives grays. Use `{'random': [...], 'channels': 'individual'}` for varied hues.
- **Deleting and recreating a loaded asset** makes the editor ask "Overwrite Existing Object", a modal that stalls the
  bridge. `vfx.build` empties the existing system in place instead (`ClearSystem`), which also keeps references valid.
- **`FNiagaraSystemViewModel` asserts without a `MessageLogGuid`** ("Tried to subscribe to an asset without a set asset
  key"), even in data-processing mode.
- **Curve data interfaces carry `...CookedEditorCache` copies of each curve**; set only the editable channels.
- **Some renderer classes aren't exported to Python** (`unreal.NiagaraRibbonRendererProperties` doesn't exist). Load
  them by script path: `unreal.load_class(None, '/Script/Niagara.NiagaraRibbonRendererProperties')`.
- **The first preview in a fresh editor session came out blank:** the editor compiles shaders on demand, the first
  time a material is drawn with a vertex factory (Niagara sprites, ribbons...). The preview now renders every frame
  once, waits for the shader compiler (`GShaderCompilingManager->FinishAllCompilation()`), then renders for real.
- **Importing a texture in a headless editor crashed** (`CurrentApplication.IsValid()` in Slate): the Interchange
  import reached for the Content Browser. The shared textures are created from the PNG in C++ instead
  (`CreateTextureFromFile`: `FImageUtils::LoadImage` -> `UTexture2D::Source.Init`). The cause, found later (see
  "Importing"): on 5.7, `Interchange.FeatureFlags.Import.SyncToBrowser` syncs the Content Browser after every
  import, even headless. `importer` turns it off for headless runs.
- **API differences across versions:** 5.6 has `FStringOutputDevice` in `Containers/UnrealString.h` and a
  `SetDataInterfaceValue` that takes an instance and `check()`s its class; 5.6/5.7 lack
  `FNiagaraEditorUtilities::UserParameters::AddUserParameterToSystem` (add to `GetExposedParameters()` directly).

## Materials

`ue.py mat` (`mat` in `ue.py py`; `editor/mat.py`) builds a material from a spec in which each output is an
expression, reads any material back as that same spec, and makes instances and material functions.

```json
{"params": {"Albedo": "/Game/Textures/T_Rock", "Tint": [1, 0.6, 0.4, 1], "Roughness": 0.6, "Tiling": 2},
 "let": {"tex_uv": "uv * Tiling"},
 "out": {"base_color": "sample(Albedo, tex_uv).rgb * Tint", "roughness": "Roughness", "metallic": 0}}
```

```
ue.py mat create /Game/Materials/M_Rock --spec rock.json --preview   # build (or rebuild in place), compile, save
ue.py mat describe /Game/Materials/M_Rock                            # the graph as spec text (--json: the spec itself)
ue.py mat instance /Game/Materials/MI_Rock_Red --parent /Game/Materials/M_Rock --set "Tint=[1,0.1,0.1]" Roughness=0.2
ue.py mat params /Game/Materials/MI_Rock_Red                         # every parameter, its value, overridden or not
ue.py mat preview /Game/Materials/M_Rock --mesh plane                # sphere (default), plane, cube or a mesh path
ue.py mat compile /Game/Materials/M_Rock                             # the compiler's errors and the nodes they came from
ue.py mat nodes noise                                                # node classes with their pins
```

- **Expressions are Python syntax.** Operators make Add, Multiply and the like; `x.rgb` uses the node's own channel
  outputs, or a mask. Names are parameters, `let`s and built-ins (`uv`, `time`, `world_pos`, `vertex_color`,
  `screen_uv`...). Functions cover the common nodes (`sample`, `lerp`, `fresnel`, `panner`, `switch`,
  `scene_texture`, `custom` for HLSL, `fn` for material functions), and any node class works by name
  (`RotateAboutAxis(...)`). The full list is in `editor/mat.py`'s docstring.
- **A number going into a node becomes that node's own constant** (Add's B, Lerp's Alpha), as when you type it in the
  details panel, not a separate Constant node. Repeated sub-expressions share one node.
- **Round trip:** `describe --json` gives a spec that `create` builds back into the same graph. Nodes used twice come
  back as `let`s. So: read a hand-made material, change a line, build it again.
- **Rebuilding** an existing material replaces its nodes in place (references to it stay valid) and first resets its
  settings to the engine's defaults, so the result depends only on the spec.
- **Texture samples** get their sampler type from the texture (sRGB colour, normal map, masks), as when you drop a
  texture on a node in the editor. `sample(T, uv, sampler='linear_color')` overrides it.
- **Material functions:** a spec with `"function": true`, `inputs` and `out`. Call one with
  `fn('/Game/MF_X', uv, 4).Result`.
- **Post-process materials** preview over a sphere and a cube that write custom depth and stencil value 1 (stencil
  needs `r.CustomDepth 3`). `render view|pie|actor --post` applies one to any render.
- **Needs the UETKEditor plugin** for `describe`, compile errors, and the outputs Python's `MaterialProperty` enum
  leaves out (`pixel_depth_offset`, `shading_model`, `clear_coat`, `clear_coat_roughness`, `displacement`,
  `customized_uv0`..`7`). On 5.8, building works without it. Before 5.8, Python's MaterialEditingLibrary can't list a
  material's nodes or a node's outputs, so building needs the plugin too.
- **Compile errors need a rendering editor.** A headless run (`-nullrhi`) compiles no shaders, so there `errors` is
  `null` with a note, not `[]`. Check with `ue.py mat compile PATH` in a running editor.
- **Checked on UE 5.8 (2026-11-24):**
  - Built, compiled and previewed: a textured PBR material, an instance with overrides, a post-process outline from
    custom stencil, an unlit translucent material with Custom HLSL and fresnel, and a material function with a
    material that calls it.
  - A 44-node material using most of the syntax built cleanly, and `describe` rebuilt it identically. So did the
    engine's WorldGridMaterial, BasicShapeMaterial and DefaultDeferredDecalMaterial.
  - A float2 + float3 came back as the compiler's own message, naming the node.
- **Checked on UE 5.6 and 5.7 (2026-11-24):** the plugin builds. Building, `describe` round trips, instances,
  functions and calls work headless. In a running editor, compile errors came back (a type error, and an undeclared
  name in Custom HLSL with the shader compiler's line), and previews rendered.
- **A material with pixel depth offset can vanish in a steep `--mesh plane` preview:** the offset pushes its depth
  behind the preview floor. Use `--pitch -20` or `floor=False`.
- **What tripped up the first version:**
  - `MaterialEditingLibrary.delete_all_material_expressions` (and `..._in_function`) removes nodes from the list it
    loops over, so it deletes only about half. `mat` deletes from a copy until none are left.
  - `set_material_instance_*_parameter_value` always returns False, even when it worked (5.8's source). `mat.instance`
    checks by reading the value back.
  - `connect_material_expressions` takes pin names. An empty name is the first pin, and unnamed outputs match
    "R"/"G"/"B"/"A" by their channel mask. Many single-input nodes report their input as "None".
  - When one node feeds another twice through different outputs (`lerp(t.r, t.g, x)`),
    `get_input_node_output_name_for_material_expression` returns the first match. Reading graphs goes through the
    plugin for that reason.
  - The editor's Sine and Cosine nodes have a period of 1 by default (`sin(2πx)`); `sin(x)` in a spec sets the
    period to 0, which is radians.
  - `unreal.Vector4f()` takes no constructor arguments; set x, y, z and w with `set_editor_property`.
  - UFUNCTIONs of C++ libraries are snake_case in Python (`DescribeGraph` is `describe_graph`).

## Enhanced Input

`ue.py input` (`inputs` in `ue.py py`; `editor/inputs.py`) reads and writes Enhanced Input assets as one spec:
actions with their value types, triggers and modifiers, and mapping contexts with each action's keys.

```json
{"actions": {"/Game/Input/Actions/IA_Sprint": {"type": "bool", "description": "Run while held"}},
 "contexts": {"/Game/Input/IMC_Default": {"mappings": {"IA_Sprint": ["LeftShift", "Gamepad_LeftThumbstick"]}}}}
```

```
ue.py input dump                                   # every context in /Game (and what uses it), every action
ue.py input dump /Game/Input/IMC_Default --json    # one context as a spec that `create` takes back
ue.py input create --spec input.json               # create or update actions and contexts
ue.py input map /Game/Input/IMC_Default IA_Sprint LeftShift --trigger hold
ue.py input unmap /Game/Input/IMC_Default --action IA_Sprint                # or --key LeftShift, or both
ue.py input use /Game/Blueprints/BP_PlayerController /Game/Input/IMC_Default # its default contexts (--remove)
ue.py input keys gamepad                           # key names
ue.py input active                                 # the running game's player: active contexts; add / remove CONTEXT
```

- **A context lists keys per action.** Each action listed gets exactly those keys; the context's other actions keep
  theirs (`"replace": true` clears them). Triggers and modifiers are names, or `{name: {property: value}}`:
  `{"hold": {"hold_time_threshold": 0.5}}`, `{"chord": {"chord_action": "IA_Aim"}}`, `{"swizzle": {"order": "zyx"}}`.
  The full format is in `editor/inputs.py`'s docstring.
- **Actions are named by asset name** when only one InputAction has it, otherwise by path. `dump` writes them the same
  way, so its output always builds back.
- **Blueprint logic for an action:** `g.input_action('IA_Sprint')` in `bpedit` adds the action's event node (see
  "Editing Blueprints").
- **In the running game:** `inputs.active()` lists the contexts a player has, with priorities, and
  `add_context`/`remove_context` change them. Drive keys with `ue.py press` or `ueh.key_down`: they go through the
  mappings, so triggers and modifiers run.
- **Checked on UE 5.8 (2026-11-24), in a fresh Third Person template:**
  - `dump` read the template's five contexts and eleven actions, with where each context is used and one action no
    context maps.
  - Hold-to-sprint (Left Shift, left-stick click) and hold-to-walk-slowly (Left Alt, left shoulder) went in with
    `input create` and two `input_action` events setting `MaxWalkSpeed`, without the editor UI. In PIE, with forced
    keys: 500 walking, 1000 sprinting, 200 slow, on keyboard and on gamepad.
  - A context using chord, hold, pulse, dead zone, scalar, swizzle and negate settings read back identically after a
    rebuild.
- **Checked on 5.6 and 5.7 (headless):** the same build and round trip, map and unmap, the key list, and an input
  action event compiled into a Blueprint.
- **What tripped up the first version:**
  - 5.6 keeps a context's mappings in `Mappings`; 5.7 moved them into `DefaultKeyMappings.Mappings`. `inputs` handles
    both. Per-profile overrides (`MappingProfileOverrides`, 5.7+) aren't read or written yet.
  - Before 5.8 a chord trigger's `ChordAction` is edit-on-instance-only, and Python refuses to set it ("cannot be
    edited on templates"). `uprop.set_prop` falls back to the plugin's text import.
  - Python returns `EnhancedInputLocalPlayerSubsystem.has_mapping_context`'s out-parameter, not its bool: the priority
    when the context is applied, None when not. Priority 0 is falsy.
  - When two active contexts map the same key, the first action to take it consumes it. A test context that also
    mapped `Gamepad_Left2D` silently stopped the stick from moving the character.
  - `EditorAssetLibrary` refuses to run during PIE, so `inputs` loads assets with `unreal.load_object` and finds them
    through the asset registry.
  - `ueh.prop_specs` (and `ueh.props`) skipped each property that followed one with an empty description: the
    docstring regex's `\s*` crossed the line break. `InputModifierNegate` showed x and z but not y.
  - Python has no list of key names; the plugin reads them from `EKeys` (`ue.py input keys`).

## Menus and HUD (UMG)

`ue.py ui` (`ui` in `ue.py py`; `editor/ui.py`) reads and drives the widgets on screen in the running game (PIE, or a
`ue.py net` process with `--instance`), and builds Widget Blueprints from a spec. It needs the UETKEditor plugin.

```
ue.py press P                               # open a menu the way a player would (or: ue.py ui open /Game/UI/WBP_Pause)
ue.py ui wait Paused                        # until something labelled "Paused" is on screen
ue.py ui tree                               # the widgets on screen: class, name, text, value, disabled
ue.py ui click "Save game"                  # a button or checkbox, by its label (ignoring case) or name
ue.py ui type PlayerName Ada                # an editable text: set, changed, committed with Enter
ue.py ui set Volume 0.3                     # a slider, spin box, checkbox or combo box
ue.py ui text Status                        # one widget's text; `ui text` alone: all the text on screen
ue.py ui click Resume && ue.py ui wait Paused --gone
ue.py ui create /Game/UI/WBP_Pause --spec pause.json    # a Widget Blueprint's designer tree from a spec
ue.py ui describe /Game/UI/WBP_Pause                    # read one back
```

In scenarios: `yield ui.wait('Paused')`, then `ui.click('Resume')`.

- **Why it's needed:** forced keys reach gameplay input, not a focused widget, so menus need their own driver. Clicks,
  typing and values go through the widgets' own events (a click fires OnPressed, OnReleased and OnClicked), so
  Blueprint and C++ handlers run as they do for a real click. Slate's hit testing doesn't run: a button behind another
  widget still gets clicked.
- **Targets** match a widget's name or its label (its own text, or the text inside it): exactly first, then the start
  of the label, then anywhere in it. A button and the text inside it share a label; the button wins. A missing or
  ambiguous target lists what is on screen.
- **Building a menu:** the spec nests widgets (`type`, `name`, `text`, `props`, `slot`, `children`), and every widget
  becomes a variable. Wire it with `bpedit`: `g.bound_event('ResumeButton', 'OnClicked')`. The format is in
  `editor/ui.py`'s docstring.
- **Checked on UE 5.8 (2026-11-24), in a Third Person template, with the editor in the background:**
  - A pause menu was built from a spec: a title, two buttons, a checkbox, a slider, a name field and a status line.
    Its events were wired with `bound_event`, and an `IA_Pause` action on P opened it from the character Blueprint.
  - In PIE, `press P` opened it and paused the game. Typing a name, and clicking Save, changed the status line through
    the Blueprint. The checkbox and slider took their new values. Resume closed the menu and unpaused the game.
  - The same in a `ue.py net` client process, with `--instance client1`.
- **Checked on 5.6 and 5.7:** the plugin builds on both. The same menu was built (on 5.7 headless), opened with
  `ui.open` in PIE, and driven the same way.
- **Not yet:**
  - Rectangles: `tree` shows a widget's position and size in viewport pixels only when Slate has arranged it, and in
    no run here (editor in the background, a net client's window) had it, so none were shown.
  - The entries a ListView or TileView generates aren't walked.
- **What tripped up the first version:**
  - Python can't reach a user widget's or Widget Blueprint's `WidgetTree`, or create a widget (Create Widget is
    internal to Blueprint). The plugin's `UETKWidgetLibrary` does those, and Python walks the rest
    (`PanelWidget.get_child_at`).
  - A widget added to a Widget Blueprint must be registered with `UWidgetBlueprint::OnVariableAdded`, as the designer
    does. Without it the compiler ensures for every widget ("Widget was added but did not get a GUID").
  - Marking the Blueprint structurally modified after each added widget recompiles its skeleton every time (about 3 s
    per widget). Mark it modified, and compile once at the end.
  - Struct properties compared through `str()` always differ: the text includes the struct's address. `uprop`
    compares them by `export_text()`.
  - In Python, `UWidgetBlueprintLibrary` is `unreal.WidgetLibrary` and `USlateBlueprintLibrary` is
    `unreal.SlateLibrary`.
  - `BlueprintEditorLibrary.get_blueprint_parent_class` exists from 5.8; the asset registry's `ParentClass` tag has it
    on every version.

## Spawning into the running game

`ueh.spawn(..., world='game')` spawns into PIE or a `ue.py net` game process (in a game process it's the default),
with properties set before the actor's construction script and BeginPlay run. It needs the UETKEditor plugin.

```python
ueh.spawn('/Game/BP_Pickup', (300, 0, 120), world='game', props={'Amount': 5, 'Kind': 'gold'}, label='Coin')
ueh.spawn('/Game/BP_Hat', (0, 0, 90), world='game', attach_to=pawn, scale=(0.5, 0.5, 0.5))  # keeps its world spot
ueh.actors(cls='BP_Pickup', world='game'); ueh.destroy('Coin', world='game')
```

```
ue.py --instance server py "ueh.spawn('/Game/BP_Pickup', (300, 0, 120), props={'Amount': 5})"   # clients see it
```

- **`props`** takes any property, Blueprint variables included, with the same loose values as the rest of the toolkit
  (lists for vectors and colours, enum names, asset paths). Expose-on-spawn variables are just the usual case.
- **On a net server,** spawn there: replicated actors and their replicated variables reach the clients.
- **Checked on UE 5.8 (2026-11-24):**
  - In PIE, a Blueprint spawned with two expose-on-spawn values, and its construction script saw them.
  - A second actor attached to the player's character and followed it.
  - On a `ue.py net` listen server, the same Blueprint spawned with `--instance server`, and the client saw a simulated
    proxy carrying the replicated values, the one set in the construction script included.
- **Checked on 5.6 and 5.7:** the same PIE test; on 5.7 also a net server's spawn, seen on the client with its
  replicated values.
- **What tripped up the first version:** Python can't spawn deferred (`BeginDeferredActorSpawnFromClass` and
  `FinishSpawningActor` aren't exposed), and setting properties after a normal spawn is too late for the construction
  script and BeginPlay. The plugin's `UETKGameLibrary.begin_spawn` / `finish_spawn` split the spawn around them.

## Importing

`ue.py import` (`importer` in `ue.py py`; `editor/importer.py`) imports files and whole folders: static and skeletal
meshes, animations, textures and sounds. It uses the running editor when there is one and a headless editor otherwise.

```
ue.py import D:/Art/Hero --to /Game/Characters/Hero      # a folder (recursively; subfolders are mirrored)
ue.py import D:/Art/A_Run.fbx --to /Game/Anims --skeleton /Game/Characters/Hero/SK_Hero_Skeleton
ue.py import D:/Art/Props --to /Game/Props --rule "*.wav:looping=true" --set collision=box lod_group=SmallProp
ue.py import --status /Game/Props                        # each imported asset's source file: changed or not
ue.py import --reimport /Game/Props                      # reimport the ones whose source changed (--all: every one)
```

- **What each file becomes:** an FBX is a static mesh, skeletal mesh or animation, read from its content (polygons,
  skin clusters, bones, animation curves); a name prefix (`SM_`, `SK_`, `A_`) or `--kind` overrides it. OBJ is a
  static mesh; glTF is whatever the engine's glTF importer makes of it. Images are textures, wav/ogg/flac sounds.
- **Texture presets come from the name's last suffix:** `_N` normal map; `_ORM`, `_R`, `_M`, `_AO`, `_Mask` masks
  (linear, `TC_MASKS`); `_H`, `_Height` grayscale; EXR and HDR files HDR; anything else colour (sRGB). `--texture` sets
  one for every texture, and `--rule "*_UI_*.png:texture=ui"` per file. Interchange only detects normal maps by itself:
  an ORM map came in as sRGB colour.
- **Animations need a skeleton:** `--skeleton`, or the skeletal mesh imported in the same run (animations are imported
  last). Without one the file is refused before the engine sees it.
- **Each imported asset records its source file, content hash and options** in its package metadata (tag
  `UETK.Import`), so it travels with the asset. `--status` compares hashes; `--reimport` imports the changed ones again
  with the same options and presets.
- **Options:** `--set materials=false collision=box|sphere|capsule|convex|complex|none lod_group=SmallProp scale=100
  normals=compute nanite=true physics_asset=false looping=true sound_class=/Game/... attenuation=/Game/... volume=0.5`.
  The full list is in `editor/importer.py`'s docstring.
- **Checked on UE 5.6, 5.7 and 5.8, headless (2026-11-24):**
  - One call imported a folder holding a static mesh FBX, a skeletal mesh FBX, an animation FBX, an OBJ, a glTF, three
    PNGs (colour, normal, ORM) and a WAV. The animation landed on the new skeleton, the skeletal mesh got a physics
    asset, and the OBJ got a box collision and a LOD group.
  - After three sources changed (a bigger texture, a longer sound, another mesh), `--reimport` replaced exactly those
    three and kept their presets.
  - The CLI, in a running 5.8 editor: the same import, `--status`, and a reimport of one changed texture.
- **What tripped up the first version:**
  - **5.7 crashed every headless import** (`CurrentApplication.IsValid()`): the console variable
    `Interchange.FeatureFlags.Import.SyncToBrowser` overrides AssetImportTask's "don't sync" and syncs a Content
    Browser that a headless run doesn't have. 5.8 skips it in commandlets. `importer` sets it to 0 while importing
    headless and puts it back.
  - FBX import options (`FbxImportUI`) still work through Interchange: mesh type, the animation's skeleton, materials,
    collision, normals, scale. But a skeletal import with them made no physics asset; `importer` creates one with
    `SkeletalMeshEditorSubsystem.create_physics_asset` (5.6 takes only the mesh).
  - Editor subsystems (`StaticMeshEditorSubsystem`) are None in a headless run. Collision shapes and LOD groups go
    through the UETKEditor plugin, which calls the editor's own fitting functions.
  - An animation-only FBX imported without a skeleton imports nothing ("There was nothing to import").
  - On 5.6 a headless WAV import fires a harmless engine check (`ensure(Factory)` in SoundWave.cpp); the sound imports.
- **From DCC tools:** see "From Blender (FBX)" under "Rules of thumb" (V is flipped; linear colours in an sRGB PNG come
  out too dark). Interchange also warns about FBX meshes saved without smoothing groups: turn on the exporter's
  smoothing groups option.

## Validating the project

`ue.py validate` (`validate` in `ue.py py`; `editor/validate.py`) checks content and exits with code 1 when it finds
errors, so it can gate a commit hook or a CI job. It uses the running editor when there is one and a headless editor
otherwise. It needs the UETKEditor plugin for the `data`, `blueprints` and `maps` checks; without it those are
reported as skipped.

```
ue.py validate                                   # the default checks on /Game
ue.py validate /Game/Characters --checks all     # every check on one folder
ue.py validate --maps /Game/Maps/Main /Game/Maps/Arena --strict --json
ue.py validate --fix-redirectors                 # resave the referencers, delete the redirectors
```

```
error   references  /Game/Props/BP_Target: references /Game/Old/MI_Target, which doesn't exist
error   blueprints  /Game/BP_Door: This blueprint (self) is not a TextBlock, therefore ' Target ' must have a connection.
warning textures    /Game/UI/T_Logo: 300x200 isn't a power of two, so it has no mips and doesn't stream (...)
warning maps        /Game/Maps/Main: StaticMeshActor_0 Static mesh actor has NULL StaticMesh property
FAILED: 2 error(s), 2 warning(s), 0 note(s) in /Game (checked: references 878, redirectors 0, textures 39, ...)
```

| Check | Finds | Default |
|-------|-------|---------|
| `references` | references to packages that don't exist: hard ones are errors (the asset loads with them cleared), soft ones warnings | on |
| `redirectors` | leftover redirectors (warnings); `--fix-redirectors` fixes them | on |
| `textures` | textures over `--max-texture-size` (4096) on a side, virtual textures exempt; sides that aren't powers of two outside the UI group | on |
| `blueprints` | compiles every Blueprint (Widget, Anim, Editor Utility...): the compiler's errors and warnings | on |
| `data` | Data Validation, as the Content Browser's Validate Assets runs it: the engine's and the project's validators, each asset's `IsDataValid`. It loads every asset it checks | on |
| `maps` | map check on `--maps` (or `all` maps under the roots), else the level open in the editor | on |
| `unused` | assets nothing references, as notes. Maps, Editor Utilities, and assets whose path appears in the project's `Config/` or `Source/` aren't reported; paths built at runtime aren't seen | off |
| `naming` | name prefixes by asset class (`BP_`, `M_`, `T_`, `SM_`...), as warnings. `--naming`, `"naming": true` or naming it in `--checks` uses the common conventions in `validate.NAMING`; a `{class: prefix}` map replaces them | off |

- **Project settings** go in `<Project>/.uetk/validate.json`; command-line options override them:
  ```json
  {"roots": ["/Game"], "exclude": ["/Game/Developers"], "checks": {"unused": true},
   "max_texture_size": 4096, "maps": ["/Game/Maps/Main"], "naming": true,
   "ignore": [{"check": "textures", "asset": "/Game/Sky/*"}, {"message": "*deprecated*"}],
   "fail_on": "error"}
  ```
  `checks` as a list runs exactly those. Each `ignore` entry matches findings by fnmatch patterns on any of `check`,
  `asset`, `message` and `severity`: a way to accept known findings and fail on new ones. `"fail_on": "warning"` (or
  `--strict`) fails on warnings too.
- **Exit codes:** 0 passed, 1 findings at the failing level, 2 it couldn't run (no editor and no headless start, PIE
  running, an engine error).
- **In a running editor:** it refuses while PIE runs. Map check on other maps opens each one and then the level that
  was open; it refuses if that level has unsaved changes. `--fix-redirectors` saves the referencers, unsaved changes
  in them included, as the Content Browser's Fix Up does.
- **Duplicates collapse:** map check reports a message once per component; the report shows it once with `(x3)`.
- **Checked on UE 5.6, 5.7 and 5.8 (2026-11-24),** in a windowed editor and headless, on a folder of assets with one
  known problem each: a Blueprint with a compile error, a Child Actor Component that spawns its own Blueprint (Data
  Validation and the compiler both report it), a material instance whose parent was deleted, a redirector left by
  consolidating two materials, an 8192x4 and a 300x200 texture, a Blueprint without a prefix, and a level with a
  mesh-less StaticMeshActor. Every check found its asset, and `--fix-redirectors` removed the redirector. On 5.8 it
  also resaved referencers that still named a redirector, headless and in the editor. A whole template project
  (889 packages, 4 maps) took 11 s in the 5.8 editor with the default checks, and 35 s headless with every check.
- **On Epic's own template content** it found a Blueprint with a hard reference to a material that isn't shipped
  (`BP_WobbleTarget`), a placed door in `Lvl_Combat` whose mesh is missing, and animations whose preview meshes point
  at another project's paths (soft references, warnings).
- **What tripped up the first version:**
  - `EditorValidatorSubsystem.validate_assets_with_settings` works from Python, but each asset's errors and warnings
    are in struct fields that aren't UPROPERTYs (`FValidateAssetsDetails`), so Python only gets counts. The plugin's
    `UETKValidateLibrary.validate_assets` returns them.
  - The engine's redirector fix-up (`IAssetTools::FixupReferencers`) ends in a modal "Redirector Update Report" that
    blocks a script, and isn't exposed to Python anyway. The plugin does the same steps without it, headless too.
  - A headless run only scans the asset registry on request. Without `search_all_assets(True)`, every reference into
    plugin content (`/Niagara/...`) looked broken.
  - Right after a save, the registry still has a package's old dependencies (waiting for it doesn't help). So a
    script that just saved assets would get false findings, and a rename would miss the new referencer and leave it
    pointing at a name that's gone, with no redirector. `validate` and `ueh.rename` rescan modified files first
    (`ueh.refresh_registry`, about 0.1 s per 1000 packages).
  - Asking for `LevelEditorSubsystem` in a headless run crashes the editor, so `validate` checks for `-run=` first.
  - The engine's material validator is off by default (`bEnableMaterialValidation`) and only runs on save and in the
    DataValidation commandlet, so Data Validation doesn't report material compile errors. A headless `validate` runs
    as that commandlet would, so turning it on in the project settings takes effect there.

## Performance runs

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

## Editing Blueprints

`bpedit` (pre-imported in `ue.py py`; UETKEditor plugin) is the write side of `bpdump`. It edits graphs the way the
editor does: nodes come from the editor's node creators, wires go through the K2 schema (which inserts conversion
nodes), literals are validated by it, and `compile` returns the compiler's own messages.

```python
g = bpedit.graph('/Game/BP_Door')                   # the event graph; graph(path, 'MyFunction') for a function
begin = g.event('BeginPlay')                        # the existing override (a ghost one is enabled), or a new one
say = g.call('PrintString', InString='Ready')       # pin literals as keywords
g.chain(begin, say)                                 # exec wires through nodes in order, laid out left to right
bpedit.add_variable('/Game/BP_Door', 'Health', 'float', 100, editable=True)
hit = g.custom_event('TakeHit', Amount='float')
minus = g.call('Subtract_DoubleDouble')
g.link(g.get('Health'), 'Health', minus, 'A'); g.link(hit, 'Amount', minus, 'B')
set_hp = g.set_var('Health'); g.link(minus, 'ReturnValue', set_hp, 'Health'); g.chain(hit, set_hp)
g.tidy()                                            # data nodes to the left of what uses them
bpedit.compile('/Game/BP_Door'); ueh.save('/Game/BP_Door')
```

- **To find what to edit:** `ue.py bp graph PATH [GRAPH]` (or `g.text()`) lists every node with its id, title,
  position, function, literals and links (`K2Node_CallFunction_2.execute`). `ue.py bp graphs PATH` lists the graphs.
  Address nodes by id or by a unique title, and pins by name or display name: `True`/`False` on a Branch,
  `ReturnValue`, `In String`. A Set node's value output is its variable's name, or `value`.
- **Node kinds:** `call` (any Blueprint-callable function: the Blueprint's own, Kismet libraries, GameplayStatics, or
  `cls='Character'` for others; `DestroyActor` finds `K2_DestroyActor`), `event`, `custom_event` (with typed
  parameters), `get`/`set_var`, `branch`, `sequence`, `cast`, `macro` (StandardMacros: ForEachLoop, DoOnce, Gate...),
  `self_ref`, `comment`, `input_action` (an Enhanced Input action event: Started, Triggered, Completed... and
  ActionValue), `bound_event` (a widget's or component's event, as the editor's "+" buttons add it:
  `bound_event('ResumeButton', 'OnClicked')`, `bound_event('TriggerBox', 'OnComponentBeginOverlap')`), and `any_node` (any graph node class, its properties set before its pins are made:
  `any_node('K2Node_EnhancedInputAction', InputAction='/Game/Input/IA_Jump')`, which is what `input_action` does).
  Also `add_variable` (category, instance editable,
  replicated, expose on spawn) and `add_function` (typed inputs and outputs, pure).
- **Rewiring:** `link` on an exec output replaces its link (exec outputs carry one), which is how you insert a node
  between two others: link A to new, then new to B. `unlink` breaks one link or all of a pin's links; `remove` deletes
  a node.
- **Checked against the template project (2026-11-23):**
  - A Blueprint built from nothing (BeginPlay print, a `TakeHit` event, health math, a branch, DestroyActor)
    compiled and behaved correctly in PIE.
  - `bpdump` read it back as `Health = Subtract(Health, Amount); if (Health <= 0) DestroyActor()`.
  - Edits to the template's jump pad worked: a literal, and a node inserted between a cast and a call.
  - A bool wired to a vector was refused with the schema's reason, and a missing target came back as the compiler's
    message.
- **What tripped up the first version:**
  - A removed node stays outered to its graph until garbage collection, so exports (`bpdump`) still listed it.
    `remove` now renames it into the transient package.
  - EditorAssetLibrary refuses to run during PIE, so `ueh.cls('/Game/BP_X')` now loads `/Game/BP_X.BP_X_C` directly.

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
  Use `ue.py render view --at x,y,z --look x,y,z` instead (below, "Offscreen renders"); it meters dark interiors to mid-gray, so the temporary light and hand-set exposure the old recipe needed aren't.
- **`ueh` defines its own `set()` helper,** which hides Python's built-in `set` inside `ueh.py`. Use a dict there.
- **Struct arrays come back as copies** (e.g. a static mesh's `static_materials`). Change element `i`, write it back with `arr[i] = s`, then `set_editor_property` the whole array. `for s in arr: s.set_editor_property(...)` silently does nothing.
- **A texture with `srgb` off** needs `SAMPLERTYPE_LINEAR_COLOR` on its texture-sample expression. Otherwise the material fails to compile and falls back to the default material.
- **Background throttling** (`bThrottleCPUWhenNotForeground`, about 3 fps when unfocused) can't be changed from Python or `set`: use `ue.py editor start --no-throttle` (see "Leaving the machine as it was").
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
    (see "Menus and HUD").
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
    plugin instead ("Spawning into the running game").
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
| `uetk/plugins.py` | system Python | `ue.py plugins install/status`: copies the toolkit's plugins into a project |
| `tests/` | system Python | the engine-free tests: `python -m unittest discover -s tests` |

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
