# Offscreen renders

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
