#include "UETKGameLibrary.h"

#include "Engine/Engine.h"
#include "Engine/World.h"
#include "GameFramework/Actor.h"

AActor* UUETKGameLibrary::BeginSpawn(UObject* WorldContext, UClass* Class, FTransform Transform, AActor* Owner, bool bAlwaysSpawn)
{
	UWorld* World = Cast<UWorld>(WorldContext);
	if (!World && WorldContext && GEngine)
	{
		World = GEngine->GetWorldFromContextObject(WorldContext, EGetWorldErrorMode::ReturnNull);
	}
	if (!World || !Class || !Class->IsChildOf(AActor::StaticClass()) || Class->HasAnyClassFlags(CLASS_Abstract))
	{
		return nullptr;
	}
	FActorSpawnParameters Params;
	Params.bDeferConstruction = true;
	Params.Owner = Owner;
	Params.SpawnCollisionHandlingOverride = bAlwaysSpawn
		? ESpawnActorCollisionHandlingMethod::AlwaysSpawn
		: ESpawnActorCollisionHandlingMethod::AdjustIfPossibleButDontSpawnIfColliding;
	return World->SpawnActor(Class, &Transform, Params);
}

AActor* UUETKGameLibrary::FinishSpawn(AActor* Actor, FTransform Transform)
{
	if (!Actor)
	{
		return nullptr;
	}
	Actor->FinishSpawning(Transform);
	return Actor;
}
