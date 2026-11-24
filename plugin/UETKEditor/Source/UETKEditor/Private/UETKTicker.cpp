#include "UETKTicker.h"

UUETKTicker* UUETKTicker::Get()
{
	static TWeakObjectPtr<UUETKTicker> Instance;
	if (!Instance.IsValid())
	{
		UUETKTicker* Ticker = NewObject<UUETKTicker>(GetTransientPackage(), NAME_None, RF_Transient);
		Ticker->AddToRoot();
		Ticker->Handle = FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateUObject(Ticker, &UUETKTicker::Tick));
		Instance = Ticker;
	}
	return Instance.Get();
}

bool UUETKTicker::Tick(float DeltaSeconds)
{
	OnTick.Broadcast(DeltaSeconds);
	return true;
}

void UUETKTicker::BeginDestroy()
{
	if (Handle.IsValid())
	{
		FTSTicker::GetCoreTicker().RemoveTicker(Handle);
		Handle.Reset();
	}
	Super::BeginDestroy();
}
