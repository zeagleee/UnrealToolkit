#include "UETKBlueprintLibrary.h"

#include "EdGraph/EdGraph.h"
#include "EdGraphNode_Comment.h"
#include "EdGraphSchema_K2.h"
#include "Engine/Blueprint.h"
#include "K2Node_CallFunction.h"
#include "K2Node_CustomEvent.h"
#include "K2Node_DynamicCast.h"
#include "K2Node_Event.h"
#include "K2Node_ExecutionSequence.h"
#include "K2Node_FunctionEntry.h"
#include "K2Node_FunctionResult.h"
#include "K2Node_IfThenElse.h"
#include "K2Node_MacroInstance.h"
#include "K2Node_Self.h"
#include "K2Node_VariableGet.h"
#include "K2Node_VariableSet.h"
#include "Kismet/GameplayStatics.h"
#include "Kismet/KismetArrayLibrary.h"
#include "Kismet/KismetMathLibrary.h"
#include "Kismet/KismetStringLibrary.h"
#include "Kismet/KismetSystemLibrary.h"
#include "Kismet/KismetTextLibrary.h"
#include "Kismet2/BlueprintEditorUtils.h"
#include "Kismet2/CompilerResultsLog.h"
#include "Kismet2/KismetEditorUtilities.h"
#include "Logging/TokenizedMessage.h"
#include "Misc/PackageName.h"
#include "UETKJson.h"

namespace UETKBlueprintPrivate
{
	using namespace UETKJson;

	const UEdGraphSchema_K2* Schema()
	{
		return GetDefault<UEdGraphSchema_K2>();
	}

	FString GraphKind(UBlueprint* Blueprint, UEdGraph* Graph)
	{
		if (Blueprint->UbergraphPages.Contains(Graph)) return TEXT("event");
		if (Blueprint->FunctionGraphs.Contains(Graph)) return TEXT("function");
		if (Blueprint->MacroGraphs.Contains(Graph)) return TEXT("macro");
		return TEXT("other");
	}

	UEdGraph* FindGraph(UBlueprint* Blueprint, const FString& Name, FString& Error)
	{
		TArray<UEdGraph*> Graphs;
		Blueprint->GetAllGraphs(Graphs);
		if (Name.IsEmpty())
		{
			if (UEdGraph* Event = FBlueprintEditorUtils::FindEventGraph(Blueprint))
			{
				return Event;
			}
		}
		TArray<FString> Names;
		for (UEdGraph* Graph : Graphs)
		{
			Names.Add(Graph->GetName());
			if (Norm(Graph->GetName()) == Norm(Name))
			{
				return Graph;
			}
		}
		Error = FString::Printf(TEXT("no graph '%s' in %s (graphs: %s)"), *Name, *Blueprint->GetName(), *FString::Join(Names, TEXT(", ")));
		return nullptr;
	}

	FString NodeTitle(UEdGraphNode* Node)
	{
		return Node->GetNodeTitle(ENodeTitleType::ListView).ToString();
	}

	UEdGraphNode* FindNode(UEdGraph* Graph, const FString& Id, FString& Error)
	{
		TArray<UEdGraphNode*> ByTitle;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (!Node)
			{
				continue;
			}
			if (Node->GetName() == Id)
			{
				return Node;
			}
			if (Norm(NodeTitle(Node)) == Norm(Id))
			{
				ByTitle.Add(Node);
			}
		}
		if (ByTitle.Num() == 1)
		{
			return ByTitle[0];
		}
		Error = ByTitle.Num() > 1
			? FString::Printf(TEXT("'%s' matches %d nodes by title in %s: use a node id"), *Id, ByTitle.Num(), *Graph->GetName())
			: FString::Printf(TEXT("no node '%s' in %s (ids come from DescribeGraph)"), *Id, *Graph->GetName());
		return nullptr;
	}

	FString PinNames(UEdGraphNode* Node)
	{
		TArray<FString> Names;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin && !Pin->bHidden)
			{
				Names.Add(FString::Printf(TEXT("%s(%s)"), *Pin->PinName.ToString(), Pin->Direction == EGPD_Input ? TEXT("in") : TEXT("out")));
			}
		}
		return FString::Join(Names, TEXT(", "));
	}

	/** The node's only non-exec pin of a direction, or null. */
	UEdGraphPin* SingleDataPin(UEdGraphNode* Node, EEdGraphPinDirection Direction)
	{
		UEdGraphPin* Found = nullptr;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (Pin && !Pin->bHidden && Pin->Direction == Direction && Pin->PinType.PinCategory != UEdGraphSchema_K2::PC_Exec)
			{
				if (Found)
				{
					return nullptr;
				}
				Found = Pin;
			}
		}
		return Found;
	}

	/** Pin by name or display name; Preferred direction first (EGPD_MAX = either). */
	UEdGraphPin* FindPin(UEdGraphNode* Node, const FString& Name, EEdGraphPinDirection Preferred, FString& Error)
	{
		FString Wanted = Norm(Name);
		// "value"/"output": the node's one data output. A Set node's value output is "Output_Get", unlabeled in the
		// editor, so its variable's name (asked for as an output) means that pin too.
		if (Preferred != EGPD_Input && (Wanted == TEXT("value") || Wanted == TEXT("output")))
		{
			if (UEdGraphPin* Pin = SingleDataPin(Node, EGPD_Output))
			{
				return Pin;
			}
		}
		if (UK2Node_VariableSet* SetNode = Cast<UK2Node_VariableSet>(Node))
		{
			if (Preferred == EGPD_Output && Wanted == Norm(SetNode->GetVarNameString()))
			{
				if (UEdGraphPin* Pin = SingleDataPin(Node, EGPD_Output))
				{
					return Pin;
				}
			}
		}
		static const TMap<FString, FString> Aliases = {
			{TEXT("exec"), TEXT("execute")}, {TEXT("in"), TEXT("execute")}, {TEXT("out"), TEXT("then")},
			{TEXT("true"), TEXT("then")}, {TEXT("false"), TEXT("else")}, {TEXT("target"), TEXT("self")},
			{TEXT("return"), TEXT("returnvalue")}};
		for (int32 Pass = 0; Pass < 4; ++Pass)
		{
			const bool bAlias = Pass >= 2;
			const bool bAnyDirection = Pass % 2 == 1;
			const FString* Alias = Aliases.Find(Wanted);
			if (bAlias && !Alias)
			{
				break;
			}
			const FString Key = bAlias ? *Alias : Wanted;
			for (UEdGraphPin* Pin : Node->Pins)
			{
				if (!Pin || (!bAnyDirection && Preferred != EGPD_MAX && Pin->Direction != Preferred))
				{
					continue;
				}
				if (Norm(Pin->PinName.ToString()) == Key || Norm(Pin->GetDisplayName().ToString()) == Key
					|| Norm(Pin->PinFriendlyName.ToString()) == Key)
				{
					return Pin;
				}
			}
		}
		Error = FString::Printf(TEXT("%s has no pin '%s' (pins: %s)"), *Node->GetName(), *Name, *PinNames(Node));
		return nullptr;
	}

	FString PinTypeText(const FEdGraphPinType& Type)
	{
		FString Text = Type.PinCategory.ToString();
		if (UObject* Sub = Type.PinSubCategoryObject.Get())
		{
			Text += TEXT(":") + Sub->GetName();
		}
		else if (!Type.PinSubCategory.IsNone())
		{
			Text += TEXT(":") + Type.PinSubCategory.ToString();
		}
		if (Type.ContainerType == EPinContainerType::Array) Text = TEXT("array:") + Text;
		if (Type.ContainerType == EPinContainerType::Set) Text = TEXT("set:") + Text;
		if (Type.ContainerType == EPinContainerType::Map) Text = TEXT("map:") + Text;
		return Text;
	}

	UClass* ResolveClass(const FString& InName)
	{
		const FString Name = InName.TrimStartAndEnd();
		if (Name.IsEmpty())
		{
			return nullptr;
		}
		if (Name.StartsWith(TEXT("/")))
		{
			if (Name.StartsWith(TEXT("/Script/")))
			{
				return LoadObject<UClass>(nullptr, *Name);
			}
			const FString Package = Name.Contains(TEXT(".")) ? FPackageName::ObjectPathToPackageName(Name) : Name;
			const FString Object = Package + TEXT(".") + FPackageName::GetShortName(Package);
			if (UBlueprint* Blueprint = LoadObject<UBlueprint>(nullptr, *Object))
			{
				return Blueprint->GeneratedClass;
			}
			return LoadObject<UClass>(nullptr, *Name);
		}
		TArray<FString> Candidates = {Name};
		if (Name.Len() > 1 && (Name[0] == TEXT('U') || Name[0] == TEXT('A')) && FChar::IsUpper(Name[1]))
		{
			Candidates.Add(Name.Mid(1));
		}
		for (const FString& Candidate : Candidates)
		{
			if (UClass* Class = FindFirstObject<UClass>(*Candidate, EFindFirstObjectOptions::NativeFirst))
			{
				return Class;
			}
		}
		return nullptr;
	}

	UObject* ResolveTypeObject(const FString& Name, UClass* Kind)
	{
		if (Name.StartsWith(TEXT("/")))
		{
			return StaticLoadObject(Kind, nullptr, *Name);
		}
		return StaticFindFirstObject(Kind, *Name, EFindFirstObjectOptions::NativeFirst);
	}

	bool ParsePinType(const FString& InText, FEdGraphPinType& Out, FString& Error)
	{
		FString Text = InText.TrimStartAndEnd();
		Out = FEdGraphPinType();
		if (Text.StartsWith(TEXT("array:"), ESearchCase::IgnoreCase) || Text.StartsWith(TEXT("set:"), ESearchCase::IgnoreCase))
		{
			const bool bArray = Text.StartsWith(TEXT("array:"), ESearchCase::IgnoreCase);
			if (!ParsePinType(Text.Mid(bArray ? 6 : 4), Out, Error))
			{
				return false;
			}
			Out.ContainerType = bArray ? EPinContainerType::Array : EPinContainerType::Set;
			return true;
		}
		const FString N = Norm(Text);
		auto Struct = [&Out](UScriptStruct* S) { Out.PinCategory = UEdGraphSchema_K2::PC_Struct; Out.PinSubCategoryObject = S; return true; };
		if (N == TEXT("bool") || N == TEXT("boolean")) { Out.PinCategory = UEdGraphSchema_K2::PC_Boolean; return true; }
		if (N == TEXT("int") || N == TEXT("integer") || N == TEXT("int32")) { Out.PinCategory = UEdGraphSchema_K2::PC_Int; return true; }
		if (N == TEXT("int64")) { Out.PinCategory = UEdGraphSchema_K2::PC_Int64; return true; }
		if (N == TEXT("float") || N == TEXT("double") || N == TEXT("real")) { Out.PinCategory = UEdGraphSchema_K2::PC_Real; Out.PinSubCategory = UEdGraphSchema_K2::PC_Double; return true; }
		if (N == TEXT("byte")) { Out.PinCategory = UEdGraphSchema_K2::PC_Byte; return true; }
		if (N == TEXT("string")) { Out.PinCategory = UEdGraphSchema_K2::PC_String; return true; }
		if (N == TEXT("name")) { Out.PinCategory = UEdGraphSchema_K2::PC_Name; return true; }
		if (N == TEXT("text")) { Out.PinCategory = UEdGraphSchema_K2::PC_Text; return true; }
		if (N == TEXT("vector")) return Struct(TBaseStructure<FVector>::Get());
		if (N == TEXT("rotator")) return Struct(TBaseStructure<FRotator>::Get());
		if (N == TEXT("transform")) return Struct(TBaseStructure<FTransform>::Get());
		if (N == TEXT("vector2d")) return Struct(TBaseStructure<FVector2D>::Get());
		if (N == TEXT("linearcolor") || N == TEXT("color")) return Struct(TBaseStructure<FLinearColor>::Get());
		FString Kind, Rest;
		if (Text.Split(TEXT(":"), &Kind, &Rest))
		{
			Kind = Norm(Kind);
			if (Kind == TEXT("struct"))
			{
				if (UScriptStruct* S = Cast<UScriptStruct>(ResolveTypeObject(Rest, UScriptStruct::StaticClass()))) return Struct(S);
				Error = FString::Printf(TEXT("no struct '%s'"), *Rest);
				return false;
			}
			if (Kind == TEXT("enum"))
			{
				if (UEnum* E = Cast<UEnum>(ResolveTypeObject(Rest, UEnum::StaticClass())))
				{
					Out.PinCategory = UEdGraphSchema_K2::PC_Byte;
					Out.PinSubCategoryObject = E;
					return true;
				}
				Error = FString::Printf(TEXT("no enum '%s'"), *Rest);
				return false;
			}
			UClass* Class = ResolveClass(Rest);
			if (!Class)
			{
				Error = FString::Printf(TEXT("no class '%s'"), *Rest);
				return false;
			}
			Out.PinSubCategoryObject = Class;
			if (Kind == TEXT("object")) { Out.PinCategory = UEdGraphSchema_K2::PC_Object; return true; }
			if (Kind == TEXT("class")) { Out.PinCategory = UEdGraphSchema_K2::PC_Class; return true; }
			if (Kind == TEXT("softobject")) { Out.PinCategory = UEdGraphSchema_K2::PC_SoftObject; return true; }
			if (Kind == TEXT("softclass")) { Out.PinCategory = UEdGraphSchema_K2::PC_SoftClass; return true; }
			Error = FString::Printf(TEXT("unknown type kind '%s'"), *Kind);
			return false;
		}
		if (UClass* Class = ResolveClass(Text))
		{
			Out.PinCategory = UEdGraphSchema_K2::PC_Object;
			Out.PinSubCategoryObject = Class;
			return true;
		}
		if (UScriptStruct* S = Cast<UScriptStruct>(ResolveTypeObject(Text, UScriptStruct::StaticClass())))
		{
			return Struct(S);
		}
		Error = FString::Printf(TEXT("unknown type '%s' (bool, int, float, string, name, text, vector, rotator, transform, a class, struct:X, enum:X, array:T)"), *Text);
		return false;
	}

	bool NameMatches(const UFunction* Function, const FString& Wanted)
	{
		const FString W = Norm(Wanted);
		const FString Name = Norm(Function->GetName());
		return Name == W || Name == TEXT("k2") + W || Name == TEXT("receive") + W
			|| Norm(Function->GetMetaData(TEXT("DisplayName"))) == W;
	}

	UFunction* FindFunction(UBlueprint* Blueprint, const FString& ClassName, const FString& FunctionName, FString& Error)
	{
		TArray<UClass*> Classes;
		if (!ClassName.IsEmpty())
		{
			UClass* Class = ResolveClass(ClassName);
			if (!Class)
			{
				Error = FString::Printf(TEXT("no class '%s'"), *ClassName);
				return nullptr;
			}
			Classes.Add(Class);
		}
		else
		{
			Classes.Add(Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass.Get() : Blueprint->GeneratedClass.Get());
			Classes.Append({UKismetSystemLibrary::StaticClass(), UKismetMathLibrary::StaticClass(), UKismetStringLibrary::StaticClass(),
				UKismetTextLibrary::StaticClass(), UKismetArrayLibrary::StaticClass(), UGameplayStatics::StaticClass()});
		}
		for (UClass* Class : Classes)
		{
			if (!Class)
			{
				continue;
			}
			for (TFieldIterator<UFunction> It(Class, EFieldIteratorFlags::IncludeSuper); It; ++It)
			{
				if (NameMatches(*It, FunctionName) && It->HasAnyFunctionFlags(FUNC_BlueprintCallable | FUNC_BlueprintPure))
				{
					return *It;
				}
			}
		}
		Error = FString::Printf(TEXT("no Blueprint-callable function '%s' in %s"), *FunctionName,
			ClassName.IsEmpty() ? TEXT("the Blueprint or the Kismet/GameplayStatics libraries (pass \"class\")") : *ClassName);
		return nullptr;
	}

	UFunction* FindEventFunction(UBlueprint* Blueprint, const FString& Name, FString& Error)
	{
		UClass* Parent = Blueprint->ParentClass;
		for (TFieldIterator<UFunction> It(Parent, EFieldIteratorFlags::IncludeSuper); It; ++It)
		{
			if (It->HasAnyFunctionFlags(FUNC_BlueprintEvent) && NameMatches(*It, Name))
			{
				return *It;
			}
		}
		Error = FString::Printf(TEXT("%s has no overridable event '%s'"), Parent ? *Parent->GetName() : TEXT("(no parent)"), *Name);
		return nullptr;
	}

	template <typename T>
	T* Create(UEdGraph* Graph, int32 X, int32 Y, TFunctionRef<void(T*)> Setup)
	{
		FGraphNodeCreator<T> Creator(*Graph);
		T* Node = Creator.CreateNode(false);
		Setup(Node);
		Node->NodePosX = X;
		Node->NodePosY = Y;
		Creator.Finalize();
		return Node;
	}

	void AutoPlace(UEdGraph* Graph, int32& X, int32& Y)
	{
		if (X != MIN_int32 && Y != MIN_int32)
		{
			return;
		}
		int32 MaxY = MIN_int32, MinX = MAX_int32;
		for (UEdGraphNode* Node : Graph->Nodes)
		{
			if (Node && !Node->IsA<UEdGraphNode_Comment>())
			{
				MaxY = FMath::Max(MaxY, Node->NodePosY);
				MinX = FMath::Min(MinX, Node->NodePosX);
			}
		}
		X = X != MIN_int32 ? X : (MinX == MAX_int32 ? 0 : MinX);
		Y = Y != MIN_int32 ? Y : (MaxY == MIN_int32 ? 0 : MaxY + 250);
	}

	TSharedRef<FJsonObject> DescribeNode(UEdGraphNode* Node)
	{
		TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
		Out->SetStringField(TEXT("id"), Node->GetName());
		Out->SetStringField(TEXT("class"), Node->GetClass()->GetName());
		Out->SetStringField(TEXT("title"), NodeTitle(Node).Replace(TEXT("\n"), TEXT(" | ")));
		Out->SetNumberField(TEXT("x"), Node->NodePosX);
		Out->SetNumberField(TEXT("y"), Node->NodePosY);
		if (!Node->IsNodeEnabled())
		{
			Out->SetBoolField(TEXT("disabled"), true);
		}
		if (!Node->NodeComment.IsEmpty())
		{
			Out->SetStringField(TEXT("comment"), Node->NodeComment);
		}
		if (UK2Node_CallFunction* Call = Cast<UK2Node_CallFunction>(Node))
		{
			if (UFunction* Function = Call->GetTargetFunction())
			{
				Out->SetStringField(TEXT("function"), Function->GetOwnerClass()->GetName() + TEXT(".") + Function->GetName());
			}
		}
		TArray<TSharedPtr<FJsonValue>> Pins;
		for (UEdGraphPin* Pin : Node->Pins)
		{
			if (!Pin || (Pin->bHidden && Pin->LinkedTo.Num() == 0))
			{
				continue;
			}
			TSharedRef<FJsonObject> P = MakeShared<FJsonObject>();
			P->SetStringField(TEXT("name"), Pin->PinName.ToString());
			const FString Display = Pin->GetDisplayName().ToString();
			if (!Display.IsEmpty() && Norm(Display) != Norm(Pin->PinName.ToString()))
			{
				P->SetStringField(TEXT("display"), Display);
			}
			P->SetStringField(TEXT("dir"), Pin->Direction == EGPD_Input ? TEXT("in") : TEXT("out"));
			P->SetStringField(TEXT("type"), PinTypeText(Pin->PinType));
			if (Pin->Direction == EGPD_Input && Pin->LinkedTo.Num() == 0 && !Pin->GetDefaultAsString().IsEmpty())
			{
				P->SetStringField(TEXT("default"), Pin->GetDefaultAsString());
			}
			TArray<TSharedPtr<FJsonValue>> Links;
			for (UEdGraphPin* Linked : Pin->LinkedTo)
			{
				if (Linked && Linked->GetOwningNode())
				{
					Links.Add(MakeShared<FJsonValueString>(Linked->GetOwningNode()->GetName() + TEXT(".") + Linked->PinName.ToString()));
				}
			}
			if (Links.Num())
			{
				P->SetArrayField(TEXT("links"), Links);
			}
			Pins.Add(MakeShared<FJsonValueObject>(P));
		}
		Out->SetArrayField(TEXT("pins"), Pins);
		return Out;
	}

	/** Resolve Blueprint + graph + node; on failure Error is set. */
	struct FTarget
	{
		UEdGraph* Graph = nullptr;
		UEdGraphNode* Node = nullptr;
		FString Error;
	};

	bool Resolve(UBlueprint* Blueprint, const FString& GraphName, const FString& NodeId, FTarget& T)
	{
		if (!Blueprint)
		{
			T.Error = TEXT("Blueprint is null");
			return false;
		}
		T.Graph = FindGraph(Blueprint, GraphName, T.Error);
		if (!T.Graph)
		{
			return false;
		}
		if (!NodeId.IsEmpty())
		{
			T.Node = FindNode(T.Graph, NodeId, T.Error);
			return T.Node != nullptr;
		}
		return true;
	}

	FString PinsOf(UEdGraphNode* Node, FJson Result)
	{
		Result->SetStringField(TEXT("id"), Node->GetName());
		Result->SetObjectField(TEXT("node"), DescribeNode(Node));
		return Ok(Result);
	}
}

using namespace UETKBlueprintPrivate;

FString UUETKBlueprintLibrary::ListGraphs(UBlueprint* Blueprint)
{
	if (!Blueprint)
	{
		return Fail(TEXT("Blueprint is null"));
	}
	TArray<UEdGraph*> Graphs;
	Blueprint->GetAllGraphs(Graphs);
	TArray<TSharedPtr<FJsonValue>> Items;
	for (UEdGraph* Graph : Graphs)
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("name"), Graph->GetName());
		Item->SetStringField(TEXT("kind"), GraphKind(Blueprint, Graph));
		Item->SetNumberField(TEXT("nodes"), Graph->Nodes.Num());
		Items.Add(MakeShared<FJsonValueObject>(Item));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetArrayField(TEXT("graphs"), Items);
	Result->SetStringField(TEXT("parent"), Blueprint->ParentClass ? Blueprint->ParentClass->GetPathName() : TEXT(""));
	return Ok(Result);
}

FString UUETKBlueprintLibrary::DescribeGraph(UBlueprint* Blueprint, const FString& Graph)
{
	FTarget T;
	if (!Resolve(Blueprint, Graph, FString(), T))
	{
		return Fail(T.Error);
	}
	TArray<TSharedPtr<FJsonValue>> Nodes;
	for (UEdGraphNode* Node : T.Graph->Nodes)
	{
		if (Node)
		{
			Nodes.Add(MakeShared<FJsonValueObject>(DescribeNode(Node)));
		}
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("graph"), T.Graph->GetName());
	Result->SetStringField(TEXT("kind"), GraphKind(Blueprint, T.Graph));
	Result->SetArrayField(TEXT("nodes"), Nodes);
	return Ok(Result);
}

FString UUETKBlueprintLibrary::AddNode(UBlueprint* Blueprint, const FString& Graph, const FString& Kind, const FString& SpecJson, int32 X, int32 Y)
{
	FTarget T;
	if (!Resolve(Blueprint, Graph, FString(), T))
	{
		return Fail(T.Error);
	}
	TSharedPtr<FJsonObject> Spec = Parse(SpecJson);
	if (!Spec)
	{
		return Fail(TEXT("Spec is not a JSON object"));
	}
	auto Field = [&Spec](const TCHAR* Name) { FString V; Spec->TryGetStringField(Name, V); return V; };
	AutoPlace(T.Graph, X, Y);
	T.Graph->Modify();
	Blueprint->Modify();
	const FString K = Norm(Kind);
	FString Error;
	UEdGraphNode* Node = nullptr;

	if (K == TEXT("call"))
	{
		UFunction* Function = UETKBlueprintPrivate::FindFunction(Blueprint, Field(TEXT("class")), Field(TEXT("function")), Error);
		if (!Function)
		{
			return Fail(Error);
		}
		Node = Create<UK2Node_CallFunction>(T.Graph, X, Y, [Function](UK2Node_CallFunction* N) { N->SetFromFunction(Function); });
	}
	else if (K == TEXT("event"))
	{
		UFunction* Function = FindEventFunction(Blueprint, Field(TEXT("name")), Error);
		if (!Function)
		{
			return Fail(Error);
		}
		if (Function->NumParms > 0 && Function->GetReturnProperty())
		{
			return Fail(FString::Printf(TEXT("%s returns a value: override it as a function graph"), *Function->GetName()));
		}
		UClass* Owner = Function->GetOwnerClass();
		if (UK2Node_Event* Existing = FBlueprintEditorUtils::FindOverrideForFunction(Blueprint, Owner, Function->GetFName()))
		{
			if (Existing->IsAutomaticallyPlacedGhostNode())
			{
				Existing->Modify();
				Existing->SetEnabledState(ENodeEnabledState::Enabled, false);
				Existing->NodeComment.Empty();
				Existing->bCommentBubbleVisible = false;
			}
			FJson Result = MakeShared<FJsonObject>();
			Result->SetBoolField(TEXT("existing"), true);
			return PinsOf(Existing, Result);
		}
		Node = Create<UK2Node_Event>(T.Graph, X, Y, [Function, Owner](UK2Node_Event* N)
		{
			N->EventReference.SetExternalMember(Function->GetFName(), Owner);
			N->bOverrideFunction = true;
		});
	}
	else if (K == TEXT("customevent"))
	{
		const FString Name = Field(TEXT("name"));
		if (Name.IsEmpty())
		{
			return Fail(TEXT("custom_event needs a name"));
		}
		UK2Node_CustomEvent* Event = Create<UK2Node_CustomEvent>(T.Graph, X, Y, [&Name](UK2Node_CustomEvent* N) { N->CustomFunctionName = FName(*Name); });
		const TSharedPtr<FJsonObject>* Params = nullptr;
		if (Spec->TryGetObjectField(TEXT("params"), Params))
		{
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Param : (*Params)->Values)
			{
				FEdGraphPinType Type;
				if (!ParsePinType(Param.Value->AsString(), Type, Error))
				{
					FBlueprintEditorUtils::RemoveNode(Blueprint, Event, true);
					return Fail(FString::Printf(TEXT("param %s: %s"), *Param.Key, *Error));
				}
				Event->CreateUserDefinedPin(FName(*Param.Key), Type, EGPD_Output);
			}
		}
		Node = Event;
	}
	else if (K == TEXT("get") || K == TEXT("set"))
	{
		const FName Variable(*Field(TEXT("variable")));
		UClass* Owner = Field(TEXT("class")).IsEmpty() ? nullptr : ResolveClass(Field(TEXT("class")));
		UClass* Lookup = Owner ? Owner : (Blueprint->SkeletonGeneratedClass ? Blueprint->SkeletonGeneratedClass.Get() : Blueprint->GeneratedClass.Get());
		if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, Variable) == INDEX_NONE && !(Lookup && FindFProperty<FProperty>(Lookup, Variable)))
		{
			return Fail(FString::Printf(TEXT("no variable '%s' on %s"), *Variable.ToString(), Lookup ? *Lookup->GetName() : TEXT("the Blueprint")));
		}
		auto Setup = [Variable, Owner](auto* N)
		{
			if (Owner)
			{
				N->VariableReference.SetExternalMember(Variable, Owner);
			}
			else
			{
				N->VariableReference.SetSelfMember(Variable);
			}
		};
		Node = K == TEXT("get")
			? static_cast<UEdGraphNode*>(Create<UK2Node_VariableGet>(T.Graph, X, Y, [&Setup](UK2Node_VariableGet* N) { Setup(N); }))
			: static_cast<UEdGraphNode*>(Create<UK2Node_VariableSet>(T.Graph, X, Y, [&Setup](UK2Node_VariableSet* N) { Setup(N); }));
	}
	else if (K == TEXT("branch"))
	{
		Node = Create<UK2Node_IfThenElse>(T.Graph, X, Y, [](UK2Node_IfThenElse*) {});
	}
	else if (K == TEXT("sequence"))
	{
		Node = Create<UK2Node_ExecutionSequence>(T.Graph, X, Y, [](UK2Node_ExecutionSequence*) {});
	}
	else if (K == TEXT("self"))
	{
		Node = Create<UK2Node_Self>(T.Graph, X, Y, [](UK2Node_Self*) {});
	}
	else if (K == TEXT("cast"))
	{
		UClass* Class = ResolveClass(Field(TEXT("class")));
		if (!Class)
		{
			return Fail(FString::Printf(TEXT("no class '%s'"), *Field(TEXT("class"))));
		}
		Node = Create<UK2Node_DynamicCast>(T.Graph, X, Y, [Class](UK2Node_DynamicCast* N) { N->TargetType = Class; });
	}
	else if (K == TEXT("macro"))
	{
		UBlueprint* Macros = LoadObject<UBlueprint>(nullptr, TEXT("/Engine/EditorBlueprintResources/StandardMacros.StandardMacros"));
		UEdGraph* Macro = nullptr;
		TArray<FString> Names;
		for (UEdGraph* Candidate : Macros ? Macros->MacroGraphs : TArray<TObjectPtr<UEdGraph>>())
		{
			Names.Add(Candidate->GetName());
			if (Norm(Candidate->GetName()) == Norm(Field(TEXT("name"))))
			{
				Macro = Candidate;
			}
		}
		if (!Macro)
		{
			return Fail(FString::Printf(TEXT("no standard macro '%s' (%s)"), *Field(TEXT("name")), *FString::Join(Names, TEXT(", "))));
		}
		Node = Create<UK2Node_MacroInstance>(T.Graph, X, Y, [Macro](UK2Node_MacroInstance* N) { N->SetMacroGraph(Macro); });
	}
	else if (K == TEXT("comment"))
	{
		double Width = 400, Height = 200;
		Spec->TryGetNumberField(TEXT("width"), Width);
		Spec->TryGetNumberField(TEXT("height"), Height);
		Node = Create<UEdGraphNode_Comment>(T.Graph, X, Y, [&](UEdGraphNode_Comment* N)
		{
			N->NodeComment = Field(TEXT("text"));
			N->NodeWidth = static_cast<int32>(Width);
			N->NodeHeight = static_cast<int32>(Height);
		});
	}
	else if (K == TEXT("node"))
	{
		// Any graph node class, with properties set (from Unreal's text form) before its pins are made:
		// K2Node_EnhancedInputAction {"InputAction": "/Game/Input/IA_Jump.IA_Jump"}, K2Node_GetSubsystem...
		const FString ClassName = Field(TEXT("class"));
		UClass* Class = ClassName.StartsWith(TEXT("/")) ? LoadObject<UClass>(nullptr, *ClassName)
			: FindFirstObject<UClass>(*ClassName, EFindFirstObjectOptions::NativeFirst);
		if (!Class && !ClassName.StartsWith(TEXT("K2Node_")))
		{
			Class = FindFirstObject<UClass>(*(TEXT("K2Node_") + ClassName), EFindFirstObjectOptions::NativeFirst);
		}
		if (!Class || !Class->IsChildOf(UEdGraphNode::StaticClass()) || Class->HasAnyClassFlags(CLASS_Abstract))
		{
			return Fail(FString::Printf(TEXT("no graph node class '%s' (e.g. K2Node_EnhancedInputAction; its module must be loaded)"), *ClassName));
		}
		UEdGraphNode* New = NewObject<UEdGraphNode>(T.Graph, Class, NAME_None, RF_Transactional);
		T.Graph->AddNode(New, true, false);
		const TSharedPtr<FJsonObject>* Props = nullptr;
		if (Spec->TryGetObjectField(TEXT("props"), Props))
		{
			for (const TPair<FString, TSharedPtr<FJsonValue>>& Prop : (*Props)->Values)
			{
				FProperty* Property = FindFProperty<FProperty>(Class, *Prop.Key);
				const FString Text = Prop.Value->AsString();
				if (!Property || !Property->ImportText_InContainer(*Text, New, New, PPF_None))
				{
					T.Graph->RemoveNode(New);
					return Fail(FString::Printf(TEXT("%s: can't set %s to '%s'"), *Class->GetName(), *Prop.Key, *Text));
				}
			}
		}
		New->NodePosX = X;
		New->NodePosY = Y;
		New->CreateNewGuid();
		New->PostPlacedNewNode();
		if (New->Pins.Num() == 0)
		{
			New->AllocateDefaultPins();
		}
		Node = New;
	}
	else
	{
		return Fail(FString::Printf(TEXT("unknown node kind '%s' (call, event, custom_event, get, set, branch, sequence, self, cast, macro, comment, node)"), *Kind));
	}
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
	return PinsOf(Node, MakeShared<FJsonObject>());
}

FString UUETKBlueprintLibrary::Connect(UBlueprint* Blueprint, const FString& Graph, const FString& FromNode, const FString& FromPin, const FString& ToNode, const FString& ToPin)
{
	FTarget A, B;
	if (!Resolve(Blueprint, Graph, FromNode, A))
	{
		return Fail(A.Error);
	}
	if (!Resolve(Blueprint, Graph, ToNode, B))
	{
		return Fail(B.Error);
	}
	FString Error;
	UEdGraphPin* PinA = FindPin(A.Node, FromPin, EGPD_Output, Error);
	if (!PinA)
	{
		return Fail(Error);
	}
	UEdGraphPin* PinB = FindPin(B.Node, ToPin, PinA->Direction == EGPD_Output ? EGPD_Input : EGPD_Output, Error);
	if (!PinB)
	{
		return Fail(Error);
	}
	if (PinA->Direction == PinB->Direction)
	{
		return Fail(FString::Printf(TEXT("%s.%s and %s.%s are both %s pins"), *FromNode, *FromPin, *ToNode, *ToPin,
			PinA->Direction == EGPD_Input ? TEXT("input") : TEXT("output")));
	}
	const FPinConnectionResponse Response = Schema()->CanCreateConnection(PinA, PinB);
	if (Response.Response == CONNECT_RESPONSE_DISALLOW)
	{
		return Fail(FString::Printf(TEXT("can't connect %s.%s to %s.%s: %s"), *FromNode, *PinA->PinName.ToString(), *ToNode,
			*PinB->PinName.ToString(), *Response.Message.ToString()));
	}
	A.Graph->Modify();
	const bool bConverted = Response.Response == CONNECT_RESPONSE_MAKE_WITH_CONVERSION_NODE;
	const bool bLinked = bConverted ? Schema()->CreateAutomaticConversionNodeAndConnections(PinA, PinB) : Schema()->TryCreateConnection(PinA, PinB);
	if (!bLinked)
	{
		return Fail(FString::Printf(TEXT("the schema refused %s.%s -> %s.%s"), *FromNode, *FromPin, *ToNode, *ToPin));
	}
	FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("linked"), FString::Printf(TEXT("%s.%s -> %s.%s"), *A.Node->GetName(), *PinA->PinName.ToString(), *B.Node->GetName(), *PinB->PinName.ToString()));
	if (bConverted)
	{
		Result->SetBoolField(TEXT("conversion_node"), true);
	}
	if (!Response.Message.IsEmpty())
	{
		Result->SetStringField(TEXT("note"), Response.Message.ToString());
	}
	return Ok(Result);
}

FString UUETKBlueprintLibrary::Disconnect(UBlueprint* Blueprint, const FString& Graph, const FString& FromNode, const FString& FromPin, const FString& ToNode, const FString& ToPin)
{
	FTarget A;
	if (!Resolve(Blueprint, Graph, FromNode, A))
	{
		return Fail(A.Error);
	}
	FString Error;
	UEdGraphPin* PinA = FindPin(A.Node, FromPin, EGPD_MAX, Error);
	if (!PinA)
	{
		return Fail(Error);
	}
	A.Graph->Modify();
	int32 Broken = 0;
	if (ToNode.IsEmpty())
	{
		Broken = PinA->LinkedTo.Num();
		Schema()->BreakPinLinks(*PinA, true);
	}
	else
	{
		FTarget B;
		if (!Resolve(Blueprint, Graph, ToNode, B))
		{
			return Fail(B.Error);
		}
		UEdGraphPin* PinB = FindPin(B.Node, ToPin, EGPD_MAX, Error);
		if (!PinB)
		{
			return Fail(Error);
		}
		if (PinA->LinkedTo.Contains(PinB))
		{
			Schema()->BreakSinglePinLink(PinA, PinB);
			Broken = 1;
		}
	}
	FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
	FJson Result = MakeShared<FJsonObject>();
	Result->SetNumberField(TEXT("broken"), Broken);
	return Ok(Result);
}

FString UUETKBlueprintLibrary::SetPinDefault(UBlueprint* Blueprint, const FString& Graph, const FString& Node, const FString& Pin, const FString& Value)
{
	FTarget T;
	if (!Resolve(Blueprint, Graph, Node, T))
	{
		return Fail(T.Error);
	}
	FString Error;
	UEdGraphPin* Target = FindPin(T.Node, Pin, EGPD_Input, Error);
	if (!Target)
	{
		return Fail(Error);
	}
	if (Target->Direction != EGPD_Input || Target->LinkedTo.Num() > 0)
	{
		return Fail(FString::Printf(TEXT("%s.%s is %s: only unconnected inputs hold a literal"), *Node, *Pin,
			Target->Direction != EGPD_Input ? TEXT("an output") : TEXT("connected")));
	}
	T.Graph->Modify();
	const FName Category = Target->PinType.PinCategory;
	const bool bObject = Category == UEdGraphSchema_K2::PC_Object || Category == UEdGraphSchema_K2::PC_Class
		|| Category == UEdGraphSchema_K2::PC_SoftObject || Category == UEdGraphSchema_K2::PC_SoftClass;
	if (bObject)
	{
		UObject* Object = nullptr;
		if (!Value.IsEmpty() && !Value.Equals(TEXT("None"), ESearchCase::IgnoreCase))
		{
			Object = (Category == UEdGraphSchema_K2::PC_Class || Category == UEdGraphSchema_K2::PC_SoftClass)
				? static_cast<UObject*>(ResolveClass(Value)) : StaticLoadObject(UObject::StaticClass(), nullptr, *Value);
			if (!Object)
			{
				return Fail(FString::Printf(TEXT("can't load '%s'"), *Value));
			}
		}
		Schema()->TrySetDefaultObject(*Target, Object);
	}
	else
	{
		const FString Invalid = Schema()->IsPinDefaultValid(Target, Value, nullptr, FText::GetEmpty());
		if (!Invalid.IsEmpty())
		{
			return Fail(FString::Printf(TEXT("'%s' is not valid for %s.%s (%s): %s"), *Value, *Node, *Pin, *PinTypeText(Target->PinType), *Invalid));
		}
		Schema()->TrySetDefaultValue(*Target, Value);
	}
	FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("value"), Target->GetDefaultAsString());
	return Ok(Result);
}

FString UUETKBlueprintLibrary::RemoveNode(UBlueprint* Blueprint, const FString& Graph, const FString& Node)
{
	FTarget T;
	if (!Resolve(Blueprint, Graph, Node, T))
	{
		return Fail(T.Error);
	}
	if (!T.Node->CanUserDeleteNode())
	{
		return Fail(FString::Printf(TEXT("%s can't be deleted (a function's entry node?)"), *Node));
	}
	UEdGraphNode* Removed = T.Node;
	FBlueprintEditorUtils::RemoveNode(Blueprint, Removed, true);
	// A removed node stays outered to the graph until garbage collection, so exports (bpdump) still list it; move it
	// out of the Blueprint now.
	if (IsValid(Removed) && Removed->GetOuter() == T.Graph)
	{
		Removed->Rename(nullptr, GetTransientPackage(), REN_DontCreateRedirectors | REN_DoNotDirty | REN_NonTransactional);
	}
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
	return Ok();
}

FString UUETKBlueprintLibrary::MoveNode(UBlueprint* Blueprint, const FString& Graph, const FString& Node, int32 X, int32 Y)
{
	FTarget T;
	if (!Resolve(Blueprint, Graph, Node, T))
	{
		return Fail(T.Error);
	}
	T.Node->Modify();
	T.Node->NodePosX = X;
	T.Node->NodePosY = Y;
	FBlueprintEditorUtils::MarkBlueprintAsModified(Blueprint);
	return Ok();
}

FString UUETKBlueprintLibrary::AddVariable(UBlueprint* Blueprint, const FString& Name, const FString& Type, const FString& DefaultValue,
	const FString& Category, bool bEditable, bool bReplicated, bool bExposeOnSpawn)
{
	if (!Blueprint)
	{
		return Fail(TEXT("Blueprint is null"));
	}
	FEdGraphPinType PinType;
	FString Error;
	if (!ParsePinType(Type, PinType, Error))
	{
		return Fail(Error);
	}
	const FName VarName(*Name);
	if (FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VarName) != INDEX_NONE)
	{
		return Fail(FString::Printf(TEXT("%s already has a variable '%s'"), *Blueprint->GetName(), *Name));
	}
	if (!FBlueprintEditorUtils::AddMemberVariable(Blueprint, VarName, PinType, DefaultValue))
	{
		return Fail(FString::Printf(TEXT("could not add variable '%s' (name taken by a function or component?)"), *Name));
	}
	if (!Category.IsEmpty())
	{
		FBlueprintEditorUtils::SetBlueprintVariableCategory(Blueprint, VarName, nullptr, FText::FromString(Category), true);
	}
	if (bEditable)
	{
		FBlueprintEditorUtils::SetBlueprintOnlyEditableFlag(Blueprint, VarName, false);
	}
	if (bExposeOnSpawn)
	{
		FBlueprintEditorUtils::SetBlueprintVariableMetaData(Blueprint, VarName, nullptr, FBlueprintMetadata::MD_ExposeOnSpawn, TEXT("true"));
	}
	if (bReplicated)
	{
		const int32 Index = FBlueprintEditorUtils::FindNewVariableIndex(Blueprint, VarName);
		Blueprint->NewVariables[Index].PropertyFlags |= CPF_Net;
	}
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("variable"), Name);
	Result->SetStringField(TEXT("type"), PinTypeText(PinType));
	return Ok(Result);
}

FString UUETKBlueprintLibrary::AddFunction(UBlueprint* Blueprint, const FString& Name, const FString& InputsJson, const FString& OutputsJson, bool bPure)
{
	if (!Blueprint)
	{
		return Fail(TEXT("Blueprint is null"));
	}
	TSharedPtr<FJsonObject> Inputs = Parse(InputsJson), Outputs = Parse(OutputsJson);
	if (!Inputs || !Outputs)
	{
		return Fail(TEXT("inputs/outputs must be JSON objects {name: type}"));
	}
	FString Error;
	if (FindGraph(Blueprint, Name, Error))
	{
		return Fail(FString::Printf(TEXT("%s already has a graph '%s'"), *Blueprint->GetName(), *Name));
	}
	UEdGraph* Graph = FBlueprintEditorUtils::CreateNewGraph(Blueprint, FName(*Name), UEdGraph::StaticClass(), UEdGraphSchema_K2::StaticClass());
	FBlueprintEditorUtils::AddFunctionGraph<UClass>(Blueprint, Graph, true, nullptr);
	UK2Node_FunctionEntry* Entry = nullptr;
	for (UEdGraphNode* Node : Graph->Nodes)
	{
		Entry = Entry ? Entry : Cast<UK2Node_FunctionEntry>(Node);
	}
	if (!Entry)
	{
		return Fail(TEXT("the new function graph has no entry node"));
	}
	for (const TPair<FString, TSharedPtr<FJsonValue>>& Param : Inputs->Values)
	{
		FEdGraphPinType Type;
		if (!ParsePinType(Param.Value->AsString(), Type, Error))
		{
			return Fail(FString::Printf(TEXT("input %s: %s"), *Param.Key, *Error));
		}
		Entry->CreateUserDefinedPin(FName(*Param.Key), Type, EGPD_Output);
	}
	UK2Node_FunctionResult* ResultNode = nullptr;
	if (Outputs->Values.Num())
	{
		ResultNode = Create<UK2Node_FunctionResult>(Graph, Entry->NodePosX + 600, Entry->NodePosY, [](UK2Node_FunctionResult*) {});
		for (const TPair<FString, TSharedPtr<FJsonValue>>& Param : Outputs->Values)
		{
			FEdGraphPinType Type;
			if (!ParsePinType(Param.Value->AsString(), Type, Error))
			{
				return Fail(FString::Printf(TEXT("output %s: %s"), *Param.Key, *Error));
			}
			ResultNode->CreateUserDefinedPin(FName(*Param.Key), Type, EGPD_Input);
		}
		if (!bPure)
		{
			UEdGraphPin* Then = Entry->FindPin(UEdGraphSchema_K2::PN_Then);
			UEdGraphPin* Execute = ResultNode->FindPin(UEdGraphSchema_K2::PN_Execute);
			if (Then && Execute)
			{
				Schema()->TryCreateConnection(Then, Execute);
			}
		}
	}
	if (bPure)
	{
		Entry->AddExtraFlags(FUNC_BlueprintPure);
	}
	FBlueprintEditorUtils::MarkBlueprintAsStructurallyModified(Blueprint);
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("graph"), Graph->GetName());
	Result->SetStringField(TEXT("entry"), Entry->GetName());
	Result->SetStringField(TEXT("result"), ResultNode ? ResultNode->GetName() : FString());
	return Ok(Result);
}

FString UUETKBlueprintLibrary::Compile(UBlueprint* Blueprint)
{
	if (!Blueprint)
	{
		return Fail(TEXT("Blueprint is null"));
	}
	FCompilerResultsLog Results;
	Results.SetSourcePath(Blueprint->GetPathName());
	Results.bSilentMode = true;
	FKismetEditorUtilities::CompileBlueprint(Blueprint, EBlueprintCompileOptions::None, &Results);
	TArray<TSharedPtr<FJsonValue>> Messages;
	for (const TSharedRef<FTokenizedMessage>& Message : Results.Messages)
	{
		const EMessageSeverity::Type Severity = Message->GetSeverity();
		if (Severity == EMessageSeverity::Info)
		{
			continue;
		}
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("severity"), Severity == EMessageSeverity::Error ? TEXT("error")
			: Severity == EMessageSeverity::Warning || Severity == EMessageSeverity::PerformanceWarning ? TEXT("warning") : TEXT("note"));
		Item->SetStringField(TEXT("message"), Message->ToText().ToString());
		Messages.Add(MakeShared<FJsonValueObject>(Item));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("status"), Blueprint->Status == BS_Error ? TEXT("error")
		: Blueprint->Status == BS_UpToDateWithWarnings ? TEXT("warnings") : TEXT("ok"));
	Result->SetArrayField(TEXT("messages"), Messages);
	return Ok(Result);
}
