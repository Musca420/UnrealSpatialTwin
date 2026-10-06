#include "SpatialTwinSubsystem.h"
#include "Misc/Paths.h"
#include "Misc/Parse.h"
#include "Misc/CommandLine.h"
#include "Misc/SecureHash.h"
#include "Misc/FileHelper.h"
#include "WorldPartition/ActorDescContainerInstance.h"
#include "NavigationSystem.h"
#include "NavMesh/RecastNavMesh.h"

namespace {
FString BaseRoot()
{
    FString Root=FPaths::ConvertRelativePathToFull(FPaths::ProjectSavedDir()/TEXT("SpatialTwin"));
    FParse::Value(FCommandLine::Get(),TEXT("SpatialTwinRoot="),Root);return FPaths::ConvertRelativePathToFull(Root);
}
}

FString USpatialTwinSubsystem::WorldRoot(UWorld* World) const
{
    const FString Base=BaseRoot(),Map=World->GetOutermost()->GetName();
    // Retain the original store in place. Other maps get independent histories,
    // patches and writer leases; changing maps never replaces an earlier world.
    if(FPaths::FileExists(Base/TEXT("world.sqlite")))
    {
        FSpatialTwinSQLiteDatabase Read;
        if(Read.Open(*(Base/TEXT("world.sqlite")),ESTSQLiteOpenMode::ReadOnly))
        {
            FSpatialTwinSQLiteStatement Q(Read,TEXT("SELECT map FROM snapshots WHERE state='READY' ORDER BY revision DESC LIMIT 1"));FString Existing;
            if(Q.Step()==ESTSQLiteStepResult::Row && Q.GetColumnValueByIndex(0,Existing) && Existing!=Map)
                return Base/TEXT("maps")/FMD5::HashAnsiString(*Map);
        }
    }
    return Base;
}

void USpatialTwinSubsystem::DisconnectWorld()
{
    if(!Database.Root.IsEmpty())IFileManager::Get().Delete(*(Database.Root/TEXT("heartbeat.json")),false,true);
    bReady=false;bVerifiedBaseline=false;bResumeAttempted=false;bNavigationDirty=false;bNavigationRefreshPending=false;bPartitionDirty=false;bNavigationOwnersUpgrade=false;bDataLayersDirty=false;
    DirtyActors.Reset();DirtyAssets.Reset();DeletedActors.Reset();DirtyActorIds.Reset();DirtyDescriptors.Reset();
    RegistryChanges.Reset();RegistryRemovals.Reset();SavedPackages.Reset();DirtyPackages.Reset();UnloadingActors.Reset();AssetCache.Reset();
    InstanceIds.Reset();InstanceIdentityState.Reset();ColdChangedPackages.Reset();PendingInstanceTransactions.Reset();InstanceTransactions.Reset();
    for(auto Weak:WatchedContainers)if(auto C=Weak.Get()){C->OnActorDescInstanceAddedEvent.RemoveAll(this);C->OnActorDescInstanceRemovedEvent.RemoveAll(this);C->OnActorDescInstanceUpdatedEvent.RemoveAll(this);}
    WatchedContainers.Reset();InstanceContainers.Reset();bNestedContainersUpgrade=false;NestedUpgradeLevels.Reset();
    if(auto N=WatchedNavigationSystem.Get()){N->OnNavDataRegisteredEvent.RemoveAll(this);N->OnNavigationGenerationFinishedDelegate.RemoveAll(this);}
    for(auto Weak:WatchedNavigation)if(auto N=Weak.Get())N->OnNavMeshUpdate.RemoveAll(this);
    WatchedNavigation.Reset();WatchedNavigationSystem.Reset();Database.Close();
}

bool USpatialTwinSubsystem::PublishWorld()
{
    const FString Base=BaseRoot();FString Relative=Database.Root;
    FPaths::MakePathRelativeTo(Relative,*(Base+TEXT("/")));if(Relative.IsEmpty())Relative=TEXT(".");
    auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("map"),MapId);J->SetStringField(TEXT("directory"),Relative);
    const FString Path=Base/TEXT("active_world.json");
    if(FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(J),*(Path+TEXT(".tmp")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM)
       && IFileManager::Get().Move(*Path,*(Path+TEXT(".tmp")),true))return true;
    Database.Error=TEXT("Could not publish active world discovery file");return false;
}
