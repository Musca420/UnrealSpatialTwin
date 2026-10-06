#include "SpatialTwinSubsystem.h"
#include "SpatialTwinSourcePaths.h"
#include "Editor.h"
#include "EngineUtils.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "WorldPartition/WorldPartition.h"
#include "WorldPartition/ActorDescContainerInstance.h"
#include "Misc/PackageName.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonSerializer.h"

void USpatialTwinSubsystem::QueueNestedContainerUpgrade(UWorld* World)
{
    bNestedContainersUpgrade=Database.ReadMetadata(TEXT("nested_container_export_version"))!=TEXT("2");
    if(!bNestedContainersUpgrade)return;
    auto WP=World->GetWorldPartition();auto Root=WP?WP->GetActorDescContainerInstance():nullptr;
    TSet<FString> Present;
    TArray<UActorDescContainerInstance*> Pending;
    if(Root)for(auto Child:Root->GetChildContainerInstances())Pending.Add(Child.Value);
    while(Pending.Num())
    {
        auto C=Pending.Pop();WatchContainer(C);
        NestedUpgradeLevels.Add(MapId+TEXT(":level:")+C->GetContainerPackage().ToString());
        for(FActorDescInstanceList::TIterator<> It(C);It;++It)
        {
            const FString Id=MapId+TEXT(":actor:")+C->GetContainerID().GetActorGuid(It->GetGuid()).ToString(EGuidFormats::Digits);
            Present.Add(Id);DescriptorDirty(C,*It);
        }
        for(auto Child:C->GetChildContainerInstances())Pending.Add(Child.Value);
    }
    // Old scans could collapse many instances onto a source GUID. Enumerating
    // current child descriptors recovers even copies absent from the old DB.
    // Root actors/assets/geometry are not rescanned or discarded.
    FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT id,parent_id FROM entities WHERE kind='Actor' AND json_extract(source,'$.container_id') IS NOT NULL AND json_extract(source,'$.container_id')<>'00000000000000000000000000000000'"));
    while(Q.Step()==ESTSQLiteStepResult::Row)
    {
        FString Id,Level;Q.GetColumnValueByIndex(0,Id);Q.GetColumnValueByIndex(1,Level);NestedUpgradeLevels.Add(Level);if(Present.Contains(Id))continue;
        FGuid Guid;const FString Prefix=MapId+TEXT(":actor:");
        auto CurrentRoot=Root && Id.StartsWith(Prefix) && FGuid::Parse(Id.RightChop(Prefix.Len()),Guid)?Root->GetActorDescInstance(Guid):nullptr;
        if(CurrentRoot)DescriptorDirty(Root,CurrentRoot);
        else {DeletedActors.Add(Id);DirtyActorIds.Remove(Id);DirtyDescriptors.Remove(Id);}
    }
}

void USpatialTwinSubsystem::QueueTransientActors(UWorld* World)
{
    TSet<FString> TransientClasses;
    for(TActorIterator<AActor> It(World);It;++It)
    {
        if(It->HasAnyFlags(RF_Transient) || It->GetClass()->HasAnyClassFlags(CLASS_Transient))DirtyActors.Add(*It);
        if(It->GetClass()->HasAnyClassFlags(CLASS_Transient))TransientClasses.Add(It->GetClass()->GetPathName());
    }
    // Session objects are not serialized with their map. Package signatures
    // cannot prove that their old GUID still exists after reopening that map.
    FString SQL=TEXT("SELECT id,path FROM entities WHERE kind='Actor' AND json_type(source,'$.descriptor_guid') IS NULL AND (json_extract(source,'$.transient')=1");
    for(const auto& Class:TransientClasses){(void)Class;SQL+=TEXT(" OR class=?");}SQL+=TEXT(")");
    FSpatialTwinSQLiteStatement Q(Database.DB,*SQL);int32 Bind=1;
    for(const auto& Class:TransientClasses)Q.SetBindingValueByIndex(Bind++,Class);
    while(Q.Step()==ESTSQLiteStepResult::Row)
    {
        FString Id,Path;Q.GetColumnValueByIndex(0,Id);Q.GetColumnValueByIndex(1,Path);
        auto Current=FindObject<AActor>(nullptr,*Path);
        if(!Current || Current->GetWorld()!=World || ActorId(Current)!=Id)
        {DeletedActors.Add(Id);DirtyActorIds.Remove(Id);}
    }
}

bool USpatialTwinSubsystem::ReconcileSources(const TMap<FString,FString>& Sources)
{
    TMap<FString,FString> Previous;TSet<FString> Changed,Packages;
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT path,signature FROM package_sources"));while(Q.Step()==ESTSQLiteStepResult::Row){FString Path,Signature;Q.GetColumnValueByIndex(0,Path);Q.GetColumnValueByIndex(1,Signature);Path=SpatialTwinSourcePath(Path);Previous.Add(Path,Signature);auto Current=Sources.Find(Path);if(!Current || *Current!=Signature)Changed.Add(Path);}}
    for(const auto& Entry:Sources)if(!Previous.Contains(Entry.Key))Changed.Add(Entry.Key);
    for(const auto& Path:Changed)
    {
        FString Package;
        if((!Path.EndsWith(TEXT(".uasset")) && !Path.EndsWith(TEXT(".umap"))) || !SpatialTwinSourcePackage(Path,Package))
        {Database.Error=TEXT("Project configuration or module changed; explicit reconciliation required: ")+Path;return false;}
        Packages.Add(Package);
    }
    // After an editor crash, reconcile only packages whose previously observed
    // unsaved state may differ from the now authoritative world/package sources.
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT DISTINCT json_extract(source,'$.package') FROM entities WHERE kind IN ('Actor','Asset','StaticMesh','Material') AND json_extract(source,'$.package_dirty')=1"));while(Q.Step()==ESTSQLiteStepResult::Row){FString Package;Q.GetColumnValueByIndex(0,Package);if(!Package.IsEmpty())Packages.Add(Package);}}
    if(Packages.IsEmpty())return true;
    ColdChangedPackages=Packages;
    const double Started=FPlatformTime::Seconds();auto World=GEditor->GetEditorWorldContext().World();
    auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
    TArray<FString> ChangedFiles;for(const auto& Package:Packages){FString Path;if(FPackageName::DoesPackageExist(Package,&Path))ChangedFiles.Add(Path);}
    // Reconcile the changed packages, not the entire mounted Asset Registry.
    {TGuardValue<bool> Scanning(bScanning,true);Registry.ScanFilesSynchronous(ChangedFiles,true);}
    TSet<FString> PresentActors;
    for(TActorIterator<AActor> It(World);It;++It)if(Packages.Contains(It->GetPackage()->GetName())){DirtyActors.Add(*It);PresentActors.Add(ActorId(*It));}
    TArray<UActorDescContainerInstance*> Containers;if(auto WP=World->GetWorldPartition())Containers.Add(WP->GetActorDescContainerInstance());
    while(Containers.Num())if(auto C=Containers.Pop())
    {
        WatchContainer(C);
        for(FActorDescInstanceList::TIterator<> It(C);It;++It)if(Packages.Contains(It->GetActorPackage().ToString()))
        {const FString Id=MapId+TEXT(":actor:")+C->GetContainerID().GetActorGuid(It->GetGuid()).ToString(EGuidFormats::Digits);DirtyDescriptors.Add(Id,{C,It->GetGuid()});PresentActors.Add(Id);}
        for(const auto& Child:C->GetChildContainerInstances())Containers.Add(Child.Value);
    }
    for(const auto& Package:Packages)
    {
        TArray<FAssetData> Assets;Registry.GetAssetsByPackageName(FName(*Package),Assets);
        TSet<FString> PresentAssets;for(const auto& A:Assets){const FString Path=A.GetSoftObjectPath().ToString();PresentAssets.Add(TEXT("asset:")+Path);RegistryChanges.Add(Path,A);AssetCache.Remove(Path);InvalidateAssetUsers(Path);}
        FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT id,kind,path FROM entities WHERE json_extract(source,'$.package')=? OR json_extract(source,'$.external_actor_package')=?"));Q.SetBindingValueByIndex(1,Package);Q.SetBindingValueByIndex(2,Package);
        while(Q.Step()==ESTSQLiteStepResult::Row)
        {
            FString Id,Kind,Path;Q.GetColumnValueByIndex(0,Id);Q.GetColumnValueByIndex(1,Kind);Q.GetColumnValueByIndex(2,Path);
            if(Kind==TEXT("Actor"))
            {if(!PresentActors.Contains(Id)){DeletedActors.Add(Id);DirtyActorIds.Remove(Id);}else if(!DirtyDescriptors.Contains(Id))DirtyActorIds.Add(Id);}
            else if((Kind==TEXT("Asset") || Kind==TEXT("StaticMesh") || Kind==TEXT("Material")) && !PresentAssets.Contains(Id))
            {InvalidateAssetUsers(Path);RegistryRemovals.Add(Path);AssetCache.Remove(Path);}
        }
    }
    // The normal transactional synchronizer exports only affected source owners.
    if(!Sync())return false;
    ColdChangedPackages.Reset();
    if(!Database.Begin(false,MapId))return false;
    if(!SavePackageSources()){Database.Rollback();return false;}
    if(!Database.Commit(MapId,false))return false;
    auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("state"),TEXT("RECONCILED"));Result->SetNumberField(TEXT("packages"),Packages.Num());Result->SetNumberField(TEXT("seconds"),FPlatformTime::Seconds()-Started);Result->SetNumberField(TEXT("revision"),Database.Revision);Result->SetBoolField(TEXT("full_scan"),false);
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Result),*(Database.Root/TEXT("last_reconcile_metrics.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    return true;
}
