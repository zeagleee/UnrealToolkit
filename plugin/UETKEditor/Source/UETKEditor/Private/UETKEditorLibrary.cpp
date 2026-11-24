#include "UETKEditorLibrary.h"

#include "AdvancedPreviewScene.h"
#include "AssetCompilingManager.h"
#include "Blueprint/UserWidget.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/SkeletalMeshComponent.h"
#include "Components/SkyLightComponent.h"
#include "Components/StaticMeshComponent.h"
#include "ContentStreaming.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/Blueprint.h"
#include "Engine/SkeletalMesh.h"
#include "Engine/StaticMesh.h"
#include "Engine/Texture2D.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Engine/UserInterfaceSettings.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"
#include "InputCoreTypes.h"
#include "PhysicsEngine/BodySetup.h"
#include "Materials/Material.h"
#include "Materials/MaterialInterface.h"
#include "RenderingThread.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Slate/WidgetRenderer.h"
#include "TextureResource.h"
#include "UETKCapture.h"
#include "UETKJson.h"
#include "UObject/UObjectGlobals.h"

// GeomFitUtils.h is private to UnrealEd; these are exported, with the same signatures in 5.6, 5.7 and 5.8.
UNREALED_API int32 GenerateKDopAsSimpleCollision(UStaticMesh* StaticMesh, const TArray<FVector>& dirs, bool bUpdate);
UNREALED_API int32 GenerateBoxAsSimpleCollision(UStaticMesh* StaticMesh, bool bUpdate);
UNREALED_API int32 GenerateSphereAsSimpleCollision(UStaticMesh* StaticMesh, bool bUpdate);
UNREALED_API int32 GenerateSphylAsSimpleCollision(UStaticMesh* StaticMesh, bool bUpdate);

namespace UETKEditorPrivate
{
	// The shared helpers, not copies: in a unity build another file's `using namespace UETKJson`
	// can be in scope here, and copies with the same names made every call ambiguous.
	using UETKJson::FJson;
	using UETKJson::Serialize;
	using UETKJson::Ok;
	using UETKJson::Fail;

	/** Settings.PostProcessMaterials as blendables on the capture. */
	void ApplyPostProcess(FUETKCapture& Capture, const FUETKRenderSettings& Settings)
	{
		for (UMaterialInterface* Material : Settings.PostProcessMaterials)
		{
			if (Material)
			{
				Capture.GetComponent()->PostProcessSettings.AddBlendable(Material, 1.f);
			}
		}
	}

	UWorld* EditorWorld()
	{
		return GEditor ? GEditor->GetEditorWorldContext().World() : nullptr;
	}

	/** Textures at full resolution and pending loads done, so a one-shot capture isn't blurry or missing pieces. */
	void SettleStreaming()
	{
		FlushAsyncLoading();
		FAssetCompilingManager::Get().FinishAllCompilation();  // meshes/textures built on first load (bounds too)
		IStreamingManager::Get().StreamAllResources(5.0f);
	}

	TArray<float> ViewYaws(const FUETKRenderSettings& Settings)
	{
		return Settings.Yaws.Num() ? Settings.Yaws : TArray<float>{225.f, 135.f, 45.f, 315.f};
	}

	FBox BoundsOf(const TArray<UPrimitiveComponent*>& Components)
	{
		FBox Box(ForceInit);
		for (UPrimitiveComponent* Component : Components)
		{
			if (Component && Component->IsRegistered())
			{
				Component->UpdateBounds();
				Box += Component->Bounds.GetBox();
			}
		}
		return Box;
	}

	/**
	 * Render a subject from each yaw into one sheet: frame the bounds, wait for shaders, meter each view.
	 * Labels name the yaw relative to YawOrigin (the subject's facing).
	 */
	FString RenderOrbit(UWorld* World, const FBox& Bounds, float YawOrigin, const TArray<UPrimitiveComponent*>& ShowOnly,
		const FString& OutputPng, const FUETKRenderSettings& Settings, FJson Result)
	{
		const TArray<float> Yaws = ViewYaws(Settings);
		const int32 Width = FMath::Clamp(Settings.Width, 64, 4096);
		const int32 Height = FMath::Clamp(Settings.Height, 64, 4096);
		const float Aspect = static_cast<float>(Width) / Height;
		FUETKCapture Capture(World, Width, Height);
		Capture.SetShowOnly(ShowOnly);
		ApplyPostProcess(Capture, Settings);
		TArray<TPair<FVector, FRotator>> Views;
		for (float Yaw : Yaws)
		{
			const FRotator Rotation(Settings.Pitch, YawOrigin + Yaw, 0.f);
			const float Distance = UETKCapture::FitDistance(Bounds, Rotation, Settings.FOV, Aspect, Settings.Zoom);
			Views.Emplace(Bounds.GetCenter() - Rotation.Vector() * Distance, Rotation);
		}
		const bool bShadersReady = FUETKCapture::WaitForShaders([&]()
		{
			for (const TPair<FVector, FRotator>& View : Views)
			{
				Capture.SetView(View.Key, View.Value, Settings.FOV);
				Capture.Draw();
			}
		});
		// One exposure for every view, metered over all of them, so frames compare (per-view metering blows out
		// views that look into the light and crushes the ones that look at the shadow side).
		float Bias = Settings.ExposureBias;
		if (Settings.bAutoExposure)
		{
			float Metered = 0.f;
			for (int32 Iteration = 0; Iteration < 4; ++Iteration)
			{
				Capture.SetManualExposure(Metered);
				TArray<FColor> All;
				for (const TPair<FVector, FRotator>& View : Views)
				{
					TArray<FColor> Pixels;
					Capture.SetView(View.Key, View.Value, Settings.FOV);
					if (Capture.Read(Pixels))
					{
						All.Append(Pixels);
					}
				}
				const float Mean = UETKCapture::MeanLuminance(All);
				if (Mean < 1.0e-4f)
				{
					break;
				}
				const float Step = FMath::Clamp(FMath::Log2(0.18f / Mean), -3.f, 3.f);
				Metered = FMath::Clamp(Metered + Step, -10.f, 16.f);
				if (FMath::Abs(Step) < 0.2f)
				{
					break;
				}
			}
			Bias += Metered;
		}
		Capture.SetManualExposure(Bias);
		const int32 Columns = Settings.Columns > 0 ? Settings.Columns : (Views.Num() == 4 ? 2 : 0);
		FUETKContactSheet Sheet(Width, Height, Views.Num(), Columns);
		TArray<TSharedPtr<FJsonValue>> Frames;
		Result->SetNumberField(TEXT("exposure_bias"), Bias);
		for (int32 i = 0; i < Views.Num(); ++i)
		{
			Capture.SetView(Views[i].Key, Views[i].Value, Settings.FOV);
			TArray<FColor> Pixels;
			const FString Label = Settings.bLabels ? FString::Printf(TEXT("yaw %.0f"), Yaws[i]) : FString();
			if (Capture.Read(Pixels, Label))
			{
				Sheet.SetCell(i, Pixels);
			}
			TSharedRef<FJsonObject> Frame = MakeShared<FJsonObject>();
			Frame->SetNumberField(TEXT("yaw"), Yaws[i]);
			Frames.Add(MakeShared<FJsonValueObject>(Frame));
		}
		FString FullPath;
		if (!Sheet.Save(OutputPng, FullPath))
		{
			return Fail(FString::Printf(TEXT("could not write %s"), *FullPath));
		}
		Result->SetStringField(TEXT("path"), FullPath);
		Result->SetArrayField(TEXT("frames"), Frames);
		Result->SetStringField(TEXT("bounds_min"), Bounds.Min.ToCompactString());
		Result->SetStringField(TEXT("bounds_max"), Bounds.Max.ToCompactString());
		if (!bShadersReady)
		{
			Result->SetStringField(TEXT("warning"), TEXT("shaders were still compiling: some materials may show as default; render again"));
		}
		return Ok(Result);
	}

	UClass* WidgetClassOf(UObject* Object)
	{
		if (UClass* Class = Cast<UClass>(Object))
		{
			return Class->IsChildOf(UUserWidget::StaticClass()) ? Class : nullptr;
		}
		if (UBlueprint* Blueprint = Cast<UBlueprint>(Object))
		{
			UClass* Generated = Blueprint->GeneratedClass;
			return Generated && Generated->IsChildOf(UUserWidget::StaticClass()) ? Generated : nullptr;
		}
		return nullptr;
	}
}

using namespace UETKEditorPrivate;

FString UUETKEditorLibrary::RenderView(UObject* WorldContext, const FString& OutputPng, FVector Location, FRotator Rotation, const FUETKRenderSettings& Settings)
{
	UWorld* World = WorldContext ? WorldContext->GetWorld() : EditorWorld();
	if (!World)
	{
		return Fail(TEXT("no world (pass an object in the world, or none for the editor level)"));
	}
	if (!FApp::CanEverRender())
	{
		return Fail(TEXT("this editor can't render (headless / -nullrhi)"));
	}
	SettleStreaming();
	FUETKCapture Capture(World, Settings.Width, Settings.Height);
	Capture.SetView(Location, Rotation, Settings.FOV);
	ApplyPostProcess(Capture, Settings);
	USceneCaptureComponent2D* Component = Capture.GetComponent();
	Component->bEnableFirstPersonFieldOfView = Settings.bUseFirstPersonFieldOfView;
	Component->FirstPersonFieldOfView = Settings.FirstPersonFieldOfView;
	Component->bEnableFirstPersonScale = Settings.bUseFirstPersonScale;
	Component->FirstPersonScale = Settings.FirstPersonScale;
	for (UPrimitiveComponent* Hidden : Settings.HiddenComponents)
	{
		Component->HiddenComponents.Add(Hidden);
	}
	const bool bShadersReady = FUETKCapture::WaitForShaders([&]() { Capture.Draw(); });
	float Bias = Settings.ExposureBias;
	if (Settings.bAutoExposure)
	{
		Bias += Capture.AutoExpose();
	}
	Capture.SetManualExposure(Bias);
	TArray<FColor> Pixels;
	if (!Capture.Read(Pixels))
	{
		return Fail(TEXT("could not read the capture back"));
	}
	FUETKContactSheet Sheet(Capture.GetWidth(), Capture.GetHeight(), 1);
	Sheet.SetCell(0, Pixels);
	FString FullPath;
	if (!Sheet.Save(OutputPng, FullPath))
	{
		return Fail(FString::Printf(TEXT("could not write %s"), *FullPath));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("path"), FullPath);
	Result->SetStringField(TEXT("world"), World->GetPathName());
	Result->SetStringField(TEXT("location"), Location.ToCompactString());
	Result->SetStringField(TEXT("rotation"), Rotation.ToCompactString());
	Result->SetNumberField(TEXT("exposure_bias"), Bias);
	const float Mean = UETKCapture::MeanLuminance(Pixels);
	Result->SetNumberField(TEXT("mean_luminance"), Mean);
	if (Mean < 0.002f)
	{
		Result->SetStringField(TEXT("warning"), TEXT("the frame is black: nothing lit in view, or the editor is minimized (captures need it rendering)"));
	}
	if (!bShadersReady)
	{
		Result->SetStringField(TEXT("warning"), TEXT("shaders were still compiling: some materials may show as default; render again"));
	}
	return Ok(Result);
}

FString UUETKEditorLibrary::RenderActor(AActor* Actor, const FString& OutputPng, const FUETKRenderSettings& Settings)
{
	if (!Actor || !Actor->GetWorld())
	{
		return Fail(TEXT("Actor is null or not in a world"));
	}
	if (!FApp::CanEverRender())
	{
		return Fail(TEXT("this editor can't render (headless / -nullrhi)"));
	}
	SettleStreaming();
	TArray<UPrimitiveComponent*> Primitives;
	Actor->GetComponents<UPrimitiveComponent>(Primitives);
	FBox Bounds = Actor->GetComponentsBoundingBox(true);
	if (!Bounds.IsValid || Bounds.GetExtent().GetMax() < 1.f)
	{
		Bounds = FBox::BuildAABB(Actor->GetActorLocation(), FVector(50.f));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("actor"), Actor->GetActorLabel());
	return RenderOrbit(Actor->GetWorld(), Bounds, Actor->GetActorRotation().Yaw,
		Settings.bIsolate ? Primitives : TArray<UPrimitiveComponent*>(), OutputPng, Settings, Result);
}

FString UUETKEditorLibrary::RenderAsset(UObject* Asset, const FString& OutputPng, const FUETKRenderSettings& Settings)
{
	if (!Asset)
	{
		return Fail(TEXT("Asset is null"));
	}
	if (!FApp::CanEverRender())
	{
		return Fail(TEXT("this editor can't render (headless / -nullrhi)"));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("asset"), Asset->GetPathName());
	Result->SetStringField(TEXT("class"), Asset->GetClass()->GetName());

	// Textures: the source image, as is.
	if (UTexture2D* Texture = Cast<UTexture2D>(Asset))
	{
		FImage Image;
		if (!Texture->Source.IsValid() || !Texture->Source.GetMipImage(Image, 0))
		{
			return Fail(TEXT("the texture has no source image"));
		}
		Image.ChangeFormat(ERawImageFormat::BGRA8, EGammaSpace::sRGB);
		FUETKContactSheet Sheet(Image.SizeX, Image.SizeY, 1);
		Sheet.Image = Image;
		FString FullPath;
		if (!Sheet.Save(OutputPng, FullPath))
		{
			return Fail(FString::Printf(TEXT("could not write %s"), *FullPath));
		}
		Result->SetStringField(TEXT("path"), FullPath);
		Result->SetStringField(TEXT("size"), FString::Printf(TEXT("%dx%d"), Image.SizeX, Image.SizeY));
		return Ok(Result);
	}
	if (WidgetClassOf(Asset))
	{
		return RenderWidget(Asset, OutputPng, Settings.Width, Settings.Height, nullptr, FLinearColor(0.02f, 0.02f, 0.02f, 1.f));
	}

	FUETKRenderSettings Effective = Settings;
	FAdvancedPreviewScene Scene{FPreviewScene::ConstructionValues()};
	Scene.SetFloorVisibility(Settings.bShowFloor, true);
	Scene.SetEnvironmentVisibility(Settings.bShowEnvironment, true);
	UWorld* World = Scene.GetWorld();
	TArray<UPrimitiveComponent*> Primitives;
	AActor* Spawned = nullptr;

	UClass* ActorClass = nullptr;
	if (UBlueprint* Blueprint = Cast<UBlueprint>(Asset))
	{
		ActorClass = Blueprint->GeneratedClass && Blueprint->GeneratedClass->IsChildOf(AActor::StaticClass()) ? Blueprint->GeneratedClass.Get() : nullptr;
	}
	else if (UClass* Class = Cast<UClass>(Asset))
	{
		ActorClass = Class->IsChildOf(AActor::StaticClass()) ? Class : nullptr;
	}

	if (UStaticMesh* Mesh = Cast<UStaticMesh>(Asset))
	{
		UStaticMeshComponent* Component = NewObject<UStaticMeshComponent>(GetTransientPackage(), NAME_None, RF_Transient);
		Component->SetStaticMesh(Mesh);
		Scene.AddComponent(Component, FTransform::Identity);
		Primitives.Add(Component);
	}
	else if (USkeletalMesh* Skeletal = Cast<USkeletalMesh>(Asset))
	{
		USkeletalMeshComponent* Component = NewObject<USkeletalMeshComponent>(GetTransientPackage(), NAME_None, RF_Transient);
		Component->SetSkeletalMeshAsset(Skeletal);
		Scene.AddComponent(Component, FTransform::Identity);
		Primitives.Add(Component);
	}
	else if (UMaterialInterface* Material = Cast<UMaterialInterface>(Asset))
	{
		const UMaterial* Base = Material->GetMaterial();
		const EMaterialDomain Domain = Base ? Base->MaterialDomain.GetValue() : MD_Surface;
		if (Domain == MD_PostProcess)
		{
			// Drawn over a sphere and a cube, both written to custom depth and stencil (value 1) for outline-style
			// materials that read them.
			Effective.PostProcessMaterials.Add(Material);
			const TCHAR* Shapes[] = {TEXT("/Engine/BasicShapes/Sphere.Sphere"), TEXT("/Engine/BasicShapes/Cube.Cube")};
			for (int32 Index = 0; Index < 2; ++Index)
			{
				UStaticMeshComponent* Component = NewObject<UStaticMeshComponent>(GetTransientPackage(), NAME_None, RF_Transient);
				Component->SetStaticMesh(LoadObject<UStaticMesh>(nullptr, Shapes[Index]));
				Component->SetRenderCustomDepth(true);
				Component->SetCustomDepthStencilValue(1);
				Scene.AddComponent(Component, FTransform(FRotator::ZeroRotator, FVector(0.f, Index == 0 ? -75.f : 75.f, 0.f)));
				Primitives.Add(Component);
			}
		}
		else if (Domain == MD_Surface)
		{
			UStaticMesh* SurfaceMesh = Settings.PreviewMesh ? Settings.PreviewMesh.Get()
				: LoadObject<UStaticMesh>(nullptr, TEXT("/Engine/BasicShapes/Sphere.Sphere"));
			UStaticMeshComponent* Component = NewObject<UStaticMeshComponent>(GetTransientPackage(), NAME_None, RF_Transient);
			Component->SetStaticMesh(SurfaceMesh);
			for (int32 Slot = 0; Slot < FMath::Max(1, Component->GetNumMaterials()); ++Slot)
			{
				Component->SetMaterial(Slot, Material);
			}
			Scene.AddComponent(Component, FTransform(FRotator::ZeroRotator, FVector::ZeroVector, FVector(Settings.PreviewMesh ? 1.f : 2.f)));
			Primitives.Add(Component);
		}
		else
		{
			return Fail(FString::Printf(TEXT("can't preview a %s material yet (surface and post-process materials work)"),
				*StaticEnum<EMaterialDomain>()->GetNameStringByValue(Domain)));
		}
	}
	else if (ActorClass)
	{
		FActorSpawnParameters Params;
		Params.SpawnCollisionHandlingOverride = ESpawnActorCollisionHandlingMethod::AlwaysSpawn;
		Params.ObjectFlags = RF_Transient;
		Spawned = World->SpawnActor<AActor>(ActorClass, FTransform::Identity, Params);
		if (!Spawned)
		{
			return Fail(FString::Printf(TEXT("could not spawn %s in the preview scene"), *ActorClass->GetName()));
		}
		Spawned->GetComponents<UPrimitiveComponent>(Primitives);
	}
	else
	{
		return Fail(FString::Printf(TEXT("can't preview a %s (static/skeletal mesh, material, Blueprint actor, widget Blueprint, texture)"),
			*Asset->GetClass()->GetName()));
	}

	TArray<TSharedPtr<FJsonValue>> Drawn;
	for (UPrimitiveComponent* Component : Primitives)
	{
		Drawn.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("%s (%s)%s"), *Component->GetName(),
			*Component->GetClass()->GetName(), Component->IsVisible() ? TEXT("") : TEXT(" hidden"))));
	}
	Result->SetArrayField(TEXT("components"), Drawn);
	SettleStreaming();
	// The preview scene's sky light captures its cubemap when the world ticks; nothing ticks this one, so without
	// this the shadow sides render black.
	World->SendAllEndOfFrameUpdates();
	USkyLightComponent::UpdateSkyCaptureContents(World);
	FBox Bounds = BoundsOf(Primitives);
	if (!Bounds.IsValid || Bounds.GetExtent().GetMax() < 0.5f)
	{
		Bounds = FBox::BuildAABB(FVector::ZeroVector, FVector(50.f));
	}
	// Stand the subject on the floor's top (z = 0 in 5.8; -0.25 in 5.7, where the floor is a box). A flat subject (a
	// plane) goes 1 cm above it rather than into the floor's surface.
	float FloorTop = 0.f;
	if (const UStaticMeshComponent* Floor = Scene.GetFloorMeshComponent())
	{
		FloorTop = Floor->Bounds.GetBox().Max.Z;
	}
	const float Raise = FloorTop - Bounds.Min.Z + (Bounds.GetExtent().Z < 0.5f ? 1.f : 0.f);
	if (Settings.bShowFloor && Raise > 0.f)
	{
		const FVector Lift(0.f, 0.f, Raise);
		if (Spawned)
		{
			Spawned->AddActorWorldOffset(Lift);
		}
		else
		{
			for (UPrimitiveComponent* Component : Primitives)
			{
				Component->AddWorldOffset(Lift);
			}
		}
		Bounds = Bounds.ShiftBy(Lift);
	}
	const FString Rendered = RenderOrbit(World, Bounds, 0.f, TArray<UPrimitiveComponent*>(), OutputPng, Effective, Result);
	if (Spawned)
	{
		Spawned->Destroy();
	}
	for (UPrimitiveComponent* Component : Primitives)
	{
		if (!Spawned && Component)
		{
			Scene.RemoveComponent(Component);
		}
	}
	return Rendered;
}

FString UUETKEditorLibrary::RenderWidget(UObject* Widget, const FString& OutputPng, int32 Width, int32 Height, UObject* WorldContext, FLinearColor Background)
{
	if (!Widget)
	{
		return Fail(TEXT("Widget is null"));
	}
	if (!FApp::CanEverRender())
	{
		return Fail(TEXT("this editor can't render (headless / -nullrhi)"));
	}
	Width = FMath::Clamp(Width, 16, 8192);
	Height = FMath::Clamp(Height, 16, 8192);
	UUserWidget* Instance = Cast<UUserWidget>(Widget);
	bool bCreated = false;
	if (!Instance)
	{
		UClass* Class = WidgetClassOf(Widget);
		if (!Class)
		{
			return Fail(FString::Printf(TEXT("%s is not a widget, widget class or Widget Blueprint"), *Widget->GetName()));
		}
		UWorld* World = WorldContext ? WorldContext->GetWorld() : EditorWorld();
		if (!World)
		{
			return Fail(TEXT("no world to create the widget in"));
		}
		Instance = CreateWidget<UUserWidget>(World, Class);
		bCreated = Instance != nullptr;
		if (!Instance)
		{
			return Fail(TEXT("CreateWidget failed"));
		}
	}
	const FVector2D Size(Width, Height);
	const float Scale = GetDefault<UUserInterfaceSettings>()->GetDPIScaleBasedOnSize(FIntPoint(Width, Height));
	TSharedRef<SWidget> Slate = Instance->TakeWidget();
	FWidgetRenderer Renderer(true, true);
	UTextureRenderTarget2D* Target = FWidgetRenderer::CreateTargetFor(Size, TF_Bilinear, true);
	Target->ClearColor = FLinearColor::Transparent;
	// A few passes: the first lays out and requests fonts and images; later ones draw them.
	for (int32 Pass = 0; Pass < 3; ++Pass)
	{
		Renderer.DrawWidget(Target, Slate, Scale, Size, 1.f / 30.f);
		FlushRenderingCommands();
		if (Pass == 0)
		{
			SettleStreaming();
		}
	}
	TArray<FColor> Pixels;
	if (!Target->GameThread_GetRenderTargetResource()->ReadPixels(Pixels) || Pixels.Num() != Width * Height)
	{
		return Fail(TEXT("could not read the widget back"));
	}
	const FColor Back = Background.ToFColor(true);
	int32 Covered = 0;
	for (FColor& Pixel : Pixels)
	{
		const float A = Pixel.A / 255.f;
		Covered += Pixel.A > 8 ? 1 : 0;
		Pixel = FColor(
			static_cast<uint8>(FMath::RoundToInt(Pixel.R * A + Back.R * (1.f - A))),
			static_cast<uint8>(FMath::RoundToInt(Pixel.G * A + Back.G * (1.f - A))),
			static_cast<uint8>(FMath::RoundToInt(Pixel.B * A + Back.B * (1.f - A))), 255);
	}
	if (bCreated)
	{
		Instance->RemoveFromParent();
	}
	FUETKContactSheet Sheet(Width, Height, 1);
	Sheet.SetCell(0, Pixels);
	FString FullPath;
	if (!Sheet.Save(OutputPng, FullPath))
	{
		return Fail(FString::Printf(TEXT("could not write %s"), *FullPath));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("path"), FullPath);
	Result->SetStringField(TEXT("widget"), Instance->GetClass()->GetPathName());
	Result->SetNumberField(TEXT("dpi_scale"), Scale);
	Result->SetNumberField(TEXT("coverage"), static_cast<double>(Covered) / Pixels.Num());
	if (Covered == 0)
	{
		Result->SetStringField(TEXT("warning"), TEXT("nothing drew: the widget is empty, collapsed, or waits for game state"));
	}
	return Ok(Result);
}

TArray<FString> UUETKEditorLibrary::ListKeys()
{
	TArray<FKey> Keys;
	EKeys::GetAllKeys(Keys);
	TArray<FString> Names;
	for (const FKey& Key : Keys)
	{
		Names.Add(Key.ToString());
	}
	Names.Sort();
	return Names;
}

FString UUETKEditorLibrary::SetSimpleCollision(UStaticMesh* Mesh, const FString& Shape)
{
	if (!Mesh)
	{
		return Fail(TEXT("Mesh is null"));
	}
	const FString Kind = UETKJson::Norm(Shape);
	Mesh->Modify();
	Mesh->CreateBodySetup();
	UBodySetup* Body = Mesh->GetBodySetup();
	Body->Modify();
	if (Kind == TEXT("complex"))
	{
		Body->CollisionTraceFlag = CTF_UseComplexAsSimple;
	}
	else
	{
		Body->RemoveSimpleCollision();
		Body->CollisionTraceFlag = CTF_UseDefault;
		if (Kind == TEXT("box"))
		{
			GenerateBoxAsSimpleCollision(Mesh, true);
		}
		else if (Kind == TEXT("sphere"))
		{
			GenerateSphereAsSimpleCollision(Mesh, true);
		}
		else if (Kind == TEXT("capsule") || Kind == TEXT("sphyl"))
		{
			GenerateSphylAsSimpleCollision(Mesh, true);
		}
		else if (Kind == TEXT("convex") || Kind == TEXT("kdop26"))
		{
			TArray<FVector> Dirs;
			for (int32 X = -1; X <= 1; ++X)
			{
				for (int32 Y = -1; Y <= 1; ++Y)
				{
					for (int32 Z = -1; Z <= 1; ++Z)
					{
						if (X || Y || Z)
						{
							Dirs.Add(FVector(X, Y, Z).GetSafeNormal());
						}
					}
				}
			}
			GenerateKDopAsSimpleCollision(Mesh, Dirs, true);
		}
		else if (Kind != TEXT("none"))
		{
			return Fail(FString::Printf(TEXT("unknown collision shape '%s' (box, sphere, capsule, convex, complex, none)"), *Shape));
		}
	}
	Body->InvalidatePhysicsData();
	Body->CreatePhysicsMeshes();
	Mesh->MarkPackageDirty();
	FJson Result = MakeShared<FJsonObject>();
	Result->SetNumberField(TEXT("boxes"), Body->AggGeom.BoxElems.Num());
	Result->SetNumberField(TEXT("spheres"), Body->AggGeom.SphereElems.Num());
	Result->SetNumberField(TEXT("capsules"), Body->AggGeom.SphylElems.Num());
	Result->SetNumberField(TEXT("convex"), Body->AggGeom.ConvexElems.Num());
	Result->SetBoolField(TEXT("complex_as_simple"), Body->CollisionTraceFlag == CTF_UseComplexAsSimple);
	return Ok(Result);
}

FString UUETKEditorLibrary::SetLODGroup(UStaticMesh* Mesh, FName Group)
{
	if (!Mesh)
	{
		return Fail(TEXT("Mesh is null"));
	}
	TArray<FName> Groups;
	UStaticMesh::GetLODGroups(Groups);
	if (!Groups.Contains(Group))
	{
		TArray<FString> Names;
		for (const FName& Name : Groups)
		{
			Names.Add(Name.ToString());
		}
		return Fail(FString::Printf(TEXT("no LOD group '%s' (%s)"), *Group.ToString(), *FString::Join(Names, TEXT(", "))));
	}
	Mesh->SetLODGroup(Group, true);
	Mesh->MarkPackageDirty();
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("lod_group"), Group.ToString());
	Result->SetNumberField(TEXT("lods"), Mesh->GetNumSourceModels());
	return Ok(Result);
}
