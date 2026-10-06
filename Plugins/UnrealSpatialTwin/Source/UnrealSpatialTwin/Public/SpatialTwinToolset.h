#pragma once
#include "CoreMinimal.h"
#include "ToolsetRegistry/ToolsetDefinition.h"
#include "Commandlets/Commandlet.h"
#include "SpatialTwinToolset.generated.h"

UCLASS()
class UNREALSPATIALTWIN_API USpatialTwinToolset : public UToolsetDefinition
{
    GENERATED_BODY()
public:
    UFUNCTION(meta=(AICallable),Category="SpatialTwin")
    static FString spatial_twin_status();
    UFUNCTION(meta=(AICallable),Category="SpatialTwin")
    static FString spatial_twin_sync();
    UFUNCTION(meta=(AICallable),Category="SpatialTwin")
    static FString spatial_twin_rebuild();
    /** Start/poll a read-only headless integrity audit. RUNNING is not validation success. */
    UFUNCTION(meta=(AICallable),Category="SpatialTwin")
    static FString spatial_twin_validate();
    UFUNCTION(meta=(AICallable),Category="SpatialTwin")
    static FString spatial_twin_apply_patch(const FString& patch_id,bool save_packages=true);
    UFUNCTION(meta=(AICallable),Category="SpatialTwin")
    static FString spatial_twin_save_patch(const FString& patch_id,bool resume=false);
    /** Journal an official tool batch result before its MCP response. Does not apply or confirm a patch. */
    UFUNCTION(meta=(AICallable),Category="SpatialTwin")
    static FString spatial_twin_record_patch_result(const FString& patch_id,const FString& operations_digest,const FString& result_json);
    UFUNCTION(meta=(AICallable),Category="SpatialTwin")
    static FString spatial_twin_cache_asset(const FString& object_path);
    /** Resolve a persistent Twin instance identity and compare live pose before changing it. */
    UFUNCTION(meta=(AICallable),Category="SpatialTwin")
    static bool spatial_twin_transform_instance(const FString& instance_id,const FTransform& expected_transform,const FTransform& transform);
};

UCLASS()
class UNREALSPATIALTWIN_API USpatialTwinScanCommandlet : public UCommandlet
{
    GENERATED_BODY()
public:
    USpatialTwinScanCommandlet();
    virtual int32 Main(const FString& Params) override;
};
UCLASS()
class UNREALSPATIALTWIN_API USpatialTwinValidateCommandlet : public UCommandlet
{
    GENERATED_BODY()
public:
    USpatialTwinValidateCommandlet();
    virtual int32 Main(const FString& Params) override;
};

UCLASS()
class UNREALSPATIALTWIN_API USpatialTwinTestCommandlet : public UCommandlet
{
    GENERATED_BODY()
public:
    USpatialTwinTestCommandlet();
    virtual int32 Main(const FString& Params) override;
};

UCLASS()
class UNREALSPATIALTWIN_API USpatialTwinNavigationTestCommandlet : public UCommandlet
{
    GENERATED_BODY()
public:
    USpatialTwinNavigationTestCommandlet();
    virtual int32 Main(const FString& Params) override;
};
