#include "UETKMaterialLibrary.h"

#include "MaterialGraph/MaterialGraphNode.h"
#include "MaterialShared.h"
#include "Materials/Material.h"
#include "Materials/MaterialExpression.h"
#include "Materials/MaterialExpressionMaterialFunctionCall.h"
#include "Materials/MaterialFunction.h"
#include "Materials/MaterialInterface.h"
#include "Misc/EngineVersionComparison.h"
#include "RHI.h"
#include "SceneTypes.h"
#include "UETKJson.h"
#include "UObject/UnrealType.h"

namespace UETKMaterialPrivate
{
	using namespace UETKJson;

	// Editor bookkeeping, not part of what a node computes.
	const TSet<FName>& SkippedProperties()
	{
		static const TSet<FName> Names = {
			TEXT("MaterialExpressionEditorX"), TEXT("MaterialExpressionEditorY"), TEXT("MaterialExpressionGuid"),
			TEXT("Desc"), TEXT("bCollapsed"), TEXT("bRealtimePreview"), TEXT("bNeedToUpdatePreview"),
			TEXT("bIsParameterExpression"), TEXT("bShowOutputNameOnPin"), TEXT("bShowMaskColorsOnPin"),
			TEXT("bHidePreviewWindow"), TEXT("bShowTextureInputPin"), TEXT("bCommentBubbleVisible"),
			TEXT("ExpressionGUID"), TEXT("Outputs"), TEXT("Function"), TEXT("Material"),
		};
		return Names;
	}

	bool IsInputStruct(const UStruct* Struct)
	{
		for (const UStruct* It = Struct; It; It = It->GetSuperStruct())
		{
			const FString Name = It->GetName();
			if (Name == TEXT("ExpressionInput") || Name.EndsWith(TEXT("MaterialInput")))
			{
				return true;
			}
		}
		return false;
	}

	/** Pins are reported through "inputs"; properties holding them (directly or in arrays of structs) are skipped. */
	bool HoldsInputs(const FProperty* Property, int32 Depth = 0)
	{
		if (Depth > 3)
		{
			return false;
		}
		if (const FArrayProperty* Array = CastField<FArrayProperty>(Property))
		{
			return HoldsInputs(Array->Inner, Depth + 1);
		}
		if (const FStructProperty* StructProp = CastField<FStructProperty>(Property))
		{
			if (IsInputStruct(StructProp->Struct))
			{
				return true;
			}
			for (TFieldIterator<FProperty> It(StructProp->Struct); It; ++It)
			{
				if (HoldsInputs(*It, Depth + 1))
				{
					return true;
				}
			}
		}
		return false;
	}

	TSharedPtr<FJsonValue> ValueToJson(const FProperty* Property, const void* Value, UObject* Owner)
	{
		if (const FBoolProperty* Bool = CastField<FBoolProperty>(Property))
		{
			return MakeShared<FJsonValueBoolean>(Bool->GetPropertyValue(Value));
		}
		if (const FEnumProperty* EnumProp = CastField<FEnumProperty>(Property))
		{
			const int64 V = EnumProp->GetUnderlyingProperty()->GetSignedIntPropertyValue(Value);
			return MakeShared<FJsonValueString>(EnumProp->GetEnum()->GetNameStringByValue(V));
		}
		if (const FNumericProperty* Numeric = CastField<FNumericProperty>(Property))
		{
			if (const UEnum* Enum = Numeric->GetIntPropertyEnum())
			{
				return MakeShared<FJsonValueString>(Enum->GetNameStringByValue(Numeric->GetSignedIntPropertyValue(Value)));
			}
			if (Numeric->IsFloatingPoint())
			{
				return MakeShared<FJsonValueNumber>(Numeric->GetFloatingPointPropertyValue(Value));
			}
			return MakeShared<FJsonValueNumber>(static_cast<double>(Numeric->GetSignedIntPropertyValue(Value)));
		}
		if (const FObjectProperty* Object = CastField<FObjectProperty>(Property))
		{
			const UObject* Target = Object->GetObjectPropertyValue(Value);
			return Target ? StaticCastSharedRef<FJsonValue>(MakeShared<FJsonValueString>(Target->GetPathName()))
			              : StaticCastSharedRef<FJsonValue>(MakeShared<FJsonValueNull>());
		}
		if (const FStrProperty* Str = CastField<FStrProperty>(Property))
		{
			return MakeShared<FJsonValueString>(Str->GetPropertyValue(Value));
		}
		if (const FNameProperty* Name = CastField<FNameProperty>(Property))
		{
			return MakeShared<FJsonValueString>(Name->GetPropertyValue(Value).ToString());
		}
		if (const FTextProperty* Text = CastField<FTextProperty>(Property))
		{
			return MakeShared<FJsonValueString>(Text->GetPropertyValue(Value).ToString());
		}
		// Colours and vectors (every member numeric, at most four): a list of numbers.
		if (const FStructProperty* StructProp = CastField<FStructProperty>(Property))
		{
			TArray<TSharedPtr<FJsonValue>> Numbers;
			bool bAllNumeric = true;
			for (TFieldIterator<FProperty> It(StructProp->Struct); It; ++It)
			{
				const FNumericProperty* Member = CastField<FNumericProperty>(*It);
				if (!Member || Member->GetIntPropertyEnum() || Numbers.Num() >= 4)
				{
					bAllNumeric = false;
					break;
				}
				const void* MemberValue = Member->ContainerPtrToValuePtr<void>(Value);
				Numbers.Add(MakeShared<FJsonValueNumber>(Member->IsFloatingPoint()
					? Member->GetFloatingPointPropertyValue(MemberValue)
					: static_cast<double>(Member->GetSignedIntPropertyValue(MemberValue))));
			}
			if (bAllNumeric && Numbers.Num() >= 2)
			{
				return MakeShared<FJsonValueArray>(Numbers);
			}
		}
		FString Exported;
		Property->ExportTextItem_Direct(Exported, Value, nullptr, Owner, PPF_None);
		return MakeShared<FJsonValueString>(Exported);
	}

	/** Editable properties that differ from the class defaults (pins and editor bookkeeping left out). */
	FJson NonDefaultProps(UObject* Object)
	{
		FJson Props = MakeShared<FJsonObject>();
		const UObject* Defaults = Object->GetClass()->GetDefaultObject();
		for (TFieldIterator<FProperty> It(Object->GetClass()); It; ++It)
		{
			const FProperty* Property = *It;
			if (!Property->HasAnyPropertyFlags(CPF_Edit) || Property->HasAnyPropertyFlags(CPF_Deprecated | CPF_Transient)
				|| SkippedProperties().Contains(Property->GetFName()) || HoldsInputs(Property))
			{
				continue;
			}
			if (Property->Identical_InContainer(Object, Defaults))
			{
				continue;
			}
			Props->SetField(Property->GetName(), ValueToJson(Property, Property->ContainerPtrToValuePtr<void>(Object), Object));
		}
		return Props;
	}

	FString InputName(UMaterialExpression* Expression, int32 Index)
	{
		FName Name;
		if (UMaterialExpressionMaterialFunctionCall* Call = Cast<UMaterialExpressionMaterialFunctionCall>(Expression))
		{
			Name = Call->GetInputNameWithType(Index, false);
		}
		else
		{
			// The names MaterialEditingLibrary.connect_material_expressions takes ("Coordinates" -> "UVs").
			Name = UMaterialGraphNode::GetShortenPinName(Expression->GetInputName(Index));
		}
		return Name.IsNone() ? FString() : Name.ToString();
	}

	FJson DescribeNode(UMaterialExpression* Expression)
	{
		FJson Node = MakeShared<FJsonObject>();
		FString Class = Expression->GetClass()->GetName();
		Class.RemoveFromStart(TEXT("MaterialExpression"));
		Node->SetStringField(TEXT("id"), Expression->GetName());
		Node->SetStringField(TEXT("class"), Class);
		Node->SetNumberField(TEXT("x"), Expression->MaterialExpressionEditorX);
		Node->SetNumberField(TEXT("y"), Expression->MaterialExpressionEditorY);
		Node->SetStringField(TEXT("desc"), Expression->Desc);

		TArray<TSharedPtr<FJsonValue>> Inputs;
		for (FExpressionInputIterator It{Expression}; It; ++It)
		{
			FJson Input = MakeShared<FJsonObject>();
			Input->SetStringField(TEXT("name"), InputName(Expression, It.Index));
			if (It->Expression)
			{
				Input->SetStringField(TEXT("node"), It->Expression->GetName());
			}
			else
			{
				Input->SetField(TEXT("node"), MakeShared<FJsonValueNull>());
			}
			Input->SetNumberField(TEXT("output"), It->OutputIndex);
			Inputs.Add(MakeShared<FJsonValueObject>(Input));
		}
		Node->SetArrayField(TEXT("inputs"), Inputs);

		TArray<TSharedPtr<FJsonValue>> Outputs;
		for (const FExpressionOutput& Output : Expression->GetOutputs())
		{
			FJson Out = MakeShared<FJsonObject>();
			Out->SetStringField(TEXT("name"), Output.OutputName.IsNone() ? FString() : Output.OutputName.ToString());
			FString Mask;
			if (Output.Mask)
			{
				Mask += Output.MaskR ? TEXT("R") : TEXT("");
				Mask += Output.MaskG ? TEXT("G") : TEXT("");
				Mask += Output.MaskB ? TEXT("B") : TEXT("");
				Mask += Output.MaskA ? TEXT("A") : TEXT("");
			}
			Out->SetStringField(TEXT("mask"), Mask);
			Outputs.Add(MakeShared<FJsonValueObject>(Out));
		}
		Node->SetArrayField(TEXT("outputs"), Outputs);

		Node->SetObjectField(TEXT("props"), NonDefaultProps(Expression));
		return Node;
	}

	UEnum* PropertyEnum()
	{
		return StaticEnum<EMaterialProperty>();
	}

	/** "BaseColor" from MP_BaseColor. */
	FString PropertyName(int32 Value)
	{
		FString Name = PropertyEnum()->GetNameStringByValue(Value);
		Name.RemoveFromStart(TEXT("MP_"));
		return Name;
	}

	int32 FindProperty(const FString& Wanted)
	{
		const FString Key = Norm(Wanted);
		for (int32 Value = 0; Value < MP_MAX; ++Value)
		{
			if (Norm(PropertyName(Value)) == Key)
			{
				return Value;
			}
		}
		return INDEX_NONE;
	}
}

FString UUETKMaterialLibrary::DescribeGraph(UObject* MaterialOrFunction)
{
	using namespace UETKMaterialPrivate;
	UMaterial* Material = Cast<UMaterial>(MaterialOrFunction);
	UMaterialFunction* Function = Cast<UMaterialFunction>(MaterialOrFunction);
	if (!Material && !Function)
	{
		return Fail(FString::Printf(TEXT("not a material or material function: %s"),
			MaterialOrFunction ? *MaterialOrFunction->GetPathName() : TEXT("null")));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("kind"), Material ? TEXT("material") : TEXT("function"));
	Result->SetStringField(TEXT("path"), MaterialOrFunction->GetPathName());
	Result->SetObjectField(TEXT("props"), NonDefaultProps(MaterialOrFunction));

	TArray<TSharedPtr<FJsonValue>> Nodes;
	for (UMaterialExpression* Expression : Material ? Material->GetExpressions() : Function->GetExpressions())
	{
		if (Expression)
		{
			Nodes.Add(MakeShared<FJsonValueObject>(DescribeNode(Expression)));
		}
	}
	Result->SetArrayField(TEXT("nodes"), Nodes);

	if (Material)
	{
		TArray<TSharedPtr<FJsonValue>> Outputs;
		for (int32 Value = 0; Value < MP_MAX; ++Value)
		{
			const FExpressionInput* Input = Material->GetExpressionInputForProperty(static_cast<EMaterialProperty>(Value));
			if (Input && Input->Expression)
			{
				FJson Out = MakeShared<FJsonObject>();
				Out->SetStringField(TEXT("property"), PropertyName(Value));
				Out->SetStringField(TEXT("node"), Input->Expression->GetName());
				Out->SetNumberField(TEXT("output"), Input->OutputIndex);
				Outputs.Add(MakeShared<FJsonValueObject>(Out));
			}
		}
		Result->SetArrayField(TEXT("outputs"), Outputs);
	}
	return Ok(Result);
}

FString UUETKMaterialLibrary::CompileResult(UMaterialInterface* Material, bool bWait)
{
	using namespace UETKMaterialPrivate;
	if (!Material)
	{
		return Fail(TEXT("Material is null"));
	}
#if UE_VERSION_OLDER_THAN(5, 7, 0)
	FMaterialResource* Resource = Material->GetMaterialResource(GMaxRHIFeatureLevel);
#else
	FMaterialResource* Resource = Material->GetMaterialResource(GMaxRHIShaderPlatform);
#endif
	FJson Result = MakeShared<FJsonObject>();
	if (!Resource)
	{
		const TArray<TSharedPtr<FJsonValue>> None;
		Result->SetArrayField(TEXT("errors"), None);
		Result->SetArrayField(TEXT("error_nodes"), None);
		Result->SetBoolField(TEXT("finished"), false);
		Result->SetStringField(TEXT("note"), TEXT("no shader map for the editor's platform yet"));
		return Ok(Result);
	}
	if (bWait)
	{
		Resource->FinishCompilation();
	}
	TArray<TSharedPtr<FJsonValue>> Errors;
	for (const FString& Error : Resource->GetCompileErrors())
	{
		Errors.Add(MakeShared<FJsonValueString>(Error));
	}
	TArray<TSharedPtr<FJsonValue>> ErrorNodes;
	for (const UMaterialExpression* Expression : Resource->GetErrorExpressions())
	{
		if (Expression)
		{
			ErrorNodes.Add(MakeShared<FJsonValueString>(Expression->GetName()));
		}
	}
	Result->SetArrayField(TEXT("errors"), Errors);
	Result->SetArrayField(TEXT("error_nodes"), ErrorNodes);
	Result->SetBoolField(TEXT("finished"), Resource->IsCompilationFinished());
	return Ok(Result);
}

FString UUETKMaterialLibrary::ConnectProperty(UMaterialInterface* MaterialInterface, UMaterialExpression* From, int32 OutputIndex, const FString& Property)
{
	using namespace UETKMaterialPrivate;
	UMaterial* Material = Cast<UMaterial>(MaterialInterface);
	if (!Material)
	{
		return Fail(TEXT("material outputs belong to a Material (not an instance or function)"));
	}
	const int32 Value = FindProperty(Property);
	FExpressionInput* Input = Value == INDEX_NONE ? nullptr : Material->GetExpressionInputForProperty(static_cast<EMaterialProperty>(Value));
	if (!Input)
	{
		TArray<FString> Names;
		for (int32 V = 0; V < MP_MAX; ++V)
		{
			if (Material->GetExpressionInputForProperty(static_cast<EMaterialProperty>(V)))
			{
				Names.Add(PropertyName(V));
			}
		}
		return Fail(FString::Printf(TEXT("no material output '%s'; outputs: %s"), *Property, *FString::Join(Names, TEXT(", "))));
	}
	if (From && !From->GetOutputs().IsValidIndex(OutputIndex))
	{
		return Fail(FString::Printf(TEXT("%s has no output %d"), *From->GetName(), OutputIndex));
	}
	Material->Modify();
	if (From)
	{
		Input->Connect(OutputIndex, From);
	}
	else
	{
		Input->Expression = nullptr;
		Input->OutputIndex = 0;
	}
	Material->MarkPackageDirty();
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("property"), PropertyName(Value));
	return Ok(Result);
}

TArray<UMaterialExpression*> UUETKMaterialLibrary::ListExpressions(UObject* MaterialOrFunction)
{
	TArray<UMaterialExpression*> Out;
	if (UMaterial* Material = Cast<UMaterial>(MaterialOrFunction))
	{
		for (UMaterialExpression* Expression : Material->GetExpressions())
		{
			Out.Add(Expression);
		}
	}
	else if (UMaterialFunction* Function = Cast<UMaterialFunction>(MaterialOrFunction))
	{
		for (UMaterialExpression* Expression : Function->GetExpressions())
		{
			Out.Add(Expression);
		}
	}
	return Out;
}

TArray<FString> UUETKMaterialLibrary::ExpressionOutputNames(UMaterialExpression* Expression)
{
	TArray<FString> Names;
	if (!Expression)
	{
		return Names;
	}
	for (const FExpressionOutput& Output : Expression->GetOutputs())
	{
		FString Name;
		if (!Output.OutputName.IsNone())
		{
			Name = Output.OutputName.ToString();
		}
		else if (Output.Mask && (Output.MaskR + Output.MaskG + Output.MaskB + Output.MaskA) == 1)
		{
			Name = Output.MaskR ? TEXT("R") : Output.MaskG ? TEXT("G") : Output.MaskB ? TEXT("B") : TEXT("A");
		}
		Names.Add(Name);
	}
	return Names;
}

FString UUETKMaterialLibrary::SetPropertyText(UObject* Object, const FString& Property, const FString& Text)
{
	using namespace UETKMaterialPrivate;
	if (!Object)
	{
		return Fail(TEXT("Object is null"));
	}
	FProperty* Found = FindFProperty<FProperty>(Object->GetClass(), *Property);
	if (!Found)
	{
		const FString Key = Norm(Property);
		for (TFieldIterator<FProperty> It(Object->GetClass()); It; ++It)
		{
			FString Name = It->GetName();
			if (CastField<FBoolProperty>(*It) && Name.Len() > 1 && Name[0] == TEXT('b') && FChar::IsUpper(Name[1]))
			{
				Name.RightChopInline(1);
			}
			if (Norm(Name) == Key || Norm(It->GetName()) == Key)
			{
				Found = *It;
				break;
			}
		}
	}
	if (!Found)
	{
		return Fail(FString::Printf(TEXT("%s has no property '%s'"), *Object->GetClass()->GetName(), *Property));
	}
	Object->Modify();
	Object->PreEditChange(Found);
	const TCHAR* End = Found->ImportText_InContainer(*Text, Object, Object, PPF_None);
	FPropertyChangedEvent Event(Found, EPropertyChangeType::ValueSet);
	Object->PostEditChangeProperty(Event);
	if (!End)
	{
		return Fail(FString::Printf(TEXT("could not read '%s' as %s (%s)"), *Text, *Found->GetName(), *Found->GetCPPType()));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("property"), Found->GetName());
	return Ok(Result);
}
