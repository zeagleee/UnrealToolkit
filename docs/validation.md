# Validating the project

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
