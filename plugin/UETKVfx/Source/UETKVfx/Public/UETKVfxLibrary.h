// UnrealToolkit VFX: Niagara authoring for scripts. Python: unreal.UETKVfxLibrary (wrapped by editor/vfx.py).
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "UETKVfxLibrary.generated.h"

class UMaterialInterface;
class UNiagaraDataInterface;
class UNiagaraEmitter;
class UNiagaraRendererProperties;
class UNiagaraScript;
class UNiagaraSystem;

/** How RenderPreview frames and lights a system. */
USTRUCT(BlueprintType)
struct FUETKVfxPreviewSettings
{
	GENERATED_BODY()

	/** System ages (seconds) to show, one frame each. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	TArray<float> Times = {0.1f, 0.5f, 1.0f, 2.0f};

	/** Width and height of each frame in pixels. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	int32 FrameSize = 384;

	/** Frames per row (0 = up to 4 per row). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	int32 Columns = 0;

	/** Frame the union of the system's bounds over all times (else look at Target from CameraDistance). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	bool bAutoFrame = true;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	FVector Target = FVector::ZeroVector;

	/** 0 = fit the bounds. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	float CameraDistance = 0.f;

	/** Negative looks down. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	float CameraPitch = -15.f;

	/** 0 = the camera sits on -X looking along +X (so +Y is to the right, +Z up). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	float CameraYaw = 0.f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	float FOV = 50.f;

	/** Closer than the auto-fit when > 1 (the fit covers every time, so far-flung debris can make it loose). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	float Zoom = 1.f;

	/** Unlit material with a "Color" vector parameter, drawn as a plane behind the effect (null = black). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	TObjectPtr<UMaterialInterface> BackdropMaterial = nullptr;

	/** Backdrop colour (linear). Alpha < 0 = automatic: mid-gray when an emitter draws with a non-additive
	 *  material (smoke and other translucent or opaque sprites vanish on black), otherwise black. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	FLinearColor BackgroundColor = FLinearColor(0.f, 0.f, 0.f, -1.f);

	/** Show the preview scene's sky (the Niagara editor's backdrop) instead of black. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	bool bShowEnvironment = false;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	bool bShowFloor = false;

	/** Manual exposure compensation in stops (0 = scene luminance 1 maps to white-ish). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	float ExposureBias = 0.f;

	/** Simulation step used to seek to each time. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	float SeekDelta = 1.f / 60.f;

	/** Write "t=0.50s  n=123" on each frame. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Preview")
	bool bLabels = true;
};

/**
 * Builds and inspects Niagara systems without the editor UI, through the same stack view models the Niagara
 * editor uses (so rapid-iteration parameters, static switches, dynamic inputs and edit conditions behave as if
 * an artist clicked them).
 *
 * Every function that can fail returns JSON text: {"ok": true, ...} or {"ok": false, "error": "..."}.
 *
 * Addressing (names match ignoring case, spaces and underscores):
 *   Emitter    emitter name; "" for the system's own stages.
 *   Stage      system_spawn, system_update, emitter_spawn, emitter_update, particle_spawn, particle_update.
 *   Module     display name or function name ("Initialize Particle"); "Name#2" is the second of that name.
 *   InputPath  input name; inputs of a dynamic input are "Parent/Child" ("Lifetime/Minimum").
 *
 * Edits go through a cached data-processing view model per system; open Niagara editors for the system are
 * closed first. Call ReleaseEditing when done (vfx.py does), then Compile.
 */
UCLASS()
class UETKVFX_API UUETKVfxLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	// ------------------------------------------------------------------ emitters

	/** Add a copy of an emitter asset (e.g. a template in /Niagara/DefaultAssets/Templates/Emitters) and name it.
	 *  The copy is detached from its parent unless bKeepParent, so every module stays editable. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString AddEmitter(UNiagaraSystem* System, UNiagaraEmitter* Source, const FString& Name, bool bKeepParent = false);

	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString RemoveEmitter(UNiagaraSystem* System, const FString& Emitter);

	/** Empty a system in place so it can be rebuilt without deleting the asset (deleting and recreating a loaded
	 *  asset makes the editor ask "Overwrite Existing Object"): removes every emitter and user parameter, strips the
	 *  system stages down to System State with default inputs, and zeroes the warmup. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString ClearSystem(UNiagaraSystem* System);

	/** Emitters with their sim target and renderers (class, materials and blend modes); no view model needed. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString ListEmitters(UNiagaraSystem* System);

	/** Set an emitter property (FVersionedNiagaraEmitterData) from text, e.g. ("SimTarget", "GPUComputeSim"),
	 *  ("bLocalSpace", "true"), ("FixedBounds", "(Min=(X=-100,Y=-100,Z=0),Max=(X=100,Y=100,Z=300))"). */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetEmitterProperty(UNiagaraSystem* System, const FString& Emitter, const FString& Property, const FString& Value);

	// ------------------------------------------------------------------ modules

	/** Add a module script to a stage at Index (-1 = end). Returns {"module": "<name to address it by>"}. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString AddModule(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, UNiagaraScript* Module, int32 Index = -1);

	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString RemoveModule(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module);

	/** Modules of a stage in order: {"modules": ["Emitter State", "Spawn Rate", ...]}. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString ListModules(UNiagaraSystem* System, const FString& Emitter, const FString& Stage);

	/** One module input as JSON: {"path", "type", "mode", "value", "choices", "inputs": [...]}. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString GetInput(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath);

	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetModuleEnabled(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, bool bEnabled);

	// ------------------------------------------------------------------ module inputs

	/** Set a local value: numbers "1.5", vectors "1, 2, 3" (one number fills every component), colors "1, 0.5, 0, 1",
	 *  bools "true", enums by name ("Random"), or T3D struct text "(X=1,Y=2,Z=3)". Turns on an inline edit condition. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetInput(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, const FString& Value);

	/** Replace an input's value with a dynamic input script (e.g. /Niagara/DynamicInputs/UniformRange/UniformRangedFloat);
	 *  its own inputs are then "InputPath/<child>". */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetInputDynamic(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, UNiagaraScript* DynamicInput);

	/** Link an input to a parameter: "Particles.NormalizedAge", "User.Color", "Emitter.Age". */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetInputLinked(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, const FString& Parameter);

	/** Set an HLSL expression, e.g. "Particles.NormalizedAge * 2". */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetInputExpression(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, const FString& Expression);

	/** Set an object-asset input (a mesh, a texture...). */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetInputObject(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, UObject* Asset);

	/** Give a data-interface input a fresh data interface of a class (null = the input's own type). */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetInputDataInterface(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, UClass* DataInterfaceClass);

	/** The data interface held by an input (to set its properties from Python), or null. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static UNiagaraDataInterface* GetInputDataInterface(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath);

	/** Fill a curve data-interface input. KeysJson: [[time, v...], ...] with one v per curve channel
	 *  (float 1, vector2 2, vector 3, vector4/color 4). Interp: linear, cubic or constant. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetInputCurve(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, const FString& KeysJson, const FString& Interp = TEXT("linear"));

	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString ResetInput(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath);

	// ------------------------------------------------------------------ renderers

	/** Add a renderer (NiagaraSpriteRendererProperties, NiagaraRibbonRendererProperties, NiagaraMeshRendererProperties,
	 *  NiagaraLightRendererProperties...). Returns {"index": n}. Set its properties on GetRenderers()[n]. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString AddRenderer(UNiagaraSystem* System, const FString& Emitter, UClass* RendererClass);

	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static TArray<UNiagaraRendererProperties*> GetRenderers(UNiagaraSystem* System, const FString& Emitter);

	/** Remove one renderer, or all of them with Index -1. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString RemoveRenderer(UNiagaraSystem* System, const FString& Emitter, int32 Index = -1);

	// ------------------------------------------------------------------ user parameters & properties

	/** Add (or update) a user parameter. Type: float, int, bool, vec2, vec3, vec4, color, position. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetUserParameter(UNiagaraSystem* System, const FString& Name, const FString& Type, const FString& Value);

	/** Create (or replace) a Texture2D asset from an image file without the import pipeline, which in a headless
	 *  editor routes through the Content Browser and crashes. Not saved; save it with EditorAssetLibrary. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString CreateTextureFromFile(const FString& AssetPath, const FString& ImageFile, bool bSRGB = false);

	/** Set any edit-visible property from T3D text, with change notification; "A.B" walks into structs and
	 *  sub-objects (e.g. a renderer's "SubImageSize" = "(X=4,Y=4)"). */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString SetObjectProperty(UObject* Object, const FString& PropertyPath, const FString& Value);

	// ------------------------------------------------------------------ inspection, compile, preview

	/** The whole system as JSON: user parameters, emitters (non-default properties), every module with its
	 *  inputs (mode, value, nested dynamic inputs), renderers (non-default properties) and stack issues. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString Describe(UNiagaraSystem* System, bool bIncludeInputs = true, bool bIncludeHiddenInputs = false);

	/** Compile and wait (GPU shaders included). Returns {"errors": [...], "warnings": [...], "scripts": [...]}. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString Compile(UNiagaraSystem* System, bool bForce = true);

	/** Simulate the system offscreen at each time and write one PNG contact sheet. Nothing touches the level. */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static FString RenderPreview(UNiagaraSystem* System, const FString& OutputPng, const FUETKVfxPreviewSettings& Settings);

	/** Drop the cached editing view model for a system (or for all systems when null). */
	UFUNCTION(BlueprintCallable, Category = "UETK|VFX")
	static void ReleaseEditing(UNiagaraSystem* System = nullptr);
};
