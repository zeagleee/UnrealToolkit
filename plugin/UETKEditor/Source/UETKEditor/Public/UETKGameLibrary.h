// UnrealToolkit: running-game helpers for scripts (PIE and ue.py net game processes). Python: unreal.UETKGameLibrary
// (wrapped by ueh.spawn).
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "UETKGameLibrary.generated.h"

class AActor;

/**
 * Deferred spawning, which Python can't reach (GameplayStatics' BeginDeferredActorSpawnFromClass isn't exposed):
 * BeginSpawn makes the actor, the caller sets its properties (expose-on-spawn variables and anything else), and
 * FinishSpawn runs its construction script and BeginPlay with those values in place.
 */
UCLASS()
class UETKEDITOR_API UUETKGameLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** Start spawning Class in WorldContext's world (any object in it, or the world). Null if it couldn't. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Game")
	static AActor* BeginSpawn(UObject* WorldContext, UClass* Class, FTransform Transform, AActor* Owner, bool bAlwaysSpawn = true);

	/** Finish a BeginSpawn: construction script, components, BeginPlay. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Game")
	static AActor* FinishSpawn(AActor* Actor, FTransform Transform);
};
