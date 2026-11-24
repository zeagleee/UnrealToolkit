#include "UETKWidgetLibrary.h"

#include "Blueprint/UserWidget.h"
#include "Blueprint/WidgetTree.h"
#include "Components/ContentWidget.h"
#include "Components/PanelSlot.h"
#include "Components/PanelWidget.h"
#include "Engine/World.h"
#include "GameFramework/PlayerController.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "UETKJson.h"
#include "WidgetBlueprint.h"

namespace UETKWidgetPrivate
{
	using namespace UETKJson;

	UWidgetTree* TreeOf(UObject* Object)
	{
		if (UWidgetBlueprint* Blueprint = Cast<UWidgetBlueprint>(Object))
		{
			return Blueprint->WidgetTree;
		}
		if (UUserWidget* Widget = Cast<UUserWidget>(Object))
		{
			return Widget->WidgetTree;
		}
		return nullptr;
	}
}

UWidget* UUETKWidgetLibrary::GetRootWidget(UObject* UserWidgetOrBlueprint)
{
	UWidgetTree* Tree = UETKWidgetPrivate::TreeOf(UserWidgetOrBlueprint);
	return Tree ? Tree->RootWidget.Get() : nullptr;
}

FString UUETKWidgetLibrary::AddWidget(UWidgetBlueprint* Blueprint, UWidget* Parent, UClass* WidgetClass, FName Name)
{
	using namespace UETKWidgetPrivate;
	if (!Blueprint || !Blueprint->WidgetTree)
	{
		return Fail(TEXT("not a Widget Blueprint"));
	}
	if (!WidgetClass || !WidgetClass->IsChildOf(UWidget::StaticClass()) || WidgetClass->HasAnyClassFlags(CLASS_Abstract))
	{
		return Fail(FString::Printf(TEXT("%s is not a widget class that can be placed"), WidgetClass ? *WidgetClass->GetName() : TEXT("null")));
	}
	UWidgetTree* Tree = Blueprint->WidgetTree;
	if (!Name.IsNone() && Tree->FindWidget(Name))
	{
		return Fail(FString::Printf(TEXT("%s already has a widget named %s"), *Blueprint->GetName(), *Name.ToString()));
	}
	if (Parent && Parent->GetTypedOuter<UWidgetTree>() != Tree)
	{
		return Fail(TEXT("the parent widget isn't in this Widget Blueprint's tree"));
	}
	UPanelWidget* Panel = Cast<UPanelWidget>(Parent);
	if (Parent && !Panel)
	{
		return Fail(FString::Printf(TEXT("%s (%s) can't hold children"), *Parent->GetName(), *Parent->GetClass()->GetName()));
	}
	if (Panel && !Panel->CanAddMoreChildren())
	{
		return Fail(FString::Printf(TEXT("%s (%s) is full: it holds one child"), *Panel->GetName(), *Panel->GetClass()->GetName()));
	}
	if (!Parent && Tree->RootWidget)
	{
		return Fail(FString::Printf(TEXT("the tree already has a root (%s); give a parent"), *Tree->RootWidget->GetName()));
	}

	Blueprint->Modify();
	Tree->SetFlags(RF_Transactional);
	Tree->Modify();
	UWidget* Widget = Tree->ConstructWidget<UWidget>(WidgetClass, Name);
	Widget->SetFlags(RF_Transactional);
	Widget->bIsVariable = true;
	// What the designer does for every new widget: without it the compiler ensures ("did not get a GUID").
	Blueprint->OnVariableAdded(Widget->GetFName());
	UPanelSlot* Slot = nullptr;
	if (Panel)
	{
		Panel->Modify();
		Slot = Panel->AddChild(Widget);
	}
	else
	{
		Tree->RootWidget = Widget;
	}
	// Not structurally modified: that recompiles the skeleton on every add. The caller compiles once at the end.
	FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);

	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("widget"), Widget->GetPathName());
	Result->SetStringField(TEXT("name"), Widget->GetName());
	if (Slot)
	{
		Result->SetStringField(TEXT("slot"), Slot->GetPathName());
	}
	return Ok(Result);
}

FString UUETKWidgetLibrary::RemoveWidget(UWidgetBlueprint* Blueprint, FName Name)
{
	using namespace UETKWidgetPrivate;
	if (!Blueprint || !Blueprint->WidgetTree)
	{
		return Fail(TEXT("not a Widget Blueprint"));
	}
	UWidgetTree* Tree = Blueprint->WidgetTree;
	UWidget* Widget = Tree->FindWidget(Name);
	if (!Widget)
	{
		return Fail(FString::Printf(TEXT("%s has no widget named %s"), *Blueprint->GetName(), *Name.ToString()));
	}
	Blueprint->Modify();
	Tree->Modify();
	TArray<UWidget*> Gone;
	Gone.Add(Widget);
	UWidgetTree::GetChildWidgets(Widget, Gone);
	if (Tree->RootWidget == Widget)
	{
		Tree->RootWidget = nullptr;
	}
	else
	{
		Tree->RemoveWidget(Widget);
	}
	for (UWidget* Each : Gone)
	{
		Blueprint->OnVariableRemoved(Each->GetFName());
		// Out of the tree for good (a later widget can reuse the name).
		Each->Rename(nullptr, GetTransientPackage(), REN_DontCreateRedirectors | REN_NonTransactional);
	}
	FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
	FJson Result = MakeShared<FJsonObject>();
	Result->SetNumberField(TEXT("removed"), Gone.Num());
	return Ok(Result);
}

UUserWidget* UUETKWidgetLibrary::CreateWidget(UObject* Owner, UClass* WidgetClass)
{
	if (!Owner || !WidgetClass || !WidgetClass->IsChildOf(UUserWidget::StaticClass()))
	{
		return nullptr;
	}
	if (APlayerController* Player = Cast<APlayerController>(Owner))
	{
		return ::CreateWidget<UUserWidget>(Player, WidgetClass);
	}
	UWorld* World = Owner->GetWorld();
	return World ? ::CreateWidget<UUserWidget>(World, WidgetClass) : nullptr;
}
