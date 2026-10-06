#include "SpatialTwinSubsystem.h"
#include "SpatialTwinSourcePaths.h"
#include "Editor.h"
#include "Engine/Level.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Async/Async.h"
#include "NavigationSystem.h"
#include "NavMesh/RecastNavMesh.h"
#include "UObject/ObjectSaveContext.h"
#include "Serialization/JsonSerializer.h"
#include "WorldPartition/DataLayer/DataLayerManager.h"
#include "WorldPartition/DataLayer/DataLayerInstance.h"
#include "WorldPartition/DataLayer/DataLayerAsset.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "HAL/FileManager.h"
#include "UObject/Package.h"
#include "DataLayer/DataLayerEditorSubsystem.h"

int32 USpatialTwinSubsystem::PendingCount() const
{
    return DirtyActors.Num()+DirtyActorIds.Num()+DirtyDescriptors.Num()+DeletedActors.Num()+DirtyAssets.Num()+RegistryChanges.Num()+RegistryRemovals.Num()+SavedPackages.Num()+DirtyPackages.Num()+int32(bNavigationDirty)+int32(bPartitionDirty)+int32(bNavigationOwnersUpgrade)+int32(bDataLayersDirty)+int32(bNestedContainersUpgrade);
}

void USpatialTwinSubsystem::WatchDataLayers()
{
    if(WatchedDataLayers.IsValid())return;
    if(auto Layers=GEditor->GetEditorSubsystem<UDataLayerEditorSubsystem>())
    {
        WatchedDataLayers=Layers;
        // Modify() is coalesced per frame. Explicit layer events preserve later
        // operations in the same official-tool batch without scanning all actors.
        Layers->OnActorDataLayersChanged().AddWeakLambda(this,[this](const TWeakObjectPtr<AActor>& Actor){Dirty(Actor.Get());});
        Layers->OnDataLayerChanged().AddWeakLambda(this,[this](EDataLayerAction Action,const TWeakObjectPtr<const UDataLayerInstance>& Weak,const FName&)
        {
            if(!bReady || bScanning || !GEditor)return;
            auto World=GEditor->GetEditorWorldContext().World();auto Layer=Weak.Get(true);
            if(!World || World->GetOutermost()->GetName()!=MapId || (Layer && Layer->GetWorld()!=World))return;
            bDataLayersDirty=true;LastChange=FPlatformTime::Seconds();
            if(Action==EDataLayerAction::Delete || Action==EDataLayerAction::Reset)
            {
                const FString Id=Layer?MapId+TEXT(":data_layer:")+Layer->GetFName().ToString():FString();
                const FString SQL=TEXT("SELECT DISTINCT source FROM relationships WHERE kind='IN_DATA_LAYER'")+(Id.IsEmpty()?FString():TEXT(" AND target=?"));
                FSpatialTwinSQLiteStatement Q(Database.DB,*SQL);if(!Id.IsEmpty())Q.SetBindingValueByIndex(1,Id);
                while(Q.Step()==ESTSQLiteStepResult::Row){FString Actor;Q.GetColumnValueByIndex(0,Actor);DirtyActorIds.Add(Actor);}
            }
        });
    }
}

void USpatialTwinSubsystem::InitializeEvents()
{
    MapLoadHandle=FEditorDelegates::OnMapLoad.AddWeakLambda(this,[this](const FString&,FCanLoadMap&)
    {if(!bScanning){if(bReady)Sync();DisconnectWorld();}});
    auto& R=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
    RegistryHandles.Add(R.OnAssetAdded().AddUObject(this,&USpatialTwinSubsystem::RegistryChanged,false));
    RegistryHandles.Add(R.OnAssetUpdated().AddUObject(this,&USpatialTwinSubsystem::RegistryChanged,false));
    RegistryHandles.Add(R.OnAssetUpdatedOnDisk().AddUObject(this,&USpatialTwinSubsystem::RegistryChanged,false));
    RegistryHandles.Add(R.OnAssetRemoved().AddUObject(this,&USpatialTwinSubsystem::RegistryChanged,true));
    RegistryHandles.Add(R.OnAssetRenamed().AddWeakLambda(this,[this](const FAssetData& A,const FString& Old)
    {
        auto Apply=[Weak=TWeakObjectPtr<USpatialTwinSubsystem>(this),A,Old]()
        {
            if(auto S=Weak.Get();S && S->bReady && !S->bScanning)
            {S->InvalidateAssetUsers(Old);S->RegistryChanges.Remove(Old);S->RegistryRemovals.Add(Old);S->AssetCache.Remove(Old);S->RegistryChanged(A);}
        };
        if(IsInGameThread())Apply();else AsyncTask(ENamedThreads::GameThread,MoveTemp(Apply));
    }));
    LevelAddedHandle=FWorldDelegates::LevelAddedToWorld.AddWeakLambda(this,[this](ULevel* Level,UWorld* World)
    {
        if(!bReady || bScanning || !Level || World!=GEditor->GetEditorWorldContext().World())return;
        for(auto A:Level->Actors)if(A){UnloadingActors.Remove(ActorId(A));Dirty(A);}
        WatchNavigation(World);
    });
    LevelRemovedHandle=FWorldDelegates::PreLevelRemovedFromWorld.AddWeakLambda(this,[this](ULevel* Level,UWorld* World)
    {
        if(!bReady || bScanning || !Level || World!=GEditor->GetEditorWorldContext().World())return;
        // Streaming unload changes residency, not the existence of saved actors.
        for(auto A:Level->Actors)if(A){UnloadingActors.Add(ActorId(A));DirtyActors.Remove(A);DeletedActors.Remove(ActorId(A));}
    });
    PackageDirtyHandle=UPackage::PackageDirtyStateChangedEvent.AddWeakLambda(this,[this](UPackage* P)
    {
        auto Apply=[Weak=TWeakObjectPtr<USpatialTwinSubsystem>(this),Package=TWeakObjectPtr<UPackage>(P)]()
        {
            auto S=Weak.Get();auto Current=Package.Get();if(!S || !S->bReady || S->bScanning || !Current)return;
            FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT 1 FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,TEXT("asset_package:")+Current->GetName());
            if(Q.Step()==ESTSQLiteStepResult::Row){S->DirtyPackages.Add(Current);S->LastChange=FPlatformTime::Seconds();}
        };
        if(IsInGameThread())Apply();else AsyncTask(ENamedThreads::GameThread,MoveTemp(Apply));
    });
    PackageSavedHandle=UPackage::PackageSavedWithContextEvent.AddWeakLambda(this,[this](const FString&,UPackage* P,FObjectPostSaveContext Context)
    {
        if(!bReady || bScanning || !P || Context.IsCooking() || !FPackageName::IsValidLongPackageName(P->GetName()))return;
        SavedPackages.Add(P);LastChange=FPlatformTime::Seconds();
        FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT id FROM entities WHERE kind='Actor' AND json_extract(source,'$.package')=?"));Q.SetBindingValueByIndex(1,P->GetName());
        while(Q.Step()==ESTSQLiteStepResult::Row){FString Id;Q.GetColumnValueByIndex(0,Id);DirtyActorIds.Add(Id);}
    });
}

void USpatialTwinSubsystem::RemoveEvents()
{
    if(auto Layers=WatchedDataLayers.Get())
    {Layers->OnActorDataLayersChanged().RemoveAll(this);Layers->OnDataLayerChanged().RemoveAll(this);}
    if(auto M=FModuleManager::GetModulePtr<FAssetRegistryModule>(TEXT("AssetRegistry"));M && RegistryHandles.Num()==5)
    {
        auto& R=M->Get();R.OnAssetAdded().Remove(RegistryHandles[0]);R.OnAssetUpdated().Remove(RegistryHandles[1]);
        R.OnAssetUpdatedOnDisk().Remove(RegistryHandles[2]);R.OnAssetRemoved().Remove(RegistryHandles[3]);R.OnAssetRenamed().Remove(RegistryHandles[4]);
    }
    FWorldDelegates::LevelAddedToWorld.Remove(LevelAddedHandle);FWorldDelegates::PreLevelRemovedFromWorld.Remove(LevelRemovedHandle);
    UPackage::PackageSavedWithContextEvent.Remove(PackageSavedHandle);
    UPackage::PackageDirtyStateChangedEvent.Remove(PackageDirtyHandle);
    FEditorDelegates::OnMapLoad.Remove(MapLoadHandle);
    if(auto N=WatchedNavigationSystem.Get()){N->OnNavDataRegisteredEvent.RemoveAll(this);N->OnNavigationGenerationFinishedDelegate.RemoveAll(this);}
    for(auto Weak:WatchedNavigation)if(auto N=Weak.Get())N->OnNavMeshUpdate.RemoveAll(this);
}

void USpatialTwinSubsystem::InvalidateAssetUsers(const FString& Path)
{
    // Drive owner lookups from the dependency closure; the Actor-kind index
    // otherwise joins all actors against all entities before resolving refs.
    FSpatialTwinSQLiteStatement S(Database.DB,TEXT("WITH RECURSIVE refs(id) AS (VALUES(?) UNION SELECT r.source FROM relationships r JOIN refs ON r.target=refs.id WHERE r.kind IN ('USES_ASSET','DEPENDS_ON','IN_PACKAGE')) SELECT DISTINCT a.id FROM refs CROSS JOIN entities e ON e.id=refs.id CROSS JOIN entities a ON a.id=COALESCE(e.actor_id,e.id) WHERE a.kind='Actor'"));
    S.SetBindingValueByIndex(1,TEXT("asset:")+Path);
    while(S.Step()==ESTSQLiteStepResult::Row){FString Id;S.GetColumnValueByIndex(0,Id);DirtyActorIds.Add(Id);}
    // Registry package dependencies point at package entities, not asset objects.
    FString Package=Path;int32 Dot;if(Package.FindChar(TEXT('.'),Dot))Package.LeftInline(Dot);
    FSpatialTwinSQLiteStatement P(Database.DB,TEXT("WITH RECURSIVE refs(id) AS (VALUES(?) UNION SELECT r.source FROM relationships r JOIN refs ON r.target=refs.id WHERE r.kind IN ('USES_ASSET','DEPENDS_ON','IN_PACKAGE')) SELECT DISTINCT a.id FROM refs CROSS JOIN entities e ON e.id=refs.id CROSS JOIN entities a ON a.id=COALESCE(e.actor_id,e.id) WHERE a.kind='Actor'"));
    P.SetBindingValueByIndex(1,TEXT("asset_package:")+Package);
    while(P.Step()==ESTSQLiteStepResult::Row){FString Id;P.GetColumnValueByIndex(0,Id);DirtyActorIds.Add(Id);}
}

void USpatialTwinSubsystem::RegistryChanged(const FAssetData& A,bool Removed)
{
    if(!IsInGameThread())
    {AsyncTask(ENamedThreads::GameThread,[Weak=TWeakObjectPtr<USpatialTwinSubsystem>(this),A,Removed](){if(auto S=Weak.Get())S->RegistryChanged(A,Removed);});return;}
    if(!bReady || bScanning || !FPackageName::IsValidLongPackageName(A.PackageName.ToString()))return;
    const FString Path=A.GetSoftObjectPath().ToString();
    // Registry discovery/update notifications can repeat unchanged saved sources.
    // Do not invalidate every instance merely because the async registry caught up.
    if(!Removed && !RegistryRemovals.Contains(Path) && !RegistryChanges.Contains(Path))
    {
        auto Loaded=FindPackage(nullptr,*A.PackageName.ToString());FString File;
        if((!Loaded || !Loaded->IsDirty()) && FPackageName::DoesPackageExist(A.PackageName.ToString(),&File))
        {
            File=SpatialTwinSourcePath(File);
            const auto Stat=IFileManager::Get().GetStatData(*File);
            if(Stat.bIsValid)
            {
                FSpatialTwinSQLiteStatement Known(Database.DB,TEXT("SELECT 1 FROM package_sources p WHERE p.path=? AND p.signature=? AND EXISTS(SELECT 1 FROM entities WHERE id=? AND class=? AND label=?)"));
                Known.SetBindingValueByIndex(1,File);Known.SetBindingValueByIndex(2,FString::Printf(TEXT("%lld|%lld"),Stat.FileSize,Stat.ModificationTime.GetTicks()));
                Known.SetBindingValueByIndex(3,TEXT("asset:")+Path);Known.SetBindingValueByIndex(4,A.AssetClassPath.ToString());Known.SetBindingValueByIndex(5,A.AssetName.ToString());
                if(Known.Step()==ESTSQLiteStepResult::Row)return;
            }
        }
    }
    if(!RegistryChanges.Contains(Path) && !RegistryRemovals.Contains(Path))InvalidateAssetUsers(Path);
    AssetCache.Remove(Path);
    if(Removed){RegistryChanges.Remove(Path);RegistryRemovals.Add(Path);}
    else{RegistryRemovals.Remove(Path);RegistryChanges.Add(Path,A);}
    LastChange=FPlatformTime::Seconds();
}

bool USpatialTwinSubsystem::IndexAsset(const FAssetData& A)
{
    const FString Path=A.GetSoftObjectPath().ToString(),Id=TEXT("asset:")+Path,PackageId=TEXT("asset_package:")+A.PackageName.ToString();
    FString Source;{FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Id);if(Q.Step()==ESTSQLiteStepResult::Row)Q.GetColumnValueByIndex(0,Source);}
    TSharedPtr<FJsonObject> E;if(Source.IsEmpty() || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),E))E=MakeShared<FJsonObject>();
    E->SetStringField(TEXT("id"),Id);if(!E->HasField(TEXT("kind")))E->SetStringField(TEXT("kind"),TEXT("Asset"));
    E->SetStringField(TEXT("path"),Path);E->SetStringField(TEXT("package"),A.PackageName.ToString());E->SetStringField(TEXT("class"),A.AssetClassPath.ToString());E->SetStringField(TEXT("label"),A.AssetName.ToString());E->SetStringField(TEXT("registry_source"),TEXT("on_disk_and_live_registry"));
    const auto Loaded=FindPackage(nullptr,*A.PackageName.ToString());E->SetBoolField(TEXT("package_dirty"),Loaded && Loaded->IsDirty());
    if(!Database.Put(E))return false;
    if(!Database.Relation(Id,PackageId,TEXT("IN_PACKAGE")) || !IndexAssetPackage(A.PackageName))return false;
    if(auto Cached=AssetCache.Find(Path))*Cached=E;
    return true;
}

bool USpatialTwinSubsystem::IndexAssetPackage(FName Name)
{
    const FString PackageId=TEXT("asset_package:")+Name.ToString();
    FString Source;{FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,PackageId);if(Q.Step()==ESTSQLiteStepResult::Row)Q.GetColumnValueByIndex(0,Source);}
    TSharedPtr<FJsonObject> Package;if(Source.IsEmpty() || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Package))Package=MakeShared<FJsonObject>();
    Package->SetStringField(TEXT("id"),PackageId);Package->SetStringField(TEXT("kind"),TEXT("Asset"));Package->SetStringField(TEXT("path"),Name.ToString());Package->SetStringField(TEXT("asset_type"),TEXT("Package"));
    auto& R=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();TArray<FName> Dependencies;
    const bool Known=R.GetDependencies(Name,Dependencies);
    Package->SetStringField(TEXT("dependencies_state"),Known?TEXT("CURRENT"):TEXT("UNKNOWN"));
    const auto Loaded=FindPackage(nullptr,*Name.ToString());const bool Dirty=Loaded && Loaded->IsDirty();
    Package->SetStringField(TEXT("dependency_source"),TEXT("asset_registry_disk"));Package->SetBoolField(TEXT("package_dirty"),Dirty);
    Package->SetStringField(TEXT("live_dependencies_state"),Dirty?TEXT("UNKNOWN_UNSAVED"):Known?TEXT("CURRENT"):TEXT("UNKNOWN"));
    if(!Database.Put(Package))return false;
    Database.TrackRelations(PackageId);
    {FSpatialTwinSQLiteStatement D(Database.DB,TEXT("DELETE FROM relationships WHERE source=? AND kind='DEPENDS_ON'"));D.SetBindingValueByIndex(1,PackageId);if(D.Step()!=ESTSQLiteStepResult::Done)return false;}
    for(auto Dependency:Dependencies)
    {
        const FString DependencyId=TEXT("asset_package:")+Dependency.ToString();
        FString Existing;{FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,DependencyId);if(Q.Step()==ESTSQLiteStepResult::Row)Q.GetColumnValueByIndex(0,Existing);}
        TSharedPtr<FJsonObject> D=MakeShared<FJsonObject>();
        if(Existing.IsEmpty())
        {
            D->SetStringField(TEXT("id"),DependencyId);D->SetStringField(TEXT("kind"),TEXT("Asset"));D->SetStringField(TEXT("path"),Dependency.ToString());D->SetStringField(TEXT("asset_type"),TEXT("Package"));
        }
        else if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Existing),D))return false;
        // Even unchanged dependency identities are observed in this generation.
        // Otherwise the full-scan commit prunes them and their incoming edges.
        if(!Database.Put(D))return false;
        if(!Database.Relation(PackageId,DependencyId,TEXT("DEPENDS_ON")))return false;
    }
    TArray<TPair<FString,FString>> Owners;
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT e.id,e.source FROM relationships r JOIN entities e ON e.id=r.source WHERE r.target=? AND r.kind='IN_PACKAGE'"));Q.SetBindingValueByIndex(1,PackageId);
     while(Q.Step()==ESTSQLiteStepResult::Row){FString Id,OwnerSource;Q.GetColumnValueByIndex(0,Id);Q.GetColumnValueByIndex(1,OwnerSource);Owners.Emplace(Id,OwnerSource);}}
    for(const auto& Owner:Owners)
    {
        TSharedPtr<FJsonObject> E;if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Owner.Value),E))return false;
        E->SetBoolField(TEXT("package_dirty"),Dirty);if(!Database.Put(E))return false;
        FString Path;if(E->TryGetStringField(TEXT("path"),Path))if(auto Cached=AssetCache.Find(Path))*Cached=E;
    }
    return true;
}

bool USpatialTwinSubsystem::IndexReferencedAssetPackages()
{
    // Run once on full scan or cache migration, never on each actor movement.
    TArray<TPair<FString,FString>> Referenced;
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT id,path FROM entities e WHERE kind IN ('Asset','StaticMesh','Material') AND coalesce(json_extract(source,'$.asset_type'),'')<>'Package' AND EXISTS(SELECT 1 FROM relationships r WHERE r.target=e.id AND r.kind='USES_ASSET')"));
     while(Q.Step()==ESTSQLiteStepResult::Row){FString Id,Path;Q.GetColumnValueByIndex(0,Id);Q.GetColumnValueByIndex(1,Path);Referenced.Emplace(Id,Path);}}
    auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
    for(const auto& Entry:Referenced)
    {
        auto Asset=Registry.GetAssetByObjectPath(FSoftObjectPath(Entry.Value));
        if(Asset.IsValid()){if(!IndexAsset(Asset))return false;}
        // Runtime/non-registry objects retain their source identity; no package
        // dependency graph is fabricated. Readers report missing coverage.
    }
    return Database.Metadata(TEXT("referenced_asset_dependencies_version"),TEXT("1"));
}

bool USpatialTwinSubsystem::UpgradeAssetPackages()
{
    const bool ReferencesReady=Database.ReadMetadata(TEXT("referenced_asset_dependencies_version"))==TEXT("1");
    const bool ScopeReady=Database.ReadMetadata(TEXT("asset_dependency_scope_version"))==TEXT("1");
    if(Database.ReadMetadata(TEXT("asset_package_lifecycle_version"))==TEXT("2") && ReferencesReady && ScopeReady)return true;
    if(!Database.Begin(false,MapId))return false;
    TArray<TPair<FString,FString>> Candidates;
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT id,path FROM entities e WHERE kind='Asset' AND json_extract(source,'$.asset_type')='Package' AND path LIKE '%/__ExternalActors__/%' AND NOT EXISTS(SELECT 1 FROM relationships r WHERE r.target=e.id)"));
     while(Q.Step()==ESTSQLiteStepResult::Row){FString Id,Path;Q.GetColumnValueByIndex(0,Id);Q.GetColumnValueByIndex(1,Path);Candidates.Emplace(Id,Path);}}
    auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();bool Ok=true;
    for(const auto& Candidate:Candidates)
    {
        if(FPackageName::DoesPackageExist(Candidate.Value))continue;
        TArray<FAssetData> Assets;Registry.GetAssetsByPackageName(FName(*Candidate.Value),Assets);
        if(Assets.IsEmpty())Ok=Database.Delete(Candidate.Key) && Ok;
    }
    Ok=Database.Metadata(TEXT("asset_package_lifecycle_version"),TEXT("2")) && Ok;
    if(!ReferencesReady)Ok=IndexReferencedAssetPackages() && Ok;
    if(!ScopeReady)
    {
        TArray<FName> Packages;
        {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT path FROM entities WHERE kind='Asset' AND json_extract(source,'$.asset_type')='Package'"));
         while(Q.Step()==ESTSQLiteStepResult::Row){FString Path;Q.GetColumnValueByIndex(0,Path);Packages.Emplace(*Path);}}
        for(auto Name:Packages)Ok=IndexAssetPackage(Name) && Ok;
        Ok=Database.Metadata(TEXT("asset_dependency_scope_version"),TEXT("1")) && Ok;
    }
    if(!Ok){Database.Rollback();return false;}
    return Database.Commit(MapId,false);
}

void USpatialTwinSubsystem::WatchNavigation(UWorld* World)
{
    // Editor-layer subsystem initialization needs an existing world context.
    // Bind after scan/resume, not from the engine's early subsystem constructor.
    WatchDataLayers();
    auto N=FNavigationSystem::GetCurrent<UNavigationSystemV1>(World);if(!N)return;
    if(WatchedNavigationSystem!=N)
    {
        if(auto Old=WatchedNavigationSystem.Get()){Old->OnNavDataRegisteredEvent.RemoveAll(this);Old->OnNavigationGenerationFinishedDelegate.RemoveAll(this);}
        WatchedNavigationSystem=N;N->OnNavDataRegisteredEvent.AddUniqueDynamic(this,&USpatialTwinSubsystem::NavigationChanged);
        N->OnNavigationGenerationFinishedDelegate.AddUniqueDynamic(this,&USpatialTwinSubsystem::NavigationChanged);
    }
    for(auto Data:N->NavDataSet)if(auto R=Cast<ARecastNavMesh>(Data);R && !WatchedNavigation.Contains(R))
    {WatchedNavigation.Add(R);R->OnNavMeshUpdate.AddWeakLambda(this,[this,R](){NavigationChanged(R);});}
}

void USpatialTwinSubsystem::NavigationChanged(ANavigationData* Data)
{
    if(!bReady || !Data || Data->GetWorld()!=GEditor->GetEditorWorldContext().World())return;
    WatchNavigation(Data->GetWorld());++NavigationChangeSerial;bNavigationDirty=true;LastChange=FPlatformTime::Seconds();
}

bool USpatialTwinSubsystem::IndexDataLayers(UWorld* World)
{
    auto Manager=UDataLayerManager::GetDataLayerManager(World);if(!Manager)return true;
    for(const auto Layer:Manager->GetDataLayerInstances())
    {
        auto E=MakeShared<FJsonObject>();const FString Id=MapId+TEXT(":data_layer:")+Layer->GetFName().ToString();
        E->SetStringField(TEXT("id"),Id);E->SetStringField(TEXT("kind"),TEXT("DataLayer"));E->SetStringField(TEXT("label"),Layer->GetDataLayerShortName());E->SetStringField(TEXT("path"),Layer->GetPathName());
        E->SetBoolField(TEXT("runtime"),Layer->IsRuntime());E->SetBoolField(TEXT("loaded_in_editor"),Layer->IsLoadedInEditor());E->SetBoolField(TEXT("visible"),Layer->IsVisible());
        E->SetBoolField(TEXT("effective_loaded_in_editor"),Layer->IsEffectiveLoadedInEditor());E->SetBoolField(TEXT("effective_visible"),Layer->IsEffectiveVisible());
        E->SetNumberField(TEXT("initial_runtime_state"),(int32)Layer->GetInitialRuntimeState());
        Database.TrackRelations(Id);
        {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("DELETE FROM relationships WHERE source=?"));Q.SetBindingValueByIndex(1,Id);if(Q.Step()!=ESTSQLiteStepResult::Done)return false;}
        if(auto Parent=Layer->GetParent()){const FString ParentId=MapId+TEXT(":data_layer:")+Parent->GetFName().ToString();E->SetStringField(TEXT("parent_id"),ParentId);if(!Database.Relation(Id,ParentId,TEXT("ATTACHED_TO")))return false;}
        if(auto Asset=Layer->GetAsset())
        {const FString AssetId=TEXT("asset:")+Asset->GetPathName();E->SetStringField(TEXT("asset_id"),AssetId);if(!IndexAsset(FAssetData(Asset)) || !Database.Relation(Id,AssetId,TEXT("USES_ASSET")))return false;}
        if(!Database.Put(E))return false;
    }
    TArray<FString> Removed;{FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT id FROM entities WHERE kind='DataLayer' AND generation<>?"));Q.SetBindingValueByIndex(1,Database.Generation);while(Q.Step()==ESTSQLiteStepResult::Row){FString Id;Q.GetColumnValueByIndex(0,Id);Removed.Add(Id);}}
    for(const auto& Id:Removed)if(!Database.Delete(Id))return false;
    return true;
}
