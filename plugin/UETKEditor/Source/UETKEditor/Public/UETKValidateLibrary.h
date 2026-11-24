// UnrealToolkit: Data Validation and map check results for scripts. Python: unreal.UETKValidateLibrary (wrapped by
// editor/validate.py, `ue.py validate`).
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "UETKValidateLibrary.generated.h"

class UWorld;

/**
 * The parts of validation Python can't read: EditorValidatorSubsystem returns each asset's errors and warnings in
 * struct fields that aren't UPROPERTYs, and map check writes to a message log. Every function returns JSON text:
 * {"ok": true, ...} or {"ok": false, "error"}.
 */
UCLASS()
class UETKEDITOR_API UUETKValidateLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Data Validation on assets (object paths "/Game/A/B.B" or package names "/Game/A/B"), as the editor's Validate
	 * Assets runs it, without its progress window or message log:
	 *   {"assets": [{"asset", "result": "valid"|"invalid"|"notvalidated",
	 *                "messages": [{"severity": "error"|"warning"|"info", "message"}]}],
	 *    "missing": [paths not found], "checked", "valid", "invalid", "warnings", "skipped", "unable"}
	 * Assets that weren't loaded are loaded for it.
	 */
	UFUNCTION(BlueprintCallable, Category = "UETK|Validate")
	static FString ValidateAssets(const TArray<FString>& Paths);

	/**
	 * Map check (the editor's Build > Map Check) on World, the editor level when null:
	 *   {"map", "messages": [{"severity": "error"|"warning", "message"}]}
	 */
	UFUNCTION(BlueprintCallable, Category = "UETK|Validate")
	static FString MapCheck(UWorld* World);

	/**
	 * Fix up redirectors (package names) as the Content Browser's Fix Up does, without its dialogs: the referencers are
	 * loaded, their soft references rewritten, and saved (unsaved changes in them included), then each redirector
	 * whose referencers all saved is deleted. Works headless.
	 *   {"fixed": [package names], "remaining": {package name: why}, "resaved": count}
	 */
	UFUNCTION(BlueprintCallable, Category = "UETK|Validate")
	static FString FixRedirectors(const TArray<FString>& Packages);
};
