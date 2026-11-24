// UnrealToolkit editor helpers for scripts. Python: unreal.UETKEditorLibrary (wrapped by ueh.render_*).
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "UETKEditorLibrary.generated.h"

class AActor;
class UMaterialInterface;
class UPrimitiveComponent;
class UStaticMesh;

/** How the Render* functions frame, expose and lay out their frames. */
USTRUCT(BlueprintType)
struct FUETKRenderSettings
{
	GENERATED_BODY()

	/** Pixels per frame. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	int32 Width = 1280;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	int32 Height = 720;

	/** Horizontal field of view in degrees. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	float FOV = 60.f;

	/** Meter each frame to mid-gray (deterministic; no eye-adaptation history). Off = fixed exposure. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	bool bAutoExposure = true;

	/** Stops added after metering (or the fixed exposure's compensation). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	float ExposureBias = 0.f;

	/** RenderActor/RenderAsset: one frame per camera yaw, orbiting the subject (empty = four 3/4 views). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	TArray<float> Yaws;

	/** RenderActor/RenderAsset: camera pitch (negative looks down). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	float Pitch = -20.f;

	/** RenderActor/RenderAsset: > 1 moves closer than the fit. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	float Zoom = 1.f;

	/** Frames per row (0 = up to 4). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	int32 Columns = 0;

	/** Write the view name on each frame. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	bool bLabels = true;

	/** RenderActor: draw only the actor (its surroundings can block some angles). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	bool bIsolate = false;

	/** RenderView: draw primitives tagged FirstPerson (5.6+ first-person arms and weapons) with this field of view
	 *  and scale, as the player's camera does; ueh.render_pie copies them from the player's camera. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	bool bUseFirstPersonFieldOfView = false;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	float FirstPersonFieldOfView = 70.f;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	bool bUseFirstPersonScale = false;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	float FirstPersonScale = 1.f;

	/** RenderView: primitives not to draw. A capture has no view owner, so "owner no see" parts (a first-person
	 *  character's own body) would draw from inside; ueh.render_pie passes the view target's. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	TArray<TObjectPtr<UPrimitiveComponent>> HiddenComponents;

	/** RenderAsset: the preview scene's floor and sky (like the asset editors' viewports). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	bool bShowFloor = true;

	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	bool bShowEnvironment = true;

	/** RenderAsset: the mesh a surface material is drawn on (null = a sphere). */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	TObjectPtr<UStaticMesh> PreviewMesh;

	/** Post-process materials applied to every frame (an outline, a colour grade). RenderAsset of a post-process
	 *  material adds it here itself and draws it over the preview scene. */
	UPROPERTY(EditAnywhere, BlueprintReadWrite, Category = "Render")
	TArray<TObjectPtr<UMaterialInterface>> PostProcessMaterials;
};

/**
 * Offscreen renders to PNG for checking work without looking at the editor window: they don't need the editor in
 * front, don't move the viewport camera and don't change the level. Every function returns JSON text:
 * {"ok": true, "path": "...", ...} or {"ok": false, "error": "..."}.
 */
UCLASS()
class UETKEDITOR_API UUETKEditorLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** One view of a world (WorldContext: any object in it, or a World; null = the editor level) from a camera. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Render")
	static FString RenderView(UObject* WorldContext, const FString& OutputPng, FVector Location, FRotator Rotation, const FUETKRenderSettings& Settings);

	/** An actor in its own world from several angles (Settings.Yaws), framed to its bounds. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Render")
	static FString RenderActor(AActor* Actor, const FString& OutputPng, const FUETKRenderSettings& Settings);

	/** An asset in a lit preview scene from several angles: static or skeletal mesh, material (a surface material
	 *  on Settings.PreviewMesh, default a sphere; a post-process material over a sphere and a cube), Blueprint actor
	 *  class, or texture (drawn flat). */
	UFUNCTION(BlueprintCallable, Category = "UETK|Render")
	static FString RenderAsset(UObject* Asset, const FString& OutputPng, const FUETKRenderSettings& Settings);

	/** A UMG widget: a live instance (e.g. one on screen in PIE), a widget class, or a Widget Blueprint (created in
	 *  WorldContext's world, default the editor level). Drawn at Width x Height with the project's DPI scale, over
	 *  Background (transparent parts show it). */
	UFUNCTION(BlueprintCallable, Category = "UETK|Render")
	static FString RenderWidget(UObject* Widget, const FString& OutputPng, int32 Width, int32 Height, UObject* WorldContext, FLinearColor Background);

	/** Replace a static mesh's simple collision: box, sphere, capsule, convex (a 26-sided k-DOP), none, or complex
	 *  (use the mesh itself as simple collision). The editor's own fitting, without its subsystem (which a headless
	 *  run doesn't have). */
	UFUNCTION(BlueprintCallable, Category = "UETK|Import")
	static FString SetSimpleCollision(UStaticMesh* Mesh, const FString& Shape);

	/** Set a static mesh's LOD group (LevelArchitecture, SmallProp, LargeProp, Deco, Vista, Foliage, HighDetail...),
	 *  rebuilding its LODs; works headless. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Import")
	static FString SetLODGroup(UStaticMesh* Mesh, FName Group);

	/** Every key name the engine knows (EKeys), sorted: for input mappings and forced key presses. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Input")
	static TArray<FString> ListKeys();
};
