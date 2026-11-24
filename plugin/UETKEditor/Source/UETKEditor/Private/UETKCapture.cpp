#include "UETKCapture.h"

#include "AssetCompilingManager.h"
#include "CanvasTypes.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/Engine.h"
#include "Engine/StaticMesh.h"
#include "Engine/TextureRenderTarget2D.h"
#include "HAL/FileManager.h"
#include "ImageUtils.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Misc/Paths.h"
#include "PreviewScene.h"
#include "RenderingThread.h"
#include "ShaderCompiler.h"
#include "TextureResource.h"

// ------------------------------------------------------------------------------------------------ FUETKCapture

FUETKCapture::FUETKCapture(UWorld* InWorld, int32 InWidth, int32 InHeight)
	: World(InWorld)
	, Width(FMath::Clamp(InWidth, 16, 8192))
	, Height(FMath::Clamp(InHeight, 16, 8192))
{
	Target = NewObject<UTextureRenderTarget2D>(GetTransientPackage(), NAME_None, RF_Transient);
	Target->ClearColor = FLinearColor::Black;
	Target->InitCustomFormat(Width, Height, PF_B8G8R8A8, true);
	Target->UpdateResourceImmediate(true);

	Capture = NewObject<USceneCaptureComponent2D>(GetTransientPackage(), NAME_None, RF_Transient);
	Capture->bTickInEditor = false;
	Capture->SetComponentTickEnabled(false);
	Capture->bCaptureEveryFrame = false;
	Capture->bCaptureOnMovement = false;
	Capture->bAlwaysPersistRenderingState = true;
	Capture->CaptureSource = ESceneCaptureSource::SCS_FinalColorLDR;
	Capture->TextureTarget = Target;
	Capture->ShowFlags.SetTemporalAA(false);
	Capture->ShowFlags.SetMotionBlur(false);
	Capture->PostProcessBlendWeight = 1.f;
	FPostProcessSettings& Post = Capture->PostProcessSettings;
	Post.bOverride_MotionBlurAmount = true;
	Post.MotionBlurAmount = 0.f;
	Post.bOverride_VignetteIntensity = true;
	Post.VignetteIntensity = 0.f;
	SetManualExposure(0.f);
	Capture->RegisterComponentWithWorld(World);
}

FUETKCapture::~FUETKCapture()
{
	if (Capture)
	{
		Capture->TextureTarget = nullptr;
		if (Capture->IsRegistered())
		{
			Capture->UnregisterComponent();
		}
	}
}

void FUETKCapture::SetView(const FVector& Location, const FRotator& Rotation, float HorizontalFov)
{
	Capture->FOVAngle = FMath::Clamp(HorizontalFov, 5.f, 170.f);
	Capture->SetWorldLocationAndRotation(Location, Rotation);
}

void FUETKCapture::SetManualExposure(float Bias)
{
	FPostProcessSettings& Post = Capture->PostProcessSettings;
	Post.bOverride_AutoExposureMethod = true;
	Post.AutoExposureMethod = EAutoExposureMethod::AEM_Manual;
	Post.bOverride_AutoExposureBias = true;
	Post.AutoExposureBias = Bias;
	Post.bOverride_AutoExposureApplyPhysicalCameraExposure = true;
	Post.AutoExposureApplyPhysicalCameraExposure = false;
}

void FUETKCapture::SetShowOnly(const TArray<UPrimitiveComponent*>& Components)
{
	Capture->ShowOnlyComponents.Reset();
	for (UPrimitiveComponent* Component : Components)
	{
		Capture->ShowOnlyComponents.Add(Component);
	}
	Capture->PrimitiveRenderMode = Components.Num()
		? ESceneCapturePrimitiveRenderMode::PRM_UseShowOnlyList
		: ESceneCapturePrimitiveRenderMode::PRM_RenderScenePrimitives;
}

void FUETKCapture::Draw()
{
	Capture->CaptureScene();
}

bool FUETKCapture::Read(TArray<FColor>& OutPixels, const FString& Label)
{
	Capture->CaptureScene();
	if (!Label.IsEmpty() && GEngine)
	{
		FCanvas Canvas(Target->GameThread_GetRenderTargetResource(), nullptr, FGameTime(), GMaxRHIFeatureLevel);
		Canvas.DrawShadowedString(6.f, 4.f, *Label, GEngine->GetSmallFont(), FLinearColor(0.85f, 0.85f, 0.85f));
		Canvas.Flush_GameThread();
	}
	FlushRenderingCommands();
	OutPixels.Reset();
	if (!Target->GameThread_GetRenderTargetResource()->ReadPixels(OutPixels) || OutPixels.Num() != Width * Height)
	{
		return false;
	}
	for (FColor& Pixel : OutPixels)
	{
		Pixel.A = 255;
	}
	return true;
}

float FUETKCapture::AutoExpose(float TargetLuminance, int32 Iterations)
{
	float Bias = Capture->PostProcessSettings.AutoExposureBias;
	for (int32 i = 0; i < Iterations; ++i)
	{
		TArray<FColor> Pixels;
		if (!Read(Pixels))
		{
			break;
		}
		const float Mean = UETKCapture::MeanLuminance(Pixels);
		if (Mean < 1.0e-4f)
		{
			break;  // nothing lit to meter
		}
		const float Step = FMath::Clamp(FMath::Log2(TargetLuminance / Mean), -3.f, 3.f);
		Bias = FMath::Clamp(Bias + Step, -10.f, 16.f);
		SetManualExposure(Bias);
		if (FMath::Abs(Step) < 0.2f)
		{
			break;
		}
	}
	return Bias;
}

bool FUETKCapture::WaitForShaders(TFunctionRef<void()> RenderAll, int32 Attempts)
{
	// Meshes and textures loaded for the first time are built asynchronously too (an unbuilt mesh draws nothing).
	FAssetCompilingManager::Get().FinishAllCompilation();
	if (!GShaderCompilingManager)
	{
		RenderAll();
		return true;
	}
	for (int32 Attempt = 0; Attempt < Attempts; ++Attempt)
	{
		RenderAll();
		FlushRenderingCommands();
		const bool bAssetsBusy = FAssetCompilingManager::Get().GetNumRemainingAssets() > 0;
		if (!GShaderCompilingManager->IsCompiling() && !bAssetsBusy)
		{
			return true;
		}
		FAssetCompilingManager::Get().FinishAllCompilation();
		GShaderCompilingManager->FinishAllCompilation();
	}
	return !GShaderCompilingManager->IsCompiling();
}

void FUETKCapture::AddReferencedObjects(FReferenceCollector& Collector)
{
	Collector.AddReferencedObject(World);
	Collector.AddReferencedObject(Capture);
	Collector.AddReferencedObject(Target);
}

// ------------------------------------------------------------------------------------------------ FUETKContactSheet

FUETKContactSheet::FUETKContactSheet(int32 InCellWidth, int32 InCellHeight, int32 Count, int32 InColumns)
	: CellWidth(InCellWidth)
	, CellHeight(InCellHeight)
	, Columns(InColumns > 0 ? InColumns : FMath::Min(FMath::Max(Count, 1), 4))
{
	const int32 Rows = FMath::DivideAndRoundUp(FMath::Max(Count, 1), Columns);
	const int32 Gap = Count > 1 ? 2 : 0;
	Image.Init(Columns * CellWidth + (Columns - 1) * Gap, Rows * CellHeight + (Rows - 1) * Gap, ERawImageFormat::BGRA8, EGammaSpace::sRGB);
	for (FColor& Pixel : Image.AsBGRA8())
	{
		Pixel = FColor(48, 48, 48, 255);
	}
}

void FUETKContactSheet::SetCell(int32 Index, const TArray<FColor>& Pixels)
{
	if (Pixels.Num() != CellWidth * CellHeight)
	{
		return;
	}
	const int32 Gap = Image.SizeX > CellWidth ? 2 : 0;
	const int32 X0 = (Index % Columns) * (CellWidth + Gap);
	const int32 Y0 = (Index / Columns) * (CellHeight + Gap);
	TArrayView64<FColor> Out = Image.AsBGRA8();
	for (int32 Y = 0; Y < CellHeight && Y0 + Y < Image.SizeY; ++Y)
	{
		FMemory::Memcpy(&Out[static_cast<int64>(Y0 + Y) * Image.SizeX + X0], &Pixels[Y * CellWidth], CellWidth * sizeof(FColor));
	}
}

bool FUETKContactSheet::Save(const FString& Path, FString& OutFullPath) const
{
	OutFullPath = FPaths::ConvertRelativePathToFull(Path);
	IFileManager::Get().MakeDirectory(*FPaths::GetPath(OutFullPath), true);
	return FImageUtils::SaveImageByExtension(*OutFullPath, Image);
}

// ------------------------------------------------------------------------------------------------ helpers

float UETKCapture::FitDistance(const FBox& Bounds, const FRotator& Rotation, float HorizontalFov, float Aspect, float Zoom)
{
	const float TanH = FMath::Tan(FMath::DegreesToRadians(FMath::Clamp(HorizontalFov, 5.f, 170.f) * 0.5f));
	const float TanV = TanH / FMath::Max(Aspect, 0.1f);
	const FRotationMatrix Axes(Rotation);
	const FVector Forward = Axes.GetUnitAxis(EAxis::X);
	const FVector Right = Axes.GetUnitAxis(EAxis::Y);
	const FVector Up = Axes.GetUnitAxis(EAxis::Z);
	const FVector Center = Bounds.GetCenter();
	float Distance = 10.f;
	for (int32 Corner = 0; Corner < 8; ++Corner)
	{
		const FVector P(Corner & 1 ? Bounds.Max.X : Bounds.Min.X, Corner & 2 ? Bounds.Max.Y : Bounds.Min.Y, Corner & 4 ? Bounds.Max.Z : Bounds.Min.Z);
		const FVector Offset = P - Center;
		const float Depth = FVector::DotProduct(Offset, Forward);
		Distance = FMath::Max(Distance, FMath::Abs(FVector::DotProduct(Offset, Right)) * 1.05f / TanH - Depth);
		Distance = FMath::Max(Distance, FMath::Abs(FVector::DotProduct(Offset, Up)) * 1.05f / TanV - Depth);
	}
	return Distance / FMath::Max(Zoom, 0.05f);
}

UStaticMeshComponent* UETKCapture::AddBackdrop(FPreviewScene& Scene, const FVector& CameraLocation, const FRotator& Rotation,
	float HorizontalFov, const FBox& Bounds, UMaterialInterface* Material, const FLinearColor& Color)
{
	UStaticMesh* Plane = LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Plane.Plane"));
	if (!Plane || !Material)
	{
		return nullptr;
	}
	const FVector Forward = Rotation.Vector();
	const float Behind = FVector::Dist(CameraLocation, Bounds.GetCenter()) + Bounds.GetExtent().Size() + 20.f;
	const float HalfSize = Behind * FMath::Tan(FMath::DegreesToRadians(HorizontalFov * 0.5f)) * 1.5f;
	UStaticMeshComponent* Backdrop = NewObject<UStaticMeshComponent>(GetTransientPackage(), NAME_None, RF_Transient);
	Backdrop->SetStaticMesh(Plane);
	Backdrop->SetCastShadow(false);
	UMaterialInstanceDynamic* Instance = UMaterialInstanceDynamic::Create(Material, Backdrop);
	Instance->SetVectorParameterValue(TEXT("Color"), Color);
	Backdrop->SetMaterial(0, Instance);
	Scene.AddComponent(Backdrop, FTransform(FRotationMatrix::MakeFromZ(-Forward).Rotator(), CameraLocation + Forward * Behind,
		FVector(HalfSize * 2.f / 100.f)));
	return Backdrop;
}

float UETKCapture::MeanLuminance(const TArray<FColor>& Pixels)
{
	if (Pixels.Num() == 0)
	{
		return 0.f;
	}
	// Geometric mean, like eye adaptation: a bright sky doesn't drag the whole frame dark.
	double LogSum = 0.0;
	for (const FColor& Pixel : Pixels)
	{
		const FLinearColor Linear(Pixel);  // sRGB -> linear
		const double Y = 0.2126 * Linear.R + 0.7152 * Linear.G + 0.0722 * Linear.B;
		LogSum += FMath::Loge(Y + 1.0e-3);
	}
	return static_cast<float>(FMath::Exp(LogSum / Pixels.Num()) - 1.0e-3);
}
