using UnrealBuildTool;
public class UnrealSpatialTwin : ModuleRules
{
    public UnrealSpatialTwin(ReadOnlyTargetRules Target) : base(Target)
    {
        PCHUsage = PCHUsageMode.UseExplicitOrSharedPCHs;
        PublicDependencyModuleNames.AddRange(new[] { "Core", "CoreUObject", "Engine", "SpatialTwinCore", "ToolsetRegistry" });
        PrivateDependencyModuleNames.AddRange(new[] { "ModelContextProtocolEngine", "AIModule", "DataLayerEditor" });
        PrivateDependencyModuleNames.AddRange(new[] { "UnrealEd", "EditorSubsystem", "Json", "AssetRegistry", "Projects", "NavigationSystem", "Navmesh", "Landscape", "PhysicsCore", "Chaos", "Slate", "SlateCore", "ToolMenus", "RenderCore", "MeshDescription", "StaticMeshDescription" });
    }
}
