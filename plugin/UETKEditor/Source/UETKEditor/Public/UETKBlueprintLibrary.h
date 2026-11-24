// UnrealToolkit: edit Blueprint graphs from scripts. Python: unreal.UETKBlueprintLibrary (wrapped by editor/bpedit.py).
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "UETKBlueprintLibrary.generated.h"

class UBlueprint;

/**
 * Blueprint graph editing the way the editor does it (K2 schema connections and defaults, node creators, the
 * compiler's own messages). Every function returns JSON text: {"ok": true, ...} or {"ok": false, "error": "..."}.
 *
 * Addressing: Graph = graph name ("EventGraph", a function or macro name; "" = the event graph). Node = a node id
 * from DescribeGraph (its object name, e.g. "K2Node_CallFunction_3"). Pin = pin name or display name, ignoring case
 * and spaces ("execute", "then", "True", "ReturnValue", "In String").
 *
 * Types (variables, parameters): bool, int, int64, float, byte, string, name, text, vector, rotator, transform,
 * vector2d, linearcolor; a class name or path for an object reference (Actor, /Game/BP_Door); object:X, class:X,
 * softobject:X, struct:X, enum:X; array:<type>.
 */
UCLASS()
class UETKEDITOR_API UUETKBlueprintLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/** {"graphs": [{"name", "kind": event|function|macro, "nodes"}]} */
	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString ListGraphs(UBlueprint* Blueprint);

	/** Every node of a graph: id, class, title, position, and pins (name, direction, type, default, links). */
	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString DescribeGraph(UBlueprint* Blueprint, const FString& Graph);

	/**
	 * Add a node; returns {"id", "pins"}. Kind and Spec (JSON object):
	 *   call {"function", "class"?}  (class omitted: the Blueprint itself, then the Kismet libraries and GameplayStatics)
	 *   event {"name"}               (an overridable event: BeginPlay, Tick, ActorBeginOverlap...; the existing one if any)
	 *   custom_event {"name", "params"?: {name: type}}
	 *   get / set {"variable", "class"?}
	 *   branch, sequence, self
	 *   cast {"class"}
	 *   macro {"name"}               (StandardMacros: ForEachLoop, ForLoop, DoOnce, Gate, IsValid...)
	 *   comment {"text", "width"?, "height"?}
	 *   bound_event {"component", "event"}       (a widget's or component's event: ResumeButton OnClicked,
	 *                                             CollisionBox OnComponentBeginOverlap; the existing one if any)
	 *   node {"class", "props"?: {name: text}}   (any graph node class, e.g. K2Node_EnhancedInputAction with
	 *                                             {"InputAction": "/Game/Input/IA_Jump.IA_Jump"})
	 * X/Y: position; INT32_MIN = below the graph's lowest node.
	 */
	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString AddNode(UBlueprint* Blueprint, const FString& Graph, const FString& Kind, const FString& Spec, int32 X, int32 Y);

	/** Wire FromNode.FromPin to ToNode.ToPin (either direction), adding a conversion node when the types need one. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString Connect(UBlueprint* Blueprint, const FString& Graph, const FString& FromNode, const FString& FromPin, const FString& ToNode, const FString& ToPin);

	/** Break one link (ToNode empty: every link of the pin). */
	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString Disconnect(UBlueprint* Blueprint, const FString& Graph, const FString& FromNode, const FString& FromPin, const FString& ToNode, const FString& ToPin);

	/** Set an unconnected input pin's literal: "1.5", "true", "Hello", "1,2,3", an enum name, an asset path. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString SetPinDefault(UBlueprint* Blueprint, const FString& Graph, const FString& Node, const FString& Pin, const FString& Value);

	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString RemoveNode(UBlueprint* Blueprint, const FString& Graph, const FString& Node);

	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString MoveNode(UBlueprint* Blueprint, const FString& Graph, const FString& Node, int32 X, int32 Y);

	/** Add a member variable. Flags: editable (instance editable), replicated, expose (expose on spawn). */
	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString AddVariable(UBlueprint* Blueprint, const FString& Name, const FString& Type, const FString& DefaultValue,
		const FString& Category, bool bEditable, bool bReplicated, bool bExposeOnSpawn);

	/** Add a function graph with inputs/outputs (JSON objects {name: type}); returns {"graph", "entry", "result"}. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString AddFunction(UBlueprint* Blueprint, const FString& Name, const FString& InputsJson, const FString& OutputsJson, bool bPure);

	/** Compile; returns {"status": ok|warnings|error, "messages": [{"severity", "message"}]} from the compiler log. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Blueprint")
	static FString Compile(UBlueprint* Blueprint);
};
