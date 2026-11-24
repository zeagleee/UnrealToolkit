# Changelog

## Unreleased

- **Menus and HUD** (`ue.py ui`): the widgets on screen in the running game (PIE or a `ue.py net` process) as a
  tree; click, type, set values and read text by label or name; wait for a widget in scenarios; open a widget.
  Widget Blueprints built from a spec and read back.
- `bpedit` gains `bound_event` (a widget's or component's event, as the editor's "+" buttons add it).
- **Spawning into the running game:** `ueh.spawn(..., world='game')` in PIE or a `ue.py net` process, with
  properties (expose-on-spawn variables) set before the construction script and BeginPlay; owner, attach, scale.
  `actors`, `actor` and `destroy` take `world='game'` too.
- `ue.py editor start` names a startup dialog after about 45 s instead of waiting out its timeout.
- **Validation:** `ue.py validate` / `editor/validate.py`: Data Validation, Blueprint compiles, broken references,
  redirectors, texture sizes, map check, and opt-in unused assets and naming rules. Settings and ignore rules in
  `.uetk/validate.json`, JSON output, exit code 1 on errors. `--fix-redirectors` fixes redirectors without the
  engine's dialog. New `UETKValidateLibrary` in the UETKEditor plugin, which now turns on Data Validation.
- `ueh.rename` rescans the asset registry first, so a referencer saved moments before isn't left pointing at the
  old name; `ueh.refresh_registry` does that rescan. `ueh.fix_redirectors` works again (it called an API Python
  doesn't have).
- `ue.py` puts back `/Game/...` arguments that Git Bash rewrote; `MSYS_NO_PATHCONV=1` is no longer needed.
- **Tests:** engine-free tests in `tests/` (`python -m unittest discover -s tests`) for `ue.py`'s helpers and
  parser, logs, snapshots and ini editing, plugin installs, project and engine discovery, and scenarios.
- GitHub Actions runs the tests on Windows and Linux with Python 3.10 and 3.13 on every push and pull request.
- `import ue` and the `uetk` modules work on Linux and macOS; the Windows-only parts (process discovery,
  launching, screenshots, `bridge install`) say so there instead of failing on import.
- `ue.py build`, `editor start|restart` and `py` warn when the project's copy of a toolkit plugin differs from the
  toolkit's (run `ue.py plugins install`, then `ue.py build`).
- `ue.py --version`.
- Installable with pipx: `pipx install git+https://github.com/zeagleee/UnrealToolkit` gives a `ue` command.
- Generated references: `docs/commands.md` (every command) and `docs/helpers.md` (every editor-side helper),
  written by `scripts/gen_docs.py`; CI fails when they are out of date.

## 0.1.0 (2026-11-24)

First public release. Windows, Unreal Engine 5.6–5.8.

- **Project and editor:** find a project's engine; build, Live Coding, package, automation tests; start, stop and
  restart the editor; Python in the live editor (`ue.py py`), scenarios that wait for the game, headless runs; logs and
  crash summaries; snapshots of save games; background-throttling control.
- **Play and test:** PIE control, forced key and mouse input through Enhanced Input's mappings, multiplayer sessions
  as separate server and client processes, CSV-profiler performance runs with A/B comparison.
- **Looking at things:** offscreen renders of level views, PIE cameras, actors, assets and UMG widgets, with
  post-process materials.
- **Blueprints:** every Blueprint as readable text (`bpdump`); graph editing (`bpedit`), including Enhanced Input action
  events and any graph node class.
- **Materials** (`ue.py mat`): materials from specs whose outputs are expressions, read back as the same spec;
  instances, material functions, compile errors, previews.
- **Enhanced Input** (`ue.py input`): actions and mapping contexts as specs, key mapping, contexts on Blueprints and on
  the running player.
- **Importing** (`ue.py import`): meshes, animations, textures and sounds, single files or folders, with presets;
  reimport of changed sources.
- **Niagara** (`ue.py vfx`): particle systems from presets or specs, offscreen previews, stack descriptions.
- **C++ editor plugins** (optional): UETKEditor (renders, Blueprint and material graphs, collision and LOD helpers, key
  list) and UETKVfx (Niagara).
