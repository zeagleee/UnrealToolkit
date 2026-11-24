# Changelog

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
