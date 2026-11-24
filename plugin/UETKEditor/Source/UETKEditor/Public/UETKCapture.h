// UnrealToolkit: offscreen capture building blocks shared by the toolkit's editor plugins (UETKVfx uses them too).
#pragma once

#include "CoreMinimal.h"
#include "ImageCore.h"
#include "UObject/GCObject.h"

class UMaterialInterface;
class UPrimitiveComponent;
class USceneCaptureComponent2D;
class UStaticMeshComponent;
class UTextureRenderTarget2D;
class UWorld;
class FPreviewScene;

/**
 * A scene capture into its own render target in any world (the editor level, a PIE world, a preview scene).
 * Final colour (post-processed, tonemapped), no temporal AA, motion blur or vignette.
 */
class UETKEDITOR_API FUETKCapture : public FGCObject
{
public:
	FUETKCapture(UWorld* InWorld, int32 InWidth, int32 InHeight);
	virtual ~FUETKCapture() override;

	void SetView(const FVector& Location, const FRotator& Rotation, float HorizontalFov);

	/** Fixed exposure: compensation in stops on top of EV100 0 (scene luminance 1 is roughly mid-bright). */
	void SetManualExposure(float Bias);

	/** Render only these components (empty = the whole scene). */
	void SetShowOnly(const TArray<UPrimitiveComponent*>& Components);

	/** Render without reading back (shader warm-up, metering). */
	void Draw();

	/** Render, write Label in the top-left corner (if any), and read the pixels back (alpha forced to 255). */
	bool Read(TArray<FColor>& OutPixels, const FString& Label = FString());

	/** Meter the view: render, measure the mean luminance, move the manual exposure toward mid-gray, repeat.
	 *  Deterministic (no eye-adaptation history needed). Returns the bias it settled on. */
	float AutoExpose(float Target = 0.18f, int32 Iterations = 4);

	USceneCaptureComponent2D* GetComponent() const { return Capture; }
	int32 GetWidth() const { return Width; }
	int32 GetHeight() const { return Height; }

	/** Run RenderAll, then wait for the shaders it asked for (the editor compiles on demand, the first time a
	 *  material is drawn with a vertex factory); repeat until nothing is compiling. False if still compiling. */
	static bool WaitForShaders(TFunctionRef<void()> RenderAll, int32 Attempts = 4);

	virtual void AddReferencedObjects(FReferenceCollector& Collector) override;
	virtual FString GetReferencerName() const override { return TEXT("FUETKCapture"); }

private:
	TObjectPtr<UWorld> World;
	TObjectPtr<USceneCaptureComponent2D> Capture;
	TObjectPtr<UTextureRenderTarget2D> Target;
	int32 Width;
	int32 Height;
};

/** Frames laid out in a grid with a 2-pixel gutter, saved as one PNG. */
class UETKEDITOR_API FUETKContactSheet
{
public:
	FUETKContactSheet(int32 InCellWidth, int32 InCellHeight, int32 Count, int32 InColumns = 0);
	void SetCell(int32 Index, const TArray<FColor>& Pixels);
	/** Write the PNG (folders are created); OutFullPath gets the absolute path. */
	bool Save(const FString& Path, FString& OutFullPath) const;

	FImage Image;

private:
	int32 CellWidth;
	int32 CellHeight;
	int32 Columns;
};

namespace UETKCapture
{
	/** Camera distance from the box's centre at which every corner fits a view with this rotation, horizontal FOV
	 *  and aspect (width / height), with a 5% margin; divided by Zoom. */
	UETKEDITOR_API float FitDistance(const FBox& Bounds, const FRotator& Rotation, float HorizontalFov, float Aspect, float Zoom = 1.f);

	/** A camera-facing plane behind Bounds, coloured through Material's "Color" vector parameter. Added to Scene. */
	UETKEDITOR_API UStaticMeshComponent* AddBackdrop(FPreviewScene& Scene, const FVector& CameraLocation, const FRotator& Rotation,
		float HorizontalFov, const FBox& Bounds, UMaterialInterface* Material, const FLinearColor& Color);

	/** Mean luminance (0-1, linear) of sRGB pixels. */
	UETKEDITOR_API float MeanLuminance(const TArray<FColor>& Pixels);
}
