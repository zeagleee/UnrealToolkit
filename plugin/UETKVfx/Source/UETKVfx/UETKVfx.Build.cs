using UnrealBuildTool;

public class UETKVfx : ModuleRules
{
	public UETKVfx(ReadOnlyTargetRules Target) : base(Target)
	{
		PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;

		PublicDependencyModuleNames.AddRange(new string[]
		{
			"Core",
			"CoreUObject",
			"Engine",
			"Niagara",
		});

		PrivateDependencyModuleNames.AddRange(new string[]
		{
			"AdvancedPreviewScene",
			"AssetRegistry",
			"ImageCore",
			"ImageWrapper",
			"Json",
			"NiagaraCore",
			"NiagaraEditor",
			"NiagaraShader",
			"RenderCore",
			"RHI",
			"Slate",
			"SlateCore",
			"UETKEditor",
			"UnrealEd",
		});
	}
}
