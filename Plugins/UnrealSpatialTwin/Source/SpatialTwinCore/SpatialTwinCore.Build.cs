using UnrealBuildTool;
public class SpatialTwinCore : ModuleRules
{
    public SpatialTwinCore(ReadOnlyTargetRules Target) : base(Target)
    {
        PublicSystemLibraries.Add("winsqlite3.lib");
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "Json", "Navmesh", "Chaos", "PhysicsCore" });
    }
}
