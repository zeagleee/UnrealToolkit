# VFX (Niagara particle systems)

Python can't build Niagara systems on its own: almost nothing in Niagara's editor is exposed to scripting. The
**UETKVfx** editor plugin (`plugin/UETKVfx`, C++) drives the Niagara editor's own stack view models headlessly,
so static switches, rapid-iteration parameters, dynamic inputs and edit conditions behave as if someone clicked
them. `editor/vfx.py` (pre-imported as `vfx` in `ue.py py`) adds a spec builder, presets, shared materials, and
offscreen previews.

- **Setup, once per project:** `ue.py plugins install` (or `ue.py vfx install`, which installs UETKVfx and the
  UETKEditor it needs), then `ue.py build --restart`. See [Editor plugins](../README.md#editor-plugins).
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
  [Importing](importing.md)): on 5.7, `Interchange.FeatureFlags.Import.SyncToBrowser` syncs the Content Browser after every
  import, even headless. `importer` turns it off for headless runs.
- **API differences across versions:** 5.6 has `FStringOutputDevice` in `Containers/UnrealString.h` and a
  `SetDataInterfaceValue` that takes an instance and `check()`s its class; 5.6/5.7 lack
  `FNiagaraEditorUtilities::UserParameters::AddUserParameterToSystem` (add to `GetExposedParameters()` directly).
