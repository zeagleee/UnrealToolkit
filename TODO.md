# To do

What UnrealToolkit should cover next, in order. The toolkit is for anyone driving Unreal Engine from scripts or AI
agents, so each item is written for projects in general. Tick an item when it lands, and put what it taught into
the docs.

**How new features should work** (the same way `vfx` and `bpedit` already do):
- An `editor/` Python helper, plus a `ue.py` command when it's useful from the shell.
- A spec goes in and an asset comes out. A `describe` reads any asset of that kind back as text, including ones the
  toolkit didn't make.
- Safe to run again: building an asset that already exists updates it in place, so references stay valid.
- Headless where the engine allows it.
- Checked on a stock engine template project, on every supported engine version, before it's ticked.

## 1. Jobs every project does by hand

### [x] Materials
Done 2026-11-24: `ue.py mat` / `editor/mat.py` (README, "Materials"), checked on 5.6, 5.7 and 5.8. Still open: previews
of decal, UI and light-function materials (`render asset` refuses those domains for now).

Nearly every project builds materials, and doing it through `MaterialEditingLibrary` is long and error-prone.
- `ue.py mat create PATH --spec file.json` / `mat.build(spec, path)`. Nodes by intent: texture samples, parameters,
  math, lerp, fresnel, world position, Custom HLSL, wired to the material's outputs. Domain, blend mode, shading
  model, two-sided.
- Material instances: `mat.instance(parent, path, params)` with scalar, vector, texture and static switch values.
- Material functions: create one, call one. Post-process materials: blendable location, scene textures.
- Texture samples get the sampler type their texture needs (sRGB, linear, normal, masks). A wrong one makes the
  material fail to compile and fall back to the default material.
- `ue.py mat describe PATH`: the node graph as text, plus compile errors. Works on any material.
- Previews: `render asset` already draws a material on a sphere. Add plane and cube.
- **Done when:** a textured PBR material, an instance with overrides, a post-process outline and an unlit
  translucent material are all built from specs, compile, and render correctly.

### [x] Enhanced Input assets
Done 2026-11-24: `ue.py input` / `editor/inputs.py`, `bpedit` `input_action` (README, "Enhanced Input"), checked on 5.6,
5.7 and 5.8. Still open: per-profile mapping overrides (5.7+), and rebinding keys at runtime through Enhanced Input's
user settings.

- Create Input Actions (value type, triggers, modifiers) and Input Mapping Contexts.
- `map_key(imc, ia, key, triggers=[...], modifiers=[...])` for keyboard, mouse and gamepad keys, and chorded actions.
- Set the default mapping context on a character or controller Blueprint; add or remove one at runtime.
- Player-mappable key settings (rebindable keys) where the engine version has them.
- `ue.py input dump`: every context, mapping, trigger and modifier in the project.
- **Done when:** a template character gets a new action (hold to sprint, on keyboard and gamepad) with no editor UI,
  and `ue.py press` drives it.

### [x] Import
Done 2026-11-24: `ue.py import` / `editor/importer.py` (README, "Importing"), checked headless on 5.6, 5.7 and 5.8.
Still open: import options for glTF files (the engine's defaults are used).

- `ue.py import FILES... --to /Game/...`, with a preset picked from each file's type and overridable:
  - static mesh: collision, LODs, combine, normals
  - skeletal mesh: onto an existing skeleton or a new one
  - animation: onto a skeleton
  - texture: sRGB, linear, normal or mask; compression; LOD group
  - audio: wav to SoundWave, attenuation, sound class
  - FBX and glTF
- Reimport with the same settings; import a whole folder; report what was created or replaced.
- Works headless. Importing a texture headless crashes through Interchange (it reaches for the Content Browser);
  fall back to the C++ path UETKVfx already uses (`CreateTextureFromFile`).
- A short "from Blender and other DCC tools" doc: V is flipped on import; linear colours written into an sRGB PNG
  come out too dark.
- **Done when:** a folder of FBX meshes, textures and wavs imports in one headless command on every supported
  version, and reimports after the files change.

### [ ] Menus and HUD in a running game (UMG)
Forced keys reach gameplay input, not a focused widget, so tests can't drive menus today without project code.
- `ueh.ui()`: the live widget tree of the PIE or game world (class, name, text, visibility, on-screen rect).
- `ui_click(label_or_name)`, `ui_type(widget, text)`, `ui_text(name)`, and `yield ueh.ui_wait(label)` in scenarios.
  They work in `ue.py net` game processes too (`--instance`).
- Later: edit a Widget Blueprint's designer tree (add and nest widgets, slot and layout properties, bindings) through
  the UETKEditor plugin, as `bpedit` does for graphs.
- **Done when:** a scenario starts PIE, opens a menu, clicks a button by its label and checks what happened, with the
  editor in the background.

### [ ] Spawning into a PIE or game world
`GameplayStatics.begin_deferred_actor_spawn_from_class` isn't exposed to Python, so scripts can't spawn into PIE.
- A BlueprintCallable in UETKEditor that spawns a class into a given world, with expose-on-spawn values set before
  `BeginPlay`. `ueh.spawn(..., world=ueh.pie_world())`.
- Attach on spawn; destroy.
- **Done when:** a scenario spawns a Blueprint actor with expose-on-spawn values into PIE, and on a `ue.py net`
  server it replicates to the clients.

## 2. Parts of the engine it doesn't cover yet

Most projects need these:

### [ ] Validation: `ue.py validate`
- Data Validation (`EditorValidatorSubsystem`), map check, Blueprint compile errors across the project, broken
  references, leftover redirectors, unused assets, oversized textures, optional naming rules.
- JSON output, and a non-zero exit code on errors, so it can gate a commit hook or CI.

### [ ] Unreal Insights: `ue.py trace`, `ue.py mem`
`perf run` gives medians, not which function caused a hitch.
- Start and stop a trace in the editor, PIE or a game process, with chosen channels (cpu, gpu, frame, memory,
  loadtime...).
- Summarize it through Unreal Insights' command-line export: top timers, the worst frames and what ran in them.
- `ue.py mem`: `memreport -full` summarized (textures, meshes, top classes by count and size).

### [ ] Project settings: `ue.py config get|set|list`
- Knows which ini a section lives in (Engine, Game, Input, Scalability, per-platform) and writes the project's
  `Default*.ini`.
- Warns when a setting can't take effect from that file (a scalability ini only sets scalability cvars; project
  renderer settings outrank scalability) and when it needs an editor restart.
- Reads the effective value from a running editor.

### [ ] Gameplay tags
- Add, rename and remove tags (ini or tag tables); list where a tag is used.

### [ ] Packaged builds: `ue.py package --run`
A packaged game has no Python, so the bridge can't reach it.
- Launch the build, run console commands (`-ExecCmds`), wait for log markers, check the log for errors, take a
  screenshot, quit.
- Cook errors summarized.
- Platforms beyond Win64 (Linux, Android) in `package`.

### [ ] Animation
- `bpdump`: anim graphs as text: state machines with states, transitions and their rules, blend spaces, slots, linked
  layers. Today it only counts anim nodes (`uetk/bpdump.py:660`).
- Create montages (sections, slots), notifies on sequences, blend spaces from samples; batch retargeting with an IK
  Retargeter.
- Later: edit state machines with `bpedit`.

### [ ] Navigation and AI
- Rebuild the navmesh; `ueh.nav_path(a, b)` for reachability and path length; show the navmesh in `render view`.
- Behavior Trees, Blackboards and StateTrees as text, like `bpdump`. Later: create and edit them.

### [ ] Sequencer and Movie Render Queue
- Create a Level Sequence: bind actors, camera cuts, transform keys, events. Play it in PIE.
- Render it with Movie Render Queue to images or video.

### [ ] Source control
- Perforce: files under Perforce are read-only until checked out. Check out before save, mark new files for add,
  revert. Git LFS: lock before editing a binary asset.

Bigger or more specialized, later:
- [ ] World Partition, data layers, level instances; HLOD builds
- [ ] Landscape, foliage, PCG graphs
- [ ] Baked lighting (Lightmass), reflection captures
- [ ] Physics: collision presets, simple collision generation, physics assets, physical materials
- [ ] Audio: MetaSounds, sound classes, mixes and submixes
- [ ] Gameplay Ability System: abilities, effects and attribute sets as text; effects from specs
- [ ] Localization: gather, export and import PO files, compile
- [ ] Data: curve tables and data assets from JSON (data tables already work)

## 3. The toolkit itself

### Going public (done 2026-11-24)
- [x] MIT `LICENSE`.
- [x] Project-specific content out of the README; the general lessons moved to `docs/unreal-notes.md`.
- [x] A fresh public repo from one squashed commit (the earlier history stays in the private repo).
- [x] No fixed install path in the code or docs.
- [x] No engine source in `plugin/` (no Epic headers or copied files; it declares four exported UnrealEd functions
  whose header is private).
- [x] The bridge's trust model in the README ("Security").
- [x] README quickstart, supported versions and platform; `CONTRIBUTING.md`, `CHANGELOG.md`, the `v0.1.0` tag.
- [ ] Issue templates.
- [ ] A per-session token in bridge requests, so only the client that started an editor can drive it.

### Install and reach
- [ ] **Installable package:** `pip install` or `pipx` with a `ue` command, still standard-library only.
- [ ] **MCP server** over the same commands, so agents in any MCP client get typed tools instead of shell lines. Ship
  a ready-made `CLAUDE.md` / `AGENTS.md` snippet and a Claude Code skill for projects to copy.
- [ ] **Linux and macOS.** Engine discovery without the registry, editor executables and build scripts, process
  discovery, screenshots (`uetk/winshot.py` is Windows-only). Live Coding is Windows-only.
- [ ] **Engine versions.** State the supported range (5.6–5.8 so far) and decide how far back to go.
- [ ] **Blueprint-only projects.** `editor start` fails with "No Editor target"; launch the editor directly instead.
- [ ] **Plugin versions.** `plugins status` says when a project's copy of UETKEditor or UETKVfx is older than the
  toolkit's.

### Docs
- [ ] The README holds the commands and the quickstart, and each feature's section moves to `docs/`. The general Unreal
  lessons already did (`docs/unreal-notes.md`).
- [ ] A command reference generated from `ue.py`'s argparse and `ueh.help()`.

### Tests
- [ ] Unit tests for the parts that don't need an engine (scenario rewriting, perf CSV parsing, bpdump condensing,
  project and engine discovery), run by GitHub Actions.
- [ ] `scripts/selftest.py`: runs every command against an engine template project on a given engine version and
  reports pass or fail. Run it on every supported version before a release. It also settles the untried cases
  (VFX previews on 5.6 and 5.7, level Blueprint export).
