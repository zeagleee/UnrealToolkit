# Unreal Engine notes

Things learned while building games with UnrealToolkit on UE 5.6–5.8 that aren't about the toolkit itself:
performance measurement, networking, rendering, online sessions, project setup. Each was found the hard way and
checked at the time; versions and dates are given where they matter.

## Measuring performance

- **`csvprofile start` / `csvprofile stop`** (console, in PIE or a game) writes
  `<Project>/Saved/Profiling/CSV/Profile(<time>).csv`: one row per frame with `FrameTime`, `GameThreadTime`,
  `RenderThreadTime`, `GPUTime`, `RHI/DrawCalls`, `DrawCall/ShadowDepths`, `RDGCount/Passes` and the
  `Exclusive/GameThread/*` categories. The first column (`EVENTS`) is empty, and the file ends with metadata rows.
  `ue.py perf run` does all of this.
  - **Parse by the header at the end, not the first row.** The profiler adds columns while it records, so each row
    holds only the columns known when it was written, and the full header is repeated as the last `EVENTS` row
    (`[HasHeaderRowAtEnd] 1`). Every row is a prefix of that last header.
  - A parser that read the first row and skipped rows of another length kept 65 of 362 frames, all of them early, and
    lost every column added later (`RenderThreadTime` among them). Two of its game-thread medians were 4 ms too high.
- **Bring the editor to the front** (unfocused, it runs at about 3 fps) or start it with `ue.py editor start
  --no-throttle`, and compare A and B within one PIE session: the baseline drifts between editor sessions by 20% or
  more.
- **PIE's game thread carries the editor's UI** (about 5.5 ms, "UI" in `ue.py perf run`), and it grows while shaders
  compile after a settings change. While the game thread is the bottleneck the GPU clocks down, so GPU time per frame
  goes up (4.5 ms in one run, 6.6 ms in the next, same scene). For real numbers use a game process at the real size:
  `ue.py net start --clients 0 --render server`, then `ue.py --instance server exec "r.SetRes 1920x1080w"`.
  `r.ScreenPercentage` above 100 in a small viewport isn't a 1080p stand-in: TSR then downsamples, and its history
  follows the output size.
- **Benchmarking from Python:** apply a configuration through cvars, wait, `csvprofile start`/`stop` for 5 s per scene,
  take medians. Run the scalability presets first: console cvars outrank scalability and stay set until the editor
  restarts.
- **The `ProfileGPU` log dump** is a box-drawn table: split rows on '┃'; the inclusive time is the last 'ms' in the
  second column, and the name's indent (3 spaces per level) is the depth.

## Scene captures

- **A `SceneCaptureComponent2D` costs about a full extra scene render** (about 2 ms of render thread in a Lumen/VSM
  project, mostly fixed per-view cost). Turning off shadows, Lumen and AO in its show flags saves little;
  `LODDistanceFactor` and `bAlwaysPersistRenderingState` saved nothing. Render fewer captures: cull off-screen ones,
  budget them per frame, or cap their rate.
- **A capture that sees the sky renders `VolumetricCloud` from scratch each time** (no frame-to-frame history), at up to
  about 10 ms of GPU per capture. It showed up as a vehicle's mirrors costing nothing on the road and tripling the frame
  time when the vehicle nosed down in a jump. Found by freezing the pose and toggling one capture show flag at a time;
  `Cloud` was the only one that mattered. Fix: `Capture->ShowFlags.SetCloud(false)`.
- Show-flag A/B at runtime from Python: `capture.set_editor_property('show_flag_settings',
  [unreal.EngineShowFlagsSetting(show_flag_name='DynamicShadows', enabled=False)])`. Setting `texture_target` to None
  stops a capture rendering.
- **Tuning a Blueprint class default during PIE doesn't reach new spawns** if the Blueprint's value equalled the C++
  default when it was last compiled: a Blueprint actor's construction copies only the properties the Blueprint overrides
  (its compiled custom property list), so a new value on the CDO is ignored until a recompile. Set the live instances
  instead (both machines' copies). Setting the CDO also propagates to level instances and dirties the level: discard it.
  Seen with a held item's `HeldInView` set on its Blueprint's CDO from Python during PIE (5.8, 2026-11-25).
- **`HighResShot` sent to a PIE client's world can capture the editor's active viewport (the host) instead,** now and
  then; take two shots a second apart and check whose view each is.
- **A full-size shot of one PIE player's view:** run `HighResShot 1600x900` on that player's PIE world
  (`SystemLibrary.execute_console_command(world, ...)`); it lands in `Saved/Screenshots/WindowsEditor/` without the HUD.
  A window screenshot of the small client window keeps the HUD (prompts, hints) but at its size.
- **`AutomationLibrary.take_high_res_screenshot` called from a slate tick during PIE stalls the game for seconds**
  (world time stops, then jumps on; 5.8, 2026-11-27), so it can't time a sequence. To catch the phases of a short
  animation, slow the world instead (`slomo 0.2` on the server's PIE world; time dilation replicates to clients) and
  take window screenshots about a second apart.
- **`CaptureScene()` renders only when the main view does**, so with the editor minimized an exported capture is black.
  Window screenshots skip minimized windows too. `ue.py render` captures and reads back in the same call
  (`FlushRenderingCommands()`), so it works minimized.
- **A studio capture far below the level comes out fog-coloured:** exponential height fog gets denser the lower you
  go, so objects at Z −90000 rendered as flat fog. Shoot such captures high above the level (Z +60000) and turn the
  `Fog` show flags off (5.6, 2026-11).

## Rendering cost and look (UE 5.8)

- **What Epic quality costs** (1080p, a greybox scene, RTX 4060): 11–15.6 ms of GPU. Removing one thing at a time
  saved: Lumen GI and reflections plus hardware ray tracing 1.4–2.3 ms; virtual shadow maps → cascaded 0.2–2.0
  (outdoors); six shadowed point lights 1.1; volumetric clouds plus the sky light's real-time capture 1.1–1.6 (the clouds
  also render into the capture); TSR from 75% about 1.0. Everything removed: 5 ms.
- **Epic anti-aliasing keeps TSR's history at 200%** (`r.TSR.History.ScreenPercentage`): about 1.3 ms of 9.3 at a real
  1080p on an RTX 4060.
- **`r.RayTracing=True` in `DefaultEngine.ini`** builds ray-tracing data even with it off at runtime (scene updates,
  memory, shaders, cook time). Turn the project setting off if nothing uses it.
- **Scalability presets that switch features off change the look,** not just the cost: no Lumen indoors is dark and blue
  at Epic quality, flat white at Low (where distance-field AO is off too). For one look at every preset, drop the
  features in the project and let the presets scale resolution, shadow range and AO quality.
- **5.8's `BaseScalability.ini` changes the look at Low:** `[ShadowQuality@0]` sets `r.ShadowQuality=0` (no shadows at
  all) and `r.Shadow.CSM.TransitionScale=0` (a hard cascade seam); `[GlobalIlluminationQuality@0]` dims the sky light
  (`r.SkylightIntensityMultiplier=0.8`) while levels 1–3 turn on distance-field AO; `[PostProcessQuality@0]` has no SSAO
  (`r.AmbientOcclusionLevels=0`). For one look, override those in your `DefaultScalability.ini`.
- **Project settings outrank scalability:** `r.Shadow.Virtual.Enable=1` under `[/Script/Engine.RendererSettings]` can't
  be switched off by `[ShadowQuality@0]`; move it into `DefaultScalability.ini`'s sections.
  - A scalability ini can only set `ECVF_Scalability` variables. `r.RayTracing.Enable` isn't one (it ensures and is
    ignored); set it from your `UGameUserSettings::ApplyNonResolutionSettings` at `ECVF_SetByGameSetting`, only when it
    changes. In 5.8, `r.RayTracing.EnableOnDemand` defaults to 1, so it can be toggled at runtime.
- **A post-process volume can't dim the sky light:** Indirect Lighting Intensity only scales precomputed (lightmap)
  lighting (`PrecomputedIndirectLightingColorScale` in `BasePassPixelShader.usf`). Without Lumen or distance fields,
  give interiors unshadowed fill lights: with exposure fixed, the sky light alone left a vehicle's interior dim and
  blue, and distance-field AO changed almost nothing once a warm fill light was on. A "flat white" interior is auto
  exposure brightening a sky-only room.
- **A cheap cloud sky without `VolumetricCloud`:** an unlit, translucent, two-sided material on the engine's plane
  (100 km square, 2 km up), density from a tileable noise texture in world XY, faded toward the plane's edge, lit by
  `SkyAtmosphereLightIlluminance` (light 0) and `SkyAtmosphereViewLuminance` so it follows the sun and the exposure.
  The sky light captures it once (real-time capture off).
- **Lumen trails behind moving objects:** bright objects moving just above a surface (a held object over paper) left
  lighter copies of themselves that faded over about 10 frames.
  - Not TSR, motion blur or virtual shadow maps: switch each off, one at a time, while sweeping the object, and compare
    screenshots. The cause was Lumen's screen-probe gather history; on rough surfaces (roughness above
    `MaxRoughnessToTrace`, 0.4) it also supplies the specular.
  - `r.Lumen.ScreenProbeGather.Temporal.RejectBasedOnNormal 1` (default 0) mostly removes them; with
    `...Temporal.MaxFramesAccumulated 5` (default 10) they're gone. Interiors render darker with it, since some
    brightness was carried in the history.
- **Post-process outline in 5.8:** a Custom node with `SceneTextureFetchFunc(Parameters, 25, PixelOffset)` (25 =
  CustomStencil; the offset is in the texture's own pixels) and a SceneTexture:CustomStencil node wired into it, which
  makes the shader bind the texture. Blendable location: after tonemapping.
- **An outline-only "ghost"** (where something will go): a mesh with `SetRenderInMainPass(false)` and
  `SetRenderInDepthPass(false)` still renders into custom depth when highlighted, so a post-process outline draws its
  shape with nothing else visible.

## Rendering behaviour (UE 5.6)

Seen on 5.6 between 2026-10 and 2026-11 unless noted.

- **Virtual shadow maps don't follow a displacement or WPO change driven by a material parameter collection.** Cached
  VSM pages keep the old shape: a displaced landscape shadowed itself in big triangles near the view, which looked like
  a tessellation bug. Set the component's Shadow Cache Invalidation Behavior to Always while its shape can change, or
  mark its render state dirty once after a change. `r.Shadow.Virtual.Cache 0` tells whether this is the cause.
- **A translucent material whose texture scrolls writes no velocity,** so TSR smeared its moving dots into streaks
  (responsive AA didn't help). Put it in the After Motion Blur translucency pass, which is composited after TSR.
  Compare with `r.AntiAliasingMethod 0` to tell the anti-aliasing from the material.
- **Small fast particles in shots from a background editor:** at about 3 fps a snowflake moves so far between frames
  that TSR wipes it out. Take such shots under `slomo 0.05`, or start the editor with `--no-throttle`.
- **Faint ghosts on a widget's render target just after its text changes,** with the camera still, are TSR history,
  not the widget (they go with `r.AntiAliasingMethod 0`).
- **Translucent materials don't write custom depth** by default, so a stencil outline misses them. Turn on Allow
  Custom Depth Writes in the material.
- **An outline added as a post-process blendable on the player's camera** shows only through that camera. Switch the
  view target to another camera (a fixed close-up view of a workstation) and every outline is gone: add the blendable
  to each gameplay camera.
- **With manual exposure in an unbound post-process volume, only the exposure bias counts.** A camera that overrides
  min and max brightness changes nothing; read the level's bias and offset it.
- **One-sided thin-shell meshes** vanish when seen from inside or through a gap, and a custom-depth outline then draws
  the edges of whatever is behind them. Give them an inner wall (flipped faces a few millimetres in) and weld the
  seams when preparing the mesh.

## Materials and textures from Python (UE 5.6)

- **`MaterialEditingLibrary.delete_all_material_expressions` or `delete_material_expression` on a loaded material
  asserts `!IsRooted()` and crashes the editor,** also when the material is only held by a C++ class default
  (`ConstructorHelpers`). Patch the existing nodes in place, or delete the `.uasset` with the editor closed and build
  the material again.
- **Deleting a material and creating it again at the same path** (`delete_asset` + `create_asset`) clears it from every
  mesh slot that used it; those meshes draw the default material. Patch the existing material in place.
- **A sampler type that doesn't match the texture fails the material** (default material or checker grid, and only a
  log line says so): `SAMPLERTYPE_GRAYSCALE` on a linear `TC_GRAYSCALE` texture, `LINEAR_GRAYSCALE` on a
  colour-compressed one. Match the sampler to the texture's compression and sRGB flag, and grep the log for
  `Failed to compile Material` after a material script.
- **A grayscale texture sample's default output ("") is still RGB:** wired into float2 maths it fails with
  "Arithmetic between types float3 and float2". Connect the `R` pin.
- **A bluish RGBA texture can be imported as a normal map** (a moon with a blue halo was): the material fails and
  draws a grey square. Set `compression_settings` to `TC_DEFAULT` and `srgb` after the import.
- **Canvas triangles hand vertex colours to a render-target material sRGB-encoded.** `FCanvasUVTri` colours drawn with
  `K2_DrawMaterialTriangle` are stored with `ToFColor(true)` and the material reads them raw, so 0.2 arrived as about
  0.48. Pass `FLinearColor(FColor(value × 255))` so the decode undoes the encode.

## Networking

### Physics vehicles and seats (Chaos)

- **Stock Chaos input RPC:** a Chaos vehicle with `SetRequiresControllerForInputs(false)` takes inputs locally on every
  machine and sends `ServerUpdateState`, a reliable server RPC, every tick. Clients that don't own it log "No owning
  connection ... ServerUpdateState" each frame.
  - Override `UpdateState` in a subclass: smooth the raw inputs into `ReplicatedState`, then call `Super` with
    `bRequiresControllerForInputs` true.
  - Set the class with `ObjectInitializer.SetDefaultSubobjectClass<...>(AWheeledVehiclePawn::VehicleMovementComponentName)`.
    The Blueprint's component settings survived the switch.
- **Characters attached to a moving vehicle** still send moves. Unless `bIgnoreClientMovementErrorChecksAndCorrection` is
  on while attached, the server "corrects" them by the gap between its copy of the vehicle and the client's. That moves
  the capsule off its attach point, and the camera wanders.
- **A seated player's look on other clients:** a character attached to a seat with `bUseControllerRotationYaw` still
  turns its actor with the view, and the attachment's relative rotation replicates, so the look yaw arrives. The pitch
  may not: `APawn::GetBaseAimRotation` uses the replicated view pitch only when the actor's own pitch is exactly zero,
  and a vehicle's tilt (−0.19° parked, UE 5.8) gives the attached actor some. Read the pitch from
  `FRotator::DecompressAxisFromShort(GetRemoteViewPitch())` yourself on machines without a controller.
- **Seat changes:** replicate any data the client needs for a seat change (an exit spot) in the same actor as the seat
  property, not in an RPC. Properties changed in the same frame arrive together, and their OnReps run after all of them
  are applied; an RPC can arrive later.
- **Moves in flight across a server-side state change:** after the server teleports a player out of a seat, the
  client's moves made while it still sat keep arriving for a round trip. Tell them apart by the packed movement mode
  (`UnpackNetworkMovementMode` → `MOVE_None`), and neither correct them nor take them for a moment.
- **Server clock on clients:** `AGameStateBase::GetServerWorldTimeSeconds()` runs a one-way trip behind the real server
  clock (no latency compensation). Add `PlayerState->GetPingInMilliseconds() / 2000`.
- **Don't measure acceleration on a follower copy:** a copy whose velocity is steered toward 30 Hz samples reads each
  correction step as acceleration. At 48 km/h under 150 ms of lag it read up to 5x the real value (21.5 m/s² against
  4.1). Send the simulating machine's filtered reading in the samples instead; it arrives about a one-way trip late.
- **`AWheeledVehiclePawn` is a Pawn.** A trace that skips pawns (so as not to land on a player) skips the vehicle too,
  and a dropped item fell through its floor. Skip `ACharacter` instead.

### Cameras on a listen server

- **A listen server runs a `PlayerCameraManager::UpdateCamera` for every remote player too** (`UWorld::Tick` calls
  `UpdateCameraManager` on every player controller). Its view comes from that client's `ServerUpdateCamera` reports: an
  `FVector_NetQuantize` (whole centimetres), sent at the network rate, a few frames late. Anything an `UpdateCamera`
  override does to its view target (placing first-person hands, say) must check `PCOwner->IsLocalController()`, or the
  host moves remote players' parts from that stale, stepped view. Seen in a co-op vehicle game (UE 5.8, 2026-11-26):
  a seated passenger's first-person hands, seen from the host, swung 0–50 cm along the vehicle at 30 km/h; walking
  players' stepped at the network rate. Tell-tale sign: the "view" has whole-number coordinates that change every
  other frame.

### Replicating a growing list (`FFastArraySerializer`)

- **It doesn't keep order on clients** (its header says so; loss and resends can reorder). Give each item its start
  index and reassemble in order, stopping at a gap.
- **React in `PostReplicatedReceive` with a flag,** not by calling into the owner: the item callbacks run mid-receive.
  Don't store an owner pointer in the serializer struct either; the archetype's copy assignment copies it.
- **Byte payloads:** a `TArray` of a two-`uint8` struct replicates per element with property handles. Wrap it in a
  struct with `NetSerialize` (a packed count, then the bytes) and `WithNetSerializer` + `WithIdenticalViaEquality`.
- **Component RPCs** need the component to replicate (`SetIsReplicatedByDefault(true)`), even with no replicated
  properties.
- **Skipping your own echoes by player id** breaks when the actor is re-created (it left relevancy and came back): the
  fresh copy never drew them. Skip only on the instance that drew them live.

### Items carried and placed by every machine

- **The engine replicates attachment anyway.** `AActor::GatherCurrentMovement` fills `AttachmentReplication` whenever the
  root is attached, even with movement replication off (it's active unless the root component replicates). If every
  machine attaches the item itself from a replicated state (a hand, a slot, a grab point), override
  `OnRep_AttachmentReplication` to re-apply that state instead.
- **Taking an item with a floating hand:** don't attach it when the server says "taken". If the hand's reach targets a
  part of the item, the hand chases its own item. Keep the item where it lay until the hand touches it (a short pause
  reads as a grab), then attach it keeping its world transform and let the hand's return carry it.
- **Placing a held item with a grip pose:** fix the item's spot in view, and put the hand where the grip frame needs it:
  hand = ItemInHand⁻¹ × ItemInView, with ItemInHand from the pose's grip frame. Of the item's corners and faces, pick by
  handedness first (only half can match the grip's frame), then by closeness to the empty hand's rest.
- **A flat sheet held up in first person** facing the eye straight on sits in its own shade and reads black; tilt it back
  about 35 degrees, as a hand would.
- **To judge what a first-person hand hides without rendering:** project its bones
  (`PoseableMeshComponent.get_bone_location_by_name`, world space) into the camera manager's view, give each segment a
  radius, and rasterize against the target's screen quad.

### Timing a player's action against something moving (2026-11-24)

- **Let the player's machine decide the moment** and send what it saw (the moving thing's progress); the server checks it
  against its own copy with a window. Under `NetEmulation.PktEmulationProfile Bad` (5 % loss each way) one reliable RPC
  arrived **1.9 s** after it was sent (a resend), and a window sized from the player's ping refused it. Size the window
  by a fixed time (2 s) instead, and keep the thing open long enough after its last target for a late claim to land.
- **A minimum gap between a client's events refuses real ones** that a resend bunches together. Use a token bucket
  with a burst.
- **Share a moving thing as a line, not a stream:** the server keeps `Progress + Rate × (t − Time)` and re-sends it only
  when the rate moves a step (or it starts, stops, or gets old); everyone else evaluates it at their estimate of the
  server clock (`AGameStateBase::GetServerWorldTimeSeconds()` plus half the ping). Under the Bad profile that estimate
  ran about 0.1 s ahead of the server.
- **Easing toward a moving target trails it** by rate × the ease's time constant (0.08 rows at 0.8 rows/s with 0.1 s).
  Advance the shown value at the line's own rate first, then ease out only the remaining error.

### Online sessions, travel and settings

- **Steam Sockets config:** put the `NetDriverDefinitions` under `[/Script/Engine.GameEngine]`, not
  `[/Script/Engine.Engine]`, so the editor and PIE (`UEditorEngine`) keep the base IP driver.
  - Give the Steam driver `/Script/OnlineSubsystemUtils.IpNetDriver` as its fallback. Without Steam,
    `USteamSocketsNetDriver::IsAvailable()` is false, and Epic's sample (Steam Sockets as its own fallback) leaves the
    game with no net driver.
  - The Steam OSS only starts in a game process (`IsRunningGame()`); the editor gets the Null subsystem. `-nosteam` does
    the same in a game process, which is how two processes on one PC test hosting and joining over the LAN.
- **Multi-player PIE can't change maps.** With `net.AllowPIESeamlessTravel 1` the clients' seamless travel stalls (they
  never load the transition map), and a hard `ServerTravel` drops them. Test travel between real processes
  (`ue.py net`, or `UnrealEditor.exe <uproject> -game -nosteam -nullrhi -ExecCmds="..."`).
  - Several *rendering* game processes next to an editor in PIE ran a 16 GB machine out of memory, and the editor died
    of a GPU crash. Use `-nullrhi` for network tests.
- **In-process PIE ignores `AdditionalServerGameOptions`** (only new-process PIE reads it), and its server starts
  listening only after `InitGame`. So decide anything that depends on "am I a listen server" when it's used, not in
  `InitGame`, and use a cheat cvar in PIE where a URL option would do in the game.
- **Console commands a script runs run inside Python too,** so their server/client RPCs run locally, like UFUNCTIONs
  called from Python. Test commands that send RPCs (a host's "return to main menu" to its clients) should act a tick
  later (`FTSTicker`).
- **A PIE client window behind the editor doesn't paint, so its UMG widgets don't tick.** Anything a menu must show at
  once belongs where it's built, not in `NativeTick`.
- **`UGameUserSettings::ApplyResolutionSettings` isn't virtual.** To keep the editor's window alone, override
  `ApplySettings` and skip it when `GIsEditor`.
- **Code-built UMG menus:** a `UButton` subclass holding a `TFunction<void()>`, with one UFUNCTION handler bound by
  `OnClicked.AddDynamic`, makes rows and steppers without a UFUNCTION each. `ue.py ui click <label>` clicks one (it
  fires the button's OnClicked, as `button.on_clicked.broadcast()` does).

## Editor scripting and PIE (UE 5.6)

- **`unreal.Rotator(a, b, c)` is (roll, pitch, yaw)**, not C++'s (pitch, yaw, roll): a view meant to look 40° down came
  out rolled 40°. Use keywords: `unreal.Rotator(roll=0, pitch=-40, yaw=10)`.
- **Structs read with `get_editor_property` are copies.** Editing an element of `asset.get_editor_property("pieces")`
  changes nothing; copy the list, edit it, and set the whole array back.
- **`StaticMaterial` has no `material`;** the field is `material_interface`.
- **A C++ class without `BlueprintType`** is wrapped as its nearest exposed parent (a `UDataAsset` subclass shows up
  as `DataAsset`, and none of its properties are found), and an enum without it doesn't exist in `unreal` at all.
  Add `BlueprintType`; `unreal.load_class(None, "/Script/Module.Class")` finds the class.
- **`isinstance(c, unreal.ChildActorComponent)` can throw;** compare `c.get_class().get_name()` instead.
- **`EditorAssetLibrary.load_asset` returns None during PIE** ("The Editor is currently in a play mode"). An import
  earlier in the same script still lands, but nothing after the load runs or saves. Stop PIE before asset scripts.
- **Setting an inherited component's properties on a child Blueprint from Python wrote them to the parent class,**
  and broke the other children. Change such values by hand in the child's Details panel, or expose a property on the
  C++ class.
- **Python edits to actors in the editor world mark the level dirty,** even on transient actors. Set properties with
  `PropertyAccessChangeNotifyMode.NEVER`, and still expect to discard the level afterwards.
- **`/Game/...` paths passed from Git Bash on Windows** become `C:/Program Files/Git/Game/...` (MSYS path
  conversion), as arguments and in files a Bash loop writes; a batch then silently matches nothing. Prefix the command
  with `MSYS_NO_PATHCONV=1`, or drive the batch from Python.
- **A force-killed editor stops at a modal "Restore Packages" dialog on its next start** (frame 0, no remote call is
  answered). Move `Saved/Autosaves/PackageRestoreData.json` aside before starting it again.
- **A Live Coding patch lives only in that editor session;** a restarted editor loads the old DLL. Build fully before
  any restart.
- **The level editor's shortcuts stay live during PIE:** P toggles the navigation mesh display. Pick debug keys the
  level editor doesn't use.
- **Restoring a minimized editor window (`ShowWindow`) during PIE** crashed the D3D12 renderer once. Leave the window
  state alone.

## Moving assets in bulk (UE 5.6)

- **Moving thousands of assets in one session** ran out of memory on a batch of big textures, then crashed the GPU. A
  crash mid-batch leaves copies at the new path and some originals already turned into redirectors. Start the editor
  with `-nullrhi`, move at most about 25 assets per `AssetTools.rename_assets` call, and drop Python references and
  collect garbage between calls. After a crash, delete the half-written copies and restore the originals from version
  control.
- **`rename_assets` on an asset a C++ class default holds** (`ConstructorHelpers`), or on a map in the editor's recent
  maps, opens an OK/Cancel dialog ("Source code, config INI ... may need Find/Replace"); unattended it answers Cancel
  and nothing moves. Change the code and ini paths first, rebuild and restart, then move it.
- **A moved skeletal mesh leaves a full copy behind:** its old package can't be saved as a redirector ("Illegal
  reference to private object"). Check that nothing references it, then
  `EditorAssetLibrary.consolidate_assets(new, [old])`, or delete the old file with the editor closed.
- **Fix Up Redirectors has no Python API.** Load and re-save every package that references a redirector, then delete
  the redirectors nothing references; Blueprint redirectors may refuse, so delete their files with the editor closed.

## Project setup and gameplay code

- **New C++ project without the editor UI:** `scripts/new_from_template.py`. The editor's new-project path
  (`GameProjectUtils`) also writes `r.Substrate=True`, virtual shadow maps, Lumen and distance fields; set your
  renderer settings before the first launch so shaders compile once.
- **One File Per Actor on a non-partitioned level:** `LevelEditorSubsystem.new_level(path, False)` makes the level
  without it, and Python can't reach `ULevel`. Call `ULevel::ConvertAllActorsToPackaging(true)` from a small
  BlueprintCallable in your game module (body under `WITH_EDITOR`).
- **UHT forbids a `UPROPERTY` name in a child class that a parent already has.** Adding `SprintAction` to the First
  Person template's base character broke its Horror variant, which declares its own. Engine classes count too: a
  `UPROPERTY` named `Cursor` on a `UUserWidget` clashes with `UWidget::Cursor`.
- **Changing a `UPROPERTY`'s object type** (e.g. `USceneComponent*` to a subclass) nulls the saved value in Blueprints
  and levels on load ("Serialized Class X for a property of Class Y. Reference will be nulled"), even for a default
  subobject pointer. Rename the property instead, so the old saved value is skipped and the constructor's stands; then
  recompile and re-save the Blueprints and levels that place it.
- **Actor components aren't active by default** (`bAutoActivate` false): gating logic on `IsActive()` silently turns it
  off.
- **A pawn spawned inside a trigger** overlaps before its controller possesses it, so a "player only" begin-overlap
  ignores it and never fires again. Poll `GetOverlappingActors` (10 Hz is plenty) for player triggers.
- **`FOverlapResult`** needs `#include "Engine/OverlapResult.h"`; `WorldCollision.h` only forward-declares it.
- The templates' `/Game/LevelPrototyping/Meshes/SM_Cube` pivots on a corner; `/Engine/BasicShapes/Cube` is centred.
- **Unreal's axes are left-handed, its cross product isn't:** `FVector::CrossProduct` is the usual algebra, so a frame
  described as (normal, up, in) from a right-handed tool can come out mirrored in Unreal, one axis reversed. Build
  frames from two trusted vectors (`FRotationMatrix::MakeFromXZ`) and derive the third, rather than trusting all
  three labels. Seen with a Blender hand pose's grip frame, whose "up" pointed down (2026-11-25).
- **`ACharacter::GetMovementBase()` is deprecated in 5.8** (warning C4996): use `GetMovementBaseObject()` (a `UObject`;
  cast it to `UPrimitiveComponent`) or `GetMovementBaseInterfaceData()`. `ACharacter::BaseChange()` still fires when the
  base changes (also when a stay-based-in-air character finally lets go of a moving floor).
- **A spline mesh's cross-section follows the Hermite tangent.** To make the tube leave a socket exactly along an axis
  (a sleeve into a cuff), keep that end's tangent along the axis and put any sag or swing into the other end's tangent:
  the middle moves by (T0 − T1) / 8, so a bend B costs 8·B on one tangent. `SetEndScale` tapers the tube toward that
  end (the scale is interpolated along the spline).
- **Asset-registry class paths moved in UE 5.7:** `UserDefinedStruct` is `/Script/CoreUObject.UserDefinedStruct`, not
  `/Script/Engine`. An `ARFilter` on the old path silently finds nothing. `UserDefinedEnum` is still in
  `/Script/Engine`.

Seen on 5.6 (2026-10 to 2026-11):

- **Loading a skeletal mesh and an animation Blueprint with `ConstructorHelpers` in a component's constructor hung the
  editor at start-up** (splash screen, frame 0, CPU idle, no log line; twice). Use `TSoftObjectPtr` /
  `TSoftClassPtr` loaded at BeginPlay, and make sure the cooker sees them (see Packaging).
- **`HideBoneByName` does nothing on a skeletal mesh that follows a leader pose** (`ShouldUpdateBoneVisibility` is
  false with a leader). Give that mesh its own anim instance.
- **A scene component made with `CreateDefaultSubobject` in a native actor whose Blueprint child has its own root**
  became the actor's root, and everything in the Blueprint moved with it. Create such helper components at BeginPlay
  with `NewObject` + `RegisterComponent`.
- **Header-inline code that uses a type the header only forward-declares** compiles until new files regroup the unity
  build, then fails. Keep such bodies in the `.cpp`.
- **Explicit (Euler) springs stepped once per frame diverge at low frame rates.** At a background editor's 3 fps a
  landing spring on the camera went to infinity and NaN, and the renderer asserted (`NumPlanes <= 12`, distance-field
  shadowing). Sub-step springs at a fixed step (1/120 s, the frame clamped to 0.25 s) and clamp the result.
- **Characters push light physics items through thin floors** by pressing their weight onto them when they walk over.
  Set the character movement's `StandingDownwardForceScale` to 0, and give the items `CanCharacterStepUpOn` No and CCD.
- **A skeletal mesh without a Physics Asset is invisible to traces;** give the actor a collision box, or make a
  Physics Asset.
- **Object-type traces for the floor hit trigger volumes;** trace the Visibility channel, or WorldStatic only.
- **A looping sound started with `PlaySoundAtLocation` can't be stopped;** keep the `UAudioComponent` from
  `SpawnSoundAtLocation` / `SpawnSoundAttached` and fade it out.
- **A widget component screen whose border only gets `UBorder::SetBrushColor`** keeps old frames in its render target
  (ghost lines after a layout change): the default brush draws nothing. Give the border a real fill brush.
- **Text set on a code-built widget before it is on screen is lost** (its Slate widgets don't exist yet). Call
  `TakeWidget()` first.
- **Check a marketplace mesh's collision** (`body_setup.agg_geom`): a broom came with one 39 × 38 × 145 cm box, so it
  stood upright and weighed 56 kg.
- **Placing a tilted mesh by its bounding-box corners** leaves it floating (a box's corners reach past round wheels).
  Use the real vertex positions (`StaticMesh.get_static_mesh_description(0)`).
- **A normal averaged over a double-sided mesh's faces cancels out** (front and back faces); use the known facing
  direction.

## Packaging (UE 5.8)

- **Shipping packages:** BuildCookRun copies the `.pdb` (~200 MB) unless `-nodebuginfo` (`ue.py package --config
  Shipping` passes it). The default-enabled `NNEDenoiser` plugin ships ~35 MB of ONNX/DirectML DLLs: disable it if you
  don't path-trace. Shipping writes no log file. Its window title has trailing spaces, so find the window by the
  process's `MainWindowHandle`, not `FindWindow`.
- **5.8 cooks into Zen:** no loose cooked files; `Saved/Cooked/Windows/<Project>/Content/ShaderAssetInfo-*.assetinfo.json`
  lists the materials that were cooked. Soft references in a `UDeveloperSettings` config were cooked.
- **First-launch quality:** `RunHardwareBenchmark()` + `ApplyHardwareBenchmarkResults()` from `UGameInstance::Init`
  works in Shipping; it can pick Cinematic, so clamp the groups to 3.
- **On 5.6, assets referenced only from Project Settings or by soft paths in C++ weren't cooked:** soft references in a
  `UDeveloperSettings` config, so a Shipping build had none of its game sounds (2026-11-09). Unlike the 5.8 note
  above, so check either way: list the folders under `DirectoriesToAlwaysCook` in `DefaultGame.ini`, and look for the
  assets in the build's `Manifest_UFSFiles_Win64.txt`.
- **Shipping ignores a map given on the command line** (`UE_ALLOW_MAP_OVERRIDE_IN_SHIPPING` is off), so it always
  boots the default map, and only maps that were cooked can be opened later: cook them all, `-map=A+B+C` (5.6).

## From Blender (FBX)

- **Unreal flips V on import:** `V_ue = 1 − v_blender`. A texture's top row (Unreal V = 0) is Blender v = 1.
  - Check UVs with a test texture whose PNG is stored upright. A test image written through Blender's `image.pixels`,
    which starts at the bottom row, is upside down and hides a flipped UV.
- **Images written through `image.pixels`** store the values as given. Linear colours written into an sRGB-tagged PNG
  come out too dark and too saturated in Unreal. Encode to sRGB before writing, or import that texture with `srgb` off.
- Interchange warns about FBX meshes saved without smoothing groups: turn on the exporter's smoothing groups option.
- **A pivot survives only as the FBX origin.** Unreal bakes each node's transform into the vertices, so an object
  exported standing at its pivot (Blender origin set to a hinge, object placed there) arrives with its vertices in
  world position and its pivot at (0,0,0). Move the object to the origin before exporting. Blender's own FBX round
  trip doesn't catch it, because Blender keeps the node transform; check the bounds in Unreal (5.8, 2026-11-25).
- **A rigged character from Blender imports with a ×100 scale on its root bone** and every other bone in metres (the
  FBX unit conversion; the reference pose and the animations alike). Code that writes bone transforms (a poseable mesh)
  must multiply the root's scale, never set it: setting it to 1 shrank the body a hundredfold (2026-11-26).
- **A material on a skeletal or spline mesh needs its usage flag** (`bUsedWithSkeletalMesh`, `bUsedWithSplineMeshes`;
  `MaterialEditingLibrary.set_material_usage`), or the mesh draws the default grey material in the game. Engine
  content (`/Engine/BasicShapes/BasicShapeMaterial`) can't be flagged: make your own flat-colour material (2026-11-26).
- **UCX_ collision needs `auto_generate_collision` on** (`FbxStaticMeshImportData`). Through Interchange (5.8), False
  turns collision import off altogether, UCX_ boxes included (`InterchangeFbxAssetImportDataConverter.cpp`, which
  maps it to the mesh pipeline's `bCollision`). With True, meshes that carry UCX_ get those; the rest get an 18-DOP.
- **An FBX whose triangles each have their own three corners** (nothing welded) made 5.6's Interchange log "Bad
  MeshDescription" and build empty meshes (0 triangles, zero bounds). Weld corners at the same position, keeping
  normals and UVs per corner, and check `get_num_triangles(0)` after the import.
- **Blender 5.x has no Collada importer** (`bpy.ops.wm.collada_import` is gone). A flat `.dae` is XML: read the
  geometry yourself and build the meshes in Python, or convert it elsewhere first.
