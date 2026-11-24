// UnrealToolkit VFX: Niagara authoring through the Niagara editor's own stack view models.
#include "UETKVfxLibrary.h"

#include "UETKCapture.h"

#include "AdvancedPreviewScene.h"
#include "CanvasTypes.h"
#include "Components/SceneCaptureComponent2D.h"
#include "Components/StaticMeshComponent.h"
#include "Engine/StaticMesh.h"
#include "Materials/MaterialInstanceDynamic.h"
#include "Materials/MaterialInterface.h"
#include "Dom/JsonObject.h"
#include "Editor.h"
#include "Engine/Engine.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Engine/Texture2D.h"
#include "Engine/TextureRenderTarget2D.h"
#include "Misc/PackageName.h"
#include "HAL/FileManager.h"
#include "ImageCore.h"
#include "ImageUtils.h"
#include "Misc/EngineVersionComparison.h"
#include "Misc/Paths.h"
#if !UE_VERSION_OLDER_THAN(5, 7, 0)
#include "Misc/StringOutputDevice.h"  // 5.6 declares FStringOutputDevice in Containers/UnrealString.h
#endif
#include "NiagaraComponent.h"
#include "NiagaraDataInterface.h"
#include "NiagaraDataInterfaceCurveBase.h"
#include "NiagaraEditorUtilities.h"
#include "NiagaraEmitter.h"
#include "NiagaraEmitterHandle.h"
#include "NiagaraEmitterInstance.h"
#include "NiagaraNodeFunctionCall.h"
#include "NiagaraRendererProperties.h"
#include "NiagaraScript.h"
#include "NiagaraShared.h"
#include "NiagaraSystem.h"
#include "NiagaraSystemInstance.h"
#include "NiagaraSystemInstanceController.h"
#include "NiagaraTypes.h"
#include "NiagaraWorldManager.h"
#include "RenderingThread.h"
#include "ShaderCompiler.h"
#include "Serialization/JsonReader.h"
#include "Serialization/JsonSerializer.h"
#include "Serialization/JsonWriter.h"
#include "Subsystems/AssetEditorSubsystem.h"
#include "UObject/StructOnScope.h"
#include "ViewModels/NiagaraEmitterHandleViewModel.h"
#include "ViewModels/NiagaraSystemViewModel.h"
#include "ViewModels/Stack/NiagaraStackEntry.h"
#include "ViewModels/Stack/NiagaraStackFunctionInput.h"
#include "ViewModels/Stack/NiagaraStackGraphUtilities.h"
#include "ViewModels/Stack/NiagaraStackModuleItem.h"
#include "ViewModels/Stack/NiagaraStackScriptItemGroup.h"
#include "ViewModels/Stack/NiagaraStackViewModel.h"

namespace UETKVfxPrivate
{
	// ---------------------------------------------------------------- JSON results

	using FJson = TSharedRef<FJsonObject>;

	FString Serialize(const FJson& Object)
	{
		FString Out;
		const TSharedRef<TJsonWriter<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>> Writer =
			TJsonWriterFactory<TCHAR, TCondensedJsonPrintPolicy<TCHAR>>::Create(&Out);
		FJsonSerializer::Serialize(Object, Writer);
		return Out;
	}

	FString Ok(FJson Object = MakeShared<FJsonObject>())
	{
		Object->SetBoolField(TEXT("ok"), true);
		return Serialize(Object);
	}

	FString Fail(const FString& Error)
	{
		FJson Object = MakeShared<FJsonObject>();
		Object->SetBoolField(TEXT("ok"), false);
		Object->SetStringField(TEXT("error"), Error);
		return Serialize(Object);
	}

	/** Lower case without spaces, underscores or dashes: "Lifetime Min" == "lifetime_min" == "LifetimeMin". */
	FString Norm(const FString& In)
	{
		FString Out;
		Out.Reserve(In.Len());
		for (TCHAR C : In)
		{
			if (C != TEXT(' ') && C != TEXT('_') && C != TEXT('-'))
			{
				Out.AppendChar(FChar::ToLower(C));
			}
		}
		return Out;
	}

	// ---------------------------------------------------------------- editing sessions (one view model per system)

	TMap<TWeakObjectPtr<UNiagaraSystem>, TSharedPtr<FNiagaraSystemViewModel>>& Sessions()
	{
		static TMap<TWeakObjectPtr<UNiagaraSystem>, TSharedPtr<FNiagaraSystemViewModel>> Map;
		return Map;
	}

	TSharedPtr<FNiagaraSystemViewModel> ViewModelFor(UNiagaraSystem* System)
	{
		for (auto It = Sessions().CreateIterator(); It; ++It)
		{
			if (!It.Key().IsValid())
			{
				It.RemoveCurrent();
			}
		}
		if (TSharedPtr<FNiagaraSystemViewModel>* Found = Sessions().Find(System))
		{
			return *Found;
		}
		// An open Niagara editor keeps its own view model of the system; close it so the two can't disagree.
		if (GEditor)
		{
			if (UAssetEditorSubsystem* AssetEditors = GEditor->GetEditorSubsystem<UAssetEditorSubsystem>())
			{
				AssetEditors->CloseAllEditorsForAsset(System);
			}
		}
		TSharedPtr<FNiagaraSystemViewModel> ViewModel = MakeShared<FNiagaraSystemViewModel>();
		FNiagaraSystemViewModelOptions Options;
		Options.bCanAutoCompile = false;
		Options.bCanModifyEmittersFromTimeline = false;
		Options.bCanSimulate = false;
		Options.bCompileForEdit = false;
		Options.bIsForDataProcessingOnly = true;
		Options.EditMode = ENiagaraSystemViewModelEditMode::SystemAsset;
		// The stack's message subscriptions assert without a key; a new asset can lack a GUID, so fall back to a fresh one.
		Options.MessageLogGuid = System->GetAssetGuid().IsValid() ? System->GetAssetGuid() : FGuid::NewGuid();
		ViewModel->Initialize(*System, Options);
		Sessions().Add(System, ViewModel);
		return ViewModel;
	}

	// ---------------------------------------------------------------- stages

	struct FStageInfo
	{
		const TCHAR* Name;
		ENiagaraScriptUsage Usage;
		bool bSystem;
	};

	const FStageInfo GStages[] = {
		{TEXT("system_spawn"), ENiagaraScriptUsage::SystemSpawnScript, true},
		{TEXT("system_update"), ENiagaraScriptUsage::SystemUpdateScript, true},
		{TEXT("emitter_spawn"), ENiagaraScriptUsage::EmitterSpawnScript, false},
		{TEXT("emitter_update"), ENiagaraScriptUsage::EmitterUpdateScript, false},
		{TEXT("particle_spawn"), ENiagaraScriptUsage::ParticleSpawnScript, false},
		{TEXT("particle_update"), ENiagaraScriptUsage::ParticleUpdateScript, false},
	};

	const FStageInfo* FindStage(const FString& In)
	{
		for (const FStageInfo& Stage : GStages)
		{
			if (Norm(Stage.Name) == Norm(In))
			{
				return &Stage;
			}
		}
		return nullptr;
	}

	bool UsageMatches(ENiagaraScriptUsage GroupUsage, ENiagaraScriptUsage Wanted)
	{
		return GroupUsage == Wanted
			|| (Wanted == ENiagaraScriptUsage::ParticleSpawnScript && GroupUsage == ENiagaraScriptUsage::ParticleSpawnScriptInterpolated);
	}

	FString StageName(ENiagaraScriptUsage Usage)
	{
		for (const FStageInfo& Stage : GStages)
		{
			if (UsageMatches(Usage, Stage.Usage))
			{
				return Stage.Name;
			}
		}
		switch (Usage)
		{
		case ENiagaraScriptUsage::ParticleGPUComputeScript: return TEXT("gpu_compute");
		case ENiagaraScriptUsage::ParticleEventScript: return TEXT("event");
		case ENiagaraScriptUsage::ParticleSimulationStageScript: return TEXT("simulation_stage");
		default: return FString::Printf(TEXT("usage_%d"), static_cast<int32>(Usage));
		}
	}

	// ---------------------------------------------------------------- stack traversal

	template <typename T>
	void CollectOfType(UNiagaraStackEntry* Entry, TArray<T*>& Out)
	{
		TArray<UNiagaraStackEntry*> Children;
		Entry->GetUnfilteredChildren(Children);
		for (UNiagaraStackEntry* Child : Children)
		{
			if (T* Match = Cast<T>(Child))
			{
				Out.Add(Match);
				continue;
			}
			CollectOfType(Child, Out);
		}
	}

	TSharedPtr<FNiagaraEmitterHandleViewModel> FindEmitter(FNiagaraSystemViewModel& ViewModel, const FString& Name, FString& Error)
	{
		TArray<FString> Names;
		for (const TSharedRef<FNiagaraEmitterHandleViewModel>& Handle : ViewModel.GetEmitterHandleViewModels())
		{
			Names.Add(Handle->GetName().ToString());
			if (Norm(Handle->GetName().ToString()) == Norm(Name))
			{
				return Handle;
			}
		}
		Error = FString::Printf(TEXT("no emitter '%s' (emitters: %s)"), *Name, Names.Num() ? *FString::Join(Names, TEXT(", ")) : TEXT("none"));
		return nullptr;
	}

	FString ModuleLabel(UNiagaraStackModuleItem* Module)
	{
		return Module->GetDisplayName().ToString();
	}

	struct FInputHit
	{
		UNiagaraStackFunctionInput* Input = nullptr;
		TArray<TPair<FString, FString>> Segments;  // (handle name, display name) per nesting level
		FString Path;
	};

	FString InputName(UNiagaraStackFunctionInput* Input)
	{
		return Input->GetInputParameterHandle().GetName().ToString();
	}

	void CollectInputs(UNiagaraStackEntry* Entry, const FInputHit& Parent, TArray<FInputHit>& Out)
	{
		TArray<UNiagaraStackEntry*> Children;
		Entry->GetUnfilteredChildren(Children);
		for (UNiagaraStackEntry* Child : Children)
		{
			if (UNiagaraStackFunctionInput* Input = Cast<UNiagaraStackFunctionInput>(Child))
			{
				FInputHit Hit;
				Hit.Input = Input;
				Hit.Segments = Parent.Segments;
				Hit.Segments.Emplace(InputName(Input), Input->GetDisplayName().ToString());
				Hit.Path = Parent.Path.IsEmpty() ? InputName(Input) : Parent.Path + TEXT("/") + InputName(Input);
				Out.Add(Hit);
				CollectInputs(Input, Hit, Out);
			}
			else if (!Child->IsA<UNiagaraStackModuleItem>())
			{
				CollectInputs(Child, Parent, Out);
			}
		}
	}

	/** Everything an input edit needs, resolved from the string address. */
	struct FTarget
	{
		TSharedPtr<FNiagaraSystemViewModel> ViewModel;
		TSharedPtr<FNiagaraEmitterHandleViewModel> Emitter;
		const FStageInfo* Stage = nullptr;
		UNiagaraStackScriptItemGroup* Group = nullptr;
		UNiagaraStackModuleItem* Module = nullptr;
		UNiagaraStackFunctionInput* Input = nullptr;
		FString InputPath;
		FString Error;
	};

	bool ResolveGroup(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, FTarget& T)
	{
		if (!System)
		{
			T.Error = TEXT("System is null");
			return false;
		}
		T.Stage = FindStage(Stage);
		if (!T.Stage)
		{
			T.Error = FString::Printf(TEXT("unknown stage '%s' (system_spawn, system_update, emitter_spawn, emitter_update, particle_spawn, particle_update)"), *Stage);
			return false;
		}
		T.ViewModel = ViewModelFor(System);
		UNiagaraStackEntry* Root = nullptr;
		if (T.Stage->bSystem)
		{
			if (!Emitter.IsEmpty())
			{
				T.Error = FString::Printf(TEXT("stage %s belongs to the system: pass Emitter \"\""), T.Stage->Name);
				return false;
			}
			Root = T.ViewModel->GetSystemStackViewModel()->GetRootEntry();
		}
		else
		{
			T.Emitter = FindEmitter(*T.ViewModel, Emitter, T.Error);
			if (!T.Emitter)
			{
				return false;
			}
			Root = T.Emitter->GetEmitterStackViewModel()->GetRootEntry();
		}
		if (!Root)
		{
			T.Error = TEXT("no stack for that emitter");
			return false;
		}
		TArray<UNiagaraStackScriptItemGroup*> Groups;
		CollectOfType(Root, Groups);
		for (UNiagaraStackScriptItemGroup* Group : Groups)
		{
			if (UsageMatches(Group->GetScriptUsage(), T.Stage->Usage))
			{
				T.Group = Group;
				Group->RefreshChildren();
				return true;
			}
		}
		T.Error = FString::Printf(TEXT("stage %s not found in the stack"), T.Stage->Name);
		return false;
	}

	bool ResolveModule(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, FTarget& T)
	{
		if (!ResolveGroup(System, Emitter, Stage, T))
		{
			return false;
		}
		FString Base = Module;
		int32 Nth = 1;
		FString Left, Right;
		if (Module.Split(TEXT("#"), &Left, &Right, ESearchCase::CaseSensitive, ESearchDir::FromEnd) && Right.IsNumeric())
		{
			Base = Left;
			Nth = FMath::Max(1, FCString::Atoi(*Right));
		}
		TArray<UNiagaraStackModuleItem*> Modules;
		CollectOfType(T.Group, Modules);
		TArray<FString> Names;
		int32 Seen = 0;
		for (UNiagaraStackModuleItem* Item : Modules)
		{
			UNiagaraNodeFunctionCall& Node = Item->GetModuleNode();
			Names.Add(ModuleLabel(Item));
			const FString Wanted = Norm(Base);
			const bool bMatch = Norm(ModuleLabel(Item)) == Wanted || Norm(Node.GetFunctionName()) == Wanted
				|| (Node.FunctionScript && Norm(Node.FunctionScript->GetName()) == Wanted);
			if (bMatch && ++Seen == Nth)
			{
				T.Module = Item;
				return true;
			}
		}
		T.Error = FString::Printf(TEXT("no module '%s' in %s (modules: %s)"), *Module, T.Stage->Name,
			Names.Num() ? *FString::Join(Names, TEXT(", ")) : TEXT("none"));
		return false;
	}

	bool ResolveInput(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, FTarget& T)
	{
		if (!ResolveModule(System, Emitter, Stage, Module, T))
		{
			return false;
		}
		T.Module->RefreshChildren();
		TArray<FInputHit> Hits;
		CollectInputs(T.Module, FInputHit(), Hits);
		// The stack nests inputs under the switch that reveals them ("Lifetime Mode/Lifetime") and under dynamic
		// inputs ("Lifetime/Minimum"), and some names contain '/' themselves ("Box / Plane Mode"). So compare whole
		// normalized paths: an exact match wins; otherwise the path must end with "/<wanted>" for exactly one
		// input (or one shallowest input), so "Lifetime" finds "Lifetime Mode/Lifetime".
		const FString Wanted = Norm(InputPath);
		auto JoinedPaths = [](const FInputHit& Hit)
		{
			TArray<FString> Keys, Displays;
			for (const TPair<FString, FString>& Segment : Hit.Segments)
			{
				Keys.Add(Segment.Key);
				Displays.Add(Segment.Value);
			}
			return TPair<FString, FString>(Norm(FString::Join(Keys, TEXT("/"))), Norm(FString::Join(Displays, TEXT("/"))));
		};
		const FInputHit* Best = nullptr;
		int32 BestDepth = MAX_int32;
		bool bAmbiguous = false;
		TArray<FString> Candidates;
		for (const FInputHit& Hit : Hits)
		{
			const TPair<FString, FString> Paths = JoinedPaths(Hit);
			if (Paths.Key == Wanted || Paths.Value == Wanted)
			{
				Best = &Hit;  // exact full path wins
				bAmbiguous = false;
				break;
			}
			const FString Suffix = TEXT("/") + Wanted;
			if (!Paths.Key.EndsWith(Suffix) && !Paths.Value.EndsWith(Suffix))
			{
				continue;
			}
			Candidates.Add(Hit.Path);
			if (Hit.Segments.Num() < BestDepth)
			{
				Best = &Hit;
				BestDepth = Hit.Segments.Num();
				bAmbiguous = false;
			}
			else if (Hit.Segments.Num() == BestDepth)
			{
				bAmbiguous = true;
			}
		}
		if (Best && !bAmbiguous)
		{
			T.Input = Best->Input;
			T.InputPath = Best->Path;
			return true;
		}
		if (bAmbiguous)
		{
			T.Error = FString::Printf(TEXT("input '%s' of '%s' is ambiguous: %s"), *InputPath, *ModuleLabel(T.Module), *FString::Join(Candidates, TEXT(", ")));
			return false;
		}
		TArray<FString> Paths;
		for (const FInputHit& Hit : Hits)
		{
			Paths.Add(Hit.Path);
		}
		T.Error = FString::Printf(TEXT("module '%s' has no input '%s' (inputs: %s)"), *ModuleLabel(T.Module), *InputPath,
			Paths.Num() ? *FString::Join(Paths, TEXT(", ")) : TEXT("none"));
		return false;
	}

	/** Resolve an input and switch on its inline edit condition ("use this input" checkbox) if it has one that is off. */
	bool ResolveEditableInput(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, FTarget& T)
	{
		if (!ResolveInput(System, Emitter, Stage, Module, InputPath, T))
		{
			return false;
		}
		if (T.Input->GetHasEditCondition() && T.Input->GetShowEditConditionInline() && !T.Input->GetEditConditionEnabled())
		{
			T.Input->SetEditConditionEnabled(true);
			FTarget Fresh;
			if (!ResolveInput(System, Emitter, Stage, Module, InputPath, Fresh))
			{
				T.Error = Fresh.Error;
				return false;
			}
			T = Fresh;
		}
		return true;
	}

	/** Give an input a fresh data interface of Class. 5.6 takes an instance to copy and check()s that its class is
	 *  exactly the input's type, so other classes are refused there instead of crashing the editor. */
	bool SetDataInterfaceClass(UNiagaraStackFunctionInput* Input, UClass* Class, FString& Error)
	{
#if UE_VERSION_OLDER_THAN(5, 7, 0)
		if (Class != Input->GetInputType().GetClass())
		{
			Error = FString::Printf(TEXT("UE 5.6 only sets an input's own data interface type (%s)"), *Input->GetInputType().GetName());
			return false;
		}
		UNiagaraDataInterface* Fresh = NewObject<UNiagaraDataInterface>(GetTransientPackage(), Class, NAME_None, RF_Transient);
		Input->SetDataInterfaceValue(*Fresh);
#else
		Input->SetDataInterfaceValue(Class);
#endif
		return true;
	}

	// ---------------------------------------------------------------- values

	int64 ResolveEnum(const UEnum* Enum, const FString& Text)
	{
		const int32 Count = Enum->NumEnums() - (Enum->ContainsExistingMax() ? 1 : 0);
		for (int32 i = 0; i < Count; ++i)
		{
			const FString Short = Enum->GetNameStringByIndex(i);
			if (Norm(Short) == Norm(Text) || Norm(Enum->GetDisplayNameTextByIndex(i).ToString()) == Norm(Text))
			{
				return Enum->GetValueByIndex(i);
			}
		}
		if (Text.IsNumeric())
		{
			return FCString::Atoi64(*Text);
		}
		return INDEX_NONE;
	}

	FString EnumChoices(const UEnum* Enum)
	{
		TArray<FString> Names;
		const int32 Count = Enum->NumEnums() - (Enum->ContainsExistingMax() ? 1 : 0);
		for (int32 i = 0; i < Count; ++i)
		{
			Names.Add(Enum->GetDisplayNameTextByIndex(i).ToString());
		}
		return FString::Join(Names, TEXT(", "));
	}

	bool ParseBool(const FString& Text, bool& bOut)
	{
		const FString N = Norm(Text);
		if (N == TEXT("true") || N == TEXT("1") || N == TEXT("yes") || N == TEXT("on")) { bOut = true; return true; }
		if (N == TEXT("false") || N == TEXT("0") || N == TEXT("no") || N == TEXT("off")) { bOut = false; return true; }
		return false;
	}

	/** Parse text into the memory of a Niagara type: numbers, "1, 2, 3", bools, enum names or T3D struct text. */
	bool ParseValue(const FNiagaraTypeDefinition& Type, const FString& InText, uint8* Memory, FString& Error)
	{
		const FString Text = InText.TrimStartAndEnd();
		if (Type.IsEnum())
		{
			const int64 Value = ResolveEnum(Type.GetEnum(), Text);
			if (Value == INDEX_NONE)
			{
				Error = FString::Printf(TEXT("'%s' is not a value of %s (choices: %s)"), *Text, *Type.GetName(), *EnumChoices(Type.GetEnum()));
				return false;
			}
			reinterpret_cast<FNiagaraInt32*>(Memory)->Value = static_cast<int32>(Value);
			return true;
		}
		if (Type == FNiagaraTypeDefinition::GetBoolDef())
		{
			bool bValue = false;
			if (!ParseBool(Text, bValue))
			{
				Error = FString::Printf(TEXT("'%s' is not a bool"), *Text);
				return false;
			}
			reinterpret_cast<FNiagaraBool*>(Memory)->SetValue(bValue);
			return true;
		}
		const UScriptStruct* Struct = Type.GetScriptStruct();
		if (!Struct)
		{
			Error = FString::Printf(TEXT("type %s has no local value"), *Type.GetName());
			return false;
		}
		if (Text.StartsWith(TEXT("(")) && Text.Contains(TEXT("=")))
		{
			FStringOutputDevice Errors;
			if (!Struct->ImportText(*Text, Memory, nullptr, PPF_None, &Errors, Struct->GetName()) || !Errors.IsEmpty())
			{
				Error = FString::Printf(TEXT("could not parse '%s' as %s: %s"), *Text, *Type.GetName(), *Errors);
				return false;
			}
			return true;
		}
		TArray<FNumericProperty*> Fields;
		for (TFieldIterator<FProperty> It(Struct); It; ++It)
		{
			if (FNumericProperty* Numeric = CastField<FNumericProperty>(*It))
			{
				Fields.Add(Numeric);
			}
		}
		FString Clean = Text;
		for (const TCHAR* Strip : {TEXT("("), TEXT(")"), TEXT("["), TEXT("]")})
		{
			Clean.ReplaceInline(Strip, TEXT(" "));
		}
		Clean.ReplaceInline(TEXT(","), TEXT(" "));
		Clean.ReplaceInline(TEXT(";"), TEXT(" "));
		TArray<FString> Parts;
		Clean.ParseIntoArrayWS(Parts);
		if (Fields.Num() == 0 || Parts.Num() == 0 || (Parts.Num() != 1 && Parts.Num() != Fields.Num()))
		{
			Error = FString::Printf(TEXT("'%s' does not fit %s (%d component%s)"), *Text, *Type.GetName(), Fields.Num(), Fields.Num() == 1 ? TEXT("") : TEXT("s"));
			return false;
		}
		for (int32 i = 0; i < Fields.Num(); ++i)
		{
			const FString& Part = Parts[Parts.Num() == 1 ? 0 : i];
			if (!Part.IsNumeric())
			{
				Error = FString::Printf(TEXT("'%s' is not a number"), *Part);
				return false;
			}
			void* FieldMemory = Fields[i]->ContainerPtrToValuePtr<void>(Memory);
			if (Fields[i]->IsFloatingPoint())
			{
				Fields[i]->SetFloatingPointPropertyValue(FieldMemory, FCString::Atod(*Part));
			}
			else
			{
				Fields[i]->SetIntPropertyValue(FieldMemory, static_cast<int64>(FMath::RoundToDouble(FCString::Atod(*Part))));
			}
		}
		return true;
	}

	FString FormatNumber(double Value)
	{
		FString Text = FString::SanitizeFloat(Value, 0);
		return Text;
	}

	/** Text for a value of a Niagara type, in the form ParseValue accepts. */
	FString FormatValue(const FNiagaraTypeDefinition& Type, const uint8* Memory)
	{
		if (Type.IsEnum())
		{
			const int32 Value = reinterpret_cast<const FNiagaraInt32*>(Memory)->Value;
			const int32 Index = Type.GetEnum()->GetIndexByValue(Value);
			return Index == INDEX_NONE ? FString::FromInt(Value) : Type.GetEnum()->GetDisplayNameTextByIndex(Index).ToString();
		}
		if (Type == FNiagaraTypeDefinition::GetBoolDef())
		{
			return reinterpret_cast<const FNiagaraBool*>(Memory)->GetValue() ? TEXT("true") : TEXT("false");
		}
		const UScriptStruct* Struct = Type.GetScriptStruct();
		if (!Struct)
		{
			return TEXT("?");
		}
		TArray<FString> Parts;
		for (TFieldIterator<FProperty> It(Struct); It; ++It)
		{
			if (const FNumericProperty* Numeric = CastField<FNumericProperty>(*It))
			{
				const void* FieldMemory = Numeric->ContainerPtrToValuePtr<void>(Memory);
				Parts.Add(Numeric->IsFloatingPoint() ? FormatNumber(Numeric->GetFloatingPointPropertyValue(FieldMemory))
					: FString::Printf(TEXT("%lld"), Numeric->GetSignedIntPropertyValue(FieldMemory)));
			}
		}
		if (Parts.Num() == 0)
		{
			FString Exported;
			Struct->ExportText(Exported, Memory, nullptr, nullptr, PPF_None, nullptr);
			return Exported;
		}
		return FString::Join(Parts, TEXT(", "));
	}

	bool ParseTypeName(const FString& In, FNiagaraTypeDefinition& Out)
	{
		const FString N = Norm(In);
		if (N == TEXT("float")) { Out = FNiagaraTypeDefinition::GetFloatDef(); return true; }
		if (N == TEXT("int") || N == TEXT("int32")) { Out = FNiagaraTypeDefinition::GetIntDef(); return true; }
		if (N == TEXT("bool")) { Out = FNiagaraTypeDefinition::GetBoolDef(); return true; }
		if (N == TEXT("vec2") || N == TEXT("vector2") || N == TEXT("vector2d")) { Out = FNiagaraTypeDefinition::GetVec2Def(); return true; }
		if (N == TEXT("vec3") || N == TEXT("vector") || N == TEXT("vec")) { Out = FNiagaraTypeDefinition::GetVec3Def(); return true; }
		if (N == TEXT("vec4") || N == TEXT("vector4")) { Out = FNiagaraTypeDefinition::GetVec4Def(); return true; }
		if (N == TEXT("color") || N == TEXT("linearcolor")) { Out = FNiagaraTypeDefinition::GetColorDef(); return true; }
		if (N == TEXT("position")) { Out = FNiagaraTypeDefinition::GetPositionDef(); return true; }
		if (N == TEXT("quat")) { Out = FNiagaraTypeDefinition::GetQuatDef(); return true; }
		return false;
	}

	// ---------------------------------------------------------------- reflection helpers

	FProperty* FindPropertyLoose(const UStruct* Struct, const FString& Name)
	{
		if (FProperty* Exact = FindFProperty<FProperty>(Struct, *Name))
		{
			return Exact;
		}
		const FString Wanted = Norm(Name);
		for (TFieldIterator<FProperty> It(Struct); It; ++It)
		{
			const FString Have = Norm(It->GetName());
			if (Have == Wanted || (It->IsA<FBoolProperty>() && Have == TEXT("b") + Wanted))
			{
				return *It;
			}
		}
		return nullptr;
	}

	FString ExportProperty(const FProperty* Property, const void* ContainerMemory, UObject* Owner)
	{
		FString Text;
		Property->ExportTextItem_Direct(Text, Property->ContainerPtrToValuePtr<void>(ContainerMemory), nullptr, Owner, PPF_None);
		if (Text.Len() > 300)
		{
			Text = Text.Left(300) + TEXT("...");
		}
		return Text;
	}

	/** Edit-visible properties whose value differs from Defaults, as {name: text}. */
	TSharedRef<FJsonObject> DiffProperties(const UStruct* Struct, const void* Memory, const void* Defaults, UObject* Owner)
	{
		TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
		for (TFieldIterator<FProperty> It(Struct); It; ++It)
		{
			const FProperty* Property = *It;
			if (!Property->HasAnyPropertyFlags(CPF_Edit) || Property->HasAnyPropertyFlags(CPF_Deprecated | CPF_Transient))
			{
				continue;
			}
			if (Defaults && Property->Identical_InContainer(Memory, Defaults))
			{
				continue;
			}
			Out->SetStringField(Property->GetName(), ExportProperty(Property, Memory, Owner));
		}
		return Out;
	}

	const UScriptStruct* EmitterDataStruct()
	{
		static const UScriptStruct* Struct = FindObject<UScriptStruct>(nullptr, TEXT("/Script/Niagara.VersionedNiagaraEmitterData"));
		return Struct;
	}

	// ---------------------------------------------------------------- describe

	FString ValueModeName(UNiagaraStackFunctionInput::EValueMode Mode)
	{
		using EMode = UNiagaraStackFunctionInput::EValueMode;
		switch (Mode)
		{
		case EMode::Local: return TEXT("local");
		case EMode::Linked: return TEXT("linked");
		case EMode::Data: return TEXT("data");
		case EMode::ObjectAsset: return TEXT("object");
		case EMode::Expression: return TEXT("expression");
		case EMode::Dynamic: return TEXT("dynamic");
		case EMode::DefaultFunction: return TEXT("default_function");
		case EMode::UnsupportedDefault: return TEXT("unsupported_default");
		case EMode::InvalidOverride: return TEXT("invalid_override");
		default: return TEXT("none");
		}
	}

	/** An editable curve of a curve data interface (XCurve, RedCurve...), not its "...CookedEditorCache" copies. */
	bool IsCurveChannel(const FStructProperty* Property)
	{
		return Property->Struct == FRichCurve::StaticStruct() && Property->HasAnyPropertyFlags(CPF_Edit)
			&& !Property->GetName().EndsWith(TEXT("CookedEditorCache"));
	}

	FString CurveSummary(UNiagaraDataInterface* DataInterface)
	{
		TArray<FString> Curves;
		for (TFieldIterator<FStructProperty> It(DataInterface->GetClass()); It; ++It)
		{
			if (!IsCurveChannel(*It))
			{
				continue;
			}
			const FRichCurve* Curve = It->ContainerPtrToValuePtr<FRichCurve>(DataInterface);
			TArray<FString> Keys;
			for (const FRichCurveKey& Key : Curve->GetConstRefOfKeys())
			{
				Keys.Add(FString::Printf(TEXT("%s:%s"), *FormatNumber(Key.Time), *FormatNumber(Key.Value)));
			}
			Curves.Add(FString::Printf(TEXT("%s[%s]"), *It->GetName(), *FString::Join(Keys, TEXT(" "))));
		}
		return FString::Join(Curves, TEXT(" "));
	}

	TSharedRef<FJsonObject> DescribeInput(const FInputHit& Hit, const TArray<FInputHit>& All, bool bIncludeHidden)
	{
		UNiagaraStackFunctionInput* Input = Hit.Input;
		TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
		Out->SetStringField(TEXT("name"), Hit.Segments.Last().Key);
		Out->SetStringField(TEXT("path"), Hit.Path);
		if (Hit.Segments.Last().Value != Hit.Segments.Last().Key)
		{
			Out->SetStringField(TEXT("display"), Hit.Segments.Last().Value);
		}
		const FNiagaraTypeDefinition& Type = Input->GetInputType();
		Out->SetStringField(TEXT("type"), Type.GetName());
		const UNiagaraStackFunctionInput::EValueMode Mode = Input->GetValueMode();
		Out->SetStringField(TEXT("mode"), ValueModeName(Mode));
		if (Input->IsStaticParameter())
		{
			Out->SetBoolField(TEXT("static"), true);
		}
		if (!Input->GetShouldPassFilterForVisibleCondition())
		{
			Out->SetBoolField(TEXT("hidden"), true);
		}
		if (Input->GetHasEditCondition() && !Input->GetEditConditionEnabled())
		{
			Out->SetBoolField(TEXT("disabled"), true);
		}
		if (Input->CanReset())
		{
			Out->SetBoolField(TEXT("changed"), true);  // differs from the module's default
		}
		switch (Mode)
		{
		case UNiagaraStackFunctionInput::EValueMode::Local:
			if (TSharedPtr<const FStructOnScope> Local = Input->GetLocalValueStruct())
			{
				Out->SetStringField(TEXT("value"), FormatValue(Type, Local->GetStructMemory()));
			}
			break;
		case UNiagaraStackFunctionInput::EValueMode::Linked:
			Out->SetStringField(TEXT("value"), Input->GetLinkedParameterValue().GetName().ToString());
			break;
		case UNiagaraStackFunctionInput::EValueMode::Dynamic:
			if (UNiagaraNodeFunctionCall* Node = Input->GetDynamicInputNode())
			{
				Out->SetStringField(TEXT("value"), Node->FunctionScript ? Node->FunctionScript->GetPathName() : Node->GetFunctionName());
			}
			break;
		case UNiagaraStackFunctionInput::EValueMode::Data:
			if (UNiagaraDataInterface* DataInterface = Input->GetDataValueObject())
			{
				FString Text = DataInterface->GetClass()->GetName();
				const FString Curves = CurveSummary(DataInterface);
				Out->SetStringField(TEXT("value"), Curves.IsEmpty() ? Text : Text + TEXT(" ") + Curves);
			}
			break;
		case UNiagaraStackFunctionInput::EValueMode::ObjectAsset:
			if (UObject* Asset = Input->GetObjectAssetValue())
			{
				Out->SetStringField(TEXT("value"), Asset->GetPathName());
			}
			break;
		case UNiagaraStackFunctionInput::EValueMode::Expression:
			Out->SetStringField(TEXT("value"), Input->GetCustomExpressionText().ToString());
			break;
		case UNiagaraStackFunctionInput::EValueMode::DefaultFunction:
			if (UNiagaraNodeFunctionCall* Node = Input->GetDefaultFunctionNode())
			{
				Out->SetStringField(TEXT("value"), Node->GetFunctionName());
			}
			break;
		default:
			break;
		}
		if (Type.IsEnum())
		{
			Out->SetStringField(TEXT("choices"), EnumChoices(Type.GetEnum()));
		}
		TArray<TSharedPtr<FJsonValue>> Children;
		for (const FInputHit& Other : All)
		{
			if (Other.Segments.Num() == Hit.Segments.Num() + 1 && Other.Path.StartsWith(Hit.Path + TEXT("/")))
			{
				if (bIncludeHidden || Other.Input->GetShouldPassFilterForVisibleCondition())
				{
					Children.Add(MakeShared<FJsonValueObject>(DescribeInput(Other, All, bIncludeHidden)));
				}
			}
		}
		if (Children.Num())
		{
			Out->SetArrayField(TEXT("inputs"), Children);
		}
		return Out;
	}

	TSharedRef<FJsonObject> DescribeModule(UNiagaraStackModuleItem* Module, bool bIncludeInputs, bool bIncludeHidden)
	{
		TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
		UNiagaraNodeFunctionCall& Node = Module->GetModuleNode();
		Out->SetStringField(TEXT("name"), ModuleLabel(Module));
		if (Node.FunctionScript)
		{
			Out->SetStringField(TEXT("script"), Node.FunctionScript->GetPathName());
		}
		if (!Module->GetIsEnabled())
		{
			Out->SetBoolField(TEXT("enabled"), false);
		}
		if (bIncludeInputs)
		{
			Module->RefreshChildren();
			TArray<FInputHit> Hits;
			CollectInputs(Module, FInputHit(), Hits);
			TArray<TSharedPtr<FJsonValue>> Inputs;
			for (const FInputHit& Hit : Hits)
			{
				if (Hit.Segments.Num() == 1 && (bIncludeHidden || Hit.Input->GetShouldPassFilterForVisibleCondition()))
				{
					Inputs.Add(MakeShared<FJsonValueObject>(DescribeInput(Hit, Hits, bIncludeHidden)));
				}
			}
			Out->SetArrayField(TEXT("inputs"), Inputs);
		}
		return Out;
	}

	FString SeverityName(EStackIssueSeverity Severity)
	{
		switch (Severity)
		{
		case EStackIssueSeverity::Error: return TEXT("error");
		case EStackIssueSeverity::Warning: return TEXT("warning");
		case EStackIssueSeverity::Info: return TEXT("info");
		default: return TEXT("note");
		}
	}

	TArray<TSharedPtr<FJsonValue>> DescribeIssues(UNiagaraStackEntry* Root)
	{
		TArray<TSharedPtr<FJsonValue>> Out;
		TArray<UNiagaraStackEntry*> Entries = Root->GetAllChildrenWithIssues();
		Entries.AddUnique(Root);
		for (UNiagaraStackEntry* Entry : Entries)
		{
			for (const UNiagaraStackEntry::FStackIssue& Issue : Entry->GetIssues())
			{
				TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
				Item->SetStringField(TEXT("severity"), SeverityName(Issue.GetSeverity()));
				Item->SetStringField(TEXT("where"), Entry->GetDisplayName().ToString());
				Item->SetStringField(TEXT("summary"), Issue.GetShortDescription().ToString());
				Item->SetStringField(TEXT("detail"), Issue.GetLongDescription().ToString());
				Out.Add(MakeShared<FJsonValueObject>(Item));
			}
		}
		return Out;
	}

	TSharedRef<FJsonObject> DescribeStages(UNiagaraStackEntry* Root, bool bIncludeInputs, bool bIncludeHidden)
	{
		TSharedRef<FJsonObject> Stages = MakeShared<FJsonObject>();
		TArray<UNiagaraStackScriptItemGroup*> Groups;
		CollectOfType(Root, Groups);
		for (UNiagaraStackScriptItemGroup* Group : Groups)
		{
			TArray<UNiagaraStackModuleItem*> Modules;
			CollectOfType(Group, Modules);
			TArray<TSharedPtr<FJsonValue>> Items;
			for (UNiagaraStackModuleItem* Module : Modules)
			{
				Items.Add(MakeShared<FJsonValueObject>(DescribeModule(Module, bIncludeInputs, bIncludeHidden)));
			}
			FString Name = StageName(Group->GetScriptUsage());
			if (Stages->HasField(Name))
			{
				Name += TEXT(":") + Group->GetDisplayName().ToString();
			}
			Stages->SetArrayField(Name, Items);
		}
		return Stages;
	}

	TSharedRef<FJsonObject> DescribeRenderer(UNiagaraRendererProperties* Renderer, int32 Index)
	{
		TSharedRef<FJsonObject> Out = MakeShared<FJsonObject>();
		Out->SetNumberField(TEXT("index"), Index);
		Out->SetStringField(TEXT("class"), Renderer->GetClass()->GetName());
		const UObject* Defaults = Renderer->GetClass()->GetDefaultObject();
		Out->SetObjectField(TEXT("properties"), DiffProperties(Renderer->GetClass(), Renderer, Defaults, Renderer));
		return Out;
	}

	// ---------------------------------------------------------------- compile

	FString CompileStatusName(ENiagaraScriptCompileStatus Status)
	{
		switch (Status)
		{
		case ENiagaraScriptCompileStatus::NCS_Dirty: return TEXT("dirty");
		case ENiagaraScriptCompileStatus::NCS_Error: return TEXT("error");
		case ENiagaraScriptCompileStatus::NCS_UpToDate: return TEXT("ok");
		case ENiagaraScriptCompileStatus::NCS_BeingCreated: return TEXT("being_created");
		case ENiagaraScriptCompileStatus::NCS_UpToDateWithWarnings: return TEXT("warnings");
		case ENiagaraScriptCompileStatus::NCS_ComputeUpToDateWithWarnings: return TEXT("compute_warnings");
		default: return TEXT("unknown");
		}
	}

	// ---------------------------------------------------------------- preview

	void SeekTo(UNiagaraComponent* Component, float Time, float SeekDelta)
	{
		Component->SetSeekDelta(SeekDelta);
		Component->SeekToDesiredAge(Time);
		UWorld* World = Component->GetWorld();
		World->TimeSeconds = Time;
		World->UnpausedTimeSeconds = Time;
		World->RealTimeSeconds = Time;
		World->DeltaRealTimeSeconds = SeekDelta;
		World->DeltaTimeSeconds = SeekDelta;
		World->Tick(ELevelTick::LEVELTICK_PauseTick, 0.0f);
		Component->TickComponent(SeekDelta, ELevelTick::LEVELTICK_All, nullptr);
		World->SendAllEndOfFrameUpdates();
		if (FNiagaraWorldManager* WorldManager = FNiagaraWorldManager::Get(World))
		{
			WorldManager->FlushComputeAndDeferredQueues(false);
		}
	}

	FString BlendModeName(EBlendMode Mode)
	{
		switch (Mode)
		{
		case BLEND_Opaque: return TEXT("opaque");
		case BLEND_Masked: return TEXT("masked");
		case BLEND_Translucent: return TEXT("translucent");
		case BLEND_Additive: return TEXT("additive");
		case BLEND_Modulate: return TEXT("modulate");
		case BLEND_AlphaComposite: return TEXT("alpha_composite");
		case BLEND_AlphaHoldout: return TEXT("alpha_holdout");
		default: return FString::Printf(TEXT("blend_%d"), static_cast<int32>(Mode));
		}
	}

	/** Materials of every enabled renderer of every enabled emitter. */
	TArray<UMaterialInterface*> SystemMaterials(UNiagaraSystem* System)
	{
		TArray<UMaterialInterface*> Out;
		for (const FNiagaraEmitterHandle& Handle : System->GetEmitterHandles())
		{
			const FVersionedNiagaraEmitterData* Data = Handle.GetEmitterData();
			if (!Data || !Handle.GetIsEnabled())
			{
				continue;
			}
			for (UNiagaraRendererProperties* Renderer : Data->GetRenderers())
			{
				if (Renderer && Renderer->GetIsEnabled())
				{
					Renderer->GetUsedMaterials(nullptr, Out);
				}
			}
		}
		Out.Remove(nullptr);
		return Out;
	}

	void Restart(UNiagaraComponent* Component)
	{
		Component->ReinitializeSystem();
		Component->SetDesiredAge(0.f);
	}

	TArray<TSharedPtr<FJsonValue>> ParticleCounts(UNiagaraComponent* Component, UNiagaraSystem* System, int32& OutTotal)
	{
		TArray<TSharedPtr<FJsonValue>> Out;
		OutTotal = 0;
		FNiagaraSystemInstanceControllerPtr Controller = Component->GetSystemInstanceController();
		FNiagaraSystemInstance* Instance = Controller.IsValid() ? Controller->GetSystemInstance_Unsafe() : nullptr;
		if (!Instance)
		{
			return Out;
		}
		const TArray<FNiagaraEmitterHandle>& Handles = System->GetEmitterHandles();
		TArrayView<FNiagaraEmitterInstanceRef> Emitters = Instance->GetEmitters();
		for (int32 i = 0; i < Emitters.Num(); ++i)
		{
			TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
			const int32 Count = Emitters[i]->GetNumParticles();
			Item->SetStringField(TEXT("emitter"), Handles.IsValidIndex(i) ? Handles[i].GetName().ToString() : FString::FromInt(i));
			Item->SetNumberField(TEXT("particles"), Count);
			if (Handles.IsValidIndex(i) && Handles[i].GetEmitterData() && Handles[i].GetEmitterData()->SimTarget == ENiagaraSimTarget::GPUComputeSim)
			{
				Item->SetBoolField(TEXT("gpu"), true);
			}
			OutTotal += Count;
			Out.Add(MakeShared<FJsonValueObject>(Item));
		}
		return Out;
	}
}

using namespace UETKVfxPrivate;

// ==================================================================== emitters

FString UUETKVfxLibrary::AddEmitter(UNiagaraSystem* System, UNiagaraEmitter* Source, const FString& Name, bool bKeepParent)
{
	if (!System || !Source)
	{
		return Fail(TEXT("System and Source emitter are required"));
	}
	TSharedPtr<FNiagaraSystemViewModel> ViewModel = ViewModelFor(System);
	TSharedPtr<FNiagaraEmitterHandleViewModel> Handle = ViewModel->AddEmitter(*Source, Source->GetExposedVersion().VersionGuid);
	if (!Handle.IsValid())
	{
		return Fail(TEXT("AddEmitter failed"));
	}
	if (!Name.IsEmpty())
	{
		Handle->SetName(FName(*Name));
	}
	if (!bKeepParent)
	{
		if (FNiagaraEmitterHandle* EmitterHandle = Handle->GetEmitterHandle())
		{
			FVersionedNiagaraEmitterData* Data = EmitterHandle->GetEmitterData();
			if (Data && Data->GetParent().Emitter)
			{
				EmitterHandle->GetInstance().Emitter->Modify();
				Data->RemoveParent();
			}
		}
	}
	ViewModel->RefreshAll();
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("emitter"), Handle->GetName().ToString());
	return Ok(Result);
}

FString UUETKVfxLibrary::RemoveEmitter(UNiagaraSystem* System, const FString& Emitter)
{
	if (!System)
	{
		return Fail(TEXT("System is null"));
	}
	TSharedPtr<FNiagaraSystemViewModel> ViewModel = ViewModelFor(System);
	FString Error;
	TSharedPtr<FNiagaraEmitterHandleViewModel> Handle = FindEmitter(*ViewModel, Emitter, Error);
	if (!Handle)
	{
		return Fail(Error);
	}
	TSet<FGuid> Ids;
	Ids.Add(Handle->GetId());
	ViewModel->DeleteEmitters(Ids);
	ViewModel->RefreshAll();
	return Ok();
}

FString UUETKVfxLibrary::ClearSystem(UNiagaraSystem* System)
{
	if (!System)
	{
		return Fail(TEXT("System is null"));
	}
	TSharedPtr<FNiagaraSystemViewModel> ViewModel = ViewModelFor(System);
	System->Modify();

	TSet<FGuid> Ids;
	for (const TSharedRef<FNiagaraEmitterHandleViewModel>& Handle : ViewModel->GetEmitterHandleViewModels())
	{
		Ids.Add(Handle->GetId());
	}
	if (Ids.Num())
	{
		ViewModel->DeleteEmitters(Ids);
	}

	TArray<FNiagaraVariable> UserParameters;
	System->GetExposedParameters().GetParameters(UserParameters);
	for (const FNiagaraVariable& Parameter : UserParameters)
	{
#if UE_VERSION_OLDER_THAN(5, 8, 0)
		System->GetExposedParameters().RemoveParameter(Parameter);
#else
		FNiagaraEditorUtilities::UserParameters::RemoveUserParameterFromSystem(*System, Parameter);  // also drops its metadata
#endif
	}

	ViewModel->RefreshAll();
	if (UNiagaraStackEntry* Root = ViewModel->GetSystemStackViewModel()->GetRootEntry())
	{
		Root->RefreshChildren();
		TArray<UNiagaraStackScriptItemGroup*> Groups;
		CollectOfType(Root, Groups);
		for (UNiagaraStackScriptItemGroup* Group : Groups)
		{
			TArray<UNiagaraStackModuleItem*> Modules;
			CollectOfType(Group, Modules);
			for (UNiagaraStackModuleItem* Module : Modules)
			{
				UNiagaraNodeFunctionCall& Node = Module->GetModuleNode();
				if (Node.FunctionScript && Node.FunctionScript->GetName() == TEXT("SystemState"))
				{
					continue;
				}
				FText Why;
				if (Module->TestCanDeleteWithMessage(Why))
				{
					Module->Delete();
				}
			}
			Group->RefreshChildren();
			Modules.Reset();
			CollectOfType(Group, Modules);
			for (UNiagaraStackModuleItem* Module : Modules)
			{
				// Resetting an input can rebuild the module's children, so collect afresh after each reset.
				for (int32 Guard = 0; Guard < 64; ++Guard)
				{
					Module->RefreshChildren();
					TArray<FInputHit> Hits;
					CollectInputs(Module, FInputHit(), Hits);
					UNiagaraStackFunctionInput* Resettable = nullptr;
					for (const FInputHit& Hit : Hits)
					{
						if (Hit.Input->CanReset())
						{
							Resettable = Hit.Input;
							break;
						}
					}
					if (!Resettable)
					{
						break;
					}
					Resettable->Reset();
				}
			}
		}
	}
	System->SetWarmupTime(0.f);
	ViewModel->RefreshAll();
	return Ok();
}

FString UUETKVfxLibrary::ListEmitters(UNiagaraSystem* System)
{
	if (!System)
	{
		return Fail(TEXT("System is null"));
	}
	TArray<TSharedPtr<FJsonValue>> Emitters;
	for (const FNiagaraEmitterHandle& Handle : System->GetEmitterHandles())
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("name"), Handle.GetName().ToString());
		Item->SetBoolField(TEXT("enabled"), Handle.GetIsEnabled());
		const FVersionedNiagaraEmitterData* Data = Handle.GetEmitterData();
		if (Data)
		{
			Item->SetStringField(TEXT("sim"), Data->SimTarget == ENiagaraSimTarget::GPUComputeSim ? TEXT("gpu") : TEXT("cpu"));
			TArray<TSharedPtr<FJsonValue>> Renderers;
			for (UNiagaraRendererProperties* Renderer : Data->GetRenderers())
			{
				if (!Renderer)
				{
					continue;
				}
				TSharedRef<FJsonObject> R = MakeShared<FJsonObject>();
				R->SetStringField(TEXT("class"), Renderer->GetClass()->GetName());
				R->SetBoolField(TEXT("enabled"), Renderer->GetIsEnabled());
				TArray<UMaterialInterface*> Materials;
				Renderer->GetUsedMaterials(nullptr, Materials);
				TArray<TSharedPtr<FJsonValue>> MaterialItems;
				for (UMaterialInterface* Material : Materials)
				{
					if (Material)
					{
						TSharedRef<FJsonObject> M = MakeShared<FJsonObject>();
						M->SetStringField(TEXT("path"), Material->GetPathName());
						M->SetStringField(TEXT("blend"), BlendModeName(Material->GetBlendMode()));
						MaterialItems.Add(MakeShared<FJsonValueObject>(M));
					}
				}
				R->SetArrayField(TEXT("materials"), MaterialItems);
				Renderers.Add(MakeShared<FJsonValueObject>(R));
			}
			Item->SetArrayField(TEXT("renderers"), Renderers);
		}
		Emitters.Add(MakeShared<FJsonValueObject>(Item));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetArrayField(TEXT("emitters"), Emitters);
	return Ok(Result);
}

FString UUETKVfxLibrary::SetEmitterProperty(UNiagaraSystem* System, const FString& Emitter, const FString& Property, const FString& Value)
{
	if (!System)
	{
		return Fail(TEXT("System is null"));
	}
	TSharedPtr<FNiagaraSystemViewModel> ViewModel = ViewModelFor(System);
	FString Error;
	TSharedPtr<FNiagaraEmitterHandleViewModel> Handle = FindEmitter(*ViewModel, Emitter, Error);
	if (!Handle)
	{
		return Fail(Error);
	}
	FNiagaraEmitterHandle* EmitterHandle = Handle->GetEmitterHandle();
	FVersionedNiagaraEmitter Versioned = EmitterHandle ? EmitterHandle->GetInstance() : FVersionedNiagaraEmitter();
	FVersionedNiagaraEmitterData* Data = Versioned.GetEmitterData();
	const UScriptStruct* Struct = EmitterDataStruct();
	if (!Data || !Struct)
	{
		return Fail(TEXT("emitter has no data"));
	}
	FProperty* Prop = FindPropertyLoose(Struct, Property);
	if (!Prop)
	{
		return Fail(FString::Printf(TEXT("FVersionedNiagaraEmitterData has no property '%s'"), *Property));
	}
	Versioned.Emitter->Modify();
	FStringOutputDevice Errors;
	if (!Prop->ImportText_Direct(*Value, Prop->ContainerPtrToValuePtr<void>(Data), Versioned.Emitter, PPF_None, &Errors) || !Errors.IsEmpty())
	{
		return Fail(FString::Printf(TEXT("could not parse '%s' for %s: %s"), *Value, *Prop->GetName(), *Errors));
	}
	FPropertyChangedEvent Event(Prop, EPropertyChangeType::ValueSet);
	Versioned.Emitter->PostEditChangeVersionedProperty(Event, Versioned.Version);
	ViewModel->RefreshAll();
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("value"), ExportProperty(Prop, Data, Versioned.Emitter));
	return Ok(Result);
}

// ==================================================================== modules

FString UUETKVfxLibrary::AddModule(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, UNiagaraScript* Module, int32 Index)
{
	if (!Module)
	{
		return Fail(TEXT("Module script is null (check the asset path)"));
	}
	FTarget T;
	if (!ResolveGroup(System, Emitter, Stage, T))
	{
		return Fail(T.Error);
	}
	UNiagaraNodeOutput* Output = T.Group->GetScriptOutputNode();
	if (!Output)
	{
		return Fail(TEXT("stage has no output node"));
	}
	UNiagaraNodeFunctionCall* Node = FNiagaraStackGraphUtilities::AddScriptModuleToStack(Module, *Output, Index);
	if (!Node)
	{
		return Fail(FString::Printf(TEXT("could not add %s to %s"), *Module->GetPathName(), T.Stage->Name));
	}
	T.Group->RefreshChildren();
	// Address it by its display name; count earlier modules of the same name for "#n".
	TArray<UNiagaraStackModuleItem*> Modules;
	CollectOfType(T.Group, Modules);
	FString Label = Node->GetFunctionName();
	int32 Nth = 0;
	for (UNiagaraStackModuleItem* Item : Modules)
	{
		const bool bSame = &Item->GetModuleNode() == Node;
		if (bSame)
		{
			Label = ModuleLabel(Item);
		}
		if (Norm(ModuleLabel(Item)) == Norm(Label) || bSame)
		{
			++Nth;
		}
		if (bSame)
		{
			break;
		}
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("module"), Nth > 1 ? FString::Printf(TEXT("%s#%d"), *Label, Nth) : Label);
	return Ok(Result);
}

FString UUETKVfxLibrary::RemoveModule(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module)
{
	FTarget T;
	if (!ResolveModule(System, Emitter, Stage, Module, T))
	{
		return Fail(T.Error);
	}
	FText Why;
	if (!T.Module->TestCanDeleteWithMessage(Why))
	{
		return Fail(FString::Printf(TEXT("cannot delete '%s': %s"), *Module, *Why.ToString()));
	}
	T.Module->Delete();
	T.Group->RefreshChildren();
	return Ok();
}

FString UUETKVfxLibrary::ListModules(UNiagaraSystem* System, const FString& Emitter, const FString& Stage)
{
	FTarget T;
	if (!ResolveGroup(System, Emitter, Stage, T))
	{
		return Fail(T.Error);
	}
	TArray<UNiagaraStackModuleItem*> Modules;
	CollectOfType(T.Group, Modules);
	TArray<TSharedPtr<FJsonValue>> Names;
	for (UNiagaraStackModuleItem* Item : Modules)
	{
		Names.Add(MakeShared<FJsonValueString>(ModuleLabel(Item)));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetArrayField(TEXT("modules"), Names);
	return Ok(Result);
}

FString UUETKVfxLibrary::GetInput(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath)
{
	FTarget T;
	if (!ResolveInput(System, Emitter, Stage, Module, InputPath, T))
	{
		return Fail(T.Error);
	}
	TArray<FInputHit> Hits;
	CollectInputs(T.Module, FInputHit(), Hits);
	for (const FInputHit& Hit : Hits)
	{
		if (Hit.Input == T.Input)
		{
			FJson Result = DescribeInput(Hit, Hits, true);
			return Ok(Result);
		}
	}
	return Fail(TEXT("input vanished after lookup"));
}

FString UUETKVfxLibrary::SetModuleEnabled(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, bool bEnabled)
{
	FTarget T;
	if (!ResolveModule(System, Emitter, Stage, Module, T))
	{
		return Fail(T.Error);
	}
	FNiagaraStackGraphUtilities::SetModuleIsEnabled(T.Module->GetModuleNode(), bEnabled);
	T.Group->RefreshChildren();
	return Ok();
}

// ==================================================================== inputs

FString UUETKVfxLibrary::SetInput(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, const FString& Value)
{
	FTarget T;
	if (!ResolveEditableInput(System, Emitter, Stage, Module, InputPath, T))
	{
		return Fail(T.Error);
	}
	const FNiagaraTypeDefinition& Type = T.Input->GetInputType();
	const UScriptStruct* Struct = Type.GetScriptStruct();
	if (!Struct || Type.IsDataInterface() || Type.IsUObject())
	{
		return Fail(FString::Printf(TEXT("input '%s' is a %s: use SetInputCurve/SetInputDataInterface/SetInputObject"), *T.InputPath, *Type.GetName()));
	}
	TSharedRef<FStructOnScope> Local = MakeShared<FStructOnScope>(Struct);
	if (TSharedPtr<const FStructOnScope> Current = T.Input->GetLocalValueStruct())
	{
		if (Current->GetStruct() == Struct)
		{
			Struct->CopyScriptStruct(Local->GetStructMemory(), Current->GetStructMemory());
		}
	}
	FString Error;
	if (!ParseValue(Type, Value, Local->GetStructMemory(), Error))
	{
		return Fail(FString::Printf(TEXT("%s: %s"), *T.InputPath, *Error));
	}
	T.Input->SetLocalValue(Local);
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("input"), T.InputPath);
	Result->SetStringField(TEXT("value"), FormatValue(Type, Local->GetStructMemory()));
	return Ok(Result);
}

FString UUETKVfxLibrary::SetInputDynamic(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, UNiagaraScript* DynamicInput)
{
	if (!DynamicInput)
	{
		return Fail(TEXT("DynamicInput script is null (check the asset path)"));
	}
	FTarget T;
	if (!ResolveEditableInput(System, Emitter, Stage, Module, InputPath, T))
	{
		return Fail(T.Error);
	}
	T.Input->SetDynamicInput(DynamicInput);
	// Report the dynamic input's own inputs so the caller can fill them in.
	FTarget After;
	TArray<TSharedPtr<FJsonValue>> Children;
	if (ResolveInput(System, Emitter, Stage, Module, InputPath, After))
	{
		TArray<FInputHit> Hits;
		CollectInputs(After.Input, FInputHit(), Hits);
		for (const FInputHit& Hit : Hits)
		{
			if (Hit.Segments.Num() == 1)
			{
				Children.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("%s/%s (%s)"), *After.InputPath, *Hit.Path, *Hit.Input->GetInputType().GetName())));
			}
		}
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetArrayField(TEXT("inputs"), Children);
	return Ok(Result);
}

FString UUETKVfxLibrary::SetInputLinked(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, const FString& Parameter)
{
	FTarget T;
	if (!ResolveEditableInput(System, Emitter, Stage, Module, InputPath, T))
	{
		return Fail(T.Error);
	}
	T.Input->SetLinkedParameterValue(FNiagaraVariableBase(T.Input->GetInputType(), FName(*Parameter)));
	return Ok();
}

FString UUETKVfxLibrary::SetInputExpression(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, const FString& Expression)
{
	FTarget T;
	if (!ResolveEditableInput(System, Emitter, Stage, Module, InputPath, T))
	{
		return Fail(T.Error);
	}
	T.Input->SetCustomExpression(Expression);
	return Ok();
}

FString UUETKVfxLibrary::SetInputObject(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, UObject* Asset)
{
	FTarget T;
	if (!ResolveEditableInput(System, Emitter, Stage, Module, InputPath, T))
	{
		return Fail(T.Error);
	}
	T.Input->SetObjectAssetValue(Asset);
	return Ok();
}

FString UUETKVfxLibrary::SetInputDataInterface(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, UClass* DataInterfaceClass)
{
	FTarget T;
	if (!ResolveEditableInput(System, Emitter, Stage, Module, InputPath, T))
	{
		return Fail(T.Error);
	}
	UClass* Class = DataInterfaceClass ? DataInterfaceClass : T.Input->GetInputType().GetClass();
	if (!Class || !Class->IsChildOf(UNiagaraDataInterface::StaticClass()))
	{
		return Fail(FString::Printf(TEXT("input '%s' (%s) is not a data interface"), *T.InputPath, *T.Input->GetInputType().GetName()));
	}
	FString Error;
	if (!SetDataInterfaceClass(T.Input, Class, Error))
	{
		return Fail(Error);
	}
	return Ok();
}

UNiagaraDataInterface* UUETKVfxLibrary::GetInputDataInterface(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath)
{
	FTarget T;
	if (!ResolveInput(System, Emitter, Stage, Module, InputPath, T))
	{
		return nullptr;
	}
	return T.Input->GetDataValueObject();
}

FString UUETKVfxLibrary::SetInputCurve(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath, const FString& KeysJson, const FString& Interp)
{
	FTarget T;
	if (!ResolveEditableInput(System, Emitter, Stage, Module, InputPath, T))
	{
		return Fail(T.Error);
	}
	UClass* Class = T.Input->GetInputType().GetClass();
	if (!Class || !Class->IsChildOf(UNiagaraDataInterfaceCurveBase::StaticClass()))
	{
		return Fail(FString::Printf(TEXT("input '%s' is a %s, not a curve: give it a 'from curve' dynamic input first and set the curve on its child"),
			*T.InputPath, *T.Input->GetInputType().GetName()));
	}
	UNiagaraDataInterface* DataInterface = T.Input->GetDataValueObject();
	if (!DataInterface || !DataInterface->IsA(Class))
	{
		FString Error;
		if (!SetDataInterfaceClass(T.Input, Class, Error))
		{
			return Fail(Error);
		}
		FTarget Fresh;
		if (!ResolveInput(System, Emitter, Stage, Module, InputPath, Fresh))
		{
			return Fail(Fresh.Error);
		}
		DataInterface = Fresh.Input->GetDataValueObject();
	}
	if (!DataInterface)
	{
		return Fail(TEXT("the input has no data interface object"));
	}

	TArray<TSharedPtr<FJsonValue>> Keys;
	const TSharedRef<TJsonReader<TCHAR>> Reader = TJsonReaderFactory<TCHAR>::Create(KeysJson);
	if (!FJsonSerializer::Deserialize(Reader, Keys) || Keys.Num() == 0)
	{
		return Fail(TEXT("KeysJson must be a non-empty JSON array of [time, value...] arrays"));
	}
	TArray<FStructProperty*> CurveProperties;
	for (TFieldIterator<FStructProperty> It(DataInterface->GetClass()); It; ++It)
	{
		if (IsCurveChannel(*It))
		{
			CurveProperties.Add(*It);
		}
	}
	if (CurveProperties.Num() == 0)
	{
		return Fail(FString::Printf(TEXT("%s has no curves"), *DataInterface->GetClass()->GetName()));
	}
	ERichCurveInterpMode Mode = RCIM_Linear;
	if (Norm(Interp) == TEXT("cubic") || Norm(Interp) == TEXT("smooth")) { Mode = RCIM_Cubic; }
	else if (Norm(Interp) == TEXT("constant") || Norm(Interp) == TEXT("step")) { Mode = RCIM_Constant; }

	DataInterface->Modify();
	TArray<FRichCurve*> Curves;
	for (FStructProperty* Property : CurveProperties)
	{
		FRichCurve* Curve = Property->ContainerPtrToValuePtr<FRichCurve>(DataInterface);
		Curve->Reset();
		Curves.Add(Curve);
	}
	for (const TSharedPtr<FJsonValue>& KeyValue : Keys)
	{
		const TArray<TSharedPtr<FJsonValue>>* Key = nullptr;
		if (!KeyValue->TryGetArray(Key) || Key->Num() < 2)
		{
			return Fail(TEXT("each key must be [time, value...]"));
		}
		const int32 Values = Key->Num() - 1;
		if (Values != 1 && Values != Curves.Num())
		{
			return Fail(FString::Printf(TEXT("%s has %d curve channel(s); a key gave %d value(s)"), *DataInterface->GetClass()->GetName(), Curves.Num(), Values));
		}
		const float Time = static_cast<float>((*Key)[0]->AsNumber());
		for (int32 c = 0; c < Curves.Num(); ++c)
		{
			const float V = static_cast<float>((*Key)[Values == 1 ? 1 : c + 1]->AsNumber());
			const FKeyHandle KeyHandle = Curves[c]->AddKey(Time, V);
			Curves[c]->SetKeyInterpMode(KeyHandle, Mode);
			if (Mode == RCIM_Cubic)
			{
				Curves[c]->SetKeyTangentMode(KeyHandle, RCTM_Auto);
			}
		}
	}
	for (int32 c = 0; c < Curves.Num(); ++c)
	{
		Curves[c]->AutoSetTangents();
		FPropertyChangedEvent Event(CurveProperties[c], EPropertyChangeType::ValueSet);
		DataInterface->PostEditChangeProperty(Event);
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("curves"), CurveSummary(DataInterface));
	return Ok(Result);
}

FString UUETKVfxLibrary::ResetInput(UNiagaraSystem* System, const FString& Emitter, const FString& Stage, const FString& Module, const FString& InputPath)
{
	FTarget T;
	if (!ResolveInput(System, Emitter, Stage, Module, InputPath, T))
	{
		return Fail(T.Error);
	}
	if (T.Input->CanReset())
	{
		T.Input->Reset();
	}
	return Ok();
}

// ==================================================================== renderers

FString UUETKVfxLibrary::AddRenderer(UNiagaraSystem* System, const FString& Emitter, UClass* RendererClass)
{
	if (!System || !RendererClass || !RendererClass->IsChildOf(UNiagaraRendererProperties::StaticClass()))
	{
		return Fail(TEXT("System and a UNiagaraRendererProperties subclass are required"));
	}
	TSharedPtr<FNiagaraSystemViewModel> ViewModel = ViewModelFor(System);
	FString Error;
	TSharedPtr<FNiagaraEmitterHandleViewModel> Handle = FindEmitter(*ViewModel, Emitter, Error);
	if (!Handle)
	{
		return Fail(Error);
	}
	FVersionedNiagaraEmitter Versioned = Handle->GetEmitterHandle()->GetInstance();
	UNiagaraRendererProperties* Renderer = NewObject<UNiagaraRendererProperties>(Versioned.Emitter, RendererClass, NAME_None, RF_Transactional);
	Versioned.Emitter->Modify();
	Versioned.Emitter->AddRenderer(Renderer, Versioned.Version);
	ViewModel->RefreshAll();
	FJson Result = MakeShared<FJsonObject>();
	Result->SetNumberField(TEXT("index"), Versioned.GetEmitterData()->GetRenderers().IndexOfByKey(Renderer));
	return Ok(Result);
}

TArray<UNiagaraRendererProperties*> UUETKVfxLibrary::GetRenderers(UNiagaraSystem* System, const FString& Emitter)
{
	TArray<UNiagaraRendererProperties*> Out;
	if (!System)
	{
		return Out;
	}
	for (const FNiagaraEmitterHandle& Handle : System->GetEmitterHandles())
	{
		if (Norm(Handle.GetName().ToString()) == Norm(Emitter) && Handle.GetEmitterData())
		{
			Out = Handle.GetEmitterData()->GetRenderers();
		}
	}
	return Out;
}

FString UUETKVfxLibrary::RemoveRenderer(UNiagaraSystem* System, const FString& Emitter, int32 Index)
{
	if (!System)
	{
		return Fail(TEXT("System is null"));
	}
	TSharedPtr<FNiagaraSystemViewModel> ViewModel = ViewModelFor(System);
	FString Error;
	TSharedPtr<FNiagaraEmitterHandleViewModel> Handle = FindEmitter(*ViewModel, Emitter, Error);
	if (!Handle)
	{
		return Fail(Error);
	}
	FVersionedNiagaraEmitter Versioned = Handle->GetEmitterHandle()->GetInstance();
	TArray<UNiagaraRendererProperties*> Renderers = Versioned.GetEmitterData()->GetRenderers();
	if (Index >= Renderers.Num())
	{
		return Fail(FString::Printf(TEXT("emitter has %d renderer(s)"), Renderers.Num()));
	}
	Versioned.Emitter->Modify();
	for (int32 i = 0; i < Renderers.Num(); ++i)
	{
		if (Index < 0 || i == Index)
		{
			Versioned.Emitter->RemoveRenderer(Renderers[i], Versioned.Version);
		}
	}
	ViewModel->RefreshAll();
	return Ok();
}

// ==================================================================== user parameters & generic properties

FString UUETKVfxLibrary::SetUserParameter(UNiagaraSystem* System, const FString& Name, const FString& TypeName, const FString& Value)
{
	if (!System)
	{
		return Fail(TEXT("System is null"));
	}
	FNiagaraTypeDefinition Type;
	if (!ParseTypeName(TypeName, Type))
	{
		return Fail(FString::Printf(TEXT("unknown user parameter type '%s' (float, int, bool, vec2, vec3, vec4, color, position, quat)"), *TypeName));
	}
	FString Short = Name;
	Short.RemoveFromStart(TEXT("User."));
	const FNiagaraVariable Variable(Type, FName(*(TEXT("User.") + Short)));
	FNiagaraUserRedirectionParameterStore& Store = System->GetExposedParameters();
	bool bExists = false;
	for (const FNiagaraVariableWithOffset& Existing : Store.ReadParameterVariables())
	{
		if (Existing.GetName() == Variable.GetName())
		{
			if (Existing.GetType() != Type)
			{
				return Fail(FString::Printf(TEXT("user parameter %s already exists as %s"), *Variable.GetName().ToString(), *Existing.GetType().GetName()));
			}
			bExists = true;
		}
	}
	if (!bExists)
	{
		// What FNiagaraSystemViewModel::AddParameter does (5.8's AddUserParameterToSystem wraps it; 5.6/5.7 lack that).
		System->Modify();
		Store.AddParameter(Variable, true);
	}
	if (!Value.IsEmpty())
	{
		FStructOnScope Memory(Type.GetScriptStruct());
		FString Error;
		if (!ParseValue(Type, Value, Memory.GetStructMemory(), Error))
		{
			return Fail(Error);
		}
		System->Modify();
		Store.SetParameterData(Memory.GetStructMemory(), Variable, true);
	}
	if (TSharedPtr<FNiagaraSystemViewModel>* Session = Sessions().Find(System))
	{
		(*Session)->RefreshAll();
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("parameter"), Variable.GetName().ToString());
	return Ok(Result);
}

FString UUETKVfxLibrary::CreateTextureFromFile(const FString& AssetPath, const FString& ImageFile, bool bSRGB)
{
	FImage Image;
	if (!FImageUtils::LoadImage(*ImageFile, Image))
	{
		return Fail(FString::Printf(TEXT("could not read image %s"), *ImageFile));
	}
	Image.ChangeFormat(ERawImageFormat::BGRA8, bSRGB ? EGammaSpace::sRGB : EGammaSpace::Linear);
	const FString PackageName = AssetPath.Contains(TEXT(".")) ? FPackageName::ObjectPathToPackageName(AssetPath) : AssetPath;
	const FString Name = FPackageName::GetLongPackageAssetName(PackageName);
	UPackage* Package = CreatePackage(*PackageName);
	Package->FullyLoad();
	UTexture2D* Texture = FindObject<UTexture2D>(Package, *Name);
	const bool bExisted = Texture != nullptr;
	if (!Texture)
	{
		Texture = NewObject<UTexture2D>(Package, *Name, RF_Public | RF_Standalone | RF_Transactional);
	}
	Texture->Modify();
	Texture->Source.Init(Image);
	Texture->SRGB = bSRGB;
	Texture->CompressionSettings = TC_Default;
	Texture->MipGenSettings = TMGS_FromTextureGroup;
	Texture->PostEditChange();
	Package->MarkPackageDirty();
	if (!bExisted)
	{
		FAssetRegistryModule::AssetCreated(Texture);
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("texture"), Texture->GetPathName());
	return Ok(Result);
}

FString UUETKVfxLibrary::SetObjectProperty(UObject* Object, const FString& PropertyPath, const FString& Value)
{
	if (!Object)
	{
		return Fail(TEXT("Object is null"));
	}
	TArray<FString> Parts;
	PropertyPath.ParseIntoArray(Parts, TEXT("."));
	if (Parts.Num() == 0)
	{
		return Fail(TEXT("empty property path"));
	}
	UObject* Owner = Object;
	void* Container = Object;
	const UStruct* Struct = Object->GetClass();
	FProperty* Outer = nullptr;  // the owner's property that holds the struct we walk into
	for (int32 i = 0; i < Parts.Num() - 1; ++i)
	{
		FProperty* Step = FindPropertyLoose(Struct, Parts[i]);
		if (FStructProperty* StructProp = CastField<FStructProperty>(Step))
		{
			Outer = Outer ? Outer : StructProp;
			Container = StructProp->ContainerPtrToValuePtr<void>(Container);
			Struct = StructProp->Struct;
		}
		else if (FObjectPropertyBase* ObjectProp = CastField<FObjectPropertyBase>(Step))
		{
			UObject* Sub = ObjectProp->GetObjectPropertyValue_InContainer(Container);
			if (!Sub)
			{
				return Fail(FString::Printf(TEXT("%s is null"), *Parts[i]));
			}
			Owner = Sub;
			Container = Sub;
			Struct = Sub->GetClass();
			Outer = nullptr;
		}
		else
		{
			return Fail(FString::Printf(TEXT("'%s' is not a struct or object property of %s"), *Parts[i], *Struct->GetName()));
		}
	}
	FProperty* Leaf = FindPropertyLoose(Struct, Parts.Last());
	if (!Leaf)
	{
		return Fail(FString::Printf(TEXT("%s has no property '%s'"), *Struct->GetName(), *Parts.Last()));
	}
	FProperty* Notified = Outer ? Outer : Leaf;
	Owner->Modify();
	Owner->PreEditChange(Notified);
	FStringOutputDevice Errors;
	const TCHAR* Parsed = Leaf->ImportText_Direct(*Value, Leaf->ContainerPtrToValuePtr<void>(Container), Owner, PPF_None, &Errors);
	FPropertyChangedEvent Event(Notified, EPropertyChangeType::ValueSet);
	Owner->PostEditChangeProperty(Event);
	if (!Parsed || !Errors.IsEmpty())
	{
		return Fail(FString::Printf(TEXT("could not parse '%s' for %s: %s"), *Value, *Leaf->GetName(), *Errors));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("value"), ExportProperty(Leaf, Container, Owner));
	return Ok(Result);
}

// ==================================================================== describe, compile, preview

FString UUETKVfxLibrary::Describe(UNiagaraSystem* System, bool bIncludeInputs, bool bIncludeHiddenInputs)
{
	if (!System)
	{
		return Fail(TEXT("System is null"));
	}
	TSharedPtr<FNiagaraSystemViewModel> ViewModel = ViewModelFor(System);
	ViewModel->RefreshAll();
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("system"), System->GetPathName());

	TArray<TSharedPtr<FJsonValue>> UserParameters;
	for (const FNiagaraVariableWithOffset& Variable : System->GetExposedParameters().ReadParameterVariables())
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("name"), Variable.GetName().ToString());
		Item->SetStringField(TEXT("type"), Variable.GetType().GetName());
		if (!Variable.GetType().IsDataInterface() && !Variable.GetType().IsUObject() && Variable.GetType().GetScriptStruct())
		{
			if (const uint8* Data = System->GetExposedParameters().GetParameterData(Variable))
			{
				Item->SetStringField(TEXT("value"), FormatValue(Variable.GetType(), Data));
			}
		}
		UserParameters.Add(MakeShared<FJsonValueObject>(Item));
	}
	Result->SetArrayField(TEXT("user_parameters"), UserParameters);

	const TArray<FText> Ignored;
	UNiagaraStackEntry* SystemRoot = ViewModel->GetSystemStackViewModel()->GetRootEntry();
	if (SystemRoot)
	{
		SystemRoot->RefreshChildren();
		Result->SetObjectField(TEXT("system_stages"), DescribeStages(SystemRoot, bIncludeInputs, bIncludeHiddenInputs));
		Result->SetArrayField(TEXT("system_issues"), DescribeIssues(SystemRoot));
	}

	TArray<TSharedPtr<FJsonValue>> Emitters;
	for (const TSharedRef<FNiagaraEmitterHandleViewModel>& Handle : ViewModel->GetEmitterHandleViewModels())
	{
		TSharedRef<FJsonObject> Item = MakeShared<FJsonObject>();
		Item->SetStringField(TEXT("name"), Handle->GetName().ToString());
		FNiagaraEmitterHandle* EmitterHandle = Handle->GetEmitterHandle();
		if (!EmitterHandle)
		{
			continue;
		}
		if (!EmitterHandle->GetIsEnabled())
		{
			Item->SetBoolField(TEXT("enabled"), false);
		}
		FVersionedNiagaraEmitter Versioned = EmitterHandle->GetInstance();
		if (FVersionedNiagaraEmitterData* Data = Versioned.GetEmitterData())
		{
			if (const UScriptStruct* Struct = EmitterDataStruct())
			{
				FStructOnScope Defaults(Struct);
				TSharedRef<FJsonObject> Properties = DiffProperties(Struct, Data, Defaults.GetStructMemory(), Versioned.Emitter);
				Properties->SetStringField(TEXT("SimTarget"), Data->SimTarget == ENiagaraSimTarget::GPUComputeSim ? TEXT("GPUComputeSim") : TEXT("CPUSim"));
				Item->SetObjectField(TEXT("properties"), Properties);
			}
			TArray<TSharedPtr<FJsonValue>> Renderers;
			int32 Index = 0;
			for (UNiagaraRendererProperties* Renderer : Data->GetRenderers())
			{
				Renderers.Add(MakeShared<FJsonValueObject>(DescribeRenderer(Renderer, Index++)));
			}
			Item->SetArrayField(TEXT("renderers"), Renderers);
		}
		if (UNiagaraStackEntry* Root = Handle->GetEmitterStackViewModel()->GetRootEntry())
		{
			Root->RefreshChildren();
			Item->SetObjectField(TEXT("stages"), DescribeStages(Root, bIncludeInputs, bIncludeHiddenInputs));
			Item->SetArrayField(TEXT("issues"), DescribeIssues(Root));
		}
		Emitters.Add(MakeShared<FJsonValueObject>(Item));
	}
	Result->SetArrayField(TEXT("emitters"), Emitters);
	return Ok(Result);
}

FString UUETKVfxLibrary::Compile(UNiagaraSystem* System, bool bForce)
{
	if (!System)
	{
		return Fail(TEXT("System is null"));
	}
	System->RequestCompile(bForce);
	System->WaitForCompilationComplete(true, false);

	TArray<TSharedPtr<FJsonValue>> Errors, Warnings, ScriptStates;
	auto Collect = [&](UNiagaraScript* Script, const FString& Owner)
	{
		if (!Script)
		{
			return;
		}
		const FString Where = FString::Printf(TEXT("%s/%s"), *Owner, *StageName(Script->GetUsage()));
		TSharedRef<FJsonObject> State = MakeShared<FJsonObject>();
		State->SetStringField(TEXT("script"), Where);
		State->SetStringField(TEXT("status"), CompileStatusName(Script->GetLastCompileStatus()));
		ScriptStates.Add(MakeShared<FJsonValueObject>(State));
		for (const FNiagaraCompileEvent& Event : Script->GetVMExecutableData().LastCompileEvents)
		{
			const FString Line = FString::Printf(TEXT("%s: %s"), *Where, *Event.Message);
			if (Event.Severity == FNiagaraCompileEventSeverity::Error)
			{
				Errors.AddUnique(MakeShared<FJsonValueString>(Line));
			}
			else if (Event.Severity == FNiagaraCompileEventSeverity::Warning)
			{
				Warnings.AddUnique(MakeShared<FJsonValueString>(Line));
			}
		}
		if (Script->GetLastCompileStatus() == ENiagaraScriptCompileStatus::NCS_Error && !Script->GetVMExecutableData().ErrorMsg.IsEmpty())
		{
			Errors.Add(MakeShared<FJsonValueString>(FString::Printf(TEXT("%s: %s"), *Where, *Script->GetVMExecutableData().ErrorMsg)));
		}
	};
	Collect(System->GetSystemSpawnScript(), TEXT("System"));
	Collect(System->GetSystemUpdateScript(), TEXT("System"));
	for (const FNiagaraEmitterHandle& Handle : System->GetEmitterHandles())
	{
		FVersionedNiagaraEmitterData* Data = Handle.GetEmitterData();
		if (!Data || !Handle.GetIsEnabled())
		{
			continue;
		}
		TArray<UNiagaraScript*> Scripts;
		Data->GetScripts(Scripts, false);
		for (UNiagaraScript* Script : Scripts)
		{
			const bool bGpu = Data->SimTarget == ENiagaraSimTarget::GPUComputeSim;
			const bool bCpuParticleScript = Script->IsParticleScript() && !Script->IsGPUScript();
			if (bGpu && bCpuParticleScript)
			{
				continue;  // CPU particle scripts aren't compiled for GPU emitters
			}
			if (!bGpu && Script->IsGPUScript())
			{
				continue;
			}
			Collect(Script, Handle.GetName().ToString());
		}
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetBoolField(TEXT("ready"), System->IsReadyToRun());
	Result->SetArrayField(TEXT("errors"), Errors);
	Result->SetArrayField(TEXT("warnings"), Warnings);
	Result->SetArrayField(TEXT("scripts"), ScriptStates);
	return Ok(Result);
}

FString UUETKVfxLibrary::RenderPreview(UNiagaraSystem* System, const FString& OutputPng, const FUETKVfxPreviewSettings& Settings)
{
	if (!System)
	{
		return Fail(TEXT("System is null"));
	}
	if (Settings.Times.Num() == 0)
	{
		return Fail(TEXT("Settings.Times is empty"));
	}
	if (!FApp::CanEverRender())
	{
		return Fail(TEXT("this editor can't render (headless / -nullrhi): preview from a normal editor session"));
	}
	System->WaitForCompilationComplete(true, false);
	if (!System->IsReadyToRun())
	{
		return Fail(TEXT("the system is not ready to run (compile errors?): run Compile and check its errors"));
	}
	const int32 Size = FMath::Clamp(Settings.FrameSize, 64, 2048);
	const float SeekDelta = FMath::Clamp(Settings.SeekDelta, 1.f / 240.f, 0.25f);
	TArray<float> Times = Settings.Times;
	Times.Sort();

	TSharedPtr<FAdvancedPreviewScene> Scene = MakeShared<FAdvancedPreviewScene>(FPreviewScene::ConstructionValues());
	Scene->SetFloorVisibility(Settings.bShowFloor, true);
	Scene->SetEnvironmentVisibility(Settings.bShowEnvironment, true);
	UWorld* World = Scene->GetWorld();

	UNiagaraComponent* Component = NewObject<UNiagaraComponent>(GetTransientPackage(), NAME_None, RF_Transient);
	Component->CastShadow = 1;
	Component->bCastDynamicShadow = 1;
	Component->SetAllowScalability(false);
	Component->SetAsset(System);
	Component->SetForceSolo(true);
	Component->SetAgeUpdateMode(ENiagaraAgeUpdateMode::DesiredAge);
	Component->SetCanRenderWhileSeeking(true);
	Component->SetMaxSimTime(0.0f);
	Component->Activate(true);
	Scene->AddComponent(Component, FTransform::Identity);

	// Pass 1: the bounds over every time, for framing.
	FBox Bounds(ForceInit);
	for (float Time : Times)
	{
		SeekTo(Component, Time, SeekDelta);
		Component->UpdateBounds();
		const FBox Box = Component->Bounds.GetBox();
		if (Box.IsValid && Box.GetExtent().GetMax() < 1.0e5f)
		{
			Bounds += Box;
		}
	}
	if (!Bounds.IsValid || Bounds.GetExtent().GetMax() < 1.f)
	{
		Bounds = FBox(FVector(-50.f), FVector(50.f));
	}
	const FRotator ViewRotation(Settings.CameraPitch, Settings.CameraYaw, 0.f);
	const FBox Framed = Settings.bAutoFrame ? Bounds : Bounds.ShiftBy(Settings.Target - Bounds.GetCenter());
	const float Distance = Settings.CameraDistance > 0.f ? Settings.CameraDistance : UETKCapture::FitDistance(Framed, ViewRotation, Settings.FOV, 1.f, Settings.Zoom);
	const FVector CameraLocation = Framed.GetCenter() - ViewRotation.Vector() * Distance;

	// Backdrop: mid-gray behind non-additive materials (smoke vanishes on black), else black.
	FLinearColor Background = Settings.BackgroundColor;
	if (Background.A < 0.f)
	{
		bool bNeedsGray = false;
		for (UMaterialInterface* Material : SystemMaterials(System))
		{
			bNeedsGray |= Material->GetBlendMode() != BLEND_Additive;
		}
		Background = bNeedsGray ? FLinearColor(0.18f, 0.18f, 0.18f, 1.f) : FLinearColor::Black;
	}
	UStaticMeshComponent* Backdrop = nullptr;
	if (Settings.BackdropMaterial && !Background.Equals(FLinearColor::Black))
	{
		Backdrop = UETKCapture::AddBackdrop(*Scene, CameraLocation, ViewRotation, Settings.FOV, Framed, Settings.BackdropMaterial, Background);
	}

	TOptional<FUETKCapture> Capture;
	Capture.Emplace(World, Size, Size);
	Capture->SetView(CameraLocation, ViewRotation, Settings.FOV);
	Capture->SetManualExposure(Settings.ExposureBias);

	// Every time once, then wait for the shaders the editor compiles on demand (particles don't draw until then).
	const bool bShadersReady = FUETKCapture::WaitForShaders([&]()
	{
		Restart(Component);
		for (float Time : Times)
		{
			SeekTo(Component, Time, SeekDelta);
			Capture->Draw();
		}
	});

	FUETKContactSheet Sheet(Size, Size, Times.Num(), Settings.Columns);
	Restart(Component);
	TArray<TSharedPtr<FJsonValue>> Frames;
	for (int32 FrameIndex = 0; FrameIndex < Times.Num(); ++FrameIndex)
	{
		const float Time = Times[FrameIndex];
		SeekTo(Component, Time, SeekDelta);
		int32 Total = 0;
		TArray<TSharedPtr<FJsonValue>> Counts = ParticleCounts(Component, System, Total);
		TArray<FColor> Pixels;
		if (Capture->Read(Pixels, Settings.bLabels ? FString::Printf(TEXT("t=%.2fs  n=%d"), Time, Total) : FString()))
		{
			Sheet.SetCell(FrameIndex, Pixels);
		}
		TSharedRef<FJsonObject> Frame = MakeShared<FJsonObject>();
		Frame->SetNumberField(TEXT("time"), Time);
		Frame->SetNumberField(TEXT("particles"), Total);
		Frame->SetArrayField(TEXT("emitters"), Counts);
		Frames.Add(MakeShared<FJsonValueObject>(Frame));
	}

	Capture.Reset();
	if (Backdrop)
	{
		Scene->RemoveComponent(Backdrop);
	}
	Scene->RemoveComponent(Component);
	Scene.Reset();

	FString FullPath;
	if (!Sheet.Save(OutputPng, FullPath))
	{
		return Fail(FString::Printf(TEXT("could not write %s"), *FullPath));
	}
	FJson Result = MakeShared<FJsonObject>();
	Result->SetStringField(TEXT("path"), FullPath);
	Result->SetArrayField(TEXT("frames"), Frames);
	auto Vec = [](const FVector& V) { return FString::Printf(TEXT("%.0f, %.0f, %.0f"), V.X, V.Y, V.Z); };
	Result->SetStringField(TEXT("bounds_min"), Vec(Bounds.Min));
	Result->SetStringField(TEXT("bounds_max"), Vec(Bounds.Max));
	Result->SetStringField(TEXT("camera"), Vec(CameraLocation));
	Result->SetNumberField(TEXT("distance"), Distance);
	Result->SetStringField(TEXT("background"), FString::Printf(TEXT("%.2f, %.2f, %.2f"), Background.R, Background.G, Background.B));
	if (!bShadersReady)
	{
		Result->SetStringField(TEXT("warning"), TEXT("shaders were still compiling: some particles may be missing; preview again"));
	}
	return Ok(Result);
}

void UUETKVfxLibrary::ReleaseEditing(UNiagaraSystem* System)
{
	if (System)
	{
		Sessions().Remove(System);
	}
	else
	{
		Sessions().Reset();
	}
}
