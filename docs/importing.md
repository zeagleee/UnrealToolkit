# Importing

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
- **From DCC tools:** see [From Blender (FBX)](unreal-notes.md#from-blender-fbx) (V is flipped; linear colours in an sRGB PNG come
  out too dark). Interchange also warns about FBX meshes saved without smoothing groups: turn on the exporter's
  smoothing groups option.
