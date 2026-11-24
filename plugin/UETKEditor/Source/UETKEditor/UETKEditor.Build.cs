using UnrealBuildTool;

public class UETKEditor : ModuleRules
{
	public UETKEditor(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"ImageCore",
			"InputCore",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"AdvancedPreviewScene",
			"AssetRegistry",
			"AssetTools",
			"BlueprintGraph",
			"DataValidation",
			"Json",
			"RenderCore",
			"RHI",
			"Slate",
			"SlateCore",
			"UMG",
			"UMGEditor",
			"UnrealEd",
		});
	}
}
