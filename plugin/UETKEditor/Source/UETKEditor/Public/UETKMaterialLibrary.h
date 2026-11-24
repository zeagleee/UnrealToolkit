// UnrealToolkit: read material graphs and compile results from scripts. Python: unreal.UETKMaterialLibrary
// (wrapped by editor/mat.py, which builds materials with the engine's MaterialEditingLibrary).
#pragma once

#include "CoreMinimal.h"
#include "Kismet/BlueprintFunctionLibrary.h"
#include "UETKMaterialLibrary.generated.h"

class UMaterialExpression;
class UMaterialInterface;

/**
 * The parts of material editing Python can't reach: which output feeds each input (MaterialEditingLibrary only
 * reports the first match when one node feeds another twice), compile errors, and the material outputs that
 * Python's MaterialProperty enum hides. Every function returns JSON text: {"ok": true, ...} or {"ok": false, "error"}.
 */
UCLASS()
class UETKEDITOR_API UUETKMaterialLibrary : public UBlueprintFunctionLibrary
{
	GENERATED_BODY()

public:
	/**
	 * Every node of a material or material function:
	 *   {"kind": "material"|"function", "props": {the asset's own non-default properties},
	 *    "nodes": [{"id", "class", "x", "y", "desc",
	 *     "inputs": [{"name", "node" (id or null), "output" (index)}], "outputs": [{"name", "mask" ("RGB", "" = all)}],
	 *     "props": {name: value}}],
	 *    "outputs": [{"property", "node", "output"}]}   (material outputs that are connected; materials only)
	 * props holds the editable properties that differ from the class defaults: numbers, bools, strings (enums by
	 * name, objects by path), arrays for colours and vectors, otherwise Unreal's text form.
	 */
	UFUNCTION(BlueprintCallable, Category = "UETK|Material")
	static FString DescribeGraph(UObject* MaterialOrFunction);

	/**
	 * Compile result of a material or material instance on the editor's shader platform:
	 *   {"errors": [...], "error_nodes": [ids], "finished": bool}
	 * bWait waits for the shaders as well; errors in Custom node HLSL only show up then.
	 */
	UFUNCTION(BlueprintCallable, Category = "UETK|Material")
	static FString CompileResult(UMaterialInterface* Material, bool bWait);

	/**
	 * Connect an expression's output (by index) to a material output by name, ignoring case, spaces and
	 * underscores: BaseColor, EmissiveColor, PixelDepthOffset, ShadingModel, CustomData0 (clear coat),
	 * CustomData1 (clear coat roughness), CustomizedUVs0..7, Displacement... An empty From disconnects it.
	 */
	UFUNCTION(BlueprintCallable, Category = "UETK|Material")
	static FString ConnectProperty(UMaterialInterface* Material, UMaterialExpression* From, int32 OutputIndex, const FString& Property);

	/** Every node of a material or material function (MaterialEditingLibrary.get_material_expressions, which
	 *  Python only has from 5.8). */
	UFUNCTION(BlueprintCallable, Category = "UETK|Material")
	static TArray<UMaterialExpression*> ListExpressions(UObject* MaterialOrFunction);

	/** A node's output names as connect_material_expressions takes them: the output's name, or R/G/B/A for an
	 *  unnamed single-channel output, else "" (MaterialEditingLibrary.get_material_expression_output_names, 5.8+). */
	UFUNCTION(BlueprintCallable, Category = "UETK|Material")
	static TArray<FString> ExpressionOutputNames(UMaterialExpression* Expression);

	/** Set any editable property of an object from Unreal's text form, with the editor's change notifications. */
	UFUNCTION(BlueprintCallable, Category = "UETK|Material")
	static FString SetPropertyText(UObject* Object, const FString& Property, const FString& Text);
};
