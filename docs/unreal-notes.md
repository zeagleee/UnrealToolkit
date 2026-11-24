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
- **`CaptureScene()` renders only when the main view does**, so with the editor minimized an exported capture is black.
  Window screenshots skip minimized windows too. `ue.py render` captures and reads back in the same call
  (`FlushRenderingCommands()`), so it works minimized.

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

## Project setup and gameplay code

- **New C++ project without the editor UI:** `scripts/new_from_template.py`. The editor's new-project path
  (`GameProjectUtils`) also writes `r.Substrate=True`, virtual shadow maps, Lumen and distance fields; set your
  renderer settings before the first launch so shaders compile once.
- **One File Per Actor on a non-partitioned level:** `LevelEditorSubsystem.new_level(path, False)` makes the level
  without it, and Python can't reach `ULevel`. Call `ULevel::ConvertAllActorsToPackaging(true)` from a small
  BlueprintCallable in your game module (body under `WITH_EDITOR`).
- **UHT forbids a `UPROPERTY` name in a child class that a parent already has.** Adding `SprintAction` to the First
  Person template's base character broke its Horror variant, which declares its own.
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
- **Asset-registry class paths moved in UE 5.7:** `UserDefinedStruct` is `/Script/CoreUObject.UserDefinedStruct`, not
  `/Script/Engine`. An `ARFilter` on the old path silently finds nothing. `UserDefinedEnum` is still in
  `/Script/Engine`.

## Packaging (UE 5.8)

- **Shipping packages:** BuildCookRun copies the `.pdb` (~200 MB) unless `-nodebuginfo` (`ue.py package --config
  Shipping` passes it). The default-enabled `NNEDenoiser` plugin ships ~35 MB of ONNX/DirectML DLLs: disable it if you
  don't path-trace. Shipping writes no log file. Its window title has trailing spaces, so find the window by the
  process's `MainWindowHandle`, not `FindWindow`.
- **5.8 cooks into Zen:** no loose cooked files; `Saved/Cooked/Windows/<Project>/Content/ShaderAssetInfo-*.assetinfo.json`
  lists the materials that were cooked. Soft references in a `UDeveloperSettings` config were cooked.
- **First-launch quality:** `RunHardwareBenchmark()` + `ApplyHardwareBenchmarkResults()` from `UGameInstance::Init`
  works in Shipping; it can pick Cinematic, so clamp the groups to 3.

## From Blender (FBX)

- **Unreal flips V on import:** `V_ue = 1 − v_blender`. A texture's top row (Unreal V = 0) is Blender v = 1.
  - Check UVs with a test texture whose PNG is stored upright. A test image written through Blender's `image.pixels`,
    which starts at the bottom row, is upside down and hides a flipped UV.
- **Images written through `image.pixels`** store the values as given. Linear colours written into an sRGB-tagged PNG
  come out too dark and too saturated in Unreal. Encode to sRGB before writing, or import that texture with `srgb` off.
- Interchange warns about FBX meshes saved without smoothing groups: turn on the exporter's smoothing groups option.
