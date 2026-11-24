# Reviewing Blueprints

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
