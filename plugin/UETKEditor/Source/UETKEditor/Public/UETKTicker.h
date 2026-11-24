// UnrealToolkit: an engine-loop tick for Python where Slate doesn't tick (dedicated servers).
#pragma once

#include "CoreMinimal.h"
#include "Containers/Ticker.h"
#include "UObject/Object.h"
#include "UETKTicker.generated.h"

DECLARE_DYNAMIC_MULTICAST_DELEGATE_OneParam(FUETKTickSignature, float, DeltaSeconds);

/**
 * Broadcasts OnTick once per engine loop (FTSTicker), in every kind of process: editor, game, dedicated server.
 * Python's own tick hooks are Slate's, and a dedicated server runs no Slate, so the toolkit's bridge binds here
 * there instead:  unreal.UETKTicker.get().on_tick.add_callable(fn)  (fn(delta_seconds)).
 */
UCLASS()
class UETKEDITOR_API UUETKTicker : public UObject
{
	GENERATED_BODY()

public:
	UPROPERTY(BlueprintAssignable, Category = "UETK")
	FUETKTickSignature OnTick;

	/** The process-wide ticker (created on first use, never garbage collected). */
	UFUNCTION(BlueprintCallable, Category = "UETK")
	static UUETKTicker* Get();

	virtual void BeginDestroy() override;

private:
	bool Tick(float DeltaSeconds);

	FTSTicker::FDelegateHandle Handle;
};
