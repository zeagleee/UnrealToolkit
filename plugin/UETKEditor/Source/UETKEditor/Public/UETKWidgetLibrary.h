// UnrealToolkit: UMG widget trees for scripts. Python: unreal.UETKWidgetLibrary (wrapped by editor/ui.py).
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "UETKWidgetLibrary.generated.h"

class UUserWidget;
class UWidget;
class UWidgetBlueprint;

/**
 * The parts of a widget tree Python can't reach: a user widget's (or Widget Blueprint's) root, and adding or removing
 * widgets in a Widget Blueprint's designer tree. Everything below the root Python walks itself (PanelWidget.get_child_at,
 * ContentWidget.get_content).
 */
UCLASS()
class UETKEDITOR_API UUETKWidgetLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** The root widget of a live user widget, or of a Widget Blueprint's designer tree. Null if it has none. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Widgets")
	static UWidget* GetRootWidget(UObject* UserWidgetOrBlueprint);

	/**
	 * Add a widget to a Widget Blueprint's designer tree, as a variable: under Parent (a panel or content widget in that
	 * tree; null = the root, when the tree has none). Returns {"ok", "widget": path, "slot": path or null}. Compile the
	 * Blueprint afterwards (once, after all the changes): before that its graph can't use the new variable.
	 */
	UFUNCTION(BlueprintCallable, Category = "UETK|Widgets")
	static FString AddWidget(UWidgetBlueprint* Blueprint, UWidget* Parent, UClass* WidgetClass, FName Name);

	/** Create a user widget, as Blueprint's Create Widget node does (Python can't reach it): owned by a player
	 *  controller, or by a world's game instance when Owner is any other object in that world. Not yet on screen. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Widgets")
	static UUserWidget* CreateWidget(UObject* Owner, UClass* WidgetClass);

	/** Remove a widget (and everything under it) from a Widget Blueprint's designer tree. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Widgets")
	static FString RemoveWidget(UWidgetBlueprint* Blueprint, FName Name);
};
