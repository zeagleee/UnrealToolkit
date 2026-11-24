# To do

What UnrealToolkit should cover next, in order. The toolkit is for anyone driving Unreal Engine from scripts or AI
agents, so each item is written for projects in general. Tick an item when it lands, and put what it taught into
the docs.

**Two kinds of work.** Sections 1 to 3 need Unreal Engine installed: they're checked in a running editor. Section 4
holds small tasks that don't (tests of the engine-free code, CI, packaging, docs), each doable with Python 3 and git.
To take one, open an issue or a draft pull request named after its number (N1, N2...) so nobody else starts it.

**Next up.** With Unreal Engine: `scripts/selftest.py` (section 3, "Tests"), then Unreal Insights. Without it: N1 and
N2 (the test setup and CI), then any other task in section 4.

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

### [x] Menus and HUD in a running game (UMG)
Done 2026-11-24: `ue.py ui` / `editor/ui.py`, `bpedit.bound_event` (README, "Menus and HUD"), with Widget Blueprints
built from a spec. Checked in PIE on 5.6, 5.7 and 5.8, and in a `ue.py net` client on 5.8. Still open: widget
rectangles (empty geometry in every run here) and ListView/TileView entries.

Forced keys reach gameplay input, not a focused widget, so tests can't drive menus today without project code.
- `ueh.ui()`: the live widget tree of the PIE or game world (class, name, text, visibility, on-screen rect).
- `ui_click(label_or_name)`, `ui_type(widget, text)`, `ui_text(name)`, and `yield ueh.ui_wait(label)` in scenarios.
  They work in `ue.py net` game processes too (`--instance`).
- A Widget Blueprint's designer tree from a spec (add and nest widgets, slot and layout properties, bindings): done
  as `ui.build` / `ue.py ui create`.
- **Done when:** a scenario starts PIE, opens a menu, clicks a button by its label and checks what happened, with the
  editor in the background.

### [x] Spawning into a PIE or game world
Done 2026-11-24: `ueh.spawn(..., world='game')` (README, "Spawning into the running game"), checked in PIE on 5.6, 5.7
and 5.8, and on a `ue.py net` server and client on 5.7 and 5.8.

`GameplayStatics.begin_deferred_actor_spawn_from_class` isn't exposed to Python, so scripts can't spawn into PIE.
- A BlueprintCallable in UETKEditor that spawns a class into a given world, with expose-on-spawn values set before
  `BeginPlay`. `ueh.spawn(..., world=ueh.pie_world())`.
- Attach on spawn; destroy.
- **Done when:** a scenario spawns a Blueprint actor with expose-on-spawn values into PIE, and on a `ue.py net`
  server it replicates to the clients.

## 2. Parts of the engine it doesn't cover yet

Most projects need these:

### [x] Validation: `ue.py validate`
Done 2026-11-24: `ue.py validate` / `editor/validate.py` (README, "Validating the project"), checked on 5.6, 5.7 and 5.8,
windowed and headless. Still open: a `materials` check that compiles every material and reports errors (the engine's
material validator is off by default); checking only the files a commit changes; a sample commit hook and CI job.

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

## 3. The toolkit itself (needs Unreal Engine)

The parts that don't need an engine are in section 4.

### Tests
- [ ] **`scripts/selftest.py`** (next): runs every command against an engine template project on a given engine
  version and reports pass or fail. Run it on every supported version before a release. It also settles the untried
  cases (VFX previews on 5.6 and 5.7, level Blueprint export), and would have caught `ueh.fix_redirectors` calling an
  API Python doesn't have.
- [ ] **Sample files for the engine-free tests:** a CSV profiler capture (`ue.py perf run`) and a `bpdump` export of a
  template project's Blueprints, with local paths scrubbed, in `tests/fixtures/`. N10 and N11 wait for these.

### Going public (done 2026-11-24)
- [x] MIT `LICENSE`.
- [x] Project-specific content out of the README; the general lessons moved to `docs/unreal-notes.md`.
- [x] A fresh public repo from one squashed commit (the earlier history stays in the private repo).
- [x] No fixed install path in the code or docs.
- [x] No engine source in `plugin/` (no Epic headers or copied files; it declares four exported UnrealEd functions
  whose header is private).
- [x] The bridge's trust model in the README ("Security").
- [x] README quickstart, supported versions and platform; `CONTRIBUTING.md`, `CHANGELOG.md`, the `v0.1.0` tag.
- [ ] A per-session token in bridge requests, so only the client that started an editor can drive it.
- Issue templates: N21 in section 4.

### Install and reach
- [ ] **MCP server** over the same commands, so agents in any MCP client get typed tools instead of shell lines. (The
  instructions snippet and the Claude Code skill are N20 in section 4.)
- [ ] **Linux and macOS.** Editor executables and build scripts, process discovery, screenshots (`uetk/winshot.py` is
  Windows-only), checked on a real install. Live Coding is Windows-only. Starts after N3 (imports that work off
  Windows).
- [ ] **Engine versions.** State the supported range (5.6–5.8 so far) and decide how far back to go.
- [ ] **Blueprint-only projects.** `editor start` fails with "No Editor target"; launch the editor directly instead.

## 4. Small tasks that don't need Unreal Engine

Each one needs only Python 3 and git, and is checked without an engine. Tests use the standard library's `unittest`,
so the toolkit keeps no dependencies. The code in `editor/` runs inside Unreal and imports `unreal`, so it can't be
imported outside it, except `editor/scenario.py`. Code in `uetk/` and `ue.py` is plain Python.

### Tests and CI
- [x] **N1. Test setup.** Done 2026-11-24: `tests/` with `tests/support.py`; "Running the tests" in `CONTRIBUTING.md`. A `tests/` folder run by `python -m unittest discover -s tests`, a first test
  (`uetk.plugins.with_dependencies` is an easy one), and a "Running the tests" paragraph in `CONTRIBUTING.md` in place
  of "There's no automated test suite yet". Done when the command passes in a fresh clone.
- [x] **N2. GitHub Actions.** Done 2026-11-24: `.github/workflows/tests.yml`: Windows and Linux, Python 3.10 and 3.13. `.github/workflows/tests.yml`: on every push and pull request, on `windows-latest` with
  Python 3.10 and 3.13, run the tests and `python -m compileall -q ue.py uetk editor scripts` (that catches syntax
  errors in the editor-side code the tests can't import). Done when a pull request shows the check. After N1.
- [x] **N3. Importable off Windows.** Done 2026-11-24: Win32 imports moved behind `sys.platform == "win32"` in `project.py`, `bridge.py`, `procs.py` and `winshot.py`; the Windows-only calls raise a clear `ToolkitError` elsewhere. `ubuntu-latest` is in the CI matrix. `uetk/project.py`, `uetk/bridge.py`, `uetk/procs.py` and `uetk/winshot.py`
  import `winreg` and `ctypes.windll` at the top, so `import ue` fails on Linux and macOS. Import them inside the
  functions that use them, or behind `sys.platform == "win32"`, and add `ubuntu-latest` to the CI matrix. Done when
  the tests pass on Linux and nothing changes on Windows. After N2.
- [x] **N4. Tests for `uetk/logs.py`:** Done 2026-11-24: `tests/test_logs.py`. `verbosity`, `category` and `is_problem` on sample lines
  (`[2026.11.24-14.49.05:592][  0]LogWindows: Error: ...`), Display, Warning, Error and Fatal lines, and lines without
  a timestamp.
- [x] **N5. Tests for `ue.py`'s helpers:** Done 2026-11-24: `tests/test_ue_cli.py`. `_undo_msys_paths` (fake Git Bash with `MSYSTEM` and `EXEPATH` pointing at a
  temp folder that has `usr/bin`), `_collapse_repeats`, `_dumps` (arrays of numbers stay on one line) and
  `_parse_params`; and every command's `--help` parses (`build_parser()`).
- [x] **N6. Tests for `editor/scenario.py`:** Done 2026-11-24: `tests/test_scenario.py`. `has_top_level_yield`, `wrap_scenario` and `top_level_bindings` on small
  scripts; a `Task` stepping through `Frames`, `Seconds` (with a fake clock) and `Until`; an exception inside a
  scenario reaching the caller.
- [x] **N7. Tests for `uetk/state.py`:** Done 2026-11-24: `tests/test_state.py`. `snapshot_save`, `snapshot_diff` and `snapshot_restore` in a temp folder (a
  stub with the `root` and `out_dir` attributes a `Project` has), and `ini_get`, `ini_set` and `ini_remove` keeping a
  file's encoding (UTF-16 ini files exist) and line endings.
- [x] **N8. Tests for `uetk/plugins.py`:** Done 2026-11-24: `tests/test_plugins.py`. `install` into a temp project adds, updates and removes files; `status`
  reports `up_to_date` before and after a source file changes.
- [x] **N9. Tests for `uetk/project.py`:** Done 2026-11-24: `tests/test_project.py`. `_find_uproject` from a subfolder, with `--project` (a file or a folder),
  with `UETK_PROJECT`, and the error when there's none; `_engine_from_launcher_manifest` with a fake
  `LauncherInstalled.dat` (point `PROGRAMDATA` at a temp folder).
- [ ] **N10. Tests for `uetk/perf.py`:** `parse_csv` (each row is a prefix of the header written at the end),
  `summarize` and `format_compare`. Waits for the sample capture (section 3, "Tests").
- [ ] **N11. Tests for `uetk/bpdump.py`:** `parse_t3d` and `condense` on exported Blueprints, compared with the
  expected `.txt` output. Waits for the sample export (section 3, "Tests").

### Toolkit
- [x] **N12. Warn about an out-of-date plugin.** Done 2026-11-24: `plugins.stale_warnings`, printed by `build`, `editor start|restart` and `py`; tested in `tests/test_plugins.py`. When a project's copy of UETKEditor or UETKVfx differs from the
  toolkit's (`uetk.plugins.status(project)[name]["up_to_date"]` is false), `ue.py editor start`, `ue.py build` and
  `ue.py py` print one line: "UETKEditor in this project is older than the toolkit's: run `ue.py plugins install`,
  then `ue.py build`". Today a stale copy shows up as a missing function deep inside a call. Test it with a temp
  project folder.
- [x] **N13. Version number.** Done 2026-11-24: `uetk.__version__`, `ue.py --version`; `tests/test_ue_cli.py` checks it against `CHANGELOG.md`. `__version__` in `uetk/__init__.py`, `ue.py --version` prints it, and a test checks it
  against the newest version heading in `CHANGELOG.md`.
- [x] **N14. Installable package.** Done 2026-11-24: `pyproject.toml`: `editor/`, `plugin/` and `scripts/` ship as `uetk/_editor`, `_plugin`, `_scripts` (`project.toolkit_path` finds either layout). Checked with `pip install .` in a clean virtual environment (what pipx does): `ue --help`, `ue --version`, and `ue info` outside a project. A `pyproject.toml` (setuptools, no runtime dependencies) so that
  `pipx install git+https://github.com/zeagleee/UnrealToolkit` gives a `ue` command, with `editor/`, `plugin/` and
  `scripts/` shipped too (the code finds them relative to `uetk/`: `project.EDITOR_PY_DIR`, `plugins.ROOT`). Done
  when, in a clean virtual environment, `pipx install .` works, `ue --help` lists the commands, and `ue info` outside a
  project prints the "No .uproject" error. Add it to the README's quickstart.

### Docs
- [x] **N15. Command reference.** Done 2026-11-24: `docs/commands.md`; `scripts/gen_docs.py --check` in CI and `tests/test_docs.py`. `scripts/gen_docs.py` writes `docs/commands.md` from `ue.py`'s argparse (every
  command with its arguments and help), and CI fails when the file is out of date.
- [x] **N16. Helper reference.** Done 2026-11-24: `docs/helpers.md`, from the same script. The same script writes `docs/helpers.md`: the public functions of `editor/ueh.py`,
  `bpedit.py`, `mat.py`, `inputs.py`, `importer.py`, `ui.py` and `validate.py` with signatures and docstrings, read
  with `ast` (those modules import `unreal`, which only exists inside the editor).
- [ ] **N17 to N19. Split the README** (848 lines) into `docs/`. The README keeps the overview, quickstart, security,
  the commands table, the rules of thumb and the layout, and links to each page. Move the text as it is, and fix the
  links that pointed within the README ("below, ..."). One pull request each:
  - [ ] N17: "Materials", "Enhanced Input", "Menus and HUD" to `docs/materials.md`, `docs/input.md`, `docs/ui.md`.
  - [ ] N18: "Spawning into the running game", "Importing", "Validating the project" to `docs/spawning.md`,
    `docs/importing.md`, `docs/validation.md`.
  - [ ] N19: "VFX", "Multiplayer sessions", "Performance runs", "Editing Blueprints", "Reviewing Blueprints" and
    "Offscreen renders", one page each.
- [ ] **N20. Instructions for agents.** `docs/agents.md`: a block projects can paste into their `CLAUDE.md` or
  `AGENTS.md` (where the toolkit is, the commands to reach for first, the rules of thumb agents trip on), and a Claude
  Code skill with the same content in `skills/unreal-toolkit/SKILL.md`. Written from the README.

### Repository
- [ ] **N21. Issue and pull request templates.** `.github/ISSUE_TEMPLATE/`: a bug report (engine version and
  `ue.py info` output, the command, what came back, the log excerpt `CONTRIBUTING.md` asks for) and a feature request.
  `.github/pull_request_template.md` with boxes for "checked on 5.6 / 5.7 / 5.8" and "windowed / headless".
- [ ] **N22. Release 0.2.0,** once the maintainer agrees: `CHANGELOG.md`'s "Unreleased" becomes "0.2.0 (date)", tag
  `v0.2.0`, and a GitHub release with those notes.
