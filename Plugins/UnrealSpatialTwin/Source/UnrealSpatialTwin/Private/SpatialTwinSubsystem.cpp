#include "SpatialTwinSubsystem.h"
#include "SpatialTwinDescriptorScope.h"
#include "SpatialTwinSourcePaths.h"
#include "ScopedTransaction.h"
#include "Editor.h"
#include "EditorReimportHandler.h"
#include "EngineUtils.h"
#include "Engine/StaticMesh.h"
#include "Engine/StaticMeshActor.h"
#include "StaticMeshResources.h"
#include "Components/StaticMeshComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/SplineComponent.h"
#include "Components/SplineMeshComponent.h"
#include "Components/BoxComponent.h"
#include "Components/SphereComponent.h"
#include "Components/CapsuleComponent.h"
#include "Components/BrushComponent.h"
#include "PhysicsEngine/BodySetup.h"
#include "Chaos/TriangleMeshImplicitObject.h"
#include "WorldPartition/WorldPartition.h"
#include "WorldPartition/ActorDescContainerInstance.h"
#include "WorldPartition/WorldPartitionHandle.h"
#include "WorldPartition/WorldPartitionRuntimeHash.h"
#include "WorldPartition/WorldPartitionRuntimeLevelStreamingCell.h"
#include "WorldPartition/DataLayer/DataLayerInstance.h"
#include "InstancedStaticMeshDelegates.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/FileHelper.h"
#include "Misc/SecureHash.h"
#include "Misc/EngineVersion.h"
#include "Serialization/MemoryWriter.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/UnrealType.h"
#include "SpatialTwinSQLite.h"
#include "NavMesh/RecastNavMesh.h"
#include "Detour/DetourNavMesh.h"
#include "NavigationSystem.h"
#include "AI/Navigation/NavAreaBase.h"
#include "Engine/CollisionProfile.h"
#include "Misc/App.h"
#include "Misc/CommandLine.h"
#include "Misc/Parse.h"
#include "Misc/PackageName.h"
#include "Modules/ModuleManager.h"
#include "LandscapeHeightfieldCollisionComponent.h"
#include "Chaos/HeightField.h"
#include "NavigationOctree.h"
#include "NavMesh/RecastNavMeshGenerator.h"
#include "NavMesh/RecastGeometryExport.h"
#include "NavCollision.h"
#include "AI/NavigationModifier.h"
#include "AI/Navigation/NavRelevantInterface.h"
#include "Misc/TransactionObjectEvent.h"
bool ExportSpatialTwinNavigation(UWorld*,FSpatialTwinDatabase&);
bool SpatialTwinNavigationPending(UNavigationSystemV1*);
#include "SpatialTwinNavigation.h"

namespace
{
TArray<TSharedPtr<FJsonValue>> Vec(const FVector& V) { return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(V.Z)}; }
TSharedPtr<FJsonObject> Xform(const FTransform& T)
{
    auto J=MakeShared<FJsonObject>(); const FQuat Q=T.GetRotation(); J->SetArrayField(TEXT("position"),Vec(T.GetLocation()));
    J->SetArrayField(TEXT("scale"),Vec(T.GetScale3D())); J->SetArrayField(TEXT("rotation"),{MakeShared<FJsonValueNumber>(Q.X),MakeShared<FJsonValueNumber>(Q.Y),MakeShared<FJsonValueNumber>(Q.Z),MakeShared<FJsonValueNumber>(Q.W)});return J;
}
void Box(const TSharedPtr<FJsonObject>& J,const FString& Name,const FBox& B)
{
    if (B.IsValid) J->SetArrayField(Name,{MakeShared<FJsonValueArray>(Vec(B.Min)),MakeShared<FJsonValueArray>(Vec(B.Max))});
}
TSharedPtr<FJsonObject> Entity(const FString& Id,const FString& Kind,UObject* Object=nullptr)
{
    auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("id"),Id);J->SetStringField(TEXT("kind"),Kind);
    if(Object) { J->SetStringField(TEXT("path"),Object->GetPathName());J->SetStringField(TEXT("package"),Object->GetPackage()->GetName());J->SetStringField(TEXT("class"),Object->GetClass()->GetPathName()); }
    return J;
}
FString Stable(const FString& Value) { return FMD5::HashAnsiString(*Value); }
FBox CollisionBounds(UBodySetup* Body,const FTransform& Transform)
{
    FBox Result(ForceInit);if(!Body)return Result;
    if(Body->AggGeom.GetElementCount())Result+=Body->AggGeom.CalcAABB(Transform);
    for(const auto& Tri:Body->TriMeshGeometries)if(Tri)
    {const auto B=Tri->BoundingBox();Result+=FBox(FVector(B.Min()),FVector(B.Max())).TransformBy(Transform);}
    return Result;
}
}

FString USpatialTwinSubsystem::ActorId(AActor* A) const { return MapId+TEXT(":actor:")+A->GetActorInstanceGuid().ToString(EGuidFormats::Digits); }
FString USpatialTwinSubsystem::ComponentId(UActorComponent* C) const
{
    return ActorId(C->GetOwner())+TEXT(":component:")+Stable(C->GetPathName(C->GetOwner())+TEXT("|")+C->GetName()+TEXT("|")+C->GetClass()->GetPathName());
}
void USpatialTwinSubsystem::Initialize(FSubsystemCollectionBase& Collection)
{
    Super::Initialize(Collection);
    if(GEditor)GEditor->ShouldDisableCPUThrottlingDelegates.Add(UEditorEngine::FShouldDisableCPUThrottling::CreateUObject(this,&USpatialTwinSubsystem::NeedsBackgroundTick));
    Handles.Add(FCoreUObjectDelegates::OnObjectPropertyChanged.AddLambda([this](UObject* O,FPropertyChangedEvent&){Dirty(O);}));
    Handles.Add(FCoreUObjectDelegates::OnObjectTransacted.AddLambda([this](UObject* O,const FTransactionObjectEvent& E){InstanceTransacted(O,E);Dirty(O);}));
    Handles.Add(GEngine->OnLevelActorAdded().AddLambda([this](AActor* A){UnloadingActors.Remove(ActorId(A));if(!bScanning)DeletedActors.Remove(ActorId(A));Dirty(A);}));
    Handles.Add(GEngine->OnLevelActorDeleted().AddLambda([this](AActor* A){if(!bScanning && bReady && A->GetWorld()==GEditor->GetEditorWorldContext().World() && !UnloadingActors.Contains(ActorId(A))){const FString Id=ActorId(A);DirtyActors.Remove(A);DirtyActorIds.Remove(Id);DirtyDescriptors.Remove(Id);DeletedActors.Add(Id);QueueInstanceChildren(A,true);LastChange=FPlatformTime::Seconds();}}));
    Handles.Add(GEngine->OnActorMoving().AddLambda([this](AActor* A){Dirty(A);}));
    Handles.Add(GEngine->OnLevelActorAttached().AddLambda([this](AActor* A,const AActor*){Dirty(A);}));
    Handles.Add(GEngine->OnLevelActorDetached().AddLambda([this](AActor* A,const AActor*){Dirty(A);}));
    Handles.Add(FInstancedStaticMeshDelegates::OnInstanceIndexUpdated.AddLambda([this](UInstancedStaticMeshComponent* C,TArrayView<const FInstancedStaticMeshDelegates::FInstanceIndexUpdateData> Updates)
    {
        if(bScanning || !bReady || !C || !C->GetOwner())return;
        RestoreInstanceIds(C);
        auto* Ids=InstanceIds.Find(ComponentId(C));
        if(Ids)
        {
            auto Old=*Ids;
            if(!GIsTransacting && GEditor->IsTransactionActive() && !PendingInstanceTransactions.Contains(ComponentId(C)))PendingInstanceTransactions.Add(ComponentId(C),Old);
            for(const auto& U:Updates)
            {
                using T=FInstancedStaticMeshDelegates::EInstanceIndexUpdateType;
                if(U.Type==T::Cleared || U.Type==T::Destroyed){Ids->Reset();continue;}
                if(U.Type==T::Removed){if(Ids->IsValidIndex(U.Index))(*Ids)[U.Index].Reset();continue;}
                if(U.Index<0)continue; if(Ids->Num()<=U.Index)Ids->SetNum(U.Index+1);
                if(U.Type==T::Relocated && Old.IsValidIndex(U.OldIndex))(*Ids)[U.Index]=Old[U.OldIndex];
                if(U.Type==T::Added)(*Ids)[U.Index]=FGuid::NewGuid().ToString(EGuidFormats::Digits);
            }
            Ids->SetNum(C->GetInstanceCount());
        }
        Dirty(C);
    }));
    Handles.Add(FCoreUObjectDelegates::OnObjectModified.AddLambda([this](UObject* O){InstanceModified(O);Dirty(O);}));
    Handles.Add(FCoreUObjectDelegates::OnObjectRenamed.AddLambda([this](UObject* O,UObject*,FName){Dirty(O);}));
    Handles.Add(FReimportManager::Instance()->OnPostReimport().AddLambda([this](UObject* O,bool Success){if(Success)Dirty(O);}));
    Handles.Add(UActorDescContainerInstance::OnActorDescContainerInstanceInitialized.AddWeakLambda(this,[this](UActorDescContainerInstance* C){WatchContainer(C);}));
    // Container teardown bypasses the per-container Removed event in UE5.8.
    UActorDescContainerInstance::OnActorDescInstanceRemovedFromContainer.AddWeakLambda(this,[this](UActorDescContainerInstance* C,FWorldPartitionActorDescInstance* D){DescriptorDirty(C,D,true);});
    Ticker=FTSTicker::GetCoreTicker().AddTicker(FTickerDelegate::CreateUObject(this,&USpatialTwinSubsystem::Tick));
    InitializeEvents();
}
void USpatialTwinSubsystem::InstanceModified(UObject* O)
{
    if(bScanning || !bReady || GIsTransacting || !GEditor->IsTransactionActive())return;
    if(auto C=Cast<UInstancedStaticMeshComponent>(O);C && C->GetOwner())
    {
        RestoreInstanceIds(C);
        const FString Id=ComponentId(C);
        if(!PendingInstanceTransactions.Contains(Id))if(auto Values=InstanceIds.Find(Id))PendingInstanceTransactions.Add(Id,*Values);
    }
}
bool USpatialTwinSubsystem::TransformInstance(const FString& Id,const FTransform& Expected,const FTransform& Transform)
{
    if(!bReady || bScanning || !GEditor || GEditor->PlayWorld || Expected.ContainsNaN() || Transform.ContainsNaN() ||
       !Transform.GetRotation().IsNormalized() || !Expected.GetRotation().IsNormalized() ||
       Transform.GetScale3D().GetAbsMin()<UE_DOUBLE_SMALL_NUMBER)return false;
    FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT p.path,p.id FROM entities i JOIN entities p ON p.id=i.parent_id WHERE i.id=? AND i.kind='Instance' AND p.kind='Component'"));
    Q.SetBindingValueByIndex(1,Id);if(Q.Step()!=ESTSQLiteStepResult::Row)return false;
    FString Path,CID;Q.GetColumnValueByIndex(0,Path);Q.GetColumnValueByIndex(1,CID);
    auto Component=FindObject<UInstancedStaticMeshComponent>(nullptr,*Path);
    if(!Component || Component->GetWorld()!=GEditor->GetEditorWorldContext().World() || ComponentId(Component)!=CID)return false;
    RestoreInstanceIds(Component);const auto& Ids=InstanceIds.FindChecked(CID);
    const FString Prefix=CID+TEXT(":instance:");if(!Id.StartsWith(Prefix))return false;
    const int32 Index=Ids.Find(Id.RightChop(Prefix.Len()));FTransform Actual;
    if(Index==INDEX_NONE || !Component->GetInstanceTransform(Index,Actual,true) ||
       !Actual.GetLocation().Equals(Expected.GetLocation(),.01) || !Actual.GetScale3D().Equals(Expected.GetScale3D(),.0001) ||
       1-FMath::Abs(Actual.GetRotation()|Expected.GetRotation())>1e-6)return false;
    // Identity follows native reorder notifications. Never accept a caller's
    // instance index or silently edit whichever object now occupies that slot.
    // Programmatically created/saved components need not carry the editor's
    // transactional flag. Modify alone would silently make this non-undoable.
    Component->SetFlags(RF_Transactional);Component->GetOwner()->SetFlags(RF_Transactional);
    const FScopedTransaction Transaction(NSLOCTEXT("SpatialTwin","TransformInstance","Transform Twin instance"));
    Component->GetOwner()->Modify();Component->Modify();
    if(!Component->UpdateInstanceTransform(Index,Transform,true,true,true))return false;
    Component->MarkPackageDirty();Dirty(Component);return true;
}

void USpatialTwinSubsystem::RestoreInstanceIds(UInstancedStaticMeshComponent* Component)
{
    const FString Id=ComponentId(Component);if(InstanceIds.Contains(Id))return;auto& Values=InstanceIds.Add(Id);
    if(ColdChangedPackages.Contains(Component->GetOwner()->GetPackage()->GetName()))
    {
        // Index is not identity after an edit made while the synchronizer was
        // closed. Preserve only unique exact local-transform matches. Ambiguous,
        // moved and new instances get new IDs instead of inheriting another's ID.
        TMap<FString,FString> Old;TSet<FString> Duplicate;TMap<FString,int32> Current;
        FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT id,json_extract(source,'$.instance_transform') FROM entities WHERE parent_id=? AND kind='Instance'"));Q.SetBindingValueByIndex(1,Id);
        while(Q.Step()==ESTSQLiteStepResult::Row)
        {FString Value,Pose;Q.GetColumnValueByIndex(0,Value);Q.GetColumnValueByIndex(1,Pose);if(Pose.IsEmpty())continue;if(Old.Contains(Pose))Duplicate.Add(Pose);else Old.Add(Pose,Value.RightChop((Id+TEXT(":instance:")).Len()));}
        TArray<FString> Poses;for(int32 I=0;I<Component->GetInstanceCount();++I){FTransform T;Component->GetInstanceTransform(I,T,false);FString Pose=FSpatialTwinDatabase::Json(Xform(T));Poses.Add(Pose);++Current.FindOrAdd(Pose);}
        Values.SetNum(Poses.Num());for(int32 I=0;I<Poses.Num();++I)if(Current[Poses[I]]==1 && !Duplicate.Contains(Poses[I]))if(auto Value=Old.Find(Poses[I]))Values[I]=*Value;
        InstanceIdentityState.Add(Id,TEXT("cold_unique_local_transform_match_otherwise_new_id"));return;
    }
    FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT id,json_extract(source,'$.instance_index') FROM entities WHERE parent_id=? AND kind='Instance'"));Q.SetBindingValueByIndex(1,Id);
    while(Q.Step()==ESTSQLiteStepResult::Row)
    {FString Value;int32 Index;Q.GetColumnValueByIndex(0,Value);Q.GetColumnValueByIndex(1,Index);if(Index>=0 && Index<10000000){if(Values.Num()<=Index)Values.SetNum(Index+1);Values[Index]=Value.RightChop((Id+TEXT(":instance:")).Len());}}
}
void USpatialTwinSubsystem::InstanceTransacted(UObject* O,const FTransactionObjectEvent& E)
{
    if(bScanning || !bReady)return;
    auto C=Cast<UInstancedStaticMeshComponent>(O);if(!C || !C->GetOwner())return;
    const FString Id=ComponentId(C),Key=E.GetTransactionId().ToString()+TEXT("|")+Id;
    if(E.GetEventType()==ETransactionObjectEventType::Finalized)
    {
        if(auto Before=PendingInstanceTransactions.Find(Id))
        {
            FInstanceTransaction T;T.Before=*Before;T.After=InstanceIds.FindRef(Id);InstanceTransactions.Add(Key,MoveTemp(T));PendingInstanceTransactions.Remove(Id);
        }
    }
    else if(E.GetEventType()==ETransactionObjectEventType::UndoRedo)
    {
        if(auto T=InstanceTransactions.Find(Key)){T->Applied=!T->Applied;InstanceIds.Add(Id,T->Applied?T->After:T->Before);}
    }
}
void USpatialTwinSubsystem::Deinitialize()
{
    if(GEditor)GEditor->ShouldDisableCPUThrottlingDelegates.RemoveAll([this](const UEditorEngine::FShouldDisableCPUThrottling& Delegate){return Delegate.IsBoundToObject(this);});
    RemoveEvents();
    UActorDescContainerInstance::OnActorDescInstanceRemovedFromContainer.RemoveAll(this);
    FTSTicker::GetCoreTicker().RemoveTicker(Ticker);
    if(Handles.Num()==12)
    {
        FCoreUObjectDelegates::OnObjectPropertyChanged.Remove(Handles[0]);FCoreUObjectDelegates::OnObjectTransacted.Remove(Handles[1]);
        GEngine->OnLevelActorAdded().Remove(Handles[2]);GEngine->OnLevelActorDeleted().Remove(Handles[3]);GEngine->OnActorMoving().Remove(Handles[4]);
        GEngine->OnLevelActorAttached().Remove(Handles[5]);GEngine->OnLevelActorDetached().Remove(Handles[6]);
        FInstancedStaticMeshDelegates::OnInstanceIndexUpdated.Remove(Handles[7]);
        FCoreUObjectDelegates::OnObjectModified.Remove(Handles[8]);FCoreUObjectDelegates::OnObjectRenamed.Remove(Handles[9]);FReimportManager::Instance()->OnPostReimport().Remove(Handles[10]);
        UActorDescContainerInstance::OnActorDescContainerInstanceInitialized.Remove(Handles[11]);
    }
    for(auto Weak:WatchedContainers)if(auto C=Weak.Get()){C->OnActorDescInstanceAddedEvent.RemoveAll(this);C->OnActorDescInstanceRemovedEvent.RemoveAll(this);C->OnActorDescInstanceUpdatedEvent.RemoveAll(this);}
    Database.DB.Close();Super::Deinitialize();
}
void USpatialTwinSubsystem::Dirty(UObject* O)
{
    if(bScanning || !bReady || !O) return;
    AActor* A=Cast<AActor>(O);if(!A) A=O->GetTypedOuter<AActor>();
    if(A && (!IsValid(A) || A->IsActorBeingDestroyed()))return;
    if(A && A->GetWorld()==GEditor->GetEditorWorldContext().World())
    {
        DirtyActors.Add(A);QueueInstanceChildren(A);TArray<AActor*> Children;A->GetAttachedActors(Children,true,true);
        for(auto Child:Children)DirtyActors.Add(Child);
        if(A->IsA<ARecastNavMesh>()){++NavigationChangeSerial;bNavigationDirty=true;}
        LastChange=FPlatformTime::Seconds();
    }
    else if(O->IsAsset())
    {
        DirtyAssets.Add(O);
        AssetCache.Remove(O->GetPathName());
        InvalidateAssetUsers(O->GetPathName());
        LastChange=FPlatformTime::Seconds();
    }
}
bool USpatialTwinSubsystem::Tick(float Delta)
{
    if(bReady && !bScanning && GEditor && GEditor->GetEditorWorldContext().World() && GEditor->GetEditorWorldContext().World()->GetOutermost()->GetName()!=MapId)DisconnectWorld();
    if(!bReady && !bScanning && !bResumeAttempted && GEditor && GEditor->GetEditorWorldContext().World()
       && GEditor->GetEditorWorldContext().World()->GetOutermost()->GetName().StartsWith(TEXT("/Game/")))
    {bResumeAttempted=true;Resume();}
    double Now=FPlatformTime::Seconds();
    if(bReady && !bScanning && Now-LastNavigationPoll>=1)
    {
        LastNavigationPoll=Now;
        auto Nav=FNavigationSystem::GetCurrent<UNavigationSystemV1>(GEditor->GetEditorWorldContext().World());
        // Process queued source changes without clearing dirty areas or bounds.
        // Out-of-bounds changes may drain without emitting a tile-build event.
        if(bNavigationRefreshPending && Nav && !Nav->IsNavigationOctreeLocked())Nav->ProcessPendingOctreeUpdates();
        bool HasRecast=false;if(Nav)for(auto Data:Nav->NavDataSet)HasRecast|=Data && Data->IsA<ARecastNavMesh>();
        const bool NativePending=HasRecast && SpatialTwinNavigationPending(Nav);
        // Native builds can begin after the property event was synchronized.
        // Detect that transition even without a previously pending Twin refresh.
        if((bNavigationRefreshPending && !NativePending) || (!bNavigationRefreshPending && NativePending))bNavigationDirty=true;
    }
    if(bReady && !bScanning && Now-LastHeartbeat>2)
    {
        auto J=MakeShared<FJsonObject>();J->SetNumberField(TEXT("epoch"),FDateTime::UtcNow().ToUnixTimestamp());J->SetNumberField(TEXT("pending_actors"),PendingCount());J->SetNumberField(TEXT("revision"),Database.Revision);J->SetStringField(TEXT("error"),Database.Error);
        const FString Path=Database.Root/TEXT("heartbeat.json"); FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(J),*(Path+TEXT(".tmp")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
        IFileManager::Get().Move(*Path,*(Path+TEXT(".tmp")),true);LastHeartbeat=Now;
    }
    // Keep failed work queued for explicit recovery. Repeating the same failure
    // every frame can monopolize the editor and prevent its MCP from responding.
    if(bReady && !bScanning && Database.Error.IsEmpty() && PendingCount()-int32(bPartitionDirty)>0 && (Now-LastChange>.25 || Now-LastFlush>1)) {Sync();LastFlush=Now;}
    if(bReady && !bScanning && Database.Error.IsEmpty() && bPartitionDirty && Now-LastChange>2 && Now-LastFlush>.25){RefreshPartition();LastFlush=Now;}
    return true;
}
bool USpatialTwinSubsystem::NeedsBackgroundTick() const
{
    // Native per-frame work must progress while the agent uses another app.
    // Keep the user's settings and normal idle throttling; never unlock builds.
    if(bScanning)return true;
    if(!bReady || !Database.Error.IsEmpty())return false;
    if(PendingCount()>0)return true;
    auto Nav=WatchedNavigationSystem.Get();
    bool HasRecast=false;if(Nav)for(auto Data:Nav->NavDataSet)HasRecast|=Data && Data->IsA<ARecastNavMesh>();
    return HasRecast && Nav && !Nav->IsNavigationBuildingLocked()
        && !Nav->IsNavigationOctreeLocked() && SpatialTwinNavigationPending(Nav);
}
FString USpatialTwinSubsystem::SaveGeometry(const TArray<FVector>& V,const TArray<FIntVector>& T,const FString& Role)
{
    if(V.IsEmpty() || T.IsEmpty()) return FString();
    TArray<uint8> Bytes;FMemoryWriter W(Bytes);uint32 Magic=0x31475453,Version=1,NV=V.Num(),NT=T.Num();W<<Magic<<Version<<NV<<NT;
    for(const auto& P:V){double X=P.X,Y=P.Y,Z=P.Z;W<<X<<Y<<Z;}
    for(const auto& P:T){uint32 A=P.X,B=P.Y,C=P.Z;W<<A<<B<<C;}
    FSHAHash Hash;FSHA1::HashBuffer(Bytes.GetData(),Bytes.Num(),Hash.Hash);FString Id=Hash.ToString().ToLower(),Rel=TEXT("geometry/")+Id+TEXT(".stg"),Path=Database.Root/Rel;
    IFileManager::Get().MakeDirectory(*(Database.Root/TEXT("geometry")),true);
    if(!IFileManager::Get().FileExists(*Path))
    {
        if(!FFileHelper::SaveArrayToFile(Bytes,*(Path+TEXT(".tmp"))) || !IFileManager::Get().Move(*Path,*(Path+TEXT(".tmp")),true)) { Database.Error=TEXT("Geometry publish failed");return FString(); }
    }
    auto Meta=MakeShared<FJsonObject>();Meta->SetNumberField(TEXT("vertices"),NV);Meta->SetNumberField(TEXT("triangles"),NT);Meta->SetStringField(TEXT("role"),Role);Meta->SetStringField(TEXT("format"),TEXT("STG1"));
    Database.Geometry(Id,Rel,FSpatialTwinDatabase::Json(Meta));return Id;
}
TSharedPtr<FJsonObject> USpatialTwinSubsystem::CollisionBody(UBodySetup* B)
{
    auto J=MakeShared<FJsonObject>();TArray<TSharedPtr<FJsonValue>> Complex,Simple;
    FString TraceMode=TEXT("UseSimpleAndComplex");
    if(B)
    {
        if(B->GetCollisionTraceFlag()==CTF_UseComplexAsSimple)TraceMode=TEXT("UseComplexAsSimple");
        if(B->GetCollisionTraceFlag()==CTF_UseSimpleAsComplex)TraceMode=TEXT("UseSimpleAsComplex");
        B->CreatePhysicsMeshes();
        for(const auto& Tri:B->TriMeshGeometries)
        {
            if(!Tri) continue;TArray<FVector> V;TArray<FIntVector> T;
            const auto& P=Tri->Particles();for(uint32 I=0;I<P.Size();++I)V.Add(FVector(P.GetX(I)));
            const auto& E=Tri->Elements();for(int32 I=0;I<E.GetNumTriangles();++I){if(E.RequiresLargeIndices()){auto Index=E.GetLargeIndexBuffer()[I];T.Add(FIntVector(Index[0],Index[1],Index[2]));}else{auto Index=E.GetSmallIndexBuffer()[I];T.Add(FIntVector(Index[0],Index[1],Index[2]));}}
            auto Hash=SaveGeometry(V,T,TEXT("collision_complex"));if(!Hash.IsEmpty())Complex.Add(MakeShared<FJsonValueString>(Hash));
        }
        for(const auto& Convex:B->AggGeom.ConvexElems)
        {
            TArray<FVector> V;for(const auto& P:Convex.VertexData)V.Add(Convex.GetTransform().TransformPosition(P));TArray<FIntVector>T;
            for(int32 I=0;I+2<Convex.IndexData.Num();I+=3)T.Add(FIntVector(Convex.IndexData[I],Convex.IndexData[I+1],Convex.IndexData[I+2]));
            auto Hash=SaveGeometry(V,T,TEXT("collision_convex"));if(!Hash.IsEmpty())Simple.Add(MakeShared<FJsonValueString>(Hash));
        }
        TArray<TSharedPtr<FJsonValue>> Shapes;
        for(const auto& Bx:B->AggGeom.BoxElems){auto S=MakeShared<FJsonObject>();S->SetStringField(TEXT("type"),TEXT("box"));S->SetObjectField(TEXT("transform"),Xform(Bx.GetTransform()));S->SetArrayField(TEXT("extent"),Vec(FVector(Bx.X,Bx.Y,Bx.Z)*.5));Shapes.Add(MakeShared<FJsonValueObject>(S));}
        for(const auto& Sph:B->AggGeom.SphereElems){auto S=MakeShared<FJsonObject>();S->SetStringField(TEXT("type"),TEXT("sphere"));S->SetArrayField(TEXT("center"),Vec(Sph.Center));S->SetNumberField(TEXT("radius"),Sph.Radius);Shapes.Add(MakeShared<FJsonValueObject>(S));}
        for(const auto& Cap:B->AggGeom.SphylElems){auto S=MakeShared<FJsonObject>();S->SetStringField(TEXT("type"),TEXT("capsule"));S->SetObjectField(TEXT("transform"),Xform(Cap.GetTransform()));S->SetNumberField(TEXT("radius"),Cap.Radius);S->SetNumberField(TEXT("length"),Cap.Length);Shapes.Add(MakeShared<FJsonValueObject>(S));}
        J->SetArrayField(TEXT("collision_shapes"),Shapes);
    }
    if(!J->HasField(TEXT("collision_shapes")))J->SetArrayField(TEXT("collision_shapes"),{});
    const bool Ready=B && B->bCreatedPhysicsMeshes && !B->bFailedToCreatePhysicsMeshes;
    const int32 ExportedSimple=Simple.Num()+J->GetArrayField(TEXT("collision_shapes")).Num();
    J->SetStringField(TEXT("collision_simple_coverage"),Ready && ExportedSimple==B->AggGeom.GetElementCount()?(ExportedSimple?TEXT("AVAILABLE"):TEXT("EMPTY")):TEXT("UNKNOWN"));
    J->SetStringField(TEXT("collision_complex_coverage"),Ready && Complex.Num()==B->TriMeshGeometries.Num()?(Complex.Num()?TEXT("AVAILABLE"):TEXT("EMPTY")):TEXT("UNKNOWN"));
    J->SetArrayField(TEXT("collision_complex"),Complex);J->SetArrayField(TEXT("collision_simple"),Simple);J->SetStringField(TEXT("trace_mode"),TraceMode);return J;
}
bool USpatialTwinSubsystem::EnsureAssetPackage(UObject* Asset)
{
    if(!Asset)return true;
    const FString Id=TEXT("asset:")+Asset->GetPathName();
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT 1 FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Id);
     if(Q.Step()!=ESTSQLiteStepResult::Row){auto E=Entity(Id,Asset->IsA<UMaterialInterface>()?TEXT("Material"):TEXT("Asset"),Asset);if(!Database.Put(E))return false;}}
    if(!Asset->IsAsset())return true;
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT 1 FROM relationships WHERE source=? AND kind='IN_PACKAGE'"));Q.SetBindingValueByIndex(1,Id);if(Q.Step()==ESTSQLiteStepResult::Row)return true;}
    return IndexAsset(FAssetData(Asset));
}

TSharedPtr<FJsonObject> USpatialTwinSubsystem::MeshAsset(UStaticMesh* Mesh)
{
    auto RefreshMaterials=[&](const TSharedPtr<FJsonObject>& Asset)
    {
        TArray<TSharedPtr<FJsonValue>> Materials;
        for(const auto& Material:Mesh->GetStaticMaterials())if(Material.MaterialInterface)Materials.Add(MakeShared<FJsonValueString>(TEXT("asset:")+Material.MaterialInterface->GetPathName()));
        const TArray<TSharedPtr<FJsonValue>>* Previous=nullptr;
        bool Changed=!Asset->TryGetArrayField(TEXT("materials"),Previous) || Previous->Num()!=Materials.Num();
        for(int32 I=0;!Changed && I<Materials.Num();++I)Changed=(*Previous)[I]->AsString()!=Materials[I]->AsString();
        if(!Changed)return;
        const FString Id=Asset->GetStringField(TEXT("id"));Database.TrackRelations(Id);
        {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("DELETE FROM relationships WHERE source=? AND kind='USES_ASSET'"));Q.SetBindingValueByIndex(1,Id);Q.Step();}
        for(const auto& Material:Mesh->GetStaticMaterials())if(Material.MaterialInterface){EnsureAssetPackage(Material.MaterialInterface);Database.Relation(Id,TEXT("asset:")+Material.MaterialInterface->GetPathName(),TEXT("USES_ASSET"));}
        Asset->SetArrayField(TEXT("materials"),Materials);
    };
    if(auto Existing=AssetCache.Find(Mesh->GetPathName())) {auto Cached=*Existing;RefreshMaterials(Cached);Database.Put(Cached);return Cached;}
    if(bVerifiedBaseline && !DirtyAssets.Contains(Mesh))
    {
        FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT source FROM entities WHERE id=? AND kind='StaticMesh'"));Q.SetBindingValueByIndex(1,TEXT("asset:")+Mesh->GetPathName());
        FString Source;TSharedPtr<FJsonObject> Cached;
        if(Q.Step()==ESTSQLiteStepResult::Row && Q.GetColumnValueByIndex(0,Source) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Cached))
        {RefreshMaterials(Cached);Box(Cached,TEXT("collision_local_bounds"),CollisionBounds(Mesh->GetBodySetup(),FTransform::Identity));AssetCache.Add(Mesh->GetPathName(),Cached);Database.Put(Cached);return Cached;}
    }
    ++MeshGeometryExportAttempts;
    auto J=Entity(TEXT("asset:")+Mesh->GetPathName(),TEXT("StaticMesh"),Mesh);J->SetStringField(TEXT("label"),Mesh->GetName());J->SetBoolField(TEXT("package_dirty"),Mesh->GetPackage()->IsDirty());Box(J,TEXT("local_bounds"),Mesh->GetBoundingBox());
    RefreshMaterials(J);
    TArray<TSharedPtr<FJsonValue>> Complex,Simple;
    if(FStaticMeshRenderData* R=Mesh->GetRenderData();R && R->LODResources.Num())
    {
        auto& L=R->LODResources[0];TArray<FVector> V;TArray<FIntVector> T;
        for(uint32 I=0;I<L.VertexBuffers.PositionVertexBuffer.GetNumVertices();++I) V.Add(FVector(L.VertexBuffers.PositionVertexBuffer.VertexPosition(I)));
        auto Indices=L.IndexBuffer.GetArrayView();for(int32 I=0;I+2<Indices.Num();I+=3) T.Add(FIntVector(Indices[I],Indices[I+1],Indices[I+2]));
        const FString Hash=SaveGeometry(V,T,TEXT("render_lod0"));if(!Hash.IsEmpty())J->SetStringField(TEXT("geometry_hash"),Hash);
        J->SetNumberField(TEXT("lod_count"),R->LODResources.Num());
    }
    auto Body=CollisionBody(Mesh->GetBodySetup());Complex=Body->GetArrayField(TEXT("collision_complex"));Simple=Body->GetArrayField(TEXT("collision_simple"));
    J->SetStringField(TEXT("collision_simple_coverage"),Body->GetStringField(TEXT("collision_simple_coverage")));J->SetStringField(TEXT("collision_complex_coverage"),Body->GetStringField(TEXT("collision_complex_coverage")));
    Box(J,TEXT("collision_local_bounds"),CollisionBounds(Mesh->GetBodySetup(),FTransform::Identity));
    J->SetArrayField(TEXT("collision_shapes"),Body->GetArrayField(TEXT("collision_shapes")));FString TraceMode=Body->GetStringField(TEXT("trace_mode"));
    J->SetArrayField(TEXT("collision_complex"),Complex);J->SetArrayField(TEXT("collision_simple"),Simple);J->SetStringField(TEXT("trace_mode"),TraceMode);
    auto Prototype=GetDefault<AStaticMeshActor>()->GetStaticMeshComponent();auto SpawnCollision=MakeShared<FJsonObject>();
    SpawnCollision->SetBoolField(TEXT("enabled"),Prototype->GetCollisionEnabled()!=ECollisionEnabled::NoCollision);SpawnCollision->SetStringField(TEXT("object_type"),UCollisionProfile::Get()->ReturnChannelNameFromContainerIndex(Prototype->GetCollisionObjectType()).ToString());SpawnCollision->SetStringField(TEXT("profile"),Prototype->GetCollisionProfileName().ToString());
    auto Responses=MakeShared<FJsonObject>();for(int32 Channel=0;Channel<ECC_MAX;++Channel){auto Response=Prototype->GetCollisionResponseToChannel((ECollisionChannel)Channel);Responses->SetStringField(UCollisionProfile::Get()->ReturnChannelNameFromContainerIndex(Channel).ToString(),Response==ECR_Block?TEXT("Block"):Response==ECR_Overlap?TEXT("Overlap"):TEXT("Ignore"));}
    SpawnCollision->SetObjectField(TEXT("responses"),Responses);J->SetObjectField(TEXT("native_spawn_collision"),SpawnCollision);J->SetBoolField(TEXT("native_spawn_affects_navigation"),Prototype->CanEverAffectNavigation());
    auto SpawnNavigation=MakeShared<FJsonObject>();
    SpawnNavigation->SetBoolField(TEXT("navigation_fill_underneath"),Prototype->bFillCollisionUnderneathForNavmesh);
    SpawnNavigation->SetBoolField(TEXT("navigation_filled_convex"),Prototype->bRasterizeAsFilledConvexVolume);
    const auto DefaultBody=GetDefault<UBodySetup>();
    SpawnNavigation->SetNumberField(TEXT("navigation_slope_behavior"),(int32)DefaultBody->WalkableSlopeOverride.WalkableSlopeBehavior);
    SpawnNavigation->SetNumberField(TEXT("navigation_slope_angle"),DefaultBody->WalkableSlopeOverride.WalkableSlopeAngle);
    const auto DefaultNav=GetDefault<UNavCollision>();
    const auto AsObstacle=[Prototype](const UNavCollisionBase& Value){return Prototype->bOverrideNavigationExport?bool(Prototype->bForceNavigationObstacle):Value.IsDynamicObstacle();};
    if(FEngineVersion::Current().GetMajor()==5 && FEngineVersion::Current().GetMinor()==8 && !AsObstacle(*DefaultNav) && !DefaultNav->HasSurfaceAreaClass() && DefaultNav->BoxCollision.IsEmpty() && DefaultNav->CylinderCollision.IsEmpty())
        SpawnNavigation->SetNumberField(TEXT("authored_ucx_adapter"),1);
    J->SetObjectField(TEXT("native_spawn_navigation"),SpawnNavigation);
    // Asset-local default export also exists before the first placed component.
    // Dynamic obstacles and surface-area modifiers need their native owner data.
    auto Nav=Mesh->GetNavCollision();
    if(Mesh->GetBodySetup() && (!Nav || (!AsObstacle(*Nav) && !Nav->HasSurfaceAreaClass())))
    {
        FNavigationRelevantData Data(MakeShared<FNavigationElement>(*Mesh));FRecastGeometryExport Export(Data);
        J->SetNumberField(TEXT("navigation_slope_behavior"),(int32)Mesh->GetBodySetup()->WalkableSlopeOverride.WalkableSlopeBehavior);J->SetNumberField(TEXT("navigation_slope_angle"),Mesh->GetBodySetup()->WalkableSlopeOverride.WalkableSlopeAngle);
        if(!Nav || !Nav->ExportGeometry(FTransform::Identity,Export))Export.ExportRigidBodySetup(*Mesh->GetBodySetup(),FTransform::Identity);
        TArray<FVector> V;TArray<FIntVector> T;
        for(int32 I=0;I+2<Export.VertexBuffer.Num();I+=3)V.Add(FVector(Export.VertexBuffer[I],Export.VertexBuffer[I+1],Export.VertexBuffer[I+2]));
        for(int32 I=0;I+2<Export.IndexBuffer.Num();I+=3)T.Add(FIntVector(Export.IndexBuffer[I],Export.IndexBuffer[I+1],Export.IndexBuffer[I+2]));
        auto Hash=SaveGeometry(V,T,TEXT("native_asset_navigation_input"));
        if(!Hash.IsEmpty()){J->SetStringField(TEXT("navigation_geometry_hash"),Hash);J->SetStringField(TEXT("navigation_input_coverage"),TEXT("native_default_component_geometry"));}
        else if(V.IsEmpty() && T.IsEmpty() && Mesh->GetBodySetup()->bCreatedPhysicsMeshes && !Mesh->GetBodySetup()->bFailedToCreatePhysicsMeshes)J->SetStringField(TEXT("navigation_input_coverage"),TEXT("native_empty_geometry"));
    }
    Database.Put(J);AssetCache.Add(Mesh->GetPathName(),J);IndexAsset(FAssetData(Mesh));return AssetCache[Mesh->GetPathName()];
}
bool USpatialTwinSubsystem::ExportActor(AActor* A,const FString& ExplicitId)
{
    const FString Id=ExplicitId.IsEmpty()?ActorId(A):ExplicitId;auto J=Entity(Id,TEXT("Actor"),A);
    const bool Transient=A->HasAnyFlags(RF_Transient) || A->GetClass()->HasAnyClassFlags(CLASS_Transient);
    J->SetBoolField(TEXT("transient"),Transient);
    if(Transient)
    {
        // A recreated session object can reuse its path with a fresh GUID.
        // Retire that observed predecessor atomically, including its children
        // and graph, without touching unloaded descriptor-backed identities.
        TArray<FString> Stale;
        {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT id FROM entities WHERE kind='Actor' AND path=? AND class=? AND id<>? AND json_type(source,'$.descriptor_guid') IS NULL"));
         Q.SetBindingValueByIndex(1,A->GetPathName());Q.SetBindingValueByIndex(2,A->GetClass()->GetPathName());Q.SetBindingValueByIndex(3,Id);
         while(Q.Step()==ESTSQLiteStepResult::Row){FString Old;Q.GetColumnValueByIndex(0,Old);Stale.Add(Old);}}
        for(const auto& Old:Stale)if(!Database.DeleteActor(Old))return false;
    }
    if(bFullScan)FullScanActors.Add(Id);
    // Incremental property/transform events must retain descriptor source facts
    // acquired while unloaded containers were scanned.
    {FSpatialTwinSQLiteStatement Old(Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Old.SetBindingValueByIndex(1,Id);FString Source;TSharedPtr<FJsonObject> Previous;
     if(Old.Step()==ESTSQLiteStepResult::Row && Old.GetColumnValueByIndex(0,Source) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Previous))
       for(auto Key:{TEXT("descriptor_guid"),TEXT("container_id"),TEXT("container_package"),TEXT("runtime_grid"),TEXT("descriptor_data_layers"),TEXT("descriptor_was_loaded")})if(auto Value=Previous->TryGetField(Key))J->SetField(Key,Value);}
    const FString Level=MapId+TEXT(":level:")+A->GetLevel()->GetOutermost()->GetName();
    auto LE=Entity(Level,TEXT("Level"),A->GetLevel());LE->SetStringField(TEXT("parent_id"),MapId);
    if(auto Instances=A->GetWorld()->GetSubsystem<ULevelInstanceSubsystem>())
        if(auto Owner=Cast<AActor>(Instances->GetParentLevelInstance(A)))
        {const FString OwnerId=ActorId(Owner);LE->SetStringField(TEXT("instance_owner_id"),OwnerId);LE->SetStringField(TEXT("parent_id"),OwnerId);Database.Relation(OwnerId,Level,TEXT("CONTAINS"));}
    Database.Put(LE);Database.Relation(MapId,Level,TEXT("CONTAINS"));
    J->SetStringField(TEXT("parent_id"),Level);J->SetStringField(TEXT("label"),A->GetActorLabel());J->SetStringField(TEXT("actor_guid"),A->GetActorGuid().ToString());
    J->SetObjectField(TEXT("transform"),Xform(A->GetActorTransform()));J->SetObjectField(TEXT("relative_transform"),Xform(A->GetRootComponent()?A->GetRootComponent()->GetRelativeTransform():FTransform::Identity));
    if(A->GetRootComponent())J->SetStringField(TEXT("root_component_path"),A->GetRootComponent()->GetPathName());
    FBox B=A->GetComponentsBoundingBox(true);Box(J,TEXT("bounds"),B);Box(J,TEXT("local_bounds"),A->CalculateComponentsBoundingBoxInLocalSpace(true));
    TArray<TSharedPtr<FJsonValue>> Layers;
    for(const auto& Name:A->GetDataLayerInstanceNames())
    {
        const FString LayerId=MapId+TEXT(":data_layer:")+Name.ToString();
        auto Layer=Entity(LayerId,TEXT("DataLayer"));Layer->SetStringField(TEXT("label"),Name.ToString());Database.Put(Layer);
        Layers.Add(MakeShared<FJsonValueString>(LayerId));Database.Relation(Id,LayerId,TEXT("IN_DATA_LAYER"));
    }
    J->SetArrayField(TEXT("data_layers"),Layers);
    J->SetBoolField(TEXT("package_dirty"),A->GetPackage()->IsDirty());J->SetStringField(TEXT("coverage"),TEXT("loaded_native"));
    if(auto External=A->GetExternalPackage())J->SetStringField(TEXT("external_actor_package"),External->GetName());
    if(AActor* Parent=A->GetAttachParentActor()){J->SetStringField(TEXT("attached_to"),ActorId(Parent));Database.Relation(Id,ActorId(Parent),TEXT("ATTACHED_TO"));}
    Database.Relation(Level,Id,TEXT("CONTAINS"));
    auto ReferenceAsset=[&](const FString& Source,UObject* Asset)
    {
        if(!Asset || !Asset->IsAsset())return;
        const FString AssetId=TEXT("asset:")+Asset->GetPathName();
        EnsureAssetPackage(Asset);
        Database.Relation(Source,AssetId,TEXT("USES_ASSET"));
    };
    ReferenceAsset(Id,A->GetClass()->ClassGeneratedBy);
    TArray<TSharedPtr<FJsonValue>> Tags;for(auto Tag:A->Tags)Tags.Add(MakeShared<FJsonValueString>(Tag.ToString()));J->SetArrayField(TEXT("tags"),Tags);
    auto Properties=MakeShared<FJsonObject>();auto Values=MakeShared<FJsonObject>();
    for(TFieldIterator<FProperty> It(A->GetClass());It;++It)
    {
        FProperty* P=*It;auto Definition=MakeShared<FJsonObject>();FString Type;
        const void* Value=P->ContainerPtrToValuePtr<void>(A);
        if(auto Bool=CastField<FBoolProperty>(P)){Type=TEXT("bool");if(P->GetFName()==TEXT("bHidden") || P->GetFName()==TEXT("bActorEnableCollision"))Values->SetBoolField(P->GetName(),Bool->GetPropertyValue(Value));}
        else if(auto Number=CastField<FNumericProperty>(P)){Type=Number->IsInteger()?TEXT("int64"):TEXT("double");}
        else if(CastField<FStrProperty>(P))Type=TEXT("string");
        else if(CastField<FNameProperty>(P))Type=TEXT("name");
        else if(CastField<FTextProperty>(P))Type=TEXT("text");
        else if(auto Object=CastField<FObjectPropertyBase>(P)){Type=TEXT("object");ReferenceAsset(Id,Object->GetObjectPropertyValue(Value));}
        else Type=P->GetCPPType();
        Definition->SetStringField(TEXT("type"),Type);Definition->SetStringField(TEXT("spatial_effect"),P->GetFName()==TEXT("bHidden")?TEXT("none"):P->GetFName()==TEXT("bActorEnableCollision")?TEXT("collision_enabled"):TEXT("unknown"));
        Definition->SetBoolField(TEXT("editable"),P->HasAnyPropertyFlags(CPF_Edit));Properties->SetObjectField(P->GetName(),Definition);
    }
    J->SetObjectField(TEXT("properties"),Values);Database.Put(J);
    ExportNavigation(A,A->GetActorTransform(),J);
    auto Schema=MakeShared<FJsonObject>();Schema->SetObjectField(TEXT("properties"),Properties);Schema->SetStringField(TEXT("native_class"),A->GetClass()->GetPathName());
    FSpatialTwinSQLiteStatement CS(Database.DB,TEXT("INSERT INTO class_schemas VALUES(?,?) ON CONFLICT(class) DO UPDATE SET source=excluded.source WHERE class_schemas.source<>excluded.source"));CS.SetBindingValueByIndex(1,A->GetClass()->GetPathName());CS.SetBindingValueByIndex(2,FSpatialTwinDatabase::Json(Schema));if(CS.Step()!=ESTSQLiteStepResult::Done)return false;
    FBox ActorCollisionBounds(ForceInit);
    TInlineComponentArray<UActorComponent*> Components(A);
    for(UActorComponent* C:Components)
    {
        FString CID=Id+TEXT(":component:")+Stable(C->GetPathName(A)+TEXT("|")+C->GetName()+TEXT("|")+C->GetClass()->GetPathName());auto E=Entity(CID,TEXT("Component"),C);
        E->SetStringField(TEXT("actor_id"),Id);E->SetStringField(TEXT("parent_id"),Id);E->SetStringField(TEXT("label"),C->GetName());
        for(TFieldIterator<FObjectPropertyBase> Property(C->GetClass());Property;++Property)ReferenceAsset(CID,Property->GetObjectPropertyValue_InContainer(C));
        if(auto Scene=Cast<USceneComponent>(C);Scene && Scene->GetAttachParent())
        {E->SetStringField(TEXT("attached_to"),ComponentId(Scene->GetAttachParent()));Database.Relation(CID,ComponentId(Scene->GetAttachParent()),TEXT("ATTACHED_TO"));}
        if(USceneComponent* S=Cast<USceneComponent>(C)){E->SetObjectField(TEXT("transform"),Xform(S->GetComponentTransform()));E->SetObjectField(TEXT("relative_transform"),Xform(S->GetRelativeTransform()));Box(E,TEXT("bounds"),S->Bounds.GetBox());Box(E,TEXT("local_bounds"),S->CalcBounds(FTransform::Identity).GetBox());}
        auto Collision=MakeShared<FJsonObject>();
        if(UPrimitiveComponent* P=Cast<UPrimitiveComponent>(C))
        {
            Collision->SetBoolField(TEXT("enabled"),A->GetActorEnableCollision() && P->GetCollisionEnabled()!=ECollisionEnabled::NoCollision);Collision->SetStringField(TEXT("profile"),P->GetCollisionProfileName().ToString());
            Collision->SetStringField(TEXT("object_type"),UCollisionProfile::Get()->ReturnChannelNameFromContainerIndex(P->GetCollisionObjectType()).ToString());
            Collision->SetStringField(TEXT("query_mode"),LexToString((int32)P->GetCollisionEnabled()));
            auto Responses=MakeShared<FJsonObject>();auto* Profiles=UCollisionProfile::Get();
            for(int32 Channel=0;Channel<ECC_MAX;++Channel){auto R=P->GetCollisionResponseToChannel((ECollisionChannel)Channel);Responses->SetStringField(Profiles->ReturnChannelNameFromContainerIndex(Channel).ToString(),R==ECR_Block?TEXT("Block"):R==ECR_Overlap?TEXT("Overlap"):TEXT("Ignore"));}
            Collision->SetObjectField(TEXT("responses"),Responses);E->SetBoolField(TEXT("affects_navigation"),P->CanEverAffectNavigation());
        }
        if(auto SM=Cast<UStaticMeshComponent>(C);SM && SM->GetStaticMesh())
        {
            auto M=MeshAsset(SM->GetStaticMesh());FString MID=M->GetStringField(TEXT("id"));E->SetStringField(TEXT("asset_id"),MID);Database.Relation(CID,MID,TEXT("USES_ASSET"));
            if(auto V=M->TryGetField(TEXT("geometry_hash"));V) E->SetField(TEXT("geometry_hash"),V);
            Collision->SetArrayField(TEXT("simple"),M->GetArrayField(TEXT("collision_simple")));Collision->SetArrayField(TEXT("complex"),M->GetArrayField(TEXT("collision_complex")));Collision->SetArrayField(TEXT("shapes"),M->GetArrayField(TEXT("collision_shapes")));Collision->SetStringField(TEXT("trace_mode"),M->GetStringField(TEXT("trace_mode")));
            for(const auto* Kind:{TEXT("simple"),TEXT("complex")})if(auto Value=M->TryGetField(FString(TEXT("collision_"))+Kind+TEXT("_coverage")))Collision->SetField(FString(Kind)+TEXT("_coverage"),Value);
            if(auto ISM=Cast<UInstancedStaticMeshComponent>(SM))
            {
                const FBox InstanceCollision=CollisionBounds(SM->GetStaticMesh()->GetBodySetup(),FTransform::Identity);
                Collision->SetBoolField(TEXT("aggregate_only"),true);E->SetNumberField(TEXT("instance_count"),ISM->GetInstanceCount());
                RestoreInstanceIds(ISM);auto& Ids=InstanceIds.FindChecked(CID);
                E->SetStringField(TEXT("instance_identity"),InstanceIdentityState.Contains(CID)?InstanceIdentityState[CID]:TEXT("persistent_event_tracked"));
                Ids.SetNum(ISM->GetInstanceCount());for(auto& InstanceId:Ids)if(InstanceId.IsEmpty())InstanceId=FGuid::NewGuid().ToString(EGuidFormats::Digits);
                for(int32 I=0;I<ISM->GetInstanceCount();++I)
                {
                    FTransform T;ISM->GetInstanceTransform(I,T,true);auto Instance=Entity(CID+TEXT(":instance:")+Ids[I],TEXT("Instance"));
                    Instance->SetStringField(TEXT("parent_id"),CID);Instance->SetStringField(TEXT("actor_id"),Id);Instance->SetStringField(TEXT("component_id"),CID);Instance->SetStringField(TEXT("asset_id"),MID);Instance->SetNumberField(TEXT("instance_index"),I);
                    FTransform Local;ISM->GetInstanceTransform(I,Local,false);Instance->SetObjectField(TEXT("instance_transform"),Xform(Local));
                    Instance->SetObjectField(TEXT("transform"),Xform(T));Box(Instance,TEXT("bounds"),SM->GetStaticMesh()->GetBoundingBox().TransformBy(T));Box(Instance,TEXT("local_bounds"),SM->GetStaticMesh()->GetBoundingBox());
                    if(InstanceCollision.IsValid){Box(Instance,TEXT("collision_bounds"),InstanceCollision.TransformBy(T));Box(Instance,TEXT("collision_local_bounds"),InstanceCollision);ActorCollisionBounds+=InstanceCollision.TransformBy(T);}
                    auto IC=MakeShared<FJsonObject>(*Collision);IC->SetBoolField(TEXT("aggregate_only"),false);Instance->SetObjectField(TEXT("collision"),IC);Instance->SetBoolField(TEXT("affects_navigation"),SM->CanEverAffectNavigation());Database.Put(Instance);Database.Relation(CID,Instance->GetStringField(TEXT("id")),TEXT("CONTAINS"));Database.Relation(Instance->GetStringField(TEXT("id")),MID,TEXT("USES_ASSET"));
                }
            }
        }
        E->SetObjectField(TEXT("collision"),Collision);
        if(auto P=Cast<UPrimitiveComponent>(C);P && (!Cast<UStaticMeshComponent>(C) || Cast<USplineMeshComponent>(C)))
        {
            if(auto Body=P->GetBodySetup())
            {
                auto Data=CollisionBody(Body);Collision->SetArrayField(TEXT("simple"),Data->GetArrayField(TEXT("collision_simple")));Collision->SetArrayField(TEXT("complex"),Data->GetArrayField(TEXT("collision_complex")));Collision->SetArrayField(TEXT("shapes"),Data->GetArrayField(TEXT("collision_shapes")));Collision->SetStringField(TEXT("trace_mode"),Data->GetStringField(TEXT("trace_mode")));
                Collision->SetStringField(TEXT("simple_coverage"),Data->GetStringField(TEXT("collision_simple_coverage")));Collision->SetStringField(TEXT("complex_coverage"),Data->GetStringField(TEXT("collision_complex_coverage")));
            }
            else if(Cast<UBrushComponent>(P))
            {
                // No BrushBodySetup means this component has no physical body.
                // The editor builder brush has bounds and BlockAll settings but
                // must not make an otherwise complete scene query UNKNOWN.
                Collision->SetStringField(TEXT("simple_coverage"),TEXT("EMPTY"));
                Collision->SetStringField(TEXT("complex_coverage"),TEXT("EMPTY"));
            }
        }
        if(auto L=Cast<ULandscapeHeightfieldCollisionComponent>(C);L && L->HeightfieldRef)
        {
            auto ExportHeight=[&](const Chaos::FHeightFieldPtr& H)
            {
                if(!H)return FString();TArray<FVector> V;TArray<FIntVector> T;int32 Rows=H->GetNumRows(),Cols=H->GetNumCols();
                for(int32 I=0;I<Rows*Cols;++I)V.Add(FVector(H->GetPointScaled(I)));
                for(int32 Y=0;Y<Rows-1;++Y)for(int32 X=0;X<Cols-1;++X)if(!H->IsHole(X,Y)){int32 I=Y*Cols+X;T.Add(FIntVector(I,I+Cols,I+1));T.Add(FIntVector(I+1,I+Cols,I+Cols+1));}
                return SaveGeometry(V,T,TEXT("landscape_collision"));
            };
            auto ComplexHash=ExportHeight(L->HeightfieldRef->HeightfieldGeometry);auto SimpleHash=ExportHeight(L->HeightfieldRef->HeightfieldSimpleGeometry);
            if(SimpleHash.IsEmpty())SimpleHash=ComplexHash;
            Collision->SetArrayField(TEXT("complex"),ComplexHash.IsEmpty()?TArray<TSharedPtr<FJsonValue>>():TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueString>(ComplexHash)});
            Collision->SetArrayField(TEXT("simple"),SimpleHash.IsEmpty()?TArray<TSharedPtr<FJsonValue>>():TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueString>(SimpleHash)});
            // Landscape replaces BodySetup data with its native heightfields.
            Collision->SetStringField(TEXT("simple_coverage"),SimpleHash.IsEmpty()?TEXT("UNKNOWN"):TEXT("AVAILABLE"));Collision->SetStringField(TEXT("complex_coverage"),ComplexHash.IsEmpty()?TEXT("UNKNOWN"):TEXT("AVAILABLE"));
            FTransform Rigid=L->GetComponentTransform();Rigid.SetScale3D(FVector::OneVector);E->SetObjectField(TEXT("collision_transform"),Xform(Rigid));E->SetStringField(TEXT("spatial_type"),TEXT("Landscape"));
        }
        if(auto Spline=Cast<USplineComponent>(C))
        {
            TArray<TSharedPtr<FJsonValue>> Points;
            for(int32 I=0;I<Spline->GetNumberOfSplinePoints();++I){auto P=MakeShared<FJsonObject>();P->SetArrayField(TEXT("position"),Vec(Spline->GetLocationAtSplinePoint(I,ESplineCoordinateSpace::World)));P->SetArrayField(TEXT("tangent"),Vec(Spline->GetTangentAtSplinePoint(I,ESplineCoordinateSpace::World)));P->SetNumberField(TEXT("type"),(int32)Spline->GetSplinePointType(I));Points.Add(MakeShared<FJsonValueObject>(P));}
            E->SetArrayField(TEXT("spline_points"),Points);E->SetBoolField(TEXT("closed_loop"),Spline->IsClosedLoop());
        }
        Database.Relation(Id,CID,TEXT("HAS_COMPONENT"));
        if(auto Primitive=Cast<UPrimitiveComponent>(C);Primitive && !Cast<UInstancedStaticMeshComponent>(C))
        {const FBox Local=CollisionBounds(Primitive->GetBodySetup(),FTransform::Identity);if(Local.IsValid){const FBox Physical=Local.TransformBy(Primitive->GetComponentTransform());Box(E,TEXT("collision_local_bounds"),Local);Box(E,TEXT("collision_bounds"),Physical);ActorCollisionBounds+=Physical;}}
        if(C->CanEverAffectNavigation())
        {auto Scene=Cast<USceneComponent>(C);ExportNavigation(C,Scene?Scene->GetComponentTransform():A->GetActorTransform(),E);}
        if(!Database.Put(E))return false;
        if(auto Primitive=Cast<UPrimitiveComponent>(C))for(int32 MaterialIndex=0;MaterialIndex<Primitive->GetNumMaterials();++MaterialIndex)
        {
            if(auto Material=Primitive->GetMaterial(MaterialIndex))
            {
                EnsureAssetPackage(Material);Database.Relation(CID,TEXT("asset:")+Material->GetPathName(),TEXT("USES_ASSET"));
            }
        }
    }
    Box(J,TEXT("collision_bounds"),ActorCollisionBounds);Database.Put(J);
    return Database.Error.IsEmpty();
}
void USpatialTwinSubsystem::DescriptorDirty(UActorDescContainerInstance* Container,FWorldPartitionActorDescInstance* D,bool Removed)
{
    if(bScanning || !bReady || !Container || !D)return;
    auto WP=Container->GetTopWorldPartition();if(!WP || WP->GetWorld()!=GEditor->GetEditorWorldContext().World())return;
    const FString Id=MapId+TEXT(":actor:")+Container->GetContainerID().GetActorGuid(D->GetGuid()).ToString(EGuidFormats::Digits);
    for(auto C=Container;!Removed && C && C->GetParentContainerInstance();C=const_cast<UActorDescContainerInstance*>(C->GetParentContainerInstance()))
    {
        const auto Parent=C->GetParentContainerInstance();
        const FString Owner=MapId+TEXT(":actor:")+Parent->GetContainerID().GetActorGuid(C->GetContainerActorGuid()).ToString(EGuidFormats::Digits);
        Removed=DeletedActors.Contains(Owner);
    }
    if(Removed){DirtyDescriptors.Remove(Id);DeletedActors.Add(Id);}else{DeletedActors.Remove(Id);DirtyDescriptors.Add(Id,{Container,D->GetGuid()});}
    LastChange=FPlatformTime::Seconds();
}
void USpatialTwinSubsystem::WatchContainer(UActorDescContainerInstance* C)
{
    if(!C || WatchedContainers.Contains(C))return;auto WP=C->GetTopWorldPartition();
    if(!WP || !GEditor || WP->GetWorld()!=GEditor->GetEditorWorldContext().World())return;
    WatchedContainers.Add(C);
    if(auto Parent=C->GetParentContainerInstance())InstanceContainers.Add(Parent->GetContainerID().GetActorGuid(C->GetContainerActorGuid()),C);
    C->OnActorDescInstanceAddedEvent.AddWeakLambda(this,[this,C](FWorldPartitionActorDescInstance* D){DescriptorDirty(C,D);});
    C->OnActorDescInstanceUpdatedEvent.AddWeakLambda(this,[this,C](FWorldPartitionActorDescInstance* D){DescriptorDirty(C,D);});
    C->OnActorDescInstanceRemovedEvent.AddWeakLambda(this,[this,C](FWorldPartitionActorDescInstance* D){DescriptorDirty(C,D,true);});
    // Initialization broadcasts after existing descriptors have been inserted.
    // Only this newly observed container is queued; full scans/resume are guarded.
    for(FActorDescInstanceList::TIterator<> It(C);It;++It)DescriptorDirty(C,*It);
}
void USpatialTwinSubsystem::QueueInstanceChildren(AActor* Actor,bool Removed)
{
    auto Found=InstanceContainers.Find(Actor->GetActorInstanceGuid());if(!Found || !Found->IsValid())return;
    TArray<UActorDescContainerInstance*> Pending{Found->Get()};
    while(Pending.Num())
    {
        auto C=Pending.Pop();
        for(FActorDescInstanceList::TIterator<> It(C);It;++It)DescriptorDirty(C,*It,Removed);
        for(auto Child:C->GetChildContainerInstances())Pending.Add(Child.Value);
    }
}
bool USpatialTwinSubsystem::ScanDescriptor(UActorDescContainerInstance* Container,FWorldPartitionActorDescInstance* D,FSpatialTwinDescriptorScope& Scope)
{
        FString Id=MapId+TEXT(":actor:")+Container->GetContainerID().GetActorGuid(D->GetGuid()).ToString(EGuidFormats::Digits);
        const bool WasLoaded=D->IsLoaded();FWorldPartitionReference Reference;
        AActor* A=nullptr;
        if(Container->GetParentContainerInstance())A=Scope.Resolve(Container,D->GetGuid());
        else
        {
            A=D->GetActor(false);
            if(!IsValid(A) || A->IsActorBeingDestroyed() || !A->GetLevel()->Actors.Contains(A))
            {Reference=FWorldPartitionReference(Container,D->GetGuid());A=Reference.GetActor();}
        }
        // Never collapse instances onto a source-package actor or export its
        // unregistered/local transform as the canonical world position.
        if(A && ActorId(A)!=Id)A=nullptr;
        const FString ActualId=Id;
        const bool Reuse=bFullScan && FullScanActors.Contains(ActualId);
        if(!Reuse && !Database.ResetActorRelations(ActualId))return false;
        if(A){if(Reuse)++DescriptorReusedActors;else if(!ExportActor(A))return false;}
        else
        {
            auto E=Entity(Id,TEXT("Actor"));E->SetStringField(TEXT("class"),D->GetNativeClass().ToString());E->SetStringField(TEXT("label"),D->GetActorLabel().ToString());E->SetStringField(TEXT("path"),D->GetActorSoftPath().ToString());E->SetStringField(TEXT("package"),D->GetActorPackage().ToString());E->SetObjectField(TEXT("transform"),Xform(D->GetActorTransform()*Container->GetTransform()));Box(E,TEXT("bounds"),D->GetEditorBounds());E->SetStringField(TEXT("coverage"),TEXT("descriptor_only"));Database.Put(E);
        }
        FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,ActualId);FString Source;
        if(Q.Step()==ESTSQLiteStepResult::Row && Q.GetColumnValueByIndex(0,Source))
        {
            TSharedPtr<FJsonObject> E;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),E);E->SetStringField(TEXT("descriptor_guid"),D->GetGuid().ToString());E->SetStringField(TEXT("container_id"),Container->GetContainerID().ToString());E->SetStringField(TEXT("container_package"),Container->GetContainerPackage().ToString());E->SetStringField(TEXT("external_actor_package"),D->GetActorPackage().ToString());E->SetStringField(TEXT("runtime_grid"),D->GetRuntimeGrid().ToString());
            E->SetBoolField(TEXT("descriptor_was_loaded"),WasLoaded);
            TArray<TSharedPtr<FJsonValue>> DataLayers;for(const auto& Name:D->GetDataLayers())DataLayers.Add(MakeShared<FJsonValueString>(Name.ToString()));E->SetArrayField(TEXT("descriptor_data_layers"),DataLayers);Database.Put(E);
        }
    return Database.PruneActor(ActualId) && Database.Error.IsEmpty();
}
bool USpatialTwinSubsystem::ScanContainer(UActorDescContainerInstance* Container,FSpatialTwinDescriptorScope& Scope)
{
    WatchContainer(Container);
    for(FActorDescInstanceList::TIterator<> It(Container);It;++It)if(!ScanDescriptor(Container,*It,Scope))return false;
    for(const auto& Child:Container->GetChildContainerInstances()) if(!ScanContainer(Child.Value,Scope))return false;
    return true;
}
bool USpatialTwinSubsystem::IndexAssets()
{
    auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();Registry.SearchAllAssets(true);
    TArray<FAssetData> Assets;Registry.GetAssetsByPath(TEXT("/Game"),Assets,true);
    for(const auto& Plugin:IPluginManager::Get().GetEnabledPlugins())if(Plugin->CanContainContent())Registry.GetAssetsByPath(FName(*Plugin->GetMountedAssetPath()),Assets,true);
    for(const auto& Asset:Assets)if(!IndexAsset(Asset))return false;
    return IndexReferencedAssetPackages() && Database.Error.IsEmpty();
}
void USpatialTwinSubsystem::ExportNavigation(UObject* Object,const FTransform& Transform,const TSharedPtr<FJsonObject>& SourceEntity)
{
    auto N=FNavigationSystem::GetCurrent<UNavigationSystemV1>(Object->GetWorld());if(!N || !N->GetNavOctree())return;
    auto Id=N->GetNavOctreeIdForElement(FNavigationElementHandle(Object));if(!Id)return;
    auto Octree=const_cast<FNavigationOctree*>(N->GetNavOctree());auto Data=Octree->GetMutableDataForID(*Id);if(!Data)return;
    // A child can be aggregated into another octree owner; export its native
    // owner only once. Never use octree indices or pointers as persistent IDs.
    if(Data->SourceElement->GetHandle()!=FNavigationElementHandle(Object))return;
    auto P=Cast<UPrimitiveComponent>(Object);auto E=SourceEntity;
    if(!P)
    {
        const FString Owner=SourceEntity->GetStringField(SourceEntity->GetStringField(TEXT("kind"))==TEXT("Actor")?TEXT("id"):TEXT("actor_id"));
        const FString SourceId=SourceEntity->GetStringField(TEXT("id"));
        E=Entity(SourceId+TEXT(":navigation"),TEXT("NavigationInput"),Object);
        E->SetStringField(TEXT("actor_id"),Owner);E->SetStringField(TEXT("source_entity_id"),SourceId);
        E->SetStringField(TEXT("parent_id"),Cast<USceneComponent>(Object)?SourceId:Owner);
        E->SetObjectField(TEXT("transform"),Xform(Transform));E->SetBoolField(TEXT("affects_navigation"),true);
        Box(E,TEXT("bounds"),Data->SourceElement->GetBounds());
        Box(E,TEXT("local_bounds"),Data->SourceElement->GetBounds().TransformBy(Transform.ToInverseMatrixWithScale()));
        Database.Relation(SourceId,E->GetStringField(TEXT("id")),TEXT("HAS_NAVIGATION_INPUT"));
    }
    N->DemandLazyDataGathering(*Data);
    if(Data->CollisionData.Num()>=sizeof(FRecastGeometryCache::FHeader))
    {
        FRecastGeometryCache Cache(Data->CollisionData.GetData());TArray<FVector> V;TArray<FIntVector>T;
        const bool Instanced=Data->NavDataPerInstanceTransformDelegate.IsBound();
        for(int32 I=0;I<Cache.Header.NumVerts;++I){FVector Point(-Cache.Verts[I*3],-Cache.Verts[I*3+2],Cache.Verts[I*3+1]);V.Add(Instanced?Point:Transform.InverseTransformPosition(Point));}
        for(int32 I=0;I<Cache.Header.NumFaces;++I)T.Add(FIntVector(Cache.Indices[I*3],Cache.Indices[I*3+1],Cache.Indices[I*3+2]));
        auto Hash=SaveGeometry(V,T,TEXT("native_navigation_input"));if(!Hash.IsEmpty())E->SetStringField(TEXT("navigation_geometry_hash"),Hash);
        if(auto SM=Cast<UStaticMeshComponent>(P);SM && SM->GetStaticMesh() && !Cast<USplineMeshComponent>(P))
        {
            const FString AssetPath=SM->GetStaticMesh()->GetPathName();if(auto Cached=AssetCache.Find(AssetPath)){(*Cached)->SetStringField(TEXT("navigation_geometry_hash"),Hash);Database.Put(*Cached);}
        }
        E->SetStringField(TEXT("navigation_input_coverage"),TEXT("native_recast_geometry"));E->SetNumberField(TEXT("navigation_slope_angle"),Cache.Header.SlopeOverride.WalkableSlopeAngle);E->SetNumberField(TEXT("navigation_slope_behavior"),(int32)Cache.Header.SlopeOverride.WalkableSlopeBehavior);
        if(Instanced)
        {
            TArray<FString> Sources;{FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT source FROM entities WHERE parent_id=? AND kind='Instance'"));Q.SetBindingValueByIndex(1,E->GetStringField(TEXT("id")));while(Q.Step()==ESTSQLiteStepResult::Row){FString Source;Q.GetColumnValueByIndex(0,Source);Sources.Add(Source);}}
            for(auto Source:Sources){TSharedPtr<FJsonObject> Instance;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Instance);Instance->SetStringField(TEXT("navigation_geometry_hash"),Hash);Instance->SetStringField(TEXT("navigation_input_coverage"),TEXT("native_recast_geometry"));Instance->SetBoolField(TEXT("navigation_fill_underneath"),Data->Modifiers.GetFillCollisionUnderneathForNavmesh());Instance->SetBoolField(TEXT("navigation_filled_convex"),Data->Modifiers.GetRasterizeAsFilledConvexVolume());Database.Put(Instance);}
        }
    }
    // Absence is authoritative only after gathering the exact native owner.
    // Voxel-only and still-pending inputs must remain unknown offline.
    if(!Data->HasGeometry() && !Data->IsPendingLazyGeometryGathering() && !Data->NeedAnyPendingLazyModifiersGathering())
    {
        E->SetStringField(TEXT("navigation_input_coverage"),TEXT("native_empty_geometry"));
        if(auto ISM=Cast<UInstancedStaticMeshComponent>(P))
        {
            TArray<FString> Sources;{FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT source FROM entities WHERE parent_id=? AND kind='Instance'"));Q.SetBindingValueByIndex(1,E->GetStringField(TEXT("id")));while(Q.Step()==ESTSQLiteStepResult::Row){FString Source;Q.GetColumnValueByIndex(0,Source);Sources.Add(Source);}}
            for(auto Source:Sources){TSharedPtr<FJsonObject> Instance;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Instance);Instance->SetStringField(TEXT("navigation_input_coverage"),TEXT("native_empty_geometry"));Instance->SetBoolField(TEXT("navigation_fill_underneath"),Data->Modifiers.GetFillCollisionUnderneathForNavmesh());Instance->SetBoolField(TEXT("navigation_filled_convex"),Data->Modifiers.GetRasterizeAsFilledConvexVolume());Database.Put(Instance);}
        }
    }
    TArray<TSharedPtr<FJsonValue>> Modifiers;
    for(const auto& Area:Data->Modifiers.GetAreas())
    {
        auto M=MakeShared<FJsonObject>();M->SetBoolField(TEXT("mask_fill_underneath"),Data->Modifiers.GetMaskFillCollisionUnderneathForNavmesh());M->SetStringField(TEXT("area_class"),Area.GetAreaClass()?Area.GetAreaClass()->GetPathName():TEXT(""));M->SetStringField(TEXT("replace_area_class"),Area.GetAreaClassToReplace()?Area.GetAreaClassToReplace()->GetPathName():TEXT(""));M->SetNumberField(TEXT("shape"),(int32)Area.GetShapeType());M->SetNumberField(TEXT("mode"),(int32)Area.GetApplyMode());M->SetBoolField(TEXT("include_agent_height"),Area.ShouldIncludeAgentHeight());M->SetBoolField(TEXT("expand_top"),Area.ShouldExpandTopByCellHeight());Box(M,TEXT("bounds"),Area.GetBounds());
        if(Area.GetShapeType()==ENavigationShapeType::Box){FBoxNavAreaData BoxData;Area.GetBox(BoxData);M->SetArrayField(TEXT("origin"),Vec(BoxData.Origin));M->SetArrayField(TEXT("extent"),Vec(BoxData.Extent));}
        else if(Area.GetShapeType()==ENavigationShapeType::Convex){FConvexNavAreaData Convex;Area.GetConvex(Convex);TArray<TSharedPtr<FJsonValue>> Points;for(auto Point:Convex.Points)Points.Add(MakeShared<FJsonValueArray>(Vec(Point)));M->SetArrayField(TEXT("points"),Points);M->SetNumberField(TEXT("minimum_height"),Convex.MinZ);M->SetNumberField(TEXT("maximum_height"),Convex.MaxZ);}
        else if(Area.GetShapeType()==ENavigationShapeType::Cylinder){FCylinderNavAreaData Cylinder;Area.GetCylinder(Cylinder);M->SetArrayField(TEXT("origin"),Vec(Cylinder.Origin));M->SetNumberField(TEXT("radius"),Cylinder.Radius);M->SetNumberField(TEXT("height"),Cylinder.Height);}
        Modifiers.Add(MakeShared<FJsonValueObject>(M));
    }
    TArray<TSharedPtr<FJsonValue>> Links;FString LinkCoverage=TEXT("point_links");
    auto AppendLinks=[&](const TArray<FNavigationLink>& Values,const FTransform& ToWorld,bool Processed)
    {
        for(const auto& Link:Values)
        {
            auto L=MakeShared<FJsonObject>();L->SetArrayField(TEXT("start"),Vec(ToWorld.TransformPosition(Link.Left)));L->SetArrayField(TEXT("end"),Vec(ToWorld.TransformPosition(Link.Right)));
            L->SetStringField(TEXT("area_class"),Link.GetAreaClass()?Link.GetAreaClass()->GetPathName():TEXT(""));L->SetStringField(TEXT("user_id"),LexToString(Link.NavLinkId.GetId()));
            L->SetBoolField(TEXT("bidirectional"),Link.Direction==ENavLinkDirection::BothWays);L->SetBoolField(TEXT("reversed"),Link.Direction==ENavLinkDirection::RightToLeft);
            L->SetBoolField(TEXT("snap_to_cheapest_area"),Link.bSnapToCheapestArea);L->SetBoolField(TEXT("generated"),Link.bIsGenerated);
            L->SetNumberField(TEXT("radius"),Link.SnapRadius);L->SetNumberField(TEXT("height"),Link.SnapHeight);L->SetBoolField(TEXT("use_snap_height"),Link.bUseSnapHeight);
            L->SetBoolField(TEXT("requires_projection"),Processed && (Link.LeftProjectHeight>0 || Link.MaxFallDownLength>0));
            TArray<TSharedPtr<FJsonValue>> Agents;for(int32 I=0;I<16;++I)if(Link.SupportedAgents.Contains(I))Agents.Add(MakeShared<FJsonValueNumber>(I));L->SetArrayField(TEXT("supported_agents"),Agents);
            Links.Add(MakeShared<FJsonValueObject>(L));
        }
    };
    for(const auto& Collection:Data->Modifiers.GetSimpleLinks())
    {
        AppendLinks(Collection.Links,Collection.LocalToWorld,true);
        if(Collection.SegmentLinks.Num())LinkCoverage=TEXT("segment_links_require_native_rebuild");
    }
    for(const auto& Collection:Data->Modifiers.GetCustomLinks())
    {
        if(auto Class=Collection.GetNavLinkClass().Get())AppendLinks(UNavLinkDefinition::GetLinksDefinition(Class),Collection.LocalToWorld,false);
        else LinkCoverage=TEXT("unresolved_link_definition");
    }
    E->SetArrayField(TEXT("navigation_links"),Links);E->SetStringField(TEXT("navigation_link_coverage"),LinkCoverage);
    E->SetArrayField(TEXT("navigation_modifiers"),Modifiers);E->SetBoolField(TEXT("navigation_mask_fill_underneath"),Data->Modifiers.GetMaskFillCollisionUnderneathForNavmesh());E->SetBoolField(TEXT("navigation_has_links"),Data->Modifiers.GetSimpleLinks().Num() || Data->Modifiers.GetCustomLinks().Num());E->SetBoolField(TEXT("navigation_fill_underneath"),Data->Modifiers.GetFillCollisionUnderneathForNavmesh());E->SetBoolField(TEXT("navigation_filled_convex"),Data->Modifiers.GetRasterizeAsFilledConvexVolume());
    if(!P)Database.Put(E);
}
bool USpatialTwinSubsystem::Rebuild(UWorld* World)
{
    if(bScanning){Database.Error=TEXT("Scan already in progress");return false;}
    if(!World)World=GEditor?GEditor->GetEditorWorldContext().World():nullptr;
    if(!World){Database.Error=TEXT("No editor world");return false;}
    FString Schema;auto Plugin=IPluginManager::Get().FindPlugin(TEXT("UnrealSpatialTwin"));
    if(!Plugin || !FFileHelper::LoadFileToString(Schema,*(Plugin->GetBaseDir()/TEXT("Resources/schema.sql"))))return false;
    if(Database.DB.IsValid() && World->GetOutermost()->GetName()!=MapId)DisconnectWorld();
    const FString Root=WorldRoot(World);
    if(!Database.DB.IsValid() && !Database.Open(Root,Schema))return false;
    MapId=World->GetOutermost()->GetName();bScanning=true;bVerifiedBaseline=false;AssetCache.Reset();
    if(!Database.Begin(true,MapId)){bScanning=false;return false;}
    bFullScan=true;FullScanActors.Reset();DescriptorReusedActors=0;
    const double ScanStart=FPlatformTime::Seconds();
    Database.Metadata(TEXT("engine_version"),FEngineVersion::Current().ToString());Database.Metadata(TEXT("project_id"),FApp::GetProjectName());
    Database.Metadata(TEXT("collision_bounds_version"),TEXT("2"));
    Database.Metadata(TEXT("navigation_owners_version"),TEXT("2"));
    Database.Metadata(TEXT("asset_package_lifecycle_version"),TEXT("2"));
    Database.Metadata(TEXT("asset_dependency_scope_version"),TEXT("1"));
    Database.Metadata(TEXT("nested_container_export_version"),TEXT("2"));
    Database.Metadata(TEXT("source_mode"),IsRunningCommandlet()?TEXT("saved_package_scan"):TEXT("live_editor"));
    Database.Metadata(TEXT("engine_directory"),FPaths::ConvertRelativePathToFull(FPaths::EngineDir()));
    Database.Metadata(TEXT("plugin_directory"),Plugin->GetBaseDir());
    Database.Metadata(TEXT("native_library"),FPaths::ConvertRelativePathToFull(FModuleManager::Get().GetModuleFilename(TEXT("SpatialTwinCore"))));
    auto W=Entity(MapId,TEXT("World"),World);Database.Put(W);
    bool Ok=true;
    // Loaded and descriptor records use the same map/GUID identity; container identity is added only for child instances.
    for(TActorIterator<AActor> It(World);It && Ok;++It)Ok=ExportActor(*It);
    const double LoadedDone=FPlatformTime::Seconds();
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinScan loaded_actors=%d loaded_seconds=%.3f"),FullScanActors.Num(),LoadedDone-ScanStart);
    if(auto WP=World->GetWorldPartition();Ok && WP && WP->GetActorDescContainerInstance())
    {FSpatialTwinDescriptorScope Scope(World,bScanning);Ok=ScanContainer(WP->GetActorDescContainerInstance(),Scope);}
    const double PartitionDone=FPlatformTime::Seconds();
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinScan descriptor_reused=%d partition_seconds=%.3f"),DescriptorReusedActors,PartitionDone-LoadedDone);
    if(auto WP=World->GetWorldPartition();Ok && WP && WP->RuntimeHash)
    {
        bool Generated=true;int32 Cells=0;auto Hash=WP->RuntimeHash.Get();Hash->ForEachStreamingCells([&](const UWorldPartitionRuntimeCell*){++Cells;return true;});
        // Generate cells from actual engine descriptors only in the isolated
        // headless scanner; never change the live editor's streaming policy.
        if(Cells==0 && IsRunningCommandlet() && WP->CanGenerateStreaming())
        {UWorldPartition::FGenerateStreamingParams Params;UWorldPartition::FGenerateStreamingContext Context;Generated=WP->GenerateStreaming(Params,Context);}
        Cells=0;
        Hash->ForEachStreamingCells([&](const UWorldPartitionRuntimeCell* Cell)
        {
            if(!Ok)return false;++Cells;const FString CellId=MapId+TEXT(":cell:")+Cell->GetGuid().ToString(EGuidFormats::Digits);
            auto E=Entity(CellId,TEXT("WorldPartitionCell"),const_cast<UWorldPartitionRuntimeCell*>(Cell));E->SetStringField(TEXT("parent_id"),MapId);E->SetStringField(TEXT("label"),Cell->GetDebugName());E->SetStringField(TEXT("level_package"),Cell->GetLevelPackageName().ToString());E->SetStringField(TEXT("source_type"),TEXT("native_streaming_generation"));Box(E,TEXT("bounds"),Cell->GetStreamingBounds());
            Ok=Database.Put(E) && Database.Relation(MapId,CellId,TEXT("CONTAINS"));
            if(auto LevelCell=Cast<UWorldPartitionRuntimeLevelStreamingCell>(Cell))for(const auto& Mapping:LevelCell->GetPackages())
            {
                const FString Actor=MapId+TEXT(":actor:")+Mapping.ActorInstanceGuid.ToString(EGuidFormats::Digits);
                FSpatialTwinSQLiteStatement Exists(Database.DB,TEXT("SELECT 1 FROM entities WHERE kind='Actor' AND id=?"));Exists.SetBindingValueByIndex(1,Actor);
                if(Exists.Step()==ESTSQLiteStepResult::Row)Ok=Database.Relation(Actor,CellId,TEXT("IN_PARTITION_CELL")) && Ok;
            }
            return Ok;
        });
        Database.Metadata(TEXT("partition_cell_state"),Cells?TEXT("CURRENT"):Generated?TEXT("NOT_GENERATED"):TEXT("GENERATION_FAILED"));
    }
    else Database.Metadata(TEXT("partition_cell_state"),World->GetWorldPartition()?TEXT("NOT_GENERATED"):TEXT("NOT_CONFIGURED"));
    if(Ok)Ok=IndexDataLayers(World);
    if(Ok)Ok=ExportSpatialTwinNavigation(World,Database);
    const double NavigationDone=FPlatformTime::Seconds();
    if(Ok)Ok=IndexAssets();
    const double AssetsDone=FPlatformTime::Seconds();
    if(Ok)Ok=SavePackageSources();
    if(Ok && Database.Error.IsEmpty())Ok=Database.Commit(MapId,true);else Database.Rollback();
    auto Metrics=MakeShared<FJsonObject>();Metrics->SetStringField(TEXT("operation"),TEXT("full_scan"));Metrics->SetBoolField(TEXT("success"),Ok);
    Metrics->SetNumberField(TEXT("loaded_seconds"),LoadedDone-ScanStart);Metrics->SetNumberField(TEXT("partition_seconds"),PartitionDone-LoadedDone);
    Metrics->SetNumberField(TEXT("navigation_seconds"),NavigationDone-PartitionDone);Metrics->SetNumberField(TEXT("asset_registry_seconds"),AssetsDone-NavigationDone);
    Metrics->SetNumberField(TEXT("commit_seconds"),FPlatformTime::Seconds()-AssetsDone);Metrics->SetNumberField(TEXT("total_seconds"),FPlatformTime::Seconds()-ScanStart);
    Metrics->SetNumberField(TEXT("descriptor_reused_actors"),DescriptorReusedActors);Metrics->SetNumberField(TEXT("unique_actor_exports"),FullScanActors.Num());
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Metrics),*(Database.Root/TEXT("last_scan_metrics.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    bFullScan=false;FullScanActors.Reset();bScanning=false;bReady=Ok;DirtyActors.Reset();DirtyActorIds.Reset();DirtyDescriptors.Reset();DeletedActors.Reset();DirtyAssets.Reset();
    if(Ok){RegistryChanges.Reset();RegistryRemovals.Reset();SavedPackages.Reset();DirtyPackages.Reset();WatchNavigation(World);bPartitionDirty=World->GetWorldPartition()!=nullptr;
        const FString NavState=Database.ReadMetadata(TEXT("navigation_state"));bNavigationRefreshPending=NavState.StartsWith(TEXT("STALE")) || NavState==TEXT("BUILDING");Ok=PublishWorld();}return Ok;
}
TMap<FString,FString> USpatialTwinSubsystem::PackageSources()
{
    TMap<FString,FString> Result;TArray<FString> Files;
    IFileManager::Get().FindFilesRecursive(Files,*FPaths::ProjectContentDir(),TEXT("*.uasset"),true,false);
    IFileManager::Get().FindFilesRecursive(Files,*FPaths::ProjectContentDir(),TEXT("*.umap"),true,false,false);
    IFileManager::Get().FindFilesRecursive(Files,*FPaths::ProjectConfigDir(),TEXT("*.ini"),true,false,false);
    for(const auto& Plugin:IPluginManager::Get().GetEnabledPlugins())if(Plugin->CanContainContent() && Plugin->GetLoadedFrom()==EPluginLoadedFrom::Project)
    {
        IFileManager::Get().FindFilesRecursive(Files,*Plugin->GetContentDir(),TEXT("*.uasset"),true,false,false);
        IFileManager::Get().FindFilesRecursive(Files,*Plugin->GetContentDir(),TEXT("*.umap"),true,false,false);
    }
    Files.Add(FPaths::GetProjectFilePath());
    // Sync can observe saved Engine or mounted-content packages outside the
    // project inventory. Recheck their files too; omission is not deletion.
    if(Database.DB.IsValid())
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT path FROM package_sources"));while(Q.Step()==ESTSQLiteStepResult::Row){FString Path;Q.GetColumnValueByIndex(0,Path);Files.Add(Path);}}
    const FName ProjectModule(FApp::GetProjectName());
    if(FModuleManager::Get().IsModuleLoaded(ProjectModule))Files.Add(FModuleManager::Get().GetModuleFilename(ProjectModule));
    for(auto Path:TSet<FString>(Files))
    {
        Path=SpatialTwinSourcePath(Path);
        const auto Stat=IFileManager::Get().GetStatData(*Path);
        if(Stat.bIsValid)Result.Add(Path,FString::Printf(TEXT("%lld|%lld"),Stat.FileSize,Stat.ModificationTime.GetTicks()));
    }
    return Result;
}
bool USpatialTwinSubsystem::RecordSavedPackage(UPackage* Package)
{
    if(!Package || Package->IsDirty() || !FPackageName::IsValidLongPackageName(Package->GetName()))return true;
    FString Path=FPackageName::LongPackageNameToFilename(Package->GetName(),Package->ContainsMap()?FPackageName::GetMapPackageExtension():FPackageName::GetAssetPackageExtension());
    Path=SpatialTwinSourcePath(Path);auto Stat=IFileManager::Get().GetStatData(*Path);
    FSpatialTwinSQLiteStatement Q(Database.DB,Stat.bIsValid?TEXT("INSERT INTO package_sources VALUES(?,?) ON CONFLICT(path) DO UPDATE SET signature=excluded.signature"):TEXT("DELETE FROM package_sources WHERE path=?"));
    Q.SetBindingValueByIndex(1,Path);if(Stat.bIsValid)Q.SetBindingValueByIndex(2,FString::Printf(TEXT("%lld|%lld"),Stat.FileSize,Stat.ModificationTime.GetTicks()));
    if(Q.Step()==ESTSQLiteStepResult::Done)return true;Database.Error=TEXT("Saved package signature update failed");return false;
}
bool USpatialTwinSubsystem::SavePackageSources()
{
    const auto Sources=PackageSources();
    if(!Database.Exec(TEXT("DELETE FROM package_sources")))return false;
    for(const auto& Item:Sources)
    {
        FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("INSERT INTO package_sources VALUES(?,?)"));
        Q.SetBindingValueByIndex(1,Item.Key);Q.SetBindingValueByIndex(2,Item.Value);
        if(Q.Step()!=ESTSQLiteStepResult::Done){Database.Error=TEXT("Package source signature write failed");return false;}
    }
    return Database.Metadata(TEXT("source_signatures_ready"),TEXT("1")) && Database.Metadata(TEXT("source_inventory_version"),TEXT("2"));
}
bool USpatialTwinSubsystem::CheckpointSources(const FString& Root)
{
    // Upgrade the incomplete inventory only when every saved source still predates
    // the confirmed scan, or has an exact native scoped-save signature.
    if(!IsRunningCommandlet()){Database.Error=TEXT("Source checkpoint requires the closed-editor commandlet");return false;}
    auto Plugin=IPluginManager::Get().FindPlugin(TEXT("UnrealSpatialTwin"));FString Schema,MetricsText;TSharedPtr<FJsonObject> Metrics;
    if(!Plugin || !FFileHelper::LoadFileToString(Schema,*(Plugin->GetBaseDir()/TEXT("Resources/schema.sql"))) || !Database.Open(Root,Schema))return false;
    if(Database.ReadMetadata(TEXT("project_id"))!=FApp::GetProjectName() || Database.ReadMetadata(TEXT("engine_version"))!=FEngineVersion::Current().ToString())
    {Database.Error=TEXT("Checkpoint project/engine mismatch");return false;}
    const FString MetricsPath=Root/TEXT("last_scan_metrics.json");
    if(!FFileHelper::LoadFileToString(MetricsText,*MetricsPath) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(MetricsText),Metrics) || !Metrics->GetBoolField(TEXT("success")))
    {Database.Error=TEXT("Successful native full-scan timing evidence required");return false;}
    const double Duration=Metrics->GetNumberField(TEXT("total_seconds"));auto Stat=IFileManager::Get().GetStatData(*MetricsPath);
    if(!FMath::IsFinite(Duration) || Duration<=0 || Duration>86400 || !Stat.bIsValid){Database.Error=TEXT("Invalid scan timing evidence");return false;}
    bool Confirmed=false;
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT map,timestamp FROM snapshots WHERE state='READY'"));
     while(Q.Step()==ESTSQLiteStepResult::Row){FString Map,Stamp;FDateTime Time;Q.GetColumnValueByIndex(0,Map);Q.GetColumnValueByIndex(1,Stamp);
       if(FDateTime::ParseIso8601(*Stamp,Time) && FMath::Abs((Time-Stat.ModificationTime).GetTotalSeconds())<2){MapId=Map;Confirmed=true;break;}}}
    if(!Confirmed){Database.Error=TEXT("Timing file has no matching READY native snapshot");return false;}
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT 1 FROM entities WHERE kind IN ('Actor','StaticMesh','Asset','Material') AND json_extract(source,'$.package_dirty')=1 LIMIT 1"));
     if(Q.Step()==ESTSQLiteStepResult::Row){Database.Error=TEXT("Unsaved canonical sources cannot be checkpointed");return false;}}
    auto Sources=PackageSources();TMap<FString,FString> Previous;
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT path,signature FROM package_sources"));
     while(Q.Step()==ESTSQLiteStepResult::Row){FString Path,Signature;Q.GetColumnValueByIndex(0,Path);Q.GetColumnValueByIndex(1,Signature);Previous.Add(Path,Signature);
       Path=SpatialTwinSourcePath(Path);auto Current=Sources.Find(Path);if(!Current || *Current!=Signature){Database.Error=TEXT("Previously recorded source changed: ")+Path;return false;}}}
    const FDateTime Cutoff=Stat.ModificationTime-FTimespan::FromSeconds(Duration);
    for(const auto& Source:Sources)if(!Previous.Contains(Source.Key) && IFileManager::Get().GetStatData(*Source.Key).ModificationTime>Cutoff)
    {Database.Error=TEXT("Unrecorded source changed after scan began: ")+Source.Key;return false;}
    const double Start=FPlatformTime::Seconds();
    if(!Database.Begin(false,MapId))return false;
    if(!SavePackageSources()){Database.Rollback();return false;}
    if(!Database.Commit(MapId,false))return false;
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinSources count=%d revision=%lld seconds=%.3f no_geometry_export=1"),Sources.Num(),Database.Revision,FPlatformTime::Seconds()-Start);return true;
}
bool USpatialTwinSubsystem::Resume()
{
    if(bScanning)return false;
    auto World=GEditor?GEditor->GetEditorWorldContext().World():nullptr;if(!World)return false;
    if(bReady){if(World->GetOutermost()->GetName()==MapId)return true;DisconnectWorld();}
    auto Plugin=IPluginManager::Get().FindPlugin(TEXT("UnrealSpatialTwin"));FString Schema;
    if(!Plugin || !FFileHelper::LoadFileToString(Schema,*(Plugin->GetBaseDir()/TEXT("Resources/schema.sql"))))return false;
    const FString Root=WorldRoot(World);
    if(!FPaths::FileExists(Root/TEXT("world.sqlite")))return false;
    if(!Database.DB.IsValid() && !Database.Open(Root,Schema))return false;
    if(Database.ReadMetadata(TEXT("project_id"))!=FApp::GetProjectName()){Database.Error=TEXT("Twin belongs to a different project");return false;}
    if(Database.ReadMetadata(TEXT("source_signatures_ready"))!=TEXT("1")){Database.Error=TEXT("Baseline predates package signatures; explicit reconciliation required");return false;}
    if(Database.ReadMetadata(TEXT("source_inventory_version"))!=TEXT("2")){Database.Error=TEXT("Incomplete source inventory; closed-editor checkpoint or explicit reconciliation required");return false;}
    MapId=World->GetOutermost()->GetName();
    {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT map FROM snapshots WHERE state='READY' ORDER BY revision DESC LIMIT 1"));FString Map;
     if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,Map) || Map!=MapId){Database.Error=TEXT("READY map differs; explicit scan required");return false;}}
    if(Database.ReadMetadata(TEXT("engine_version"))!=FEngineVersion::Current().ToString()){Database.Error=TEXT("Engine changed; explicit scan required");return false;}
    const double Start=FPlatformTime::Seconds();auto Sources=PackageSources();
    TArray<UActorDescContainerInstance*> Containers;if(auto Partition=World->GetWorldPartition())Containers.Add(Partition->GetActorDescContainerInstance());
    while(Containers.Num())if(auto Container=Containers.Pop()){WatchContainer(Container);for(auto Child:Container->GetChildContainerInstances())Containers.Add(Child.Value);}
    Database.Error.Reset();bReady=true;bVerifiedBaseline=true;
    QueueNestedContainerUpgrade(World);
    QueueTransientActors(World);
    if(!ReconcileSources(Sources)){bReady=false;bVerifiedBaseline=false;return false;}
    if(!Database.UpgradeCollisionBounds(MapId)){bReady=false;bVerifiedBaseline=false;return false;}
    if(!UpgradeAssetPackages()){bReady=false;bVerifiedBaseline=false;return false;}
    // Enrich only legacy brush owners whose collision coverage was absent.
    // Never infer EMPTY from an old cache; re-observe the native BodySetup.
    {FSpatialTwinSQLiteStatement Brushes(Database.DB,TEXT("SELECT DISTINCT actor_id FROM entities WHERE kind='Component' AND class='/Script/Engine.BrushComponent' AND actor_id IS NOT NULL AND (json_type(source,'$.collision.simple_coverage') IS NULL OR json_type(source,'$.collision.complex_coverage') IS NULL)"));
     while(Brushes.Step()==ESTSQLiteStepResult::Row){FString Id;Brushes.GetColumnValueByIndex(0,Id);DirtyActorIds.Add(Id);}}
    bNavigationOwnersUpgrade=Database.ReadMetadata(TEXT("navigation_owners_version"))!=TEXT("2");
    if(bNavigationOwnersUpgrade)
    {
        // One-time enrichment of legacy caches: only nonprimitive nav owners,
        // not a full rescan or geometry re-export of every static mesh actor.
        for(TActorIterator<AActor> It(World);It;++It)
        {
            bool Needed=Cast<INavRelevantInterface>(*It)!=nullptr;
            TInlineComponentArray<UActorComponent*> Components(*It);
            for(auto C:Components)if(!Cast<UPrimitiveComponent>(C) && C->CanEverAffectNavigation() && Cast<INavRelevantInterface>(C)){Needed=true;break;}
            if(Needed)DirtyActors.Add(*It);
        }
        // Existing primitive or unloaded link owners also need the point source.
        FSpatialTwinSQLiteStatement Links(Database.DB,TEXT("SELECT DISTINCT actor_id FROM entities WHERE kind IN ('Component','NavigationInput') AND json_extract(source,'$.navigation_has_links')=1 AND actor_id IS NOT NULL"));
        while(Links.Step()==ESTSQLiteStepResult::Row){FString Id;Links.GetColumnValueByIndex(0,Id);DirtyActorIds.Add(Id);}
    }
    WatchNavigation(World);
    const FString NavState=Database.ReadMetadata(TEXT("navigation_state"));
    bNavigationRefreshPending=NavState.StartsWith(TEXT("STALE")) || NavState==TEXT("BUILDING");
    bNavigationDirty=!bNavigationRefreshPending;bPartitionDirty=World->GetWorldPartition()!=nullptr;
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinResume revision=%lld seconds=%.3f full_scan=0"),Database.Revision,FPlatformTime::Seconds()-Start);
    return PublishWorld();
}
bool USpatialTwinSubsystem::CacheAsset(const FString& ObjectPath)
{
    if(!bReady || bScanning || !FPackageName::IsValidLongPackageName(FPackageName::ObjectPathToPackageName(ObjectPath))){Database.Error=TEXT("Cache asset requires READY and a mounted asset object path");return false;}
    auto Asset=LoadObject<UObject>(nullptr,*ObjectPath);
    if(!Asset || !Asset->IsAsset()){Database.Error=TEXT("Asset not found");return false;}
    Dirty(Asset);return Sync();
}
bool USpatialTwinSubsystem::Sync()
{
    if(!bReady || bScanning)return false;UWorld* W=GEditor->GetEditorWorldContext().World();
    if(!W || W->GetOutermost()->GetName()!=MapId){Database.Error=TEXT("Map changed; full scan required");return false;}
    auto Nav=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);
    if(Nav && !Nav->IsNavigationOctreeLocked())Nav->ProcessPendingOctreeUpdates();
    if(!Database.Begin(false,MapId))return false;bScanning=true;bool Ok=true;
    const double SyncStart=FPlatformTime::Seconds();const int32 SyncActors=DirtyActors.Num()+DirtyActorIds.Num(),SyncAssets=DirtyAssets.Num();
    // Refresh only packages actually saved; registry dependencies are disk data.
    // Coalesce saves into one native registry scan, never rescan for a drag.
    TArray<FString> SavedFiles;
    for(auto Weak:SavedPackages)if(auto P=Weak.Get()){FString File;if(FPackageName::DoesPackageExist(P->GetName(),&File))SavedFiles.AddUnique(File);}
    if(SavedFiles.Num())FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get().ScanFilesSynchronous(SavedFiles,true);
    TArray<FWorldPartitionReference> LoadedDescriptors;
    for(const auto& Entry:RegistryChanges)if(auto Asset=FindObject<UObject>(nullptr,*Entry.Key);Asset && Asset->IsAsset())DirtyAssets.Add(Asset);
    for(auto Weak:DirtyAssets)if(auto Asset=Weak.Get())
    {
        if(auto Mesh=Cast<UStaticMesh>(Asset))MeshAsset(Mesh);
        else
        {
            auto E=Entity(TEXT("asset:")+Asset->GetPathName(),Asset->IsA<UMaterialInterface>()?TEXT("Material"):TEXT("Asset"),Asset);
            E->SetStringField(TEXT("label"),Asset->GetName());E->SetBoolField(TEXT("package_dirty"),Asset->GetPackage()->IsDirty());Ok=Database.Put(E) && Ok;
        }
        Ok=RecordSavedPackage(Asset->GetPackage()) && Ok;
        Ok=IndexAsset(FAssetData(Asset)) && Ok;
    }
    for(auto Id:DirtyActorIds)
    {
        if(DeletedActors.Contains(Id))continue;
        FString Source;{FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Id);if(Q.Step()==ESTSQLiteStepResult::Row)Q.GetColumnValueByIndex(0,Source);}
        TSharedPtr<FJsonObject> E;if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),E))continue;
        if(auto A=FindObject<AActor>(nullptr,*E->GetStringField(TEXT("path")))){DirtyActors.Add(A);continue;}
        FString ContainerId,Descriptor;FGuid Guid;
        if(!E->TryGetStringField(TEXT("container_id"),ContainerId) || !E->TryGetStringField(TEXT("descriptor_guid"),Descriptor) || !FGuid::Parse(Descriptor,Guid)){Database.Error=TEXT("Affected unloaded actor has no descriptor identity: ")+Id+TEXT(" path=")+E->GetStringField(TEXT("path"));Ok=false;break;}
        TArray<UActorDescContainerInstance*> Containers;if(auto WP=W->GetWorldPartition())Containers.Add(WP->GetActorDescContainerInstance());
        UActorDescContainerInstance* Found=nullptr;
        while(Containers.Num()){auto C=Containers.Pop();if(!C)continue;if(C->GetContainerID().ToString()==ContainerId){Found=C;break;}for(auto Child:C->GetChildContainerInstances())Containers.Add(Child.Value);}
        if(!Found){Database.Error=TEXT("Affected unloaded actor container unavailable");Ok=false;break;}
        LoadedDescriptors.Emplace(Found,Guid);auto A=LoadedDescriptors.Last().GetActor();
        if(!A){Database.Error=TEXT("Affected unloaded actor could not be loaded for synchronization");Ok=false;break;}DirtyActors.Add(A);
    }
    TSet<FString> LevelCandidates=NestedUpgradeLevels;
    for(const auto& Id:DeletedActors)
    {
        FSpatialTwinSQLiteStatement Parent(Database.DB,TEXT("SELECT parent_id FROM entities WHERE id=?"));Parent.SetBindingValueByIndex(1,Id);FString Level;
        if(Parent.Step()==ESTSQLiteStepResult::Row && Parent.GetColumnValueByIndex(0,Level))LevelCandidates.Add(Level);Parent.Destroy();
        if(!Database.DeleteActor(Id))Ok=false;
    }
    for(const auto& Weak:DirtyActors)if(auto A=Weak.Get())
    {
        const FString Id=ActorId(A);
        if(!Database.ResetActorRelations(Id) || !ExportActor(A) || !Database.PruneActor(Id))Ok=false;
    }
    {FSpatialTwinDescriptorScope Scope(W,bScanning);
    for(const auto& Pending:DirtyDescriptors)
    {
        auto C=Pending.Value.Container.Get();auto D=C?C->GetActorDescInstance(Pending.Value.Guid):nullptr;
        if(!D || !ScanDescriptor(C,D,Scope)){Database.Error=TEXT("Changed World Partition descriptor unavailable");Ok=false;}
    }
    }
    // Retire only levels whose last source owner was removed. Keep real empty
    // world levels and empty instances whose owning Actor still exists.
    TSet<FString> NativeLevels;for(auto Level:W->GetLevels())if(Level)NativeLevels.Add(MapId+TEXT(":level:")+Level->GetOutermost()->GetName());
    for(const auto& Level:LevelCandidates)
    {
        FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT json_extract(source,'$.instance_owner_id') FROM entities WHERE id=? AND kind='Level' AND NOT EXISTS(SELECT 1 FROM entities child WHERE child.parent_id=entities.id)"));Q.SetBindingValueByIndex(1,Level);FString Owner;
        if(Q.Step()!=ESTSQLiteStepResult::Row)continue;Q.GetColumnValueByIndex(0,Owner);Q.Destroy();
        if(Owner.IsEmpty()){if(NativeLevels.Contains(Level))continue;}
        else {FSpatialTwinSQLiteStatement Exists(Database.DB,TEXT("SELECT 1 FROM entities WHERE id=?"));Exists.SetBindingValueByIndex(1,Owner);if(Exists.Step()==ESTSQLiteStepResult::Row)continue;}
        Ok=Database.Delete(Level) && Ok;
    }
    Ok=IndexDataLayers(W) && Ok;
    for(const auto& Entry:RegistryChanges)Ok=IndexAsset(Entry.Value) && Ok;
    for(const auto& Path:RegistryRemovals)
    {
        const FString AssetId=TEXT("asset:")+Path;FString Package;
        // External actors have an object path in the map but a different
        // package. Read the indexed registry identity before deleting it.
        {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT json_extract(source,'$.package') FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,AssetId);if(Q.Step()==ESTSQLiteStepResult::Row)Q.GetColumnValueByIndex(0,Package);}
        Ok=Database.Delete(AssetId) && Ok;if(Package.IsEmpty())continue;
        auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();TArray<FAssetData> Remaining;Registry.GetAssetsByPackageName(FName(*Package),Remaining);
        // Undo can keep a deleted asset UObject alive. Registry enumeration
        // includes it, but the coalesced removal event is authoritative here.
        Remaining.RemoveAll([&](const FAssetData& A){return RegistryRemovals.Contains(A.GetSoftObjectPath().ToString());});
        if(Remaining.Num())continue;
        const FString PackageId=TEXT("asset_package:")+Package;
        Database.TrackRelations(PackageId);
        {FSpatialTwinSQLiteStatement D(Database.DB,TEXT("DELETE FROM relationships WHERE source=? AND kind='DEPENDS_ON'"));D.SetBindingValueByIndex(1,PackageId);Ok=(D.Step()==ESTSQLiteStepResult::Done) && Ok;}
        bool Referenced=false;{FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT 1 FROM relationships WHERE target=? LIMIT 1"));Q.SetBindingValueByIndex(1,PackageId);Referenced=Q.Step()==ESTSQLiteStepResult::Row;}
        if(Referenced)
        {
            auto E=Entity(PackageId,TEXT("Asset"));E->SetStringField(TEXT("path"),Package);E->SetStringField(TEXT("asset_type"),TEXT("Package"));E->SetStringField(TEXT("registry_state"),TEXT("no_registered_assets"));Ok=Database.Put(E) && Ok;
        }
        else Ok=Database.Delete(PackageId) && Ok;
    }
    for(auto Weak:DirtyPackages)if(auto P=Weak.Get())
    {
        FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT 1 FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,TEXT("asset_package:")+P->GetName());
        if(Q.Step()==ESTSQLiteStepResult::Row){Q.Destroy();Ok=IndexAssetPackage(P->GetFName()) && Ok;}
    }
    for(auto Weak:SavedPackages)if(auto P=Weak.Get())
    {
        Ok=RecordSavedPackage(P) && Ok;
        FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT 1 FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,TEXT("asset_package:")+P->GetName());
        if(Q.Step()==ESTSQLiteStepResult::Row){Q.Destroy();Ok=IndexAssetPackage(P->GetFName()) && Ok;}
    }
    bool SpatialChange=false,NavigationChange=false;
    {
        FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT 1 FROM changes WHERE revision=? AND (before_json IS NULL OR after_json IS NULL OR json_extract(before_json,'$.transform') IS NOT json_extract(after_json,'$.transform') OR json_extract(before_json,'$.bounds') IS NOT json_extract(after_json,'$.bounds') OR json_extract(before_json,'$.data_layers') IS NOT json_extract(after_json,'$.data_layers') OR json_extract(before_json,'$.attached_to') IS NOT json_extract(after_json,'$.attached_to')) AND COALESCE(json_extract(after_json,'$.kind'),json_extract(before_json,'$.kind'))='Actor' LIMIT 1"));Q.SetBindingValueByIndex(1,Database.Revision+1);SpatialChange=Q.Step()==ESTSQLiteStepResult::Row;
        FSpatialTwinSQLiteStatement N(Database.DB,TEXT("SELECT 1 FROM changes WHERE revision=? AND (json_extract(before_json,'$.affects_navigation')=1 OR json_extract(after_json,'$.affects_navigation')=1) AND (json_extract(before_json,'$.transform') IS NOT json_extract(after_json,'$.transform') OR json_extract(before_json,'$.navigation_geometry_hash') IS NOT json_extract(after_json,'$.navigation_geometry_hash') OR json_extract(before_json,'$.navigation_modifiers') IS NOT json_extract(after_json,'$.navigation_modifiers') OR json_extract(before_json,'$.navigation_links') IS NOT json_extract(after_json,'$.navigation_links') OR json_extract(before_json,'$.navigation_fill_underneath') IS NOT json_extract(after_json,'$.navigation_fill_underneath') OR COALESCE(json_extract(before_json,'$.navigation_filled_convex'),0) IS NOT COALESCE(json_extract(after_json,'$.navigation_filled_convex'),0) OR json_extract(before_json,'$.affects_navigation') IS NOT json_extract(after_json,'$.affects_navigation') OR before_json IS NULL OR after_json IS NULL) LIMIT 1"));N.SetBindingValueByIndex(1,Database.Revision+1);NavigationChange=N.Step()==ESTSQLiteStepResult::Row;
    }
    if(SpatialChange || DirtyDescriptors.Num())
    {bPartitionDirty=W->GetWorldPartition()!=nullptr;Ok=Database.Metadata(TEXT("partition_cell_state"),bPartitionDirty?TEXT("PENDING_NATIVE_GENERATION"):TEXT("NOT_CONFIGURED")) && Ok;}
    bool HasRecast=false;if(Nav)for(auto Data:Nav->NavDataSet)HasRecast|=Data && Data->IsA<ARecastNavMesh>();
    const bool NavPending=HasRecast && SpatialTwinNavigationPending(Nav);
    const bool RefreshNav=bNavigationDirty || bNavigationRefreshPending || NavigationChange;
    const uint64 ObservedNavigationSerial=NavigationChangeSerial;
    if(!HasRecast)Ok=Database.Metadata(TEXT("navigation_state"),TEXT("NOT_CONFIGURED")) && Ok;
    else if(NavigationChange)Ok=Database.Metadata(TEXT("navigation_state"),TEXT("STALE_NATIVE_REBUILD_PENDING")) && Ok;
    const bool ExportNav=HasRecast && RefreshNav && !NavPending;
    if(Ok && ExportNav)Ok=ExportSpatialTwinNavigation(W,Database);
    if(NavPending && bNavigationDirty)Ok=Database.Metadata(TEXT("navigation_state"),TEXT("BUILDING")) && Ok;
    if(Ok && bNavigationOwnersUpgrade)Ok=Database.Metadata(TEXT("navigation_owners_version"),TEXT("2"));
    if(Ok && bNestedContainersUpgrade)Ok=Database.Metadata(TEXT("nested_container_export_version"),TEXT("2"));
    if(Ok && Database.Error.IsEmpty())Ok=Database.Commit(MapId,false);else Database.Rollback();
    if(Ok){bNavigationOwnersUpgrade=false;bNestedContainersUpgrade=false;NestedUpgradeLevels.Reset();}
    LoadedDescriptors.Reset(); // Temporary unloading is not canonical deletion.
    auto Metrics=MakeShared<FJsonObject>();Metrics->SetStringField(TEXT("operation"),TEXT("incremental_sync"));Metrics->SetBoolField(TEXT("success"),Ok);
    Metrics->SetNumberField(TEXT("saved_package_dependency_scan_files"),SavedFiles.Num());Metrics->SetNumberField(TEXT("dirty_packages"),DirtyPackages.Num());
    Metrics->SetNumberField(TEXT("actors"),SyncActors);Metrics->SetNumberField(TEXT("assets"),SyncAssets);Metrics->SetNumberField(TEXT("total_seconds"),FPlatformTime::Seconds()-SyncStart);Metrics->SetNumberField(TEXT("revision"),Database.Revision);
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Metrics),*(Database.Root/TEXT("last_sync_metrics.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    // A pending refresh polls only native readiness, not the database/world.
    // Retain events delivered reentrantly while extraction/export was running.
    bScanning=false;if(Ok){DirtyActors.Reset();DirtyActorIds.Reset();DirtyDescriptors.Reset();DeletedActors.Reset();DirtyAssets.Reset();RegistryChanges.Reset();RegistryRemovals.Reset();SavedPackages.Reset();DirtyPackages.Reset();bDataLayersDirty=false;bNavigationDirty=NavigationChangeSerial!=ObservedNavigationSerial;bNavigationRefreshPending=HasRecast && RefreshNav && NavPending;}return Ok;
}
FString USpatialTwinSubsystem::Status() const
{
    auto J=MakeShared<FJsonObject>();J->SetBoolField(TEXT("ready"),bReady);J->SetBoolField(TEXT("scanning"),bScanning);J->SetNumberField(TEXT("revision"),Database.Revision);J->SetStringField(TEXT("map"),MapId);J->SetStringField(TEXT("project_id"),FApp::GetProjectName());J->SetStringField(TEXT("root"),Database.Root);J->SetStringField(TEXT("error"),Database.Error);J->SetNumberField(TEXT("pending"),PendingCount());
    J->SetBoolField(TEXT("navigation_refresh_pending"),bNavigationRefreshPending);
    J->SetNumberField(TEXT("mesh_geometry_export_attempts"),MeshGeometryExportAttempts);
    J->SetBoolField(TEXT("background_tick_requested"),NeedsBackgroundTick());
    if(auto Nav=GEditor?FNavigationSystem::GetCurrent<UNavigationSystemV1>(GEditor->GetEditorWorldContext().World()):nullptr)
    {J->SetBoolField(TEXT("navigation_building"),Nav->IsNavigationBuildInProgress());J->SetNumberField(TEXT("navigation_dirty_areas"),Nav->GetNumDirtyAreas());J->SetBoolField(TEXT("navigation_locked"),Nav->IsNavigationBuildingLocked() || Nav->IsNavigationOctreeLocked());J->SetNumberField(TEXT("navigation_pending_bounds"),Nav->PendingNavBoundsUpdates.Num());}
    return FSpatialTwinDatabase::Json(J);
}
FString USpatialTwinSubsystem::Validate()
{
    if(!Database.DB.IsValid())return TEXT("{\"valid\":false,\"error\":\"No database\"}");
    FSpatialTwinSQLiteStatement S(Database.DB,TEXT("PRAGMA integrity_check"));FString V;
    if(S.Step()==ESTSQLiteStepResult::Row)S.GetColumnValueByIndex(0,V);
    auto J=MakeShared<FJsonObject>();J->SetBoolField(TEXT("valid"),V==TEXT("ok"));J->SetStringField(TEXT("integrity"),V);return FSpatialTwinDatabase::Json(J);
}
