#include "SpatialTwinSubsystem.h"
#include "SpatialTwinSQLite.h"
#include "Editor.h"
#include "FileHelpers.h"
#include "Engine/Level.h"
#include "Engine/StaticMeshActor.h"
#include "Components/StaticMeshComponent.h"
#include "LevelInstance/LevelInstanceActor.h"
#include "WorldPartition/WorldPartition.h"
#include "WorldPartition/ActorDescContainerInstance.h"
#include "WorldPartition/WorldPartitionActorDescInstance.h"
#include "Misc/App.h"
#include "Misc/FileHelper.h"
#include "Misc/PackageName.h"
#include "AssetCompilingManager.h"
#include "Serialization/JsonSerializer.h"
#include "ScopedTransaction.h"
#include "Editor/TransBuffer.h"
#include "EngineUtils.h"

int32 RunSpatialTwinNestedSourceFixture(const FString& Params)
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 390;
    const bool VerifyOnly=FParse::Param(*Params,TEXT("NestedSourceVerify"));
    const bool Delete=FParse::Param(*Params,TEXT("NestedSourceDelete"));
    if(!VerifyOnly)
    {
        // Edit the real shared source in a separate process, while the parent
        // Twin is disconnected. Resume must update every affected copy only.
        if(!FEditorFileUtils::LoadMap(TEXT("/Game/NestedLeaf"),false,true))return 391;
        auto W=GEditor->GetEditorWorldContext().World();AActor* Cube=nullptr;
        for(TActorIterator<AStaticMeshActor> It(W);It;++It)if(It->GetActorLabel()==TEXT("NestedLeafCube")){Cube=*It;break;}
        if(!Cube)return 392;
        auto Package=Cube->GetPackage();
        if(Delete){if(!W->EditorDestroyActor(Cube,true))return 393;}
        else {Cube->Modify();Cube->SetActorLocation(FVector(450,100,75));Cube->PostEditMove(true);}
        if(!UEditorLoadingAndSavingUtils::SavePackages({Package},true))return 394;
    }
    if(!FEditorFileUtils::LoadMap(TEXT("/Game/NestedParent"),false,true))return 395;
    auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();
    if(!S || !S->Resume() || !S->Sync())return 396;
    TArray<TSharedPtr<FJsonValue>> Leaves;TSet<int32> Positions;
    FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE kind='Actor' AND label='NestedLeafCube' ORDER BY id"));
    while(Q.Step()==ESTSQLiteStepResult::Row)
    {
        FString Source;Q.GetColumnValueByIndex(0,Source);TSharedPtr<FJsonObject> E;
        if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),E))return 397;
        auto P=E->GetObjectField(TEXT("transform"))->GetArrayField(TEXT("position"));
        if(!FMath::IsNearlyEqual(P[1]->AsNumber(),2100.,.01) || !FMath::IsNearlyEqual(P[2]->AsNumber(),75.,.01))return 398;
        Positions.Add(FMath::RoundToInt(P[0]->AsNumber()));Leaves.Add(MakeShared<FJsonValueObject>(E));
    }
    Q.Destroy();
    if(Delete?Leaves.Num()!=0:Leaves.Num()!=2 || !Positions.Contains(11450) || !Positions.Contains(21450))return 399;
    const auto Revision=S->Database.Revision;if(!S->Sync() || S->Database.Revision!=Revision)return 400;
    auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("state"),TEXT("PASS"));Result->SetNumberField(TEXT("revision"),Revision);
    Result->SetBoolField(TEXT("verify_only"),VerifyOnly);Result->SetBoolField(TEXT("deleted"),Delete);
    Result->SetArrayField(TEXT("leaves"),Leaves);Result->SetBoolField(TEXT("full_scan_requested"),false);Result->SetBoolField(TEXT("second_sync_noop"),true);
    const FString Name=FString(TEXT("native-nested-source"))+(Delete?TEXT("-delete"):TEXT("-move"))+(VerifyOnly?TEXT("-cold"):TEXT(""))+TEXT(".json");
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Result),*(S->Database.Root/Name),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinNestedSourceTest PASS revision=%lld"),Revision);return 0;
}

int32 RunSpatialTwinNestedResumeFixture()
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 380;
    if(!FEditorFileUtils::LoadMap(TEXT("/Game/NestedParent"),false,true))return 381;
    auto W=GEditor->GetEditorWorldContext().World();auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();
    if(!S || !S->Resume() || !S->Sync()){UE_LOG(LogTemp,Error,TEXT("Nested resume failed: %s"),S?*S->Status():TEXT("No subsystem"));return 382;}
    if(S->Database.ReadMetadata(TEXT("nested_container_export_version"))!=TEXT("2"))return 383;
    TArray<TSharedPtr<FJsonValue>> Leaves;
    FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT id,source FROM entities WHERE kind='Actor' AND label='NestedLeafCube' ORDER BY id"));
    while(Q.Step()==ESTSQLiteStepResult::Row)
    {
        FString Id,Source;Q.GetColumnValueByIndex(0,Id);Q.GetColumnValueByIndex(1,Source);
        TSharedPtr<FJsonObject> E;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),E);
        if(E->GetStringField(TEXT("coverage"))!=TEXT("loaded_native"))return 384;
        Leaves.Add(MakeShared<FJsonValueObject>(E));
    }
    if(Leaves.Num()!=3)return 385;
    Q.Destroy();const auto Revision=S->Database.Revision;if(!S->Sync() || S->Database.Revision!=Revision)return 386;
    auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("state"),TEXT("PASS"));Result->SetNumberField(TEXT("revision"),Revision);
    Result->SetArrayField(TEXT("leaves"),Leaves);Result->SetBoolField(TEXT("full_scan_requested"),false);Result->SetBoolField(TEXT("second_sync_noop"),true);
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Result),*(S->Database.Root/TEXT("native-nested-resume.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinNestedResumeTest PASS revision=%lld"),Revision);return 0;
}

// Real saved Level Instances, independent of any game content. Never run on a game project.
int32 RunSpatialTwinNestedFixture()
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 360;
    auto Fail=[](int32 Code){UE_LOG(LogTemp,Error,TEXT("SpatialTwinNestedTest FAILED check=%d"),Code);return Code;};
    auto SaveActor=[](AActor* A){A->SetPackageExternal(true);return UEditorLoadingAndSavingUtils::SavePackages({A->GetPackage()},true);};
    auto NewExternalMap=[](const TCHAR* Package)
    {
        auto W=GEditor->NewMap(false);W->PersistentLevel->SetUseExternalActors(true);
        return UEditorLoadingAndSavingUtils::SaveMap(W,Package)?W:nullptr;
    };
    auto Leaf=NewExternalMap(TEXT("/Game/NestedLeaf"));if(!Leaf)return Fail(361);
    auto Cube=Leaf->SpawnActor<AStaticMeshActor>();Cube->SetActorLabel(TEXT("NestedLeafCube"));
    Cube->GetStaticMeshComponent()->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube")));
    Cube->SetActorLocation(FVector(250,100,75));FAssetCompilingManager::Get().FinishAllCompilation();
    if(!SaveActor(Cube))return Fail(362);
    auto AddInstance=[&](UWorld* W,const TCHAR* Package,const FVector& Position)
    {
        auto A=W->SpawnActor<ALevelInstance>();A->SetActorLocation(Position);
        if(!A->SetWorldAsset(TSoftObjectPtr<UWorld>(FSoftObjectPath(FString(Package)+TEXT(".")+FPackageName::GetShortName(Package)))))return (ALevelInstance*)nullptr;
        A->PostEditMove(true);return SaveActor(A)?A:nullptr;
    };
    auto Middle=NewExternalMap(TEXT("/Game/NestedMiddle"));if(!Middle)return Fail(363);
    if(!AddInstance(Middle,TEXT("/Game/NestedLeaf"),FVector(1000,2000,0)))return Fail(364);
    auto W=GEditor->NewMap(true);
    if(!UEditorLoadingAndSavingUtils::SaveMap(W,TEXT("/Game/NestedParent")))return Fail(365);
    if(!AddInstance(W,TEXT("/Game/NestedMiddle"),FVector(10000,0,0)) ||
       !AddInstance(W,TEXT("/Game/NestedMiddle"),FVector(20000,0,0)))return Fail(366);
    // Reload from saved descriptors so residency cannot conceal missing scanning.
    if(!FEditorFileUtils::LoadMap(TEXT("/Game/NestedParent"),false,true))return Fail(367);
    W=GEditor->GetEditorWorldContext().World();auto WP=W->GetWorldPartition();if(!WP)return Fail(368);
    auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();
    auto Collect=[&]()
    {
        auto Result=MakeShared<FJsonObject>();TArray<TSharedPtr<FJsonValue>> Rows;
        TFunction<void(UActorDescContainerInstance*,int32)> Visit;
        Visit=[&](UActorDescContainerInstance* C,int32 Depth)
        {
            for(FActorDescInstanceList::TIterator<> It(C);It;++It)if(It->GetActorLabel()==FName(TEXT("NestedLeafCube")))
            {
                auto Row=MakeShared<FJsonObject>();const auto Id=S->MapId+TEXT(":actor:")+C->GetContainerID().GetActorGuid(It->GetGuid()).ToString(EGuidFormats::Digits);
                Row->SetStringField(TEXT("id"),Id);Row->SetNumberField(TEXT("depth"),Depth);Row->SetBoolField(TEXT("loaded"),It->IsLoaded());
                Row->SetStringField(TEXT("container"),C->GetContainerID().ToString());
                auto NativeActor=It->GetActor();Row->SetBoolField(TEXT("resident"),NativeActor && NativeActor->GetLevel()->Actors.Contains(NativeActor) && NativeActor->GetRootComponent() && NativeActor->GetRootComponent()->IsRegistered());
                Row->SetStringField(TEXT("bounds_center"),It->GetEditorBounds().GetCenter().ToString());
                FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Id);FString Source;
                const bool Found=Q.Step()==ESTSQLiteStepResult::Row && Q.GetColumnValueByIndex(0,Source);
                Row->SetBoolField(TEXT("indexed"),Found);
                if(Found)
                {
                    TSharedPtr<FJsonObject> Entity;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Entity);Row->SetObjectField(TEXT("source"),Entity);
                    const auto Position=Entity->GetObjectField(TEXT("transform"))->GetArrayField(TEXT("position"));
                    Row->SetBoolField(TEXT("position_matches"),FVector(Position[0]->AsNumber(),Position[1]->AsNumber(),Position[2]->AsNumber()).Equals(It->GetEditorBounds().GetCenter(),.01));
                }
                FSpatialTwinSQLiteStatement Geometry(S->Database.DB,TEXT("SELECT 1 FROM entities WHERE actor_id=? AND kind='Component' AND json_extract(source,'$.geometry_hash') IS NOT NULL"));Geometry.SetBindingValueByIndex(1,Id);
                Row->SetBoolField(TEXT("has_geometry"),Geometry.Step()==ESTSQLiteStepResult::Row);
                Rows.Add(MakeShared<FJsonValueObject>(Row));
            }
            for(const auto& Child:C->GetChildContainerInstances())Visit(Child.Value,Depth+1);
        };
        Visit(WP->GetActorDescContainerInstance(),0);Result->SetArrayField(TEXT("leaves"),Rows);Result->SetNumberField(TEXT("revision"),S->Database.Revision);return Result;
    };
    if(!S || !S->Rebuild(W))return Fail(369);
    auto Before=Collect();
    auto Added=AddInstance(W,TEXT("/Game/NestedMiddle"),FVector(30000,0,0));if(!Added)return Fail(370);
    auto Pending=Collect();if(!S->Sync())return Fail(371);auto After=Collect();
    auto Result=MakeShared<FJsonObject>();Result->SetObjectField(TEXT("before"),Before);Result->SetObjectField(TEXT("pending"),Pending);Result->SetObjectField(TEXT("after"),After);
    bool Passed=Before->GetArrayField(TEXT("leaves")).Num()==2 && After->GetArrayField(TEXT("leaves")).Num()==3;
    TSet<FString> Ids;
    for(const auto& Value:After->GetArrayField(TEXT("leaves"))){auto Row=Value->AsObject();Passed&=Row->GetBoolField(TEXT("indexed")) && Row->GetBoolField(TEXT("has_geometry")) && Row->GetBoolField(TEXT("position_matches")) && Row->GetNumberField(TEXT("depth"))==2;Ids.Add(Row->GetStringField(TEXT("id")));}
    Passed&=Ids.Num()==3;
    if(Passed)
    {
        FString AddedLeaf;
        TSet<FString> OriginalIds;for(auto V:Before->GetArrayField(TEXT("leaves")))OriginalIds.Add(V->AsObject()->GetStringField(TEXT("id")));
        for(auto Id:Ids)if(!OriginalIds.Contains(Id))AddedLeaf=Id;
        auto CheckPose=[&](const TSharedPtr<FJsonObject>& Capture,const FVector& Expected)
        {
            for(auto V:Capture->GetArrayField(TEXT("leaves")))
            {
                auto Row=V->AsObject();if(Row->GetStringField(TEXT("id"))!=AddedLeaf)continue;
                if(!Row->GetBoolField(TEXT("indexed")) || !Row->GetBoolField(TEXT("has_geometry")))return false;
                const auto P=Row->GetObjectField(TEXT("source"))->GetObjectField(TEXT("transform"))->GetArrayField(TEXT("position"));
                return FVector(P[0]->AsNumber(),P[1]->AsNumber(),P[2]->AsNumber()).Equals(Expected,.01);
            }
            return false;
        };
        if(!GEditor->Trans){auto Trans=NewObject<UTransBuffer>();Trans->Initialize(64*1024*1024);GEditor->Trans=Trans;}
        FCoreUObjectDelegates::ObjectsModifiedThisFrame.Remove(Added);FCoreUObjectDelegates::ObjectsModifiedThisFrame.Remove(Added->GetRootComponent());
        Added->SetFlags(RF_Transactional);Added->GetRootComponent()->SetFlags(RF_Transactional);
        const FTransform Original=Added->GetActorTransform();
        {
            FScopedTransaction Transaction(FText::FromString(TEXT("Nested instance unsaved transform")));
            Added->Modify();Added->GetRootComponent()->Modify();
            Added->SetActorLocation(FVector(40000,0,0));Added->SetActorRotation(FRotator(0,90,0));Added->SetActorScale3D(FVector(1.5));Added->PostEditMove(true);
        }
        const FTransform Modified=Added->GetActorTransform();
        if(!S->Sync())return Fail(376);
        auto Unsaved=Collect();Result->SetObjectField(TEXT("unsaved"),Unsaved);
        const bool UnsavedOk=CheckPose(Unsaved,Modified.TransformPosition(FVector(1250,2100,75)));
        Result->SetBoolField(TEXT("unsaved_pose_ok"),UnsavedOk);Passed&=UnsavedOk;
        if(!GEditor->UndoTransaction() || !S->Sync())return Fail(377);
        auto Undone=Collect();Result->SetObjectField(TEXT("undo"),Undone);
        const bool UndoOk=CheckPose(Undone,Original.TransformPosition(FVector(1250,2100,75)));
        Result->SetBoolField(TEXT("undo_pose_ok"),UndoOk);Passed&=UndoOk;
        if(!GEditor->RedoTransaction() || !S->Sync())return Fail(378);
        auto Redone=Collect();Result->SetObjectField(TEXT("redo"),Redone);
        const bool RedoOk=CheckPose(Redone,Modified.TransformPosition(FVector(1250,2100,75)));
        Result->SetBoolField(TEXT("redo_pose_ok"),RedoOk);Passed&=RedoOk;
        if(!SaveActor(Added) || !S->Sync())return Fail(373);
        auto Moved=Collect();Result->SetObjectField(TEXT("moved"),Moved);
        for(const auto& Value:Moved->GetArrayField(TEXT("leaves")))
        {
            auto Row=Value->AsObject();Passed&=Row->GetBoolField(TEXT("indexed")) && Row->GetBoolField(TEXT("has_geometry")) && Row->GetBoolField(TEXT("position_matches")) && Ids.Contains(Row->GetStringField(TEXT("id")));
        }
        auto DeletedPackage=Added->GetPackage();
        if(!W->EditorDestroyActor(Added,true) || !S->Sync())return Fail(374);
        Result->SetObjectField(TEXT("deleted_unsaved"),Collect());
        if(!UEditorLoadingAndSavingUtils::SavePackages({DeletedPackage},true) || !S->Sync())return Fail(375);
        auto Deleted=Collect();Result->SetObjectField(TEXT("deleted"),Deleted);
        FSpatialTwinSQLiteStatement Count(S->Database.DB,TEXT("SELECT count(*) FROM entities WHERE kind='Actor' AND label='NestedLeafCube'"));int64 Remaining=-1;
        if(Count.Step()==ESTSQLiteStepResult::Row)Count.GetColumnValueByIndex(0,Remaining);
        Result->SetNumberField(TEXT("remaining_indexed_leaves"),Remaining);Passed&=Remaining==2 && Deleted->GetArrayField(TEXT("leaves")).Num()==2;
    }
    Result->SetStringField(TEXT("state"),Passed?TEXT("PASS"):TEXT("FAILED_INCREMENTAL_NESTED"));
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Result),*(S->Database.Root/TEXT("native-nested-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    if(!Passed)return Fail(372);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinNestedTest PASS revision=%lld"),S->Database.Revision);return 0;
}
