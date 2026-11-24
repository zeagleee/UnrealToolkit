#include "UETKValidateLibrary.h"

#include "AssetRegistry/AssetRegistryModule.h"
#include "AssetRegistry/IAssetRegistry.h"
#include "AssetToolsModule.h"
#include "IAssetTools.h"
#include "Editor.h"
#include "Editor/EditorEngine.h"
#include "Engine/Blueprint.h"
#include "EditorValidatorSubsystem.h"
#include "FileHelpers.h"
#include "HAL/FileManager.h"
#include "Engine/World.h"
#include "Logging/TokenizedMessage.h"
#include "Misc/OutputDevice.h"
#include "Misc/OutputDeviceRedirector.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "ObjectTools.h"
#include "UETKJson.h"
#include "UObject/ObjectRedirector.h"
#include "UObject/Package.h"

namespace
{
	using UETKJson::FJson;

	FString SeverityName(EMessageSeverity::Type Severity)
	{
		// CriticalError (0, deprecated) and Error < PerformanceWarning < Warning < Info
		if (Severity <= EMessageSeverity::Error)
		{
			return TEXT("error");
		}
		return Severity <= EMessageSeverity::Warning ? TEXT("warning") : TEXT("info");
	}

	FString ResultName(EDataValidationResult Result)
	{
		switch (Result)
		{
		case EDataValidationResult::Valid: return TEXT("valid");
		case EDataValidationResult::Invalid: return TEXT("invalid");
		default: return TEXT("notvalidated");
		}
	}

	TSharedRef<FJsonValueObject> Message(const FString& Severity, const FString& Text)
	{
		FJson Object = MakeShared<FJsonObject>();
		Object->SetStringField(TEXT("severity"), Severity);
		Object->SetStringField(TEXT("message"), Text);
		return MakeShared<FJsonValueObject>(Object);
	}

	/** Collects the lines one log category writes while it's attached to GLog. */
	class FCategoryCapture : public FOutputDevice
	{
	public:
		explicit FCategoryCapture(FName InCategory) : Category(InCategory) {}

		virtual void Serialize(const TCHAR* Text, ELogVerbosity::Type Verbosity, const FName& InCategory) override
		{
			const ELogVerbosity::Type Level = (ELogVerbosity::Type)(Verbosity & ELogVerbosity::VerbosityMask);
			if (InCategory == Category && Level <= ELogVerbosity::Warning)
			{
				Lines.Emplace(Level <= ELogVerbosity::Error ? TEXT("error") : TEXT("warning"), FString(Text));
			}
		}

		FName Category;
		TArray<TPair<FString, FString>> Lines;
	};
}

FString UUETKValidateLibrary::ValidateAssets(const TArray<FString>& Paths)
{
	if (!GEditor)
	{
		return UETKJson::Fail(TEXT("no editor"));
	}
	UEditorValidatorSubsystem* Validator = GEditor->GetEditorSubsystem<UEditorValidatorSubsystem>();
	if (!Validator)
	{
		return UETKJson::Fail(TEXT("EditorValidatorSubsystem isn't available (is the Data Validation plugin enabled?)"));
	}

	IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
	Registry.WaitForCompletion();

	TArray<FAssetData> Assets;
	TArray<TSharedPtr<FJsonValue>> Missing;
	for (const FString& Path : Paths)
	{
		TArray<FAssetData> Found;
		if (Path.Contains(TEXT(".")))
		{
			const FAssetData Data = Registry.GetAssetByObjectPath(FSoftObjectPath(Path));
			if (Data.IsValid())
			{
				Found.Add(Data);
			}
		}
		else
		{
			Registry.GetAssetsByPackageName(FName(*Path), Found);
		}
		if (Found.IsEmpty())
		{
			Missing.Add(MakeShared<FJsonValueString>(Path));
		}
		Assets.Append(Found);
	}

	FValidateAssetsSettings Settings;
	Settings.bSilent = true;  // no progress window
	Settings.bShowIfNoFailures = false;
	Settings.ShowMessageLogSeverity.Reset();  // don't open the message log
	Settings.bCollectPerAssetDetails = true;
	// As the Content Browser's Validate Assets does, or in a headless run as -run=DataValidation does (some
	// validators, the material one among them, only run for the latter).
	Settings.ValidationUsecase = IsRunningCommandlet() ? EDataValidationUsecase::Commandlet : EDataValidationUsecase::Manual;
	FValidateAssetsResults Results;
	Validator->ValidateAssetsWithSettings(Assets, Settings, Results);

	TArray<TSharedPtr<FJsonValue>> AssetList;
	for (const TPair<FString, FValidateAssetsDetails>& Pair : Results.AssetsDetails)
	{
		const FValidateAssetsDetails& Details = Pair.Value;
		FJson Asset = MakeShared<FJsonObject>();
		Asset->SetStringField(TEXT("asset"), Pair.Key);
		Asset->SetStringField(TEXT("result"), ResultName(Details.Result));
		TArray<TSharedPtr<FJsonValue>> Messages;
		for (const TSharedRef<FTokenizedMessage>& Each : Details.ValidationMessages)
		{
			Messages.Add(Message(SeverityName(Each->GetSeverity()), Each->ToText().ToString()));
		}
		if (Details.ValidationMessages.IsEmpty())  // older validators fill only these
		{
			for (const FText& Text : Details.ValidationErrors)
			{
				Messages.Add(Message(TEXT("error"), Text.ToString()));
			}
			for (const FText& Text : Details.ValidationWarnings)
			{
				Messages.Add(Message(TEXT("warning"), Text.ToString()));
			}
		}
		Asset->SetArrayField(TEXT("messages"), Messages);
		AssetList.Add(MakeShared<FJsonValueObject>(Asset));
	}

	FJson Out = MakeShared<FJsonObject>();
	Out->SetArrayField(TEXT("assets"), AssetList);
	Out->SetArrayField(TEXT("missing"), Missing);
	Out->SetNumberField(TEXT("checked"), Results.NumChecked);
	Out->SetNumberField(TEXT("valid"), Results.NumValid);
	Out->SetNumberField(TEXT("invalid"), Results.NumInvalid);
	Out->SetNumberField(TEXT("warnings"), Results.NumWarnings);
	Out->SetNumberField(TEXT("skipped"), Results.NumSkipped);
	Out->SetNumberField(TEXT("unable"), Results.NumUnableToValidate);
	return UETKJson::Ok(Out);
}

FString UUETKValidateLibrary::MapCheck(UWorld* World)
{
	if (!GEditor)
	{
		return UETKJson::Fail(TEXT("no editor"));
	}
	if (!World)
	{
		World = GEditor->GetEditorWorldContext().World();
	}
	if (!World)
	{
		return UETKJson::Fail(TEXT("no level open"));
	}

	// Map check writes to the "MapCheck" message log, which mirrors each message to the output log under that
	// category, in a windowed editor and a headless one alike.
	FCategoryCapture Capture(TEXT("MapCheck"));
	GLog->Flush();
	GLog->AddOutputDevice(&Capture);
	GEditor->Exec(World, TEXT("MAP CHECK DONTDISPLAYDIALOG"), *GLog);  // Map_Check itself is private
	GLog->Flush();
	GLog->RemoveOutputDevice(&Capture);

	TArray<TSharedPtr<FJsonValue>> Messages;
	for (const TPair<FString, FString>& Line : Capture.Lines)
	{
		Messages.Add(Message(Line.Key, Line.Value));
	}
	FJson Out = MakeShared<FJsonObject>();
	Out->SetStringField(TEXT("map"), World->GetOutermost()->GetName());
	Out->SetArrayField(TEXT("messages"), Messages);
	return UETKJson::Ok(Out);
}

FString UUETKValidateLibrary::FixRedirectors(const TArray<FString>& Packages)
{
	// IAssetTools::FixupReferencers ends in a modal "Redirector Update Report" (5.6 to 5.8) that blocks a script and
	// needs Slate, so these are its steps without it: load the referencers (their hard references resolve to the
	// targets as they load), rewrite their soft references, save them, and delete the redirectors whose referencers
	// all saved.
	IAssetRegistry& Registry = FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
	Registry.WaitForCompletion();
	IAssetTools& AssetTools = FModuleManager::LoadModuleChecked<FAssetToolsModule>(TEXT("AssetTools")).Get();

	struct FItem
	{
		FName Package;
		UObjectRedirector* Redirector = nullptr;
		TArray<FName> Referencers;
		FString Problem;
	};
	TArray<FItem> Items;
	TSet<FName> RedirectorPackages;
	TMap<FSoftObjectPath, FSoftObjectPath> RedirectMap;
	for (const FString& Package : Packages)
	{
		TArray<FAssetData> Found;
		Registry.GetAssetsByPackageName(FName(*Package), Found);
		for (const FAssetData& Data : Found)
		{
			if (!Data.IsRedirector())
			{
				continue;
			}
			FItem& Item = Items.AddDefaulted_GetRef();
			Item.Package = Data.PackageName;
			Item.Redirector = Cast<UObjectRedirector>(Data.GetAsset());
			if (!Item.Redirector || !Item.Redirector->DestinationObject)
			{
				Item.Redirector = nullptr;
				Item.Problem = TEXT("its target is gone");
				continue;
			}
			RedirectorPackages.Add(Data.PackageName);
			Registry.GetReferencers(Data.PackageName, Item.Referencers);
			const FSoftObjectPath OldPath(Item.Redirector);
			const FSoftObjectPath NewPath(Item.Redirector->DestinationObject);
			RedirectMap.Add(OldPath, NewPath);
			if (Cast<UBlueprint>(Item.Redirector->DestinationObject))
			{
				// The generated class and its default object, as the engine's fix-up does
				RedirectMap.Add(FSoftObjectPath(OldPath.ToString() + TEXT("_C")), FSoftObjectPath(NewPath.ToString() + TEXT("_C")));
				RedirectMap.Add(
					FSoftObjectPath(FString::Printf(TEXT("%s.Default__%s_C"), *OldPath.GetLongPackageName(), *OldPath.GetAssetName())),
					FSoftObjectPath(FString::Printf(TEXT("%s.Default__%s_C"), *NewPath.GetLongPackageName(), *NewPath.GetAssetName())));
			}
		}
	}

	// Referencers to resave (redirectors that point at each other are deleted, not saved)
	TSet<FName> ReferencerNames;
	for (const FItem& Item : Items)
	{
		for (const FName& Name : Item.Referencers)
		{
			if (!RedirectorPackages.Contains(Name))
			{
				ReferencerNames.Add(Name);
			}
		}
	}
	TMap<FName, FString> Failed;
	TArray<UPackage*> ToSave;
	for (const FName& Name : ReferencerNames)
	{
		FString Filename;
		if (FPackageName::DoesPackageExist(Name.ToString(), &Filename) && IFileManager::Get().IsReadOnly(*Filename))
		{
			Failed.Add(Name, TEXT("read-only (check it out first)"));
			continue;
		}
		if (UPackage* Loaded = LoadPackage(nullptr, *Name.ToString(), LOAD_None))
		{
			ToSave.Add(Loaded);
		}
		else
		{
			Failed.Add(Name, TEXT("couldn't be loaded"));
		}
	}
	if (ToSave.Num() > 0)
	{
		AssetTools.RenameReferencingSoftObjectPaths(ToSave, RedirectMap);
		TArray<UPackage*> FailedToSave;
		FEditorFileUtils::PromptForCheckoutAndSave(ToSave, false, false, &FailedToSave);
		for (const UPackage* Package : FailedToSave)
		{
			Failed.Add(Package->GetFName(), TEXT("couldn't be saved"));
		}
		TArray<FString> Saved;
		for (const UPackage* Package : ToSave)
		{
			Saved.Add(Package->GetName());
		}
		Registry.ScanModifiedAssetFiles(Saved);
	}

	TArray<UObject*> ToDelete;
	for (FItem& Item : Items)
	{
		if (!Item.Redirector)
		{
			continue;
		}
		TArray<FString> Blocked;
		for (const FName& Name : Item.Referencers)
		{
			if (const FString* Why = Failed.Find(Name))
			{
				Blocked.Add(Name.ToString() + TEXT(" ") + *Why);
			}
		}
		if (Blocked.Num() > 0)
		{
			Item.Problem = TEXT("referencer ") + FString::Join(Blocked, TEXT(", "));
			continue;
		}
		Item.Redirector->RemoveFromRoot();
		ToDelete.Add(Item.Redirector);
	}
	if (ToDelete.Num() > 0)
	{
		ObjectTools::DeleteObjectsUnchecked(ToDelete);
	}

	TArray<TSharedPtr<FJsonValue>> Fixed;
	FJson Remaining = MakeShared<FJsonObject>();
	for (const FItem& Item : Items)
	{
		TArray<FAssetData> Found;
		Registry.GetAssetsByPackageName(Item.Package, Found);
		if (!Found.ContainsByPredicate([](const FAssetData& Data) { return Data.IsRedirector(); }))
		{
			Fixed.Add(MakeShared<FJsonValueString>(Item.Package.ToString()));
		}
		else
		{
			Remaining->SetStringField(Item.Package.ToString(), Item.Problem.IsEmpty() ? TEXT("not deleted") : Item.Problem);
		}
	}
	FJson Out = MakeShared<FJsonObject>();
	Out->SetArrayField(TEXT("fixed"), Fixed);
	Out->SetObjectField(TEXT("remaining"), Remaining);
	Out->SetNumberField(TEXT("resaved"), ToSave.Num() - (int32)Failed.Num());
	return UETKJson::Ok(Out);
}
