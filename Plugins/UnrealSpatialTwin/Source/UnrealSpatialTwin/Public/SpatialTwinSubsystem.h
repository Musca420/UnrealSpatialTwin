#pragma once
#include "CoreMinimal.h"
#include "EditorSubsystem.h"
#include "SpatialTwinDatabase.h"
#include "Containers/Ticker.h"
#include "AssetRegistry/AssetData.h"
#include "SpatialTwinSubsystem.generated.h"

UCLASS()
class UNREALSPATIALTWIN_API USpatialTwinSubsystem : public UEditorSubsystem
{
    GENERATED_BODY()
public:
    virtual void Initialize(FSubsystemCollectionBase& Collection) override;
    virtual void Deinitialize() override;
    bool Rebuild(UWorld* World=nullptr);
    bool Sync();
    bool CacheAsset(const FString& ObjectPath);
    bool TransformInstance(const FString& Id,const FTransform& Expected,const FTransform& Transform);
    bool Resume();
    bool UpgradeAssetPackages();
    bool CheckpointSources(const FString& Root);
    bool RecordSavedPackage(class UPackage* Package);
    FString Status() const;
    uint64 GetMeshGeometryExportAttemptCount() const { return MeshGeometryExportAttempts; }
    FString Validate();
    FSpatialTwinDatabase Database;
    FString MapId;
    FString ActorId(AActor* Actor) const;
    bool ExportActor(AActor* Actor,const FString& Id=FString());
    void Dirty(UObject* Object);
    bool RefreshPartition();
    FString WorldRoot(UWorld* World) const;
private:
    uint64 MeshGeometryExportAttempts=0;
    bool bScanning=false,bReady=false;
    bool bFullScan=false;
    TSet<FString> FullScanActors;
    int32 DescriptorReusedActors=0;
    bool bResumeAttempted=false;
    bool bVerifiedBaseline=false;
    void RestoreInstanceIds(class UInstancedStaticMeshComponent* Component);
    TMap<FString,FString> PackageSources();
    bool SavePackageSources();
    TSet<TWeakObjectPtr<AActor>> DirtyActors;
    TSet<TWeakObjectPtr<UObject>> DirtyAssets;
    TSet<FString> DeletedActors;
    TSet<FString> DirtyActorIds;
    struct FDescriptorPending {TWeakObjectPtr<class UActorDescContainerInstance> Container;FGuid Guid;};
    TMap<FString,FDescriptorPending> DirtyDescriptors;
    TSet<TWeakObjectPtr<class UActorDescContainerInstance>> WatchedContainers;
    TMap<FGuid,TWeakObjectPtr<class UActorDescContainerInstance>> InstanceContainers;
    void QueueInstanceChildren(AActor* Actor,bool Removed=false);
    void QueueNestedContainerUpgrade(UWorld* World);
    bool bNestedContainersUpgrade=false;
    TSet<FString> NestedUpgradeLevels;
    TMap<FString,FAssetData> RegistryChanges;
    TSet<FString> RegistryRemovals;
    TSet<TWeakObjectPtr<UPackage>> SavedPackages;
    TSet<TWeakObjectPtr<UPackage>> DirtyPackages;
    TSet<FString> UnloadingActors;
    TSet<TWeakObjectPtr<class ARecastNavMesh>> WatchedNavigation;
    TWeakObjectPtr<class UNavigationSystemV1> WatchedNavigationSystem;
    TWeakObjectPtr<class UDataLayerEditorSubsystem> WatchedDataLayers;
    TArray<FDelegateHandle> RegistryHandles;
    FDelegateHandle LevelAddedHandle,LevelRemovedHandle,PackageSavedHandle,PackageDirtyHandle,MapLoadHandle;
    void DisconnectWorld();
    bool PublishWorld();
    bool bNavigationDirty=false,bNavigationRefreshPending=false,bPartitionDirty=false,bNavigationOwnersUpgrade=false,bDataLayersDirty=false;
    uint64 NavigationChangeSerial=0;
    double LastNavigationPoll=0;
    int32 PendingCount() const;
    bool NeedsBackgroundTick() const;
    void RegistryChanged(const FAssetData& Asset,bool Removed=false);
    bool IndexAsset(const FAssetData& Asset);
    bool IndexAssetPackage(FName PackageName);
    bool EnsureAssetPackage(UObject* Asset);
    bool IndexReferencedAssetPackages();
    bool IndexDataLayers(UWorld* World);
    bool ReconcileSources(const TMap<FString,FString>& Sources);
    void QueueTransientActors(UWorld* World);
    void InvalidateAssetUsers(const FString& Path);
    void WatchNavigation(UWorld* World);
    UFUNCTION() void NavigationChanged(class ANavigationData* Data);
    void InitializeEvents();
    void WatchDataLayers();
    void RemoveEvents();
    FTSTicker::FDelegateHandle Ticker;
    TArray<FDelegateHandle> Handles;
    double LastChange=0,LastFlush=0,LastHeartbeat=0;
    bool Tick(float Delta);
    bool ScanContainer(class UActorDescContainerInstance* Container,class FSpatialTwinDescriptorScope& Scope);
    bool ScanDescriptor(class UActorDescContainerInstance* Container,class FWorldPartitionActorDescInstance* Descriptor,class FSpatialTwinDescriptorScope& Scope);
    void WatchContainer(class UActorDescContainerInstance* Container);
    void DescriptorDirty(class UActorDescContainerInstance* Container,class FWorldPartitionActorDescInstance* Descriptor,bool Removed=false);
    TSharedPtr<FJsonObject> MeshAsset(class UStaticMesh* Mesh);
    TSharedPtr<FJsonObject> CollisionBody(class UBodySetup* Body);
    bool IndexAssets();
    void ExportNavigation(UObject* Object,const FTransform& Transform,const TSharedPtr<FJsonObject>& Entity);
    FString SaveGeometry(const TArray<FVector>& Vertices,const TArray<FIntVector>& Triangles,const FString& Role);
    TMap<FString,TSharedPtr<FJsonObject>> AssetCache;
    TMap<FString,TArray<FString>> InstanceIds;
    TSet<FString> ColdChangedPackages;
    TMap<FString,FString> InstanceIdentityState;
    struct FInstanceTransaction {TArray<FString> Before,After;bool Applied=true;};
    TMap<FString,TArray<FString>> PendingInstanceTransactions;
    TMap<FString,FInstanceTransaction> InstanceTransactions;
    void InstanceModified(UObject* Object);
    void InstanceTransacted(UObject* Object,const class FTransactionObjectEvent& Event);
    FString ComponentId(UActorComponent* Component) const;
};
