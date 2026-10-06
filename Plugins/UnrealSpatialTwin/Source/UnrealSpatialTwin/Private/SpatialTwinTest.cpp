#include "SpatialTwinToolset.h"
#include "SpatialTwinSubsystem.h"
#include "Editor.h"
#include "Misc/App.h"
#include "FileHelpers.h"
#include "Engine/StaticMeshActor.h"
#include "Components/StaticMeshComponent.h"
#include "Components/InstancedStaticMeshComponent.h"
#include "Components/HierarchicalInstancedStaticMeshComponent.h"
#include "SpatialTwinSQLite.h"
#include "Misc/FileHelper.h"
#include "Serialization/JsonSerializer.h"
#include "ScopedTransaction.h"
#include "Misc/AutomationTest.h"
#include "Editor/TransBuffer.h"
#include "NavigationSystem.h"
#include "NavMesh/RecastNavMesh.h"
#include "EngineUtils.h"
#include "Interfaces/IPluginManager.h"
#include "Misc/EngineVersion.h"
#include "Misc/Parse.h"
#include "Async/TaskGraphInterfaces.h"
#include "Modules/ModuleManager.h"
#include "Misc/Paths.h"
#include "AssetCompilingManager.h"
#include "AssetRegistry/AssetRegistryModule.h"
#include "Materials/Material.h"
#include "Components/BoxComponent.h"
#include "Components/BrushComponent.h"
#include "Engine/Brush.h"
#include "WorldPartition/WorldPartition.h"
#include "Misc/CommandLine.h"
#include "PhysicsEngine/BodySetup.h"
#include "WorldPartition/WorldPartitionHandle.h"
#include "Misc/PackageName.h"
#include "NavModifierComponent.h"
#include "NavAreas/NavArea_Null.h"
#include "Navigation/NavLinkProxy.h"
#include "NavMesh/NavMeshBoundsVolume.h"
#include "Builders/CubeBuilder.h"
#include "ActorFactories/ActorFactory.h"
#include "NavModifierVolume.h"
#include "NavAreas/NavArea_Default.h"
#include "UObject/UnrealType.h"
#include "DataLayer/DataLayerEditorSubsystem.h"
#include "WorldPartition/DataLayer/DataLayerInstance.h"
#include "NavigationOctree.h"
#include "Factories/FbxFactory.h"
#include "Factories/FbxImportUI.h"
#include "Factories/FbxStaticMeshImportData.h"
#include "AssetImportTask.h"

static int32 RunSpatialTwinAuthoredNavFixture()
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 340;
    FString Source;if(!FParse::Value(FCommandLine::Get(),TEXT("SpatialTwinSource="),Source) || !FPaths::FileExists(Source))return 341;
    GEditor->NewMap(false);auto W=GEditor->GetEditorWorldContext().World();
    auto Factory=NewObject<UFbxFactory>();auto Options=Factory->ImportUI;
    Options->bAutomatedImportShouldDetectType=false;Options->bImportMesh=true;Options->bImportAsSkeletal=false;
    Options->MeshTypeToImport=FBXIT_StaticMesh;Options->OriginalImportType=FBXIT_StaticMesh;
    Options->bImportMaterials=false;Options->bImportTextures=false;Options->bImportAnimations=false;Options->StaticMeshImportData->bCombineMeshes=true;
    auto Task=NewObject<UAssetImportTask>();Task->bAutomated=true;Task->Options=Options;Factory->SetAssetImportTask(Task);
    bool Canceled=false;auto Mesh=Cast<UStaticMesh>(Factory->ImportObject(UStaticMesh::StaticClass(),CreatePackage(TEXT("/Game/SpatialTwinTests/AuthoredNavigation")),TEXT("AuthoredNavigation"),RF_Public|RF_Standalone,Source,nullptr,Canceled));
    if(!Mesh || Canceled)return 342;FAssetCompilingManager::Get().FinishAllCompilation();
    auto A=W->SpawnActor<AStaticMeshActor>();A->SetActorLabel(TEXT("AuthoredNavigation"));auto C=A->GetStaticMeshComponent();C->SetStaticMesh(Mesh);C->RecreatePhysicsState();
    auto Bounds=W->SpawnActor<ANavMeshBoundsVolume>();auto Builder=NewObject<UCubeBuilder>();Builder->X=10000;Builder->Y=10000;Builder->Z=5000;UActorFactory::CreateBrushForVolumeActor(Bounds,Builder);
    auto N=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);if(!N){FNavigationSystem::AddNavigationSystemToWorld(*W,FNavigationSystemRunMode::EditorMode);N=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);}
    if(!N)return 343;N->OnNavigationBoundsUpdated(Bounds);N->OnWorldInitDone(FNavigationSystemRunMode::EditorMode);N->UpdateActorAndComponentsInNavOctree(*A);N->ProcessPendingOctreeUpdates();
    for(int I=0;I<10;++I){W->Tick(LEVELTICK_TimeOnly,1.f/30);FTSTicker::GetCoreTicker().Tick(1.f/30);++GFrameCounter;}
    auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();if(!S || !S->Rebuild(W))return 344;
    FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT id,source FROM entities WHERE path=? AND kind='Component'"));Q.SetBindingValueByIndex(1,C->GetPathName());FString Id,Record;
    if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,Id) || !Q.GetColumnValueByIndex(1,Record))return 345;
    TSharedPtr<FJsonObject> Entity;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Record),Entity);
    auto Evidence=MakeShared<FJsonObject>();Evidence->SetStringField(TEXT("component_id"),Id);Evidence->SetStringField(TEXT("asset_id"),TEXT("asset:")+Mesh->GetPathName());Evidence->SetNumberField(TEXT("convex_count"),Mesh->GetBodySetup()->AggGeom.ConvexElems.Num());Evidence->SetObjectField(TEXT("source"),Entity);
    const bool Ready=Entity->HasField(TEXT("navigation_geometry_hash"));Evidence->SetStringField(TEXT("state"),Ready?TEXT("PASS_NATIVE_EXPORT"):TEXT("FAILED_NATIVE_EXPORT"));
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Evidence),*(S->Database.Root/TEXT("authored-navigation-probe.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);return Ready?0:346;
}

static int32 RunSpatialTwinEmptyInputFixture()
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 330;
    GEditor->NewMap(false);auto W=GEditor->GetEditorWorldContext().World();
    auto Original=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));if(!Original)return 331;
    FAssetCompilingManager::Get().FinishAllCompilation();
    auto Mesh=DuplicateObject<UStaticMesh>(Original,CreatePackage(TEXT("/Game/SpatialTwinTests/EmptyCollisionMesh")),TEXT("EmptyCollisionMesh"));
    auto Body=Mesh->GetBodySetup();if(!Body)return 332;
    Body->AggGeom.EmptyElements();Body->CollisionTraceFlag=CTF_UseSimpleAndComplex;Body->InvalidatePhysicsData();Body->CreatePhysicsMeshes();
    Mesh->SetNavCollision(nullptr);Mesh->CreateNavCollision(true);
    auto A=W->SpawnActor<AStaticMeshActor>();A->SetActorLabel(TEXT("EmptySimpleCollision"));A->SetActorLocation(FVector(0,0,100));
    auto C=A->GetStaticMeshComponent();C->SetStaticMesh(Mesh);C->SetCollisionProfileName(TEXT("BlockAll"));C->RecreatePhysicsState();
    auto Bounds=W->SpawnActor<ANavMeshBoundsVolume>();auto Builder=NewObject<UCubeBuilder>();Builder->X=2000;Builder->Y=2000;Builder->Z=1000;UActorFactory::CreateBrushForVolumeActor(Bounds,Builder);
    auto N=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);if(!N){FNavigationSystem::AddNavigationSystemToWorld(*W,FNavigationSystemRunMode::EditorMode);N=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);}
    if(!N)return 333;N->OnNavigationBoundsUpdated(Bounds);N->OnWorldInitDone(FNavigationSystemRunMode::EditorMode);
    N->UpdateActorAndComponentsInNavOctree(*A);N->ProcessPendingOctreeUpdates();
    for(int I=0;I<10;++I){W->Tick(LEVELTICK_TimeOnly,1.f/30);FTSTicker::GetCoreTicker().Tick(1.f/30);++GFrameCounter;}
    auto Evidence=MakeShared<FJsonObject>();FHitResult Hit;FCollisionQueryParams Params;
    Params.bTraceComplex=false;const bool Simple=C->LineTraceComponent(Hit,FVector(0,0,200),FVector(0,0,0),Params);
    Params.bTraceComplex=true;const bool Complex=C->LineTraceComponent(Hit,FVector(0,0,200),FVector(0,0,0),Params);
    Evidence->SetBoolField(TEXT("native_simple_hit"),Simple);Evidence->SetBoolField(TEXT("native_complex_hit"),Complex);
    Evidence->SetNumberField(TEXT("native_complex_distance"),Complex?Hit.Distance:-1);
    Evidence->SetBoolField(TEXT("physics_created"),Body->bCreatedPhysicsMeshes);Evidence->SetBoolField(TEXT("physics_failed"),Body->bFailedToCreatePhysicsMeshes);
    Evidence->SetNumberField(TEXT("simple_elements"),Body->AggGeom.GetElementCount());Evidence->SetNumberField(TEXT("complex_meshes"),Body->TriMeshGeometries.Num());
    if(auto Octree=N->GetNavOctree())if(auto Id=N->GetNavOctreeIdForElement(FNavigationElementHandle(C)))
    {
        if(auto Data=const_cast<FNavigationOctree*>(Octree)->GetMutableDataForID(*Id))
        {N->DemandLazyDataGathering(*Data);Evidence->SetBoolField(TEXT("native_nav_has_geometry"),Data->HasGeometry());Evidence->SetBoolField(TEXT("native_nav_pending_geometry"),Data->IsPendingLazyGeometryGathering());Evidence->SetNumberField(TEXT("native_nav_collision_bytes"),Data->CollisionData.Num());}
    }
    auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();if(!S || !S->Rebuild(W))return 334;
    FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT id,source FROM entities WHERE path=? AND kind='Component'"));Q.SetBindingValueByIndex(1,C->GetPathName());FString Id,Source;
    if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,Id) || !Q.GetColumnValueByIndex(1,Source))return 335;
    TSharedPtr<FJsonObject> Entity;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Entity);Evidence->SetStringField(TEXT("component_id"),Id);Evidence->SetObjectField(TEXT("source"),Entity);
    const bool Exported=Entity->GetObjectField(TEXT("collision"))->GetStringField(TEXT("simple_coverage"))==TEXT("EMPTY") && Entity->GetObjectField(TEXT("collision"))->GetStringField(TEXT("complex_coverage"))==TEXT("AVAILABLE") && Entity->GetStringField(TEXT("navigation_input_coverage"))==TEXT("native_empty_geometry");
    Evidence->SetBoolField(TEXT("exported_coverage_matches"),Exported);
    Evidence->SetStringField(TEXT("state"),!Simple && Complex && Exported && Body->bCreatedPhysicsMeshes && !Body->bFailedToCreatePhysicsMeshes?TEXT("PASS_NATIVE_PROBE"):TEXT("FAILED_NATIVE_PROBE"));
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Evidence),*(S->Database.Root/TEXT("empty-input-probe.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinEmptyInputProbe simple=%d complex=%d exported=%d"),Simple,Complex,Exported);return !Simple && Complex && Exported?0:336;
}

static int32 RunSpatialTwinInstanceFixture()
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 300;
    if(!FEditorFileUtils::LoadMap(TEXT("/Game/SpatialTwinPartitionFixture"),false,true))return 301;
    auto W=GEditor->GetEditorWorldContext().World();auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();
    if(FPaths::FileExists(S->WorldRoot(W)/TEXT("world.sqlite")))return 302;
    if(!GEditor->Trans){auto T=NewObject<UTransBuffer>();T->Initialize(64*1024*1024);GEditor->Trans=T;}
    auto Container=W->GetWorldPartition()->GetActorDescContainerInstance();FGuid Guid;
    for(FActorDescInstanceList::TIterator<> It(Container);It;++It)if(It->GetActorLabel()==FName(TEXT("GenericPartitionCube"))){Guid=It->GetGuid();break;}
    FWorldPartitionReference Reference(Container,Guid);auto A=Reference.GetActor();if(!A)return 303;
    // HISM derives from ISM: after the fixture is saved, FindComponentByClass
    // may return HISM and silently test the same component twice.
    UInstancedStaticMeshComponent* ISM=nullptr;TInlineComponentArray<UInstancedStaticMeshComponent*> MeshComponents(A);
    for(auto C:MeshComponents)if(C->GetClass()==UInstancedStaticMeshComponent::StaticClass()){ISM=C;break;}
    if(!ISM || ISM->GetInstanceCount()!=2)return 304;
    auto HISM=FindObject<UHierarchicalInstancedStaticMeshComponent>(A,TEXT("TwinInstanceTestHISM"));
    if(!HISM){HISM=NewObject<UHierarchicalInstancedStaticMeshComponent>(A,TEXT("TwinInstanceTestHISM"),RF_Transactional);A->AddInstanceComponent(HISM);HISM->SetupAttachment(A->GetRootComponent());HISM->RegisterComponent();}
    HISM->ClearInstances();HISM->SetStaticMesh(ISM->GetStaticMesh());HISM->SetCanEverAffectNavigation(false);
    HISM->SetRelativeTransform(FTransform(FQuat(FVector::UpVector,.2),FVector(0,5000,0),FVector(2,1.5,.75)));
    HISM->AddInstance(FTransform(FVector(0,500,0)));HISM->AddInstance(FTransform(FVector(300,500,0)));
    if(!S->Rebuild(W))return 305;
    auto Await=[&](){FPlatformProcess::Sleep(.3f);FTSTicker::GetCoreTicker().Tick(.3f);return S->Database.Error.IsEmpty();};
    auto IdAt=[&](UInstancedStaticMeshComponent* C,int32 Index)
    {
        FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT i.id FROM entities i JOIN entities c ON c.id=i.parent_id WHERE c.path=? AND i.kind='Instance' AND json_extract(i.source,'$.instance_index')=?"));
        Q.SetBindingValueByIndex(1,C->GetPathName());Q.SetBindingValueByIndex(2,Index);FString Id;if(Q.Step()==ESTSQLiteStepResult::Row)Q.GetColumnValueByIndex(0,Id);return Id;
    };
    auto Position=[&](const FString& Id)
    {
        FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT json_extract(source,'$.transform.position[0]'),json_extract(source,'$.transform.position[1]'),json_extract(source,'$.transform.position[2]') FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Id);
        FVector P(TNumericLimits<double>::Max());if(Q.Step()==ESTSQLiteStepResult::Row)for(int32 I=0;I<3;++I)Q.GetColumnValueByIndex(I,P[I]);return P;
    };
    TArray<TSharedPtr<FJsonValue>> Results;
    for(UInstancedStaticMeshComponent* C:TArray<UInstancedStaticMeshComponent*>{ISM,HISM})
    {
        const FString First=IdAt(C,0),Second=IdAt(C,1);FTransform Before,Other,Actual;
        if(First.IsEmpty() || Second.IsEmpty() || !C->GetInstanceTransform(0,Before,true) || !C->GetInstanceTransform(1,Other,true))return 306;
        FTransform Moved=Before;Moved.AddToTranslation(FVector(0,0,275));Moved.SetRotation(FQuat(FVector::UpVector,.4)*Before.GetRotation());Moved.SetScale3D(Before.GetScale3D()*FVector(1.2,.8,1.5));
        if(!USpatialTwinToolset::spatial_twin_transform_instance(First,Before,Moved) || !Await())return 307;
        C->GetInstanceTransform(0,Actual,true);if(!Actual.Equals(Moved,.01) || !Position(First).Equals(Moved.GetLocation(),.01))return 308;
        C->GetInstanceTransform(1,Actual,true);if(!Actual.Equals(Other,.01) || IdAt(C,1)!=Second)return 309;
        if(USpatialTwinToolset::spatial_twin_transform_instance(First,Before,Before))return 310; // stale compare-and-set
        const bool Undone=GEditor->UndoTransaction();const bool Synced=Await();C->GetInstanceTransform(0,Actual,true);
        if(!Undone || !Synced || !Position(First).Equals(Before.GetLocation(),.01))
        {UE_LOG(LogTemp,Error,TEXT("InstanceUndo class=%s undone=%d sync=%d transactional=%d native=%s twin=%s expected=%s"),*C->GetClass()->GetName(),Undone,Synced,C->HasAnyFlags(RF_Transactional),*Actual.GetLocation().ToString(),*Position(First).ToString(),*Before.GetLocation().ToString());return 311;}
        if(!GEditor->RedoTransaction() || !Await() || !Position(First).Equals(Moved.GetLocation(),.01))return 312;
        if(!USpatialTwinToolset::spatial_twin_transform_instance(First,Moved,Before) || !Await())return 313;
        {const FScopedTransaction T(NSLOCTEXT("SpatialTwin","RemoveInstanceTest","Remove fixture instance"));C->Modify();if(!C->RemoveInstance(0))return 314;}
        if(USpatialTwinToolset::spatial_twin_transform_instance(First,Before,Moved))return 315; // no sync yet; stale DB index must not redirect
        FTransform Shifted=Other;Shifted.AddToTranslation(FVector(0,0,125));
        if(!USpatialTwinToolset::spatial_twin_transform_instance(Second,Other,Shifted) || !Await() || IdAt(C,0)!=Second)return 316;
        C->GetInstanceTransform(0,Actual,true);if(!Actual.Equals(Shifted,.01))return 317;
        if(!GEditor->UndoTransaction() || !Await() || !GEditor->UndoTransaction() || !Await())return 318;
        if(C->GetInstanceCount()!=2 || IdAt(C,0)!=First || IdAt(C,1)!=Second)return 319;
        C->GetInstanceTransform(0,Actual,true);if(!Actual.Equals(Before,.01))return 320;
        C->GetInstanceTransform(1,Actual,true);if(!Actual.Equals(Other,.01))return 321;
        auto R=MakeShared<FJsonObject>();R->SetStringField(TEXT("class"),C->GetClass()->GetPathName());R->SetStringField(TEXT("first_id"),First);R->SetStringField(TEXT("second_id"),Second);Results.Add(MakeShared<FJsonValueObject>(R));
    }
    if(!UEditorLoadingAndSavingUtils::SavePackages({A->GetPackage()},true) || !S->Sync())return 322;
    auto R=MakeShared<FJsonObject>();R->SetStringField(TEXT("state"),TEXT("PASS"));R->SetNumberField(TEXT("revision"),S->Database.Revision);R->SetArrayField(TEXT("components"),Results);
    R->SetBoolField(TEXT("move_rotate_scale_undo_redo_relocation_stale_pose_deleted_identity"),true);R->SetBoolField(TEXT("other_instances_unchanged"),true);
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(R),*(S->Database.Root/TEXT("native-instance-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinInstanceTest PASS revision=%lld"),S->Database.Revision);return 0;
}

static int32 RunSpatialTwinDataLayerFixture()
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 270;
    if(!FEditorFileUtils::LoadMap(TEXT("/Game/SpatialTwinPartitionFixture"),false,true))return 271;
    auto W=GEditor->GetEditorWorldContext().World();auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();
    if(!GEditor->Trans){auto Trans=NewObject<UTransBuffer>();Trans->Initialize(64*1024*1024);GEditor->Trans=Trans;}
    if(FPaths::FileExists(S->WorldRoot(W)/TEXT("world.sqlite")))return 272;
    auto Editor=GEditor->GetEditorSubsystem<UDataLayerEditorSubsystem>();if(!Editor || !S->Rebuild(W))return 273;
    auto AwaitEvents=[&]()
    {
        // Exercise the actual debounced synchronizer, not an explicit Sync that
        // could conceal an unobserved change. This is an isolated commandlet.
        FPlatformProcess::Sleep(.3f);FTSTicker::GetCoreTicker().Tick(.3f);
        return S->Database.Error.IsEmpty();
    };
    auto Container=W->GetWorldPartition()->GetActorDescContainerInstance();FGuid Guid;
    for(FActorDescInstanceList::TIterator<> It(Container);It;++It)if(It->GetActorLabel()==FName(TEXT("GenericPartitionCube"))){Guid=It->GetGuid();break;}
    FWorldPartitionReference Reference(Container,Guid);auto A=Reference.GetActor();if(!A)return 274;
    const FString ActorId=S->ActorId(A);FDataLayerCreationParameters Parameters;Parameters.bIsPrivate=true;
    auto Parent=Editor->CreateDataLayerInstance(Parameters),Child=Editor->CreateDataLayerInstance(Parameters);if(!Parent || !Child)return 275;
    const FString ParentId=S->MapId+TEXT(":data_layer:")+Parent->GetFName().ToString(),ChildId=S->MapId+TEXT(":data_layer:")+Child->GetFName().ToString();
    auto Read=[&](const FString& Id)
    {FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Id);FString Text;TSharedPtr<FJsonObject> E;if(Q.Step()==ESTSQLiteStepResult::Row){Q.GetColumnValueByIndex(0,Text);FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),E);}return E;};
    auto Edge=[&](const FString& Source,const FString& Target,const FString& Kind)
    {FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT 1 FROM relationships WHERE source=? AND target=? AND kind=?"));Q.SetBindingValueByIndex(1,Source);Q.SetBindingValueByIndex(2,Target);Q.SetBindingValueByIndex(3,Kind);return Q.Step()==ESTSQLiteStepResult::Row;};
    if(!Editor->SetDataLayerShortName(Parent,TEXT("TwinParent")) || !Editor->SetDataLayerShortName(Child,TEXT("TwinChild")) || !AwaitEvents())return 276;
    if(!Read(ParentId) || Read(ParentId)->GetStringField(TEXT("label"))!=TEXT("TwinParent") || !Read(ChildId))return 277;
    if(!Editor->SetParentDataLayer(Child,Parent) || !Editor->AddActorToDataLayer(A,Child) || !AwaitEvents())return 278;
    if(!Edge(ChildId,ParentId,TEXT("ATTACHED_TO")) || !Edge(ActorId,ChildId,TEXT("IN_DATA_LAYER")))return 279;
    Editor->SetDataLayerVisibility(Parent,false);Editor->SetDataLayerIsLoadedInEditor(Parent,false,false);
    if(!AwaitEvents() || Read(ParentId)->GetBoolField(TEXT("visible")) || Read(ParentId)->GetBoolField(TEXT("loaded_in_editor")))return 280;
    if(Read(ChildId)->GetBoolField(TEXT("effective_visible")) || Read(ChildId)->GetBoolField(TEXT("effective_loaded_in_editor")))return 291;
    if(!Read(ActorId) || !Edge(ActorId,ChildId,TEXT("IN_DATA_LAYER")))return 281;
    Editor->SetDataLayerVisibility(Parent,true);Editor->SetDataLayerIsLoadedInEditor(Parent,true,false);
    {const FScopedTransaction Transaction(NSLOCTEXT("SpatialTwin","DataLayerRenameTest","Rename fixture layer"));if(!Editor->SetDataLayerShortName(Child,TEXT("TwinRenamed")))return 282;}
    if(!AwaitEvents() || Read(ChildId)->GetStringField(TEXT("label"))!=TEXT("TwinRenamed"))return 283;
    if(!Read(ChildId)->GetBoolField(TEXT("effective_visible")) || !Read(ChildId)->GetBoolField(TEXT("effective_loaded_in_editor")))return 292;
    if(!GEditor->UndoTransaction() || Child->GetDataLayerShortName()!=TEXT("TwinChild") || !AwaitEvents() || Read(ChildId)->GetStringField(TEXT("label"))!=TEXT("TwinChild"))return 284;
    if(!GEditor->RedoTransaction() || Child->GetDataLayerShortName()!=TEXT("TwinRenamed") || !AwaitEvents() || Read(ChildId)->GetStringField(TEXT("label"))!=TEXT("TwinRenamed"))return 285;
    if(!Editor->RemoveActorFromDataLayer(A,Child) || !Editor->SetParentDataLayer(Child,nullptr) || !AwaitEvents())return 286;
    if(Edge(ActorId,ChildId,TEXT("IN_DATA_LAYER")) || Edge(ChildId,ParentId,TEXT("ATTACHED_TO")))return 287;
    if(!Editor->AddActorToDataLayer(A,Child) || !AwaitEvents())return 289;
    Editor->DeleteDataLayers({Child,Parent});if(!AwaitEvents() || Read(ChildId) || Read(ParentId))return 288;
    if(Edge(ActorId,ChildId,TEXT("IN_DATA_LAYER")) || Read(ActorId)->GetArrayField(TEXT("data_layers")).Num()!=A->GetDataLayerInstanceNames().Num())return 290;
    auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("state"),TEXT("PASS"));Result->SetNumberField(TEXT("revision"),S->Database.Revision);
    Result->SetBoolField(TEXT("create_rename_parent_membership_visibility_loading_undo_redo_delete"),true);Result->SetBoolField(TEXT("no_forced_dirty_or_rescan_after_baseline"),true);Result->SetBoolField(TEXT("game_map_modified"),false);
    Result->SetBoolField(TEXT("automatic_tick_sync_without_explicit_sync"),true);Result->SetBoolField(TEXT("inherited_editor_state_matches_native"),true);
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Result),*(S->Database.Root/TEXT("native-data-layer-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinDataLayerTest PASS revision=%lld"),S->Database.Revision);return 0;
}

static int32 RunSpatialTwinStreamingFixture()
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 240;
    if(!FEditorFileUtils::LoadMap(TEXT("/Game/SpatialTwinPartitionFixture"),false,true))return 241;
    auto W=GEditor->GetEditorWorldContext().World();auto WP=W->GetWorldPartition();if(!WP)return 242;
    auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();auto Container=WP->GetActorDescContainerInstance();FGuid Guid;
    for(FActorDescInstanceList::TIterator<> It(Container);It;++It)if(It->GetActorLabel()==FName(TEXT("GenericPartitionCube"))){Guid=It->GetGuid();break;}
    if(!Guid.IsValid())return 243;
    auto Descriptor=Container->GetActorDescInstance(Guid);
    if(Descriptor->IsLoaded())return 244; // A real unload test needs an initially unreferenced actor.
    if(!S->Resume() || !S->Sync())return 245;
    const FString Id=S->MapId+TEXT(":actor:")+Container->GetContainerID().GetActorGuid(Guid).ToString(EGuidFormats::Digits);
    auto Capture=[&]()
    {
        auto Result=MakeShared<FJsonObject>();TArray<TSharedPtr<FJsonValue>> Entities,Relations;
        FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=? OR actor_id=? ORDER BY id"));
        Q.SetBindingValueByIndex(1,Id);Q.SetBindingValueByIndex(2,Id);
        while(Q.Step()==ESTSQLiteStepResult::Row){FString Source;Q.GetColumnValueByIndex(0,Source);TSharedPtr<FJsonObject> E;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),E);Entities.Add(MakeShared<FJsonValueObject>(E));}
        FSpatialTwinSQLiteStatement R(S->Database.DB,TEXT("SELECT source,target,kind FROM relationships WHERE source=? OR source IN (SELECT id FROM entities WHERE actor_id=?) ORDER BY source,target,kind"));
        R.SetBindingValueByIndex(1,Id);R.SetBindingValueByIndex(2,Id);
        while(R.Step()==ESTSQLiteStepResult::Row){TArray<TSharedPtr<FJsonValue>> Row;for(int I=0;I<3;++I){FString Value;R.GetColumnValueByIndex(I,Value);Row.Add(MakeShared<FJsonValueString>(Value));}Relations.Add(MakeShared<FJsonValueArray>(Row));}
        Result->SetArrayField(TEXT("entities"),Entities);Result->SetArrayField(TEXT("relationships"),Relations);return Result;
    };
    auto Before=Capture();if(Before->GetArrayField(TEXT("entities")).Num()<3)return 246;
    const int64 BeforeRevision=S->Database.Revision;
    FWorldPartitionReference Reference(Container,Guid);auto A=Reference.GetActor();if(!A || !Descriptor->IsLoaded() || S->ActorId(A)!=Id)return 247;
    // Load/unload the same saved external actor using the engine's actual hard refs.
    // No synthetic delegates, forced Dirty calls, or full scans hide missing events.
    if(!S->Sync())return 248;
    auto Loaded=Capture();if(Loaded->GetArrayField(TEXT("entities")).Num()!=Before->GetArrayField(TEXT("entities")).Num())return 249;
    Reference.Reset();
    // IsLoaded also finds an unregistered UObject retained until GC. Residency
    // is proved by level membership and component registration after releasing our ref.
    if(A->GetLevel()->Actors.Contains(A) || A->GetRootComponent()->IsRegistered())return 250;
    A=nullptr;
    if(!S->Sync())return 251;
    auto Unloaded=Capture();
    if(FSpatialTwinDatabase::Json(Loaded)!=FSpatialTwinDatabase::Json(Unloaded))return 252;
    Reference=FWorldPartitionReference(Container,Guid);A=Reference.GetActor();if(!A || S->ActorId(A)!=Id)return 253;
    if(!S->Sync())return 254;
    auto Reloaded=Capture();if(FSpatialTwinDatabase::Json(Loaded)!=FSpatialTwinDatabase::Json(Reloaded))return 255;
    const auto InitialPose=A->GetActorTransform();A->Modify();A->SetActorLocation(InitialPose.GetLocation()+FVector(125,0,0));A->PostEditMove(true);
    if(!S->Sync())return 256;
    auto Moved=Capture();bool FoundMove=false;
    for(auto Value:Moved->GetArrayField(TEXT("entities")))if(Value->AsObject()->GetStringField(TEXT("id"))==Id)
        FoundMove=Value->AsObject()->GetObjectField(TEXT("transform"))->GetArrayField(TEXT("position"))[0]->AsNumber()==A->GetActorLocation().X;
    if(!FoundMove)return 257;
    A->SetActorTransform(InitialPose);A->PostEditMove(true);
    if(!UEditorLoadingAndSavingUtils::SavePackages({A->GetPackage()},true) || !S->Sync())return 258;
    auto Restored=Capture();Reference.Reset();
    if(A->GetLevel()->Actors.Contains(A) || A->GetRootComponent()->IsRegistered())return 259;
    A=nullptr;
    if(!S->Sync() || FSpatialTwinDatabase::Json(Restored)!=FSpatialTwinDatabase::Json(Capture()))return 260;
    auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("state"),TEXT("PASS"));Result->SetStringField(TEXT("actor_id"),Id);
    Result->SetNumberField(TEXT("initial_revision"),BeforeRevision);Result->SetNumberField(TEXT("final_revision"),S->Database.Revision);
    Result->SetNumberField(TEXT("entities"),Before->GetArrayField(TEXT("entities")).Num());Result->SetNumberField(TEXT("relationships"),Restored->GetArrayField(TEXT("relationships")).Num());
    Result->SetBoolField(TEXT("native_unload_reload_retains_identity_geometry_graph"),true);Result->SetBoolField(TEXT("move_event_without_forced_dirty"),true);Result->SetBoolField(TEXT("full_scan_requested"),false);
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Result),*(S->Database.Root/TEXT("native-streaming-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinStreamingTest PASS revision=%lld"),S->Database.Revision);return 0;
}

static int32 RunSpatialTwinResumeFixture()
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 50;
    auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();if(S->Database.DB.IsValid())return 51;
    if(!FEditorFileUtils::LoadMap(TEXT("/Game/SpatialTwinPartitionFixture"),false,true))return 52;
    auto W=GEditor->GetEditorWorldContext().World();auto WP=W->GetWorldPartition();if(!WP)return 53;
    auto Container=WP->GetActorDescContainerInstance();FGuid Guid;
    for(FActorDescInstanceList::TIterator<> It(Container);It;++It)if(It->GetActorLabel()==FName(TEXT("GenericPartitionCube"))){Guid=It->GetGuid();break;}
    if(!Guid.IsValid())return 54;FWorldPartitionReference Reference(Container,Guid);auto A=Reference.GetActor();if(!A)return 55;
    const FString Root=S->WorldRoot(W);FString ExpectedInstance;
    {FSpatialTwinSQLiteDatabase Previous;if(!Previous.Open(*(Root/TEXT("world.sqlite")),ESTSQLiteOpenMode::ReadOnly))return 60;
     FSpatialTwinSQLiteStatement Q(Previous,TEXT("SELECT id FROM entities WHERE kind='Instance' AND json_extract(source,'$.instance_index')=1"));if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,ExpectedInstance))return 61;}
    auto Instances=A->FindComponentByClass<UInstancedStaticMeshComponent>();if(!Instances || Instances->GetInstanceCount()!=2)return 62;
    Instances->Modify();Instances->RemoveInstance(0);
    const FVector Expected=A->GetActorLocation()+FVector(125,0,0);A->Modify();A->SetActorLocation(Expected);A->PostEditMove(true);
    if(!UEditorLoadingAndSavingUtils::SavePackages({A->GetPackage()},true))return 56;
    // A separate process changed the saved package before the Twin resumes.
    // Resume must reconcile its owner from source inventory, never full-scan.
    if(!S->Resume() || !S->Sync() || !S->RefreshPartition())return 57;
    FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT json_extract(source,'$.transform.position[0]') FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,S->ActorId(A));double X=0;
    if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,X) || X!=Expected.X)return 58;
    {FSpatialTwinSQLiteStatement I(S->Database.DB,TEXT("SELECT id FROM entities WHERE kind='Instance'"));FString Actual;if(I.Step()!=ESTSQLiteStepResult::Row || !I.GetColumnValueByIndex(0,Actual) || Actual!=ExpectedInstance || I.Step()!=ESTSQLiteStepResult::Done)return 63;}
    FString Metrics;if(!FFileHelper::LoadFileToString(Metrics,*(S->Database.Root/TEXT("last_reconcile_metrics.json"))))return 59;
    TSharedPtr<FJsonObject> Reconciled;
    if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Metrics),Reconciled) || Reconciled->GetIntegerField(TEXT("packages"))!=1)return 94;
    auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("state"),TEXT("PASS"));Result->SetStringField(TEXT("actor_id"),S->ActorId(A));Result->SetNumberField(TEXT("native_and_canonical_x"),X);Result->SetNumberField(TEXT("revision"),S->Database.Revision);Result->SetStringField(TEXT("reconciliation"),Metrics);
    Result->SetBoolField(TEXT("cold_instance_removal_preserves_survivor_identity"),true);
    Result->SetBoolField(TEXT("unchanged_tracked_engine_package_not_reconciled"),true);
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Result),*(S->Database.Root/TEXT("native-resume-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);return 0;
}

static int32 RunSpatialTwinFixture()
{
    // Only the isolated fixture project may run this destructive map setup.
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 2;
    if(!GEditor || !FEditorFileUtils::LoadMap(TEXT("/Engine/Maps/Entry"),true,false))return 3;
    auto W=GEditor->GetEditorWorldContext().World();
    if(!GEditor->Trans){auto Trans=NewObject<UTransBuffer>();Trans->Initialize(64*1024*1024);GEditor->Trans=Trans;}
    auto Mesh=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));if(!Mesh)return 4;
    auto A=W->SpawnActor<AStaticMeshActor>();A->SetActorLabel(TEXT("TwinTestCube"));A->GetStaticMeshComponent()->SetStaticMesh(Mesh);
    A->SetActorLocation(FVector(100000000000.125,200,300));
    auto B=W->SpawnActor<AStaticMeshActor>();B->SetActorLabel(TEXT("TwinTestNeighbour"));B->GetStaticMeshComponent()->SetStaticMesh(Mesh);B->SetActorLocation(FVector(100000000300.125,200,300));
    auto Instances=NewObject<UInstancedStaticMeshComponent>(B,TEXT("TwinTestInstances"));B->AddInstanceComponent(Instances);Instances->SetStaticMesh(Mesh);Instances->RegisterComponent();
    Instances->AddInstance(FTransform(FVector(0,500,0)));Instances->AddInstance(FTransform(FVector(0,1000,0)));
    auto EmptyBrush=W->SpawnActor<ABrush>();EmptyBrush->SetActorLabel(TEXT("TwinEmptyBrush"));
    if(!EmptyBrush->GetBrushComponent() || EmptyBrush->GetBrushComponent()->GetBodySetup())return 360;
    auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();if(!S || !S->Rebuild(W)){UE_LOG(LogTemp,Error,TEXT("SpatialTwinTest scan failure"));return 5;}
    const FString CubeAsset=TEXT("asset:")+Mesh->GetPathName(),CubePackage=TEXT("asset_package:")+Mesh->GetPackage()->GetName();
    auto EngineDependenciesMatch=[&]()
    {
        FSpatialTwinSQLiteStatement Link(S->Database.DB,TEXT("SELECT 1 FROM relationships WHERE source=? AND target=? AND kind='IN_PACKAGE'"));Link.SetBindingValueByIndex(1,CubeAsset);Link.SetBindingValueByIndex(2,CubePackage);if(Link.Step()!=ESTSQLiteStepResult::Row)return false;Link.Destroy();
        TArray<FName> Dependencies;auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();const bool Known=Registry.GetDependencies(Mesh->GetPackage()->GetFName(),Dependencies);
        TSet<FString> Expected,Actual;for(auto Name:Dependencies)Expected.Add(TEXT("asset_package:")+Name.ToString());
        FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT target FROM relationships WHERE source=? AND kind='DEPENDS_ON'"));Q.SetBindingValueByIndex(1,CubePackage);while(Q.Step()==ESTSQLiteStepResult::Row){FString Id;Q.GetColumnValueByIndex(0,Id);Actual.Add(Id);}Q.Destroy();
        FSpatialTwinSQLiteStatement State(S->Database.DB,TEXT("SELECT json_extract(source,'$.dependencies_state') FROM entities WHERE id=?"));State.SetBindingValueByIndex(1,CubePackage);FString Coverage;if(State.Step()!=ESTSQLiteStepResult::Row || !State.GetColumnValueByIndex(0,Coverage))return false;
        for(const auto& Dependency:Expected)if(!Actual.Contains(Dependency))return false;
        return Expected.Num()==Actual.Num() && Coverage==(Known?TEXT("CURRENT"):TEXT("UNKNOWN"));
    };
    if(!EngineDependenciesMatch())
    {
        TArray<FName> Expected;const bool Known=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get().GetDependencies(Mesh->GetPackage()->GetFName(),Expected);
        UE_LOG(LogTemp,Error,TEXT("Engine dependency fixture mismatch package=%s native_known=%d native_count=%d"),*Mesh->GetPackage()->GetName(),Known,Expected.Num());
        for(const auto& Name:Expected)UE_LOG(LogTemp,Error,TEXT("Expected dependency %s"),*Name.ToString());
        FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,CubePackage);
        if(Q.Step()==ESTSQLiteStepResult::Row){FString Source;Q.GetColumnValueByIndex(0,Source);UE_LOG(LogTemp,Error,TEXT("Package source %s"),*Source);}Q.Destroy();
        FSpatialTwinSQLiteStatement Links(S->Database.DB,TEXT("SELECT target,kind FROM relationships WHERE source=? OR source=?"));Links.SetBindingValueByIndex(1,CubeAsset);Links.SetBindingValueByIndex(2,CubePackage);
        while(Links.Step()==ESTSQLiteStepResult::Row){FString Target,Kind;Links.GetColumnValueByIndex(0,Target);Links.GetColumnValueByIndex(1,Kind);UE_LOG(LogTemp,Error,TEXT("Actual relationship %s %s"),*Kind,*Target);}
        return 400;
    }
    {FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT json_extract(source,'$.collision.simple_coverage'),json_extract(source,'$.collision.complex_coverage') FROM entities WHERE kind='Component' AND actor_id=?"));Q.SetBindingValueByIndex(1,S->ActorId(EmptyBrush));FString Simple,Complex;
     if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,Simple) || !Q.GetColumnValueByIndex(1,Complex) || Simple!=TEXT("EMPTY") || Complex!=TEXT("EMPTY"))return 361;}
    if(FPaths::IsRelative(S->Database.ReadMetadata(TEXT("native_library"))))return 362;
    {FString Schema;FSpatialTwinDatabase Other;if(Other.Open(S->Database.Root,Schema)){UE_LOG(LogTemp,Error,TEXT("Canonical writer lease not enforced"));return 10;}}
    int64 First=S->Database.Revision;
    auto BackgroundTick=[&](){int32 Bound=0;bool Requested=false;for(const auto& D:GEditor->ShouldDisableCPUThrottlingDelegates)if(D.IsBoundToObject(S)){++Bound;Requested|=D.Execute();}return Bound==1 && Requested;};
    if(BackgroundTick())return 363;
    S->Dirty(A);if(!BackgroundTick())return 364;
    if(!S->Sync() || S->Database.Revision!=First)return 16;
    if(BackgroundTick())return 365;
    S->Dirty(A);S->Database.Error=TEXT("Fixture synchronization failure awaiting explicit recovery");
    FPlatformProcess::Sleep(.3f);
    FTSTicker::GetCoreTicker().Tick(.3f);
    if(S->Database.Error!=TEXT("Fixture synchronization failure awaiting explicit recovery") || S->Database.Revision!=First || BackgroundTick())return 456;
    if(!S->Sync() || !S->Database.Error.IsEmpty() || S->Database.Revision!=First)return 457;
    if(S->Database.RelationBaseline.Num()>32)return 22;
    const FString Id=S->ActorId(A);
    auto Read=[&](FString& Source){FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Id);return Q.Step()==ESTSQLiteStepResult::Row && Q.GetColumnValueByIndex(0,Source);};
    FString Before,After;if(!Read(Before))return 6;
    // The synchronous fixture creates, scans and modifies in one frame.
    // Reopen the notification window only for its two owned objects; the
    // production synchronizer still receives real Modify/Transacted events.
    FCoreUObjectDelegates::ObjectsModifiedThisFrame.Remove(A);FCoreUObjectDelegates::ObjectsModifiedThisFrame.Remove(A->GetRootComponent());
    A->SetFlags(RF_Transactional);A->GetRootComponent()->SetFlags(RF_Transactional);
    {FScopedTransaction Transaction(FText::FromString(TEXT("Spatial Twin fixture move")));A->Modify();A->GetRootComponent()->Modify();A->SetActorLocation(FVector(100000000100.125,200,300));}
    if(!S->Sync() || !Read(After) || Before==After || S->Database.Revision!=First+1){UE_LOG(LogTemp,Error,TEXT("Fixture event sync failure: %s"),*S->Status());return 7;}
    FSpatialTwinSQLiteStatement Change(S->Database.DB,TEXT("SELECT type FROM changes WHERE revision=? AND entity_id=?"));Change.SetBindingValueByIndex(1,S->Database.Revision);Change.SetBindingValueByIndex(2,Id);FString Type;
    if(Change.Step()!=ESTSQLiteStepResult::Row || !Change.GetColumnValueByIndex(0,Type) || Type!=TEXT("TRANSFORM"))return 8;
    Change.Destroy();
    if(!GEditor->UndoTransaction() || !S->Sync() || !Read(After) || Before!=After)return 9;
    if(!GEditor->RedoTransaction() || !S->Sync() || !Read(After) || Before==After)return 11;
    if(!GEditor->UndoTransaction() || !S->Sync() || !Read(After) || Before!=After)return 12;
    auto InstanceSources=[&](){TMap<FString,FString> Values;FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT id,source FROM entities WHERE kind='Instance'"));while(Q.Step()==ESTSQLiteStepResult::Row){FString I,V;Q.GetColumnValueByIndex(0,I);Q.GetColumnValueByIndex(1,V);Values.Add(I,V);}return Values;};
    auto InitialInstances=InstanceSources();if(InitialInstances.Num()!=2)return 17;
    FCoreUObjectDelegates::ObjectsModifiedThisFrame.Remove(Instances);Instances->SetFlags(RF_Transactional);
    {FScopedTransaction Transaction(FText::FromString(TEXT("Spatial Twin remove instance")));Instances->Modify();Instances->RemoveInstance(0);}
    if(!S->Sync() || InstanceSources().Num()!=1)return 18;
    if(!GEditor->UndoTransaction() || !S->Sync() || !InstanceSources().OrderIndependentCompareEqual(InitialInstances))return 19;
    if(!GEditor->RedoTransaction() || !S->Sync() || InstanceSources().Num()!=1)return 20;
    if(!GEditor->UndoTransaction() || !S->Sync() || !InstanceSources().OrderIndependentCompareEqual(InitialInstances))return 21;
    // Asset Registry lifecycle does not depend on an Actor using this asset.
    auto& Registry=FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get();
    auto Package=CreatePackage(TEXT("/Game/SpatialTwinTests/RegistryLifecycle"));
    auto Material=NewObject<UMaterial>(Package,TEXT("First"),RF_Public|RF_Standalone);
    const FString OldPath=Material->GetPathName();FAssetRegistryModule::AssetCreated(Material);
    auto Exists=[&](const FString& EntityId){FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT 1 FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,EntityId);return Q.Step()==ESTSQLiteStepResult::Row;};
    if(!S->Sync() || !Exists(TEXT("asset:")+OldPath))return 29;
    // Async registry rediscovery of a saved unchanged package must not dirty
    // all users; an actual unsaved package update must still enter the queue.
    Package->SetDirtyFlag(true);
    if(!UEditorLoadingAndSavingUtils::SavePackages({Package},true) || !S->Sync())return 91;
    auto Pending=[&](){TSharedPtr<FJsonObject> J;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(S->Status()),J);return J->GetIntegerField(TEXT("pending"));};
    const int32 BeforeDiscovery=Pending();Registry.OnAssetUpdated().Broadcast(FAssetData(Material));
    if(Pending()!=BeforeDiscovery)return 92;
    Package->SetDirtyFlag(true);Registry.OnAssetUpdated().Broadcast(FAssetData(Material));
    if(Pending()<=BeforeDiscovery || !S->Sync())return 93;
    Package->SetDirtyFlag(false);
    if(!Material->Rename(TEXT("Second"),Package,REN_DontCreateRedirectors))return 30;
    FAssetRegistryModule::AssetRenamed(Material,OldPath);
    if(!S->Sync() || Exists(TEXT("asset:")+OldPath) || !Exists(TEXT("asset:")+Material->GetPathName()))return 31;
    const FString NewPath=Material->GetPathName();FAssetRegistryModule::AssetDeleted(Material);
    TArray<FAssetData> RetainedForUndo;Registry.GetAssetsByPackageName(Package->GetFName(),RetainedForUndo);
    if(!RetainedForUndo.ContainsByPredicate([&](const FAssetData& Entry){return Entry.GetSoftObjectPath().ToString()==NewPath;}))return 214;
    if(!S->Sync() || Exists(TEXT("asset:")+NewPath))return 32;
    if(Exists(TEXT("asset_package:")+Package->GetName()))return 208;
    Material->ClearFlags(RF_Public|RF_Standalone);Material->MarkAsGarbage();
    // Legacy package-node repair must be scoped, transactional and idempotent.
    const FString Ghost=TEXT("asset_package:/Game/__ExternalActors__/SpatialTwinGone"),Kept=TEXT("asset_package:/Game/__ExternalActors__/SpatialTwinReferenced");
    if(!S->Database.Begin(false,S->MapId))return 209;
    for(const auto& Key:{Ghost,Kept}){auto E=MakeShared<FJsonObject>();E->SetStringField(TEXT("id"),Key);E->SetStringField(TEXT("path"),Key.RightChop(14));E->SetStringField(TEXT("kind"),TEXT("Asset"));E->SetStringField(TEXT("asset_type"),TEXT("Package"));if(!S->Database.Put(E))return 210;}
    {FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("DELETE FROM relationships WHERE source=? AND kind='IN_PACKAGE'"));Q.SetBindingValueByIndex(1,CubeAsset);if(Q.Step()!=ESTSQLiteStepResult::Done)return 401;}
    if(!S->Database.Relation(Ghost,TEXT("asset_package:/Engine/BasicShapes/Cube"),TEXT("DEPENDS_ON")) || !S->Database.Relation(Id,Kept,TEXT("DEPENDS_ON")) || !S->Database.Metadata(TEXT("asset_package_lifecycle_version"),TEXT("0")) || !S->Database.Metadata(TEXT("referenced_asset_dependencies_version"),TEXT("0")) || !S->Database.Metadata(TEXT("asset_dependency_scope_version"),TEXT("0")) || !S->Database.Commit(S->MapId,false))return 211;
    if(!S->UpgradeAssetPackages() || Exists(Ghost) || !Exists(Kept))return 212;
    if(!EngineDependenciesMatch())return 402;
    const int64 UpgradeRevision=S->Database.Revision;
    if(!S->UpgradeAssetPackages() || S->Database.Revision!=UpgradeRevision)return 213;
    const FString Mount=FPaths::ProjectSavedDir()/TEXT("SpatialTwinMountedFixture/");FPackageName::RegisterMountPoint(TEXT("/TwinFixtureContent/"),Mount);
    auto Mounted=NewObject<UMaterial>(CreatePackage(TEXT("/TwinFixtureContent/FixtureAsset")),TEXT("FixtureAsset"),RF_Public|RF_Standalone);
    FAssetRegistryModule::AssetCreated(Mounted);if(!S->Sync() || !Exists(TEXT("asset:")+Mounted->GetPathName()))return 64;
    const FString MountedPath=Mounted->GetPathName();FAssetRegistryModule::AssetDeleted(Mounted);Mounted->ClearFlags(RF_Public|RF_Standalone);Mounted->MarkAsGarbage();
    if(!S->Sync() || Exists(TEXT("asset:")+MountedPath))return 65;FPackageName::UnRegisterMountPoint(TEXT("/TwinFixtureContent/"),Mount);
    // Component lifecycle comes through native Modify, not a direct Twin Dirty call.
    FCoreUObjectDelegates::ObjectsModifiedThisFrame.Remove(A);A->Modify();
    auto Box=NewObject<UBoxComponent>(A,TEXT("RuntimeAddedBox"));A->AddInstanceComponent(Box);Box->SetupAttachment(A->GetRootComponent());Box->SetBoxExtent(FVector(120));Box->RegisterComponent();
    if(!S->Sync())return 33;
    FString BoxId;{FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT id FROM entities WHERE label='RuntimeAddedBox' AND kind='Component'"));if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,BoxId))return 34;}
    FCoreUObjectDelegates::ObjectsModifiedThisFrame.Remove(A);A->Modify();A->RemoveInstanceComponent(Box);Box->DestroyComponent();
    if(!S->Sync() || Exists(BoxId))return 35;
    // SQLITE_FULL may roll back automatically. No extractor statement may
    // silently restart writes in autocommit and corrupt a prior READY world.
    if(!S->Database.Begin(false,S->MapId))return 13;
    S->Database.DB.Execute(TEXT("ROLLBACK"));
    auto Invalid=MakeShared<FJsonObject>();Invalid->SetStringField(TEXT("id"),TEXT("must-not-persist"));Invalid->SetStringField(TEXT("kind"),TEXT("Actor"));
    if(S->Database.Put(Invalid))return 14;
    S->Database.Rollback();if(!Read(After) || Before!=After)return 15;
    S->Database.Error.Reset();
    // A high fan-out parent must not be recopied for one added edge.
    if(!S->Database.Begin(false,S->MapId))return 23;
    for(int32 I=0;I<1024;++I)if(!S->Database.Relation(TEXT("fixture:parent"),LexToString(I),TEXT("CONTAINS")))return 24;
    if(!S->Database.Commit(S->MapId,false) || !S->Database.Begin(false,S->MapId))return 25;
    if(!S->Database.Relation(TEXT("fixture:parent"),TEXT("new"),TEXT("CONTAINS")) || S->Database.RelationBaseline.Num()!=1 || !S->Database.Commit(S->MapId,false))return 26;
    const int64 RelationRevision=S->Database.Revision;
    if(!S->Database.Begin(false,S->MapId))return 27;
    S->Database.TrackRelation(TEXT("fixture:parent"),TEXT("new"),TEXT("CONTAINS"));
    if(!S->Database.Exec(TEXT("DELETE FROM relationships WHERE source='fixture:parent' AND target='new'")) || !S->Database.Relation(TEXT("fixture:parent"),TEXT("new"),TEXT("CONTAINS")) || !S->Database.Commit(S->MapId,false) || S->Database.Revision!=RelationRevision)return 28;
    auto Evidence=MakeShared<FJsonObject>();Evidence->SetStringField(TEXT("state"),TEXT("PASS"));Evidence->SetStringField(TEXT("actor_id"),Id);Evidence->SetNumberField(TEXT("first_revision"),First);Evidence->SetNumberField(TEXT("final_revision"),S->Database.Revision);
    {
        auto N=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);
        if(!N){FNavigationSystem::AddNavigationSystemToWorld(*W,FNavigationSystemRunMode::EditorMode);N=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);}
        // Entry has no navigation bounds. Register a disposable native bounds
        // volume and use public world initialization to create nav data/octree.
        if(!N)return 195;auto NavBounds=W->SpawnActor<ANavMeshBoundsVolume>();
        N->OnWorldInitDone(FNavigationSystemRunMode::EditorMode);if(!N->GetNavOctree())return 196;
        auto Owner=W->SpawnActor<AActor>();auto Scene=NewObject<USceneComponent>(Owner);Owner->SetRootComponent(Scene);Owner->AddInstanceComponent(Scene);Scene->RegisterComponent();Owner->SetActorLocation(FVector(5000,0,0));
        auto Modifier=NewObject<UNavModifierComponent>(Owner);Owner->AddInstanceComponent(Modifier);Modifier->FailsafeExtent=FVector(100,150,200);Modifier->SetAreaClass(UNavArea_Null::StaticClass());Modifier->RegisterComponent();
        auto Link=W->SpawnActor<ANavLinkProxy>();Link->SetActorLocation(FVector(7000,0,0));
        auto& PointLink=Link->PointLinks[0];PointLink.Left=FVector(100,0,0);PointLink.Right=FVector(-200,0,0);PointLink.Direction=ENavLinkDirection::RightToLeft;PointLink.MaxFallDownLength=0;PointLink.LeftProjectHeight=0;PointLink.SnapRadius=34;
        N->UpdateActorAndComponentsInNavOctree(*Owner);N->UpdateActorAndComponentsInNavOctree(*Link);N->ProcessPendingOctreeUpdates();
        S->Dirty(Owner);S->Dirty(Link);if(!S->Sync())return 96;
        auto Input=[&](AActor* Actor)->TSharedPtr<FJsonObject>
        {
            FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE actor_id=? AND kind='NavigationInput'"));Q.SetBindingValueByIndex(1,S->ActorId(Actor));FString Text;TSharedPtr<FJsonObject> Result;
            if(Q.Step()==ESTSQLiteStepResult::Row && Q.GetColumnValueByIndex(0,Text))FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Result);return Result;
        };
        auto Area=Input(Owner),Links=Input(Link);
        if(!Area || Area->GetArrayField(TEXT("navigation_modifiers")).IsEmpty() || !Links || !Links->GetBoolField(TEXT("navigation_has_links")))return 97;
        const auto& Points=Links->GetArrayField(TEXT("navigation_links"));if(Points.Num()!=1 || Links->GetStringField(TEXT("navigation_link_coverage"))!=TEXT("point_links"))return 197;
        auto Point=Points[0]->AsObject();
        if(Point->GetArrayField(TEXT("start"))[0]->AsNumber()!=6800 || Point->GetArrayField(TEXT("end"))[0]->AsNumber()!=7100 || Point->GetBoolField(TEXT("requires_projection")) || !Point->GetBoolField(TEXT("reversed")) || Point->GetBoolField(TEXT("bidirectional")) || Point->GetNumberField(TEXT("radius"))!=34 || Point->GetStringField(TEXT("user_id"))!=TEXT("0"))return 198;
        Evidence->SetObjectField(TEXT("native_point_link_source"),Point);
        Link->SetActorLocation(FVector(7050,0,0));Link->PostEditMove(true);N->UpdateActorAndComponentsInNavOctree(*Link);N->ProcessPendingOctreeUpdates();S->Dirty(Link);if(!S->Sync())return 199;
        Links=Input(Link);if(!Links || Links->GetArrayField(TEXT("navigation_links"))[0]->AsObject()->GetArrayField(TEXT("start"))[0]->AsNumber()!=6850)return 200;
        Link->PointLinks[0].SnapRadius=56;N->UpdateActorAndComponentsInNavOctree(*Link);N->ProcessPendingOctreeUpdates();S->Dirty(Link);if(!S->Sync())return 201;
        Links=Input(Link);if(!Links || Links->GetArrayField(TEXT("navigation_links"))[0]->AsObject()->GetNumberField(TEXT("radius"))!=56)return 202;
        Evidence->SetBoolField(TEXT("native_point_link_endpoints_flags_move_property"),true);
        const FString InputId=Area->GetStringField(TEXT("id"));const double PreviousNavMin=Area->GetArrayField(TEXT("bounds"))[0]->AsArray()[0]->AsNumber();
        Owner->SetActorLocation(Owner->GetActorLocation()+FVector(300,0,0));Owner->PostEditMove(true);N->UpdateActorAndComponentsInNavOctree(*Owner);N->ProcessPendingOctreeUpdates();S->Dirty(Owner);if(!S->Sync())return 98;
        Area=Input(Owner);if(!Area || Area->GetStringField(TEXT("id"))!=InputId || FMath::Abs(Area->GetArrayField(TEXT("bounds"))[0]->AsArray()[0]->AsNumber()-PreviousNavMin-300)>.01)return 99;
        Modifier->DestroyComponent();N->ProcessPendingOctreeUpdates();S->Dirty(Owner);if(!S->Sync() || Input(Owner))return 100;
        const FString OwnerId=S->ActorId(Owner),LinkId=S->ActorId(Link);W->DestroyActor(Owner);W->DestroyActor(Link);W->DestroyActor(NavBounds);if(!S->Sync())return 101;
        FSpatialTwinSQLiteStatement Remaining(S->Database.DB,TEXT("SELECT 1 FROM entities WHERE actor_id IN (?,?)"));Remaining.SetBindingValueByIndex(1,OwnerId);Remaining.SetBindingValueByIndex(2,LinkId);if(Remaining.Step()!=ESTSQLiteStepResult::Done)return 102;
        Evidence->SetBoolField(TEXT("nonprimitive_navigation_owner_identity_move_remove_and_actor_links"),true);
    }
    Evidence->SetArrayField(TEXT("checks"),{MakeShared<FJsonValueString>(TEXT("full scan native actors/components/ISM")),MakeShared<FJsonValueString>(TEXT("canonical writer lease")),MakeShared<FJsonValueString>(TEXT("native event-driven incremental transform")),MakeShared<FJsonValueString>(TEXT("native undo/redo restores canonical source")),MakeShared<FJsonValueString>(TEXT("ISM remove/undo/redo persistent identities")),MakeShared<FJsonValueString>(TEXT("no-op synchronization revision")),MakeShared<FJsonValueString>(TEXT("implicit rollback blocks canonical autocommit writes"))});
    Evidence->SetBoolField(TEXT("incremental_edge_tracking_high_fanout_noop"),true);
    Evidence->SetBoolField(TEXT("native_asset_registry_add_rename_remove"),true);
    Evidence->SetBoolField(TEXT("native_mounted_content_registry_events"),true);
    Evidence->SetBoolField(TEXT("native_component_add_remove"),true);
    {
        // Edit an owned mesh through native asset events. Both users must see
        // the new material, and the old material must lose this mesh user.
        auto Owned=DuplicateObject<UStaticMesh>(Mesh,CreatePackage(TEXT("/Game/SpatialTwinTests/MaterialReplacement")),TEXT("MaterialReplacement"));
        auto BeforeMaterial=NewObject<UMaterial>(CreatePackage(TEXT("/Game/SpatialTwinTests/MaterialBefore")),TEXT("MaterialBefore"),RF_Public|RF_Standalone);
        auto AfterMaterial=NewObject<UMaterial>(CreatePackage(TEXT("/Game/SpatialTwinTests/MaterialAfter")),TEXT("MaterialAfter"),RF_Public|RF_Standalone);
        Owned->SetMaterial(0,BeforeMaterial);FAssetRegistryModule::AssetCreated(Owned);FAssetRegistryModule::AssetCreated(BeforeMaterial);FAssetRegistryModule::AssetCreated(AfterMaterial);
        A->GetStaticMeshComponent()->SetStaticMesh(Owned);B->GetStaticMeshComponent()->SetStaticMesh(Owned);S->Dirty(A);S->Dirty(B);
        const FString MeshId=TEXT("asset:")+Owned->GetPathName(),FirstId=TEXT("asset:")+BeforeMaterial->GetPathName(),SecondId=TEXT("asset:")+AfterMaterial->GetPathName();
        auto Uses=[&](const FString& Target){FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT 1 FROM relationships WHERE source=? AND target=? AND kind='USES_ASSET'"));Q.SetBindingValueByIndex(1,MeshId);Q.SetBindingValueByIndex(2,Target);return Q.Step()==ESTSQLiteStepResult::Row;};
        if(!S->Sync() || !Uses(FirstId) || Uses(SecondId))return 403;
        auto SaveOwned=[](UObject* Asset){return UEditorLoadingAndSavingUtils::SavePackages({Asset->GetPackage()},false);};
        if(!SaveOwned(BeforeMaterial) || !SaveOwned(AfterMaterial) || !SaveOwned(Owned) || !S->Sync())return 410;
        const FString PackageId=TEXT("asset_package:")+Owned->GetPackage()->GetName();
        auto ScopeMatches=[&](bool Dirty,const FString& Live)
        {
            FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT json_extract(source,'$.package_dirty'),json_extract(source,'$.live_dependencies_state'),json_extract(source,'$.dependencies_state'),json_extract(source,'$.dependency_source') FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,PackageId);
            int32 Value;FString ActualLive,Disk,Source;
            return Q.Step()==ESTSQLiteStepResult::Row && Q.GetColumnValueByIndex(0,Value) && Q.GetColumnValueByIndex(1,ActualLive) && Q.GetColumnValueByIndex(2,Disk) && Q.GetColumnValueByIndex(3,Source)
                && bool(Value)==Dirty && ActualLive==Live && Disk==TEXT("CURRENT") && Source==TEXT("asset_registry_disk");
        };
        auto Depends=[&](const FString& Package){FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT 1 FROM relationships WHERE source=? AND target=? AND kind='DEPENDS_ON'"));Q.SetBindingValueByIndex(1,PackageId);Q.SetBindingValueByIndex(2,TEXT("asset_package:")+Package);return Q.Step()==ESTSQLiteStepResult::Row;};
        if(!ScopeMatches(false,TEXT("CURRENT")) || !Depends(BeforeMaterial->GetPackage()->GetName()) || Depends(AfterMaterial->GetPackage()->GetName()))return 411;
        // Package dirty changes alone must invalidate the live-dependency claim.
        const int64 BeforeDirty=S->Database.Revision;Owned->GetPackage()->SetDirtyFlag(true);
        if(Pending()==0 || !S->Sync() || S->Database.Revision<=BeforeDirty || !ScopeMatches(true,TEXT("UNKNOWN_UNSAVED")))return 412;
        const int64 DirtyRevision=S->Database.Revision;
        if(!S->Sync() || S->Database.Revision!=DirtyRevision)return 413;

        Owned->Modify();Owned->SetMaterial(0,AfterMaterial);Registry.OnAssetUpdated().Broadcast(FAssetData(Owned));
        if(!S->Sync() || Uses(FirstId) || !Uses(SecondId))return 404;
        if(!ScopeMatches(true,TEXT("UNKNOWN_UNSAVED")) || !Depends(BeforeMaterial->GetPackage()->GetName()) || Depends(AfterMaterial->GetPackage()->GetName()))return 414;
        if(!SaveOwned(Owned) || !S->Sync() || !ScopeMatches(false,TEXT("CURRENT")) || Depends(BeforeMaterial->GetPackage()->GetName()) || !Depends(AfterMaterial->GetPackage()->GetName()))return 415;
        Evidence->SetBoolField(TEXT("native_saved_registry_vs_unsaved_live_dependency_scope_and_dirty_event"),true);

        const int64 MaterialRevision=S->Database.Revision;S->Dirty(A);S->Dirty(B);
        if(!S->Sync() || S->Database.Revision!=MaterialRevision)return 405;
        // A material rename must refresh a cached mesh's references without
        // exporting its unchanged geometry, even when the mesh is not dirty.
        auto MeshSource=[&](){FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,MeshId);FString Text;TSharedPtr<FJsonObject> Result;if(Q.Step()==ESTSQLiteStepResult::Row && Q.GetColumnValueByIndex(0,Text))FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Result);return Result;};
        auto BeforeRename=MeshSource();if(!BeforeRename.IsValid())return 416;
        const FString OldMaterialPath=AfterMaterial->GetPathName();
        if(!AfterMaterial->Rename(TEXT("MaterialAfterRenamed"),nullptr,REN_DontCreateRedirectors))return 417;
        FAssetRegistryModule::AssetRenamed(AfterMaterial,OldMaterialPath);
        const FString RenamedId=TEXT("asset:")+AfterMaterial->GetPathName();
        if(!S->Sync())return 418;
        auto AfterRename=MeshSource();
        if(!AfterRename.IsValid() || Uses(SecondId) || !Uses(RenamedId) || AfterRename->GetArrayField(TEXT("materials")).Num()!=1 || AfterRename->GetArrayField(TEXT("materials"))[0]->AsString()!=RenamedId)
        {UE_LOG(LogTemp,Error,TEXT("Cached mesh material rename left stale source/usage references"));return 419;}
        for(const TCHAR* Field:{TEXT("geometry_hash"),TEXT("navigation_geometry_hash")})if(BeforeRename->GetStringField(Field)!=AfterRename->GetStringField(Field))return 420;
        const int64 RenameRevision=S->Database.Revision;S->Dirty(A);S->Dirty(B);if(!S->Sync() || S->Database.Revision!=RenameRevision)return 421;
        const FString RenamedPath=AfterMaterial->GetPathName();if(!AfterMaterial->Rename(TEXT("MaterialAfter"),nullptr,REN_DontCreateRedirectors))return 422;
        FAssetRegistryModule::AssetRenamed(AfterMaterial,RenamedPath);if(!S->Sync() || Uses(RenamedId) || !Uses(SecondId))return 423;
        Evidence->SetBoolField(TEXT("native_cached_mesh_material_rename_refresh_preserves_geometry_noop_and_restore"),true);
        A->GetStaticMeshComponent()->SetStaticMesh(Mesh);B->GetStaticMeshComponent()->SetStaticMesh(Mesh);S->Dirty(A);S->Dirty(B);if(!S->Sync())return 406;
        Evidence->SetBoolField(TEXT("native_mesh_material_replacement_removes_stale_usage_and_noop_revision"),true);
    }
    Evidence->SetBoolField(TEXT("native_referenced_engine_dependencies_and_legacy_repair"),true);
    {
        auto CollisionMesh=DuplicateObject<UStaticMesh>(Mesh,GetTransientPackage(),TEXT("CollisionBeyondRender"));
        auto Body=CollisionMesh->GetBodySetup();FKSphereElem Sphere;Sphere.Center=FVector(500,0,0);Sphere.Radius=30;Body->AggGeom.SphereElems.Add(Sphere);Body->InvalidatePhysicsData();Body->CreatePhysicsMeshes();
        FCoreUObjectDelegates::ObjectsModifiedThisFrame.Remove(A);A->Modify();A->GetStaticMeshComponent()->SetStaticMesh(CollisionMesh);
        if(!S->Sync())return 44;
        FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT x1,json_extract(source,'$.bounds[1][0]') FROM entities WHERE actor_id=? AND kind='Component' AND label='StaticMeshComponent0'"));Q.SetBindingValueByIndex(1,Id);
        double SpatialMax=0,RenderMax=0;
        if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,SpatialMax) || !Q.GetColumnValueByIndex(1,RenderMax) || SpatialMax-RenderMax<400)return 45;
        // Editor commandlets may omit the physics scene. Create it explicitly
        // for this owned fixture before asking Unreal to perform the reference ray.
        if(!W->GetPhysicsScene())W->CreatePhysicsScene();
        A->GetStaticMeshComponent()->RecreatePhysicsState();
        FHitResult Hit;const FVector Start=A->GetActorLocation()+FVector(500,0,100);
        if(!A->GetStaticMeshComponent()->LineTraceComponent(Hit,Start,Start-FVector(0,0,200),FCollisionQueryParams()))
        {UE_LOG(LogTemp,Error,TEXT("Collision fixture ray failed: scene=%d physics=%d mode=%d"),W->GetPhysicsScene()!=nullptr,A->GetStaticMeshComponent()->IsPhysicsStateCreated(),(int32)Body->GetCollisionTraceFlag());return 46;}
        Evidence->SetBoolField(TEXT("native_collision_outside_render_bounds_index_and_ray"),true);
        // Reproduce an old persisted Twin with render-only index extents. This
        // changes only the owned test DB, never the Unreal source objects.
        if(!S->Database.Begin(false,S->MapId))return 69;
        if(!S->Database.Exec(TEXT("UPDATE entities SET source=json_remove(source,'$.collision_bounds','$.collision_local_bounds'),x0=json_extract(source,'$.bounds[0][0]'),x1=json_extract(source,'$.bounds[1][0]'),y0=json_extract(source,'$.bounds[0][1]'),y1=json_extract(source,'$.bounds[1][1]'),z0=json_extract(source,'$.bounds[0][2]'),z1=json_extract(source,'$.bounds[1][2]')")) ||
           !S->Database.Metadata(TEXT("collision_bounds_version"),TEXT("1")) || !S->Database.Commit(S->MapId,false))return 70;
        FString OldSource,ComponentId;
        {FSpatialTwinSQLiteStatement R(S->Database.DB,TEXT("SELECT id,source FROM entities WHERE actor_id=? AND kind='Component' AND label='StaticMeshComponent0'"));R.SetBindingValueByIndex(1,Id);if(R.Step()!=ESTSQLiteStepResult::Row || !R.GetColumnValueByIndex(0,ComponentId) || !R.GetColumnValueByIndex(1,OldSource))return 74;}
        if(!S->Database.Begin(false,S->MapId))return 75;
        {FSpatialTwinSQLiteStatement Broken(S->Database.DB,TEXT("UPDATE entities SET source=json_set(source,'$.collision.shapes',json('[{\"type\":\"sphere\",\"radius\":30}]')) WHERE id=?"));Broken.SetBindingValueByIndex(1,ComponentId);if(Broken.Step()!=ESTSQLiteStepResult::Done)return 76;}
        if(!S->Database.Commit(S->MapId,false))return 77;const int64 BeforeUpgrade=S->Database.Revision;
        if(S->Database.UpgradeCollisionBounds(S->MapId) || S->Database.Revision!=BeforeUpgrade || S->Database.ReadMetadata(TEXT("collision_bounds_version"))!=TEXT("1"))return 78;
        if(!S->Database.Begin(false,S->MapId))return 79;
        {FSpatialTwinSQLiteStatement Restore(S->Database.DB,TEXT("UPDATE entities SET source=? WHERE id=?"));Restore.SetBindingValueByIndex(1,OldSource);Restore.SetBindingValueByIndex(2,ComponentId);if(Restore.Step()!=ESTSQLiteStepResult::Done)return 80;}
        if(!S->Database.Commit(S->MapId,false) || !S->Database.UpgradeCollisionBounds(S->MapId)){UE_LOG(LogTemp,Error,TEXT("Collision upgrade failed: %s"),*S->Database.Error);return 71;}
        {FSpatialTwinSQLiteStatement R(S->Database.DB,TEXT("SELECT x1,json_extract(source,'$.bounds[1][0]'),json_extract(source,'$.collision_bounds_provenance') FROM entities WHERE actor_id=? AND kind='Component' AND label='StaticMeshComponent0'"));R.SetBindingValueByIndex(1,Id);FString Provenance;
         if(R.Step()!=ESTSQLiteStepResult::Row || !R.GetColumnValueByIndex(0,SpatialMax) || !R.GetColumnValueByIndex(1,RenderMax) || SpatialMax-RenderMax<400 || !R.GetColumnValueByIndex(2,Provenance) || Provenance!=TEXT("derived_from_exported_collision_v2"))return 72;}
        const int64 UpgradedRevision=S->Database.Revision;
        if(!S->Database.UpgradeCollisionBounds(S->MapId) || S->Database.Revision!=UpgradedRevision)return 73;
        Evidence->SetBoolField(TEXT("legacy_collision_bounds_upgrade_without_actor_export_and_idempotent"),true);
        Evidence->SetBoolField(TEXT("collision_upgrade_corruption_rolls_back_then_recovers"),true);
        Evidence->SetStringField(TEXT("collision_upgrade_metrics"),S->Database.ReadMetadata(TEXT("collision_bounds_upgrade")));
    }
    {
        auto Plugin=IPluginManager::Get().FindPlugin(TEXT("UnrealSpatialTwin"));FString Schema,Source;
        if(!Plugin || !FFileHelper::LoadFileToString(Schema,*(Plugin->GetBaseDir()/TEXT("Resources/schema.sql"))) || !Read(Source))return 219;
        const int32 Offset=Schema.Find(TEXT("CREATE INDEX IF NOT EXISTS entity_search"));if(Offset==INDEX_NONE)return 220;
        const FString LegacySchema=Schema.Left(Offset),SearchRoot=S->Database.Root/TEXT("search-migration");
        TSharedPtr<FJsonObject> Entity;if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Entity))return 221;
        FSpatialTwinDatabase Search;
        if(!Search.Open(SearchRoot,LegacySchema) || !Search.Begin(true,S->MapId) || !Search.Put(Entity) || !Search.Commit(S->MapId,true))return 222;
        const int64 SearchRevision=Search.Revision;Search.Close();
        // A failed schema transaction must leave both source and old schema usable.
        if(Search.Open(SearchRoot,Schema+TEXT("\n-- @statement\nSELECT nonexistent_search_column FROM entities;")))return 223;
        Search.Close();
        if(!Search.Open(SearchRoot,LegacySchema) || !Search.ReadMetadata(TEXT("text_search_version")).IsEmpty() || Search.Revision!=SearchRevision)return 224;
        Search.Close();
        if(!Search.Open(SearchRoot,Schema) || Search.Revision!=SearchRevision || Search.ReadMetadata(TEXT("text_search_version"))!=TEXT("1"))return 225;
        auto Count=[&](const FString& Token)
        {
            FSpatialTwinSQLiteStatement Q(Search.DB,TEXT("SELECT count(*) FROM entity_text WHERE entity_text MATCH ?"));Q.SetBindingValueByIndex(1,Token);
            int32 N=-1;if(Q.Step()==ESTSQLiteStepResult::Row)Q.GetColumnValueByIndex(0,N);return N;
        };
        if(Count(TEXT("\"twi\""))!=1)return 226;
        Entity->SetStringField(TEXT("label"),TEXT("Renamed_100%Door"));
        if(!Search.Begin(false,S->MapId) || !Search.Put(Entity) || Count(TEXT("\"Ren\""))!=1)return 227;
        Search.Rollback();if(Count(TEXT("\"Ren\""))!=0 || Count(TEXT("\"twi\""))!=1)return 228;
        if(!Search.Begin(false,S->MapId) || !Search.Put(Entity) || !Search.Commit(S->MapId,false) || Count(TEXT("\"Ren\""))!=1)return 229;
        Search.Close();
        if(!Search.Open(SearchRoot,Schema) || Search.Revision!=SearchRevision+1 || Count(TEXT("\"Ren\""))!=1)return 230;
        if(!Search.Begin(false,S->MapId) || !Search.Exec(TEXT("INSERT INTO entity_text(entity_text,rank) VALUES('integrity-check',1)")) || !Search.Delete(Id) || !Search.Commit(S->MapId,false) || Count(TEXT("\"Ren\""))!=0)return 231;
        Evidence->SetBoolField(TEXT("native_text_index_legacy_migration_failure_rollback_update_delete_idempotence"),true);
    }
    if(FParse::Param(FCommandLine::Get(),TEXT("PartitionFixture")))
    {
        // NewMap is the noninteractive primitive. The editing wrapper attempts
        // to save the previous intentionally-unsaved fixture and may cancel.
        auto PartitionWorld=GEditor->NewMap(true);
        if(!PartitionWorld || !PartitionWorld->GetWorldPartition())return 36;
        if(!UEditorLoadingAndSavingUtils::SaveMap(PartitionWorld,TEXT("/Game/SpatialTwinPartitionFixture")))return 37;
        auto Cube=PartitionWorld->SpawnActor<AStaticMeshActor>();Cube->GetStaticMeshComponent()->SetStaticMesh(LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube")));Cube->SetActorLabel(TEXT("GenericPartitionCube"));
        auto ISM=NewObject<UInstancedStaticMeshComponent>(Cube,TEXT("ColdInstances"));Cube->AddInstanceComponent(ISM);ISM->SetupAttachment(Cube->GetRootComponent());ISM->SetStaticMesh(Cube->GetStaticMeshComponent()->GetStaticMesh());ISM->RegisterComponent();
        ISM->AddInstance(FTransform(FVector(0,500,0)));ISM->AddInstance(FTransform(FVector(0,1000,0)));
        if(!UEditorLoadingAndSavingUtils::SavePackages({Cube->GetPackage()},true))return 38;
        const FString PreviousRoot=S->Database.Root;
        if(!S->Rebuild(PartitionWorld))return 39;
        if(PreviousRoot==S->Database.Root)return 66;
        {FSpatialTwinSQLiteDatabase Previous;if(!Previous.Open(*(PreviousRoot/TEXT("world.sqlite")),ESTSQLiteOpenMode::ReadOnly))return 67;
         FSpatialTwinSQLiteStatement Q(Previous,TEXT("SELECT 1 FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Id);if(Q.Step()!=ESTSQLiteStepResult::Row)return 68;}
        Evidence->SetBoolField(TEXT("map_change_preserves_previous_canonical_store"),true);
        auto OriginalHash=PartitionWorld->GetWorldPartition()->RuntimeHash.Get();const bool CanGenerate=PartitionWorld->GetWorldPartition()->CanGenerateStreaming();
        if(!S->RefreshPartition() || S->Database.ReadMetadata(TEXT("partition_cell_state"))!=TEXT("CURRENT"))return 40;
        if(OriginalHash!=PartitionWorld->GetWorldPartition()->RuntimeHash.Get() || CanGenerate!=PartitionWorld->GetWorldPartition()->CanGenerateStreaming())return 41;
        {FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT count(*) FROM relationships WHERE source=? AND kind='IN_PARTITION_CELL'"));Q.SetBindingValueByIndex(1,S->ActorId(Cube));int32 Count=0;if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,Count) || Count<1)return 42;}
        const int64 LayoutRevision=S->Database.Revision;if(!S->RefreshPartition() || S->Database.Revision!=LayoutRevision)return 43;
        auto Membership=[&]()
        {
            TArray<FString> Cells;FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT target FROM relationships WHERE source=? AND kind='IN_PARTITION_CELL' ORDER BY target"));Q.SetBindingValueByIndex(1,S->ActorId(Cube));
            while(Q.Step()==ESTSQLiteStepResult::Row){FString Cell;Q.GetColumnValueByIndex(0,Cell);Cells.Add(Cell);}return Cells;
        };
        const auto OriginalCells=Membership();const FVector OriginalPosition=Cube->GetActorLocation();
        Cube->Modify();Cube->SetActorLocation(OriginalPosition+FVector(1000000,0,0));Cube->PostEditMove(true);S->Dirty(Cube);
        if(!UEditorLoadingAndSavingUtils::SavePackages({Cube->GetPackage()},true) || !S->Sync() || !S->RefreshPartition())return 215;
        const auto MovedCells=Membership();if(MovedCells.IsEmpty() || MovedCells==OriginalCells)return 216;
        Cube->Modify();Cube->SetActorLocation(OriginalPosition);Cube->PostEditMove(true);S->Dirty(Cube);
        if(!UEditorLoadingAndSavingUtils::SavePackages({Cube->GetPackage()},true) || !S->Sync() || !S->RefreshPartition() || Membership()!=OriginalCells)return 217;
        const int64 RestoredRevision=S->Database.Revision;if(!S->RefreshPartition() || S->Database.Revision!=RestoredRevision)return 218;
        Evidence->SetBoolField(TEXT("partition_membership_delta_move_restore_noop"),true);
        Evidence->SetBoolField(TEXT("native_partition_layout_membership_noop_and_policy_preserved"),true);
        Evidence->SetNumberField(TEXT("partition_revision"),S->Database.Revision);
        // Record the real immutable Engine source, as live Sync does. The cold
        // fixture must reconcile only its changed actor, not this existing file.
        if(!S->Database.Begin(false,S->MapId) || !S->RecordSavedPackage(Cube->GetStaticMeshComponent()->GetStaticMesh()->GetPackage()) || !S->Database.Commit(S->MapId,false))return 95;
    }
    Evidence->SetNumberField(TEXT("final_revision"),S->Database.Revision);
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Evidence),*(S->Database.Root/TEXT("native-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinTest PASS revision=%lld"),S->Database.Revision);S->Database.DB.Close();return 0;
}

static int32 RunSpatialTwinAssetFixture()
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 201;
    if(!GEditor || !FEditorFileUtils::LoadMap(TEXT("/Engine/Maps/Entry"),true,false))return 202;
    auto W=GEditor->GetEditorWorldContext().World();auto Cube=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));auto Sphere=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Sphere.Sphere"));if(!Cube || !Sphere)return 203;
    FAssetCompilingManager::Get().FinishAllCompilation();
    auto A=W->SpawnActor<AStaticMeshActor>();A->SetActorLabel(TEXT("TwinAssetSwap"));A->GetStaticMeshComponent()->SetStaticMesh(Cube);A->GetStaticMeshComponent()->SetCanEverAffectNavigation(false);
    auto Instances=NewObject<UInstancedStaticMeshComponent>(A,TEXT("TwinAssetInstances"));A->AddInstanceComponent(Instances);Instances->SetStaticMesh(Cube);Instances->SetCollisionProfileName(TEXT("BlockAll"));Instances->SetCanEverAffectNavigation(false);Instances->RegisterComponent();
    Instances->AddInstance(FTransform(FVector(300,0,0)));Instances->AddInstance(FTransform(FVector(600,0,0)));
    auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();if(!S || !S->Rebuild(W) || !S->CacheAsset(Sphere->GetPathName()))return 204;
    const FString ActorId=S->ActorId(A);FString ComponentId;
    {FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT id FROM entities WHERE actor_id=? AND label='TwinAssetInstances' AND kind='Component'"));Q.SetBindingValueByIndex(1,ActorId);if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,ComponentId))return 205;}
    auto Sources=[&]()
    {
        TArray<TSharedPtr<FJsonValue>> Values;FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=? OR actor_id=?"));Q.SetBindingValueByIndex(1,ActorId);Q.SetBindingValueByIndex(2,ActorId);
        while(Q.Step()==ESTSQLiteStepResult::Row){FString Text;TSharedPtr<FJsonObject> E;Q.GetColumnValueByIndex(0,Text);FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),E);Values.Add(MakeShared<FJsonValueObject>(E));}return Values;
    };
    auto Hit=[&](){FHitResult Hit;return W->LineTraceSingleByChannel(Hit,FVector(345,45,200),FVector(345,45,-200),ECC_Visibility);};
    auto Report=MakeShared<FJsonObject>();Report->SetBoolField(TEXT("cube_hit"),Hit());Report->SetStringField(TEXT("actor_id"),ActorId);Report->SetStringField(TEXT("component_id"),ComponentId);Report->SetStringField(TEXT("asset_id"),TEXT("asset:")+Sphere->GetPathName());
    Instances->SetStaticMesh(Sphere);Instances->RecreatePhysicsState();S->Dirty(A);if(!S->Sync())return 206;
    Report->SetArrayField(TEXT("expected"),Sources());Report->SetBoolField(TEXT("sphere_hit"),Hit());
    Instances->SetStaticMesh(Cube);Instances->RecreatePhysicsState();S->Dirty(A);if(!S->Sync())return 207;
    Report->SetNumberField(TEXT("baseline_revision"),S->Database.Revision);Report->SetStringField(TEXT("state"),TEXT("MEASURED"));
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Report),*(S->Database.Root/TEXT("native-asset-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    S->Database.DB.Close();return 0;
}

static int32 RunSpatialTwinTransientFixture(bool Resume)
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 370;
    FString Map;if(!FParse::Value(FCommandLine::Get(),TEXT("TransientMap="),Map) || !Map.StartsWith(TEXT("/Game/SpatialTwinTransient")))return 371;
    auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();if(!S)return 372;
    auto Mesh=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));if(!Mesh)return 373;
    UWorld* W=nullptr;AStaticMeshActor* Saved=nullptr;
    if(Resume)
    {if(!FEditorFileUtils::LoadMap(Map,false,true))return 374;W=GEditor->GetEditorWorldContext().World();}
    else
    {
        if(FPackageName::DoesPackageExist(Map))return 375;
        GEditor->NewMap(false);W=GEditor->GetEditorWorldContext().World();
        Saved=W->SpawnActor<AStaticMeshActor>();Saved->SetActorLabel(TEXT("TransientFixtureSaved"));Saved->GetStaticMeshComponent()->SetStaticMesh(Mesh);
        if(!UEditorLoadingAndSavingUtils::SaveMap(W,Map))return 376;
    }
    auto Spawn=[&](FName Name,double X){FActorSpawnParameters P;P.Name=Name;P.ObjectFlags|=RF_Transient;auto A=W->SpawnActor<AStaticMeshActor>(P);A->GetStaticMeshComponent()->SetStaticMesh(Mesh);A->SetActorLocation(FVector(X,0,500));return A;};
    auto Present=Spawn(TEXT("TwinSessionPresent"),Resume?900:300);
    if(!Present->HasAnyFlags(RF_Transient))return 377;
    const FString Root=S->WorldRoot(W),Receipt=Root/TEXT("transient-baseline.json");
    if(!Resume)
    {
        auto Gone=Spawn(TEXT("TwinSessionGone"),600);if(!S->Rebuild(W))return 378;
        const FString Old=S->ActorId(Present);FString Record;
        {FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Old);if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,Record))return 379;}
        TSharedPtr<FJsonObject> Legacy;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Record),Legacy);Legacy->RemoveField(TEXT("transient"));
        if(!S->Database.Begin(false,S->MapId) || !S->Database.Put(Legacy) || !S->Database.Commit(S->MapId,false))return 380;
        auto Report=MakeShared<FJsonObject>();Report->SetStringField(TEXT("state"),TEXT("PREPARED"));Report->SetStringField(TEXT("old_present"),Old);Report->SetStringField(TEXT("old_gone"),S->ActorId(Gone));Report->SetStringField(TEXT("saved_id"),S->ActorId(Saved));Report->SetStringField(TEXT("saved_path"),Saved->GetPathName());
        FString Metrics;FFileHelper::LoadFileToString(Metrics,*(Root/TEXT("last_scan_metrics.json")));Report->SetStringField(TEXT("scan_metrics"),Metrics);
        FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Report),*Receipt,FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
        S->Database.DB.Close();return 0;
    }
    FString Record;if(!FFileHelper::LoadFileToString(Record,*Receipt))return 381;
    TSharedPtr<FJsonObject> Before;if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Record),Before))return 382;
    // The previous process really ended without serializing these two objects.
    {FSpatialTwinSQLiteDatabase Previous;if(!Previous.Open(*(Root/TEXT("world.sqlite")),ESTSQLiteOpenMode::ReadOnly))return 383;
     for(auto Field:{TEXT("old_present"),TEXT("old_gone")}){FSpatialTwinSQLiteStatement Q(Previous,TEXT("SELECT 1 FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Before->GetStringField(Field));if(Q.Step()!=ESTSQLiteStepResult::Row)return 384;}}
    if(!S->Resume() || !S->Sync()){UE_LOG(LogTemp,Error,TEXT("Transient resume failed: %s"),*S->Status());return 385;}
    if(S->ActorId(Present)==Before->GetStringField(TEXT("old_present")))return 386;
    for(auto Field:{TEXT("old_present"),TEXT("old_gone")})
    {
        const FString Old=Before->GetStringField(Field);
        FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT 1 FROM entities WHERE id=? OR actor_id=?"));Q.SetBindingValueByIndex(1,Old);Q.SetBindingValueByIndex(2,Old);if(Q.Step()!=ESTSQLiteStepResult::Done)return 387;
        FSpatialTwinSQLiteStatement R(S->Database.DB,TEXT("SELECT 1 FROM relationships WHERE source=? OR target=?"));R.SetBindingValueByIndex(1,Old);R.SetBindingValueByIndex(2,Old);if(R.Step()!=ESTSQLiteStepResult::Done)return 388;
    }
    {FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT id,json_extract(source,'$.transient'),json_extract(source,'$.transform.position[0]') FROM entities WHERE kind='Actor' AND path=?"));Q.SetBindingValueByIndex(1,Present->GetPathName());FString Id;int32 Transient=0;double X=0;
     if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,Id) || Id!=S->ActorId(Present) || !Q.GetColumnValueByIndex(1,Transient) || Transient!=1 || !Q.GetColumnValueByIndex(2,X) || X!=900 || Q.Step()!=ESTSQLiteStepResult::Done)return 389;}
    Saved=FindObject<AStaticMeshActor>(nullptr,*Before->GetStringField(TEXT("saved_path")));if(!Saved || S->ActorId(Saved)!=Before->GetStringField(TEXT("saved_id")))return 390;
    FString Metrics;if(!FFileHelper::LoadFileToString(Metrics,*(Root/TEXT("last_scan_metrics.json"))) || Metrics!=Before->GetStringField(TEXT("scan_metrics")))return 391;
    auto Report=MakeShared<FJsonObject>();Report->SetStringField(TEXT("state"),TEXT("PASS"));Report->SetNumberField(TEXT("revision"),S->Database.Revision);Report->SetStringField(TEXT("new_present"),S->ActorId(Present));Report->SetBoolField(TEXT("old_actor_children_graph_removed"),true);Report->SetBoolField(TEXT("legacy_same_path_recreated_guid"),true);Report->SetBoolField(TEXT("missing_transient_removed"),true);Report->SetBoolField(TEXT("saved_actor_identity_unchanged"),true);Report->SetBoolField(TEXT("full_scan_unchanged"),true);
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Report),*(Root/TEXT("native-transient-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinTransientTest PASS revision=%lld"),S->Database.Revision);S->Database.DB.Close();return 0;
}

// Two real processes exercise the persisted mesh branch, not the resident cache.
static int32 RunSpatialTwinPersistedMaterialFixture(bool Resume)
{
    if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")) || !GEditor)return 430;
    const FString Map=TEXT("/Game/SpatialTwinPersistedMaterialFixture"),MeshPath=TEXT("/Game/SpatialTwinTests/PersistedMesh.PersistedMesh"),MaterialPath=TEXT("/Game/SpatialTwinTests/PersistedMaterial.PersistedMaterial");
    auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();if(!S || S->Database.DB.IsValid())return 431;
    UWorld* W=nullptr;UStaticMesh* Mesh=nullptr;UMaterial* Material=nullptr;AStaticMeshActor* Actor=nullptr;
    if(Resume)
    {
        if(!FEditorFileUtils::LoadMap(Map,false,true))return 432;W=GEditor->GetEditorWorldContext().World();
        Mesh=LoadObject<UStaticMesh>(nullptr,*MeshPath);Material=LoadObject<UMaterial>(nullptr,*MaterialPath);
        for(TActorIterator<AStaticMeshActor> It(W);It;++It)if(It->GetActorLabel()==TEXT("PersistedMaterialOwner")){if(Actor)return 433;Actor=*It;}
        if(!Mesh || !Material || !Actor || Actor->GetStaticMeshComponent()->GetStaticMesh()!=Mesh)return 434;
    }
    else
    {
        if(FPackageName::DoesPackageExist(Map) || FPackageName::DoesPackageExist(TEXT("/Game/SpatialTwinTests/PersistedMesh")))return 435;
        GEditor->NewMap(false);W=GEditor->GetEditorWorldContext().World();auto Cube=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));if(!Cube)return 436;
        Mesh=DuplicateObject<UStaticMesh>(Cube,CreatePackage(TEXT("/Game/SpatialTwinTests/PersistedMesh")),TEXT("PersistedMesh"));
        Material=NewObject<UMaterial>(CreatePackage(TEXT("/Game/SpatialTwinTests/PersistedMaterial")),TEXT("PersistedMaterial"),RF_Public|RF_Standalone);
        Mesh->SetMaterial(0,Material);FAssetRegistryModule::AssetCreated(Mesh);FAssetRegistryModule::AssetCreated(Material);
        Actor=W->SpawnActor<AStaticMeshActor>();Actor->SetActorLabel(TEXT("PersistedMaterialOwner"));Actor->GetStaticMeshComponent()->SetStaticMesh(Mesh);Actor->GetStaticMeshComponent()->SetCanEverAffectNavigation(false);Actor->SetActorLocation(FVector(1234567890.125,200,300));
        FAssetCompilingManager::Get().FinishAllCompilation();
        if(!UEditorLoadingAndSavingUtils::SavePackages({Material->GetPackage(),Mesh->GetPackage()},false) || !UEditorLoadingAndSavingUtils::SaveMap(W,Map) || !S->Rebuild(W))return 437;
    }
    const FString Root=S->WorldRoot(W),Receipt=Root/TEXT("persisted-material-baseline.json"),MeshId=TEXT("asset:")+MeshPath;
    auto ReadMesh=[&](TSharedPtr<FJsonObject>& Value){FString Text;FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,MeshId);return Q.Step()==ESTSQLiteStepResult::Row && Q.GetColumnValueByIndex(0,Text) && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Value);};
    if(!Resume)
    {
        TSharedPtr<FJsonObject> Source;if(!ReadMesh(Source) || !Source->HasField(TEXT("geometry_hash")))return 438;
        auto Report=MakeShared<FJsonObject>();Report->SetStringField(TEXT("state"),TEXT("PREPARED"));Report->SetStringField(TEXT("actor_id"),S->ActorId(Actor));Report->SetObjectField(TEXT("mesh"),Source);
        FString Metrics;if(!FFileHelper::LoadFileToString(Metrics,*(Root/TEXT("last_scan_metrics.json"))))return 439;Report->SetStringField(TEXT("scan_metrics"),Metrics);
        if(!FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Report),*Receipt,FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))return 440;S->Database.DB.Close();return 0;
    }
    FString Text;TSharedPtr<FJsonObject> Before;if(!FFileHelper::LoadFileToString(Text,*Receipt) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Text),Before))return 441;
    const uint64 ExportsBefore=S->GetMeshGeometryExportAttemptCount();if(ExportsBefore!=0)return 442;
    if(!Material->Rename(TEXT("PersistedMaterialRenamed"),nullptr,REN_DontCreateRedirectors))return 443;FAssetRegistryModule::AssetRenamed(Material,MaterialPath);
    const FString NewId=TEXT("asset:")+Material->GetPathName(),OldId=TEXT("asset:")+MaterialPath;
    if(!S->Resume()){UE_LOG(LogTemp,Error,TEXT("Persisted material resume: %s"),*S->Status());return 444;}
    S->Dirty(Actor);if(!S->Sync())return 445;
    auto Uses=[&](const FString& Target){FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT 1 FROM relationships WHERE source=? AND target=? AND kind='USES_ASSET'"));Q.SetBindingValueByIndex(1,MeshId);Q.SetBindingValueByIndex(2,Target);return Q.Step()==ESTSQLiteStepResult::Row;};
    TSharedPtr<FJsonObject> After;if(!ReadMesh(After) || Uses(OldId) || !Uses(NewId) || After->GetArrayField(TEXT("materials")).Num()!=1 || After->GetArrayField(TEXT("materials"))[0]->AsString()!=NewId)return 446;
    const auto Expected=Before->GetObjectField(TEXT("mesh"));for(auto Field:{TEXT("geometry_hash"),TEXT("navigation_geometry_hash")})if(Expected->HasField(Field)!=After->HasField(Field) || (Expected->HasField(Field) && Expected->GetStringField(Field)!=After->GetStringField(Field)))return 447;
    if(S->ActorId(Actor)!=Before->GetStringField(TEXT("actor_id")) || Actor->GetActorLocation()!=FVector(1234567890.125,200,300) || S->GetMeshGeometryExportAttemptCount()!=ExportsBefore)return 448;
    const int64 RenameRevision=S->Database.Revision;const FString RenamedPath=Material->GetPathName();
    if(!Material->Rename(TEXT("PersistedMaterial"),nullptr,REN_DontCreateRedirectors))return 449;FAssetRegistryModule::AssetRenamed(Material,RenamedPath);S->Dirty(Actor);
    if(!S->Sync() || !ReadMesh(After) || !Uses(OldId) || Uses(NewId) || After->GetArrayField(TEXT("materials"))[0]->AsString()!=OldId || S->GetMeshGeometryExportAttemptCount()!=ExportsBefore)return 450;
    const int64 RestoredRevision=S->Database.Revision;if(RestoredRevision<=RenameRevision || !S->Sync() || S->Database.Revision!=RestoredRevision)return 451;
    // Repeat an unchanged native registry notification through the real delegate.
    // Opening this fixture through a junction must not invalidate its mesh/users.
    TSharedPtr<FJsonObject> BeforeEvent,AfterEvent;
    if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(S->Status()),BeforeEvent))return 454;
    FModuleManager::LoadModuleChecked<FAssetRegistryModule>(TEXT("AssetRegistry")).Get().OnAssetUpdated().Broadcast(FAssetData(Mesh));
    if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(S->Status()),AfterEvent) ||
       BeforeEvent->GetNumberField(TEXT("pending"))!=AfterEvent->GetNumberField(TEXT("pending")) ||
       !S->Sync() || S->Database.Revision!=RestoredRevision || S->GetMeshGeometryExportAttemptCount()!=ExportsBefore)return 455;
    FString Metrics;if(!FFileHelper::LoadFileToString(Metrics,*(Root/TEXT("last_scan_metrics.json"))) || Metrics!=Before->GetStringField(TEXT("scan_metrics")))return 452;
    auto Report=MakeShared<FJsonObject>();Report->SetStringField(TEXT("state"),TEXT("PASS"));Report->SetBoolField(TEXT("cold_persisted_material_refresh"),true);Report->SetBoolField(TEXT("source_and_graph_restored"),true);Report->SetBoolField(TEXT("actor_identity_pose_preserved"),true);Report->SetBoolField(TEXT("geometry_navigation_hashes_unchanged"),true);Report->SetBoolField(TEXT("full_scan_unchanged"),true);Report->SetNumberField(TEXT("mesh_geometry_export_attempts"),S->GetMeshGeometryExportAttemptCount()-ExportsBefore);Report->SetNumberField(TEXT("rename_revision"),RenameRevision);Report->SetNumberField(TEXT("restored_revision"),RestoredRevision);
    if(!FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Report),*(Root/TEXT("native-persisted-material-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))return 453;
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinPersistedMaterialTest PASS revision=%lld exports=0"),RestoredRevision);S->Database.DB.Close();return 0;
}

USpatialTwinTestCommandlet::USpatialTwinTestCommandlet(){IsEditor=true;IsClient=false;IsServer=false;LogToConsole=true;}
extern int32 RunSpatialTwinNestedFixture();
extern int32 RunSpatialTwinNestedResumeFixture();
extern int32 RunSpatialTwinNestedSourceFixture(const FString& Params);
int32 USpatialTwinTestCommandlet::Main(const FString& Params)
{
    if(FParse::Param(*Params,TEXT("PersistedMaterialFixture")))return RunSpatialTwinPersistedMaterialFixture(FParse::Param(*Params,TEXT("PersistedMaterialResume")));
    if(FParse::Param(*Params,TEXT("NestedFixture")))return RunSpatialTwinNestedFixture();
    if(FParse::Param(*Params,TEXT("NestedResumeFixture")))return RunSpatialTwinNestedResumeFixture();
    if(FParse::Param(*Params,TEXT("NestedSourceFixture")))return RunSpatialTwinNestedSourceFixture(Params);
    if(FParse::Param(*Params,TEXT("TransientFixture")))return RunSpatialTwinTransientFixture(FParse::Param(*Params,TEXT("TransientResume")));
    if(FParse::Param(*Params,TEXT("AuthoredNavFixture")))return RunSpatialTwinAuthoredNavFixture();
    if(FParse::Param(*Params,TEXT("EmptyInputFixture")))return RunSpatialTwinEmptyInputFixture();
    if(FParse::Param(*Params,TEXT("InstanceFixture")))return RunSpatialTwinInstanceFixture();
    if(FParse::Param(*Params,TEXT("DataLayerFixture")))return RunSpatialTwinDataLayerFixture();
    if(FParse::Param(*Params,TEXT("StreamingFixture")))return RunSpatialTwinStreamingFixture();
    if(FParse::Param(*Params,TEXT("AssetSwapFixture")))return RunSpatialTwinAssetFixture();
    return FParse::Param(*Params,TEXT("ResumeFixture"))?RunSpatialTwinResumeFixture():RunSpatialTwinFixture();
}

#if WITH_DEV_AUTOMATION_TESTS
IMPLEMENT_SIMPLE_AUTOMATION_TEST(FSpatialTwinFixtureAutomation,"UnrealSpatialTwin.Fixture",EAutomationTestFlags::EditorContext|EAutomationTestFlags::EngineFilter)
bool FSpatialTwinFixtureAutomation::RunTest(const FString& Parameters)
{
    int32 Result=RunSpatialTwinFixture();if(Result!=0)AddError(FString::Printf(TEXT("Spatial Twin fixture failed at check %d"),Result));return Result==0;
}
#endif

USpatialTwinNavigationTestCommandlet::USpatialTwinNavigationTestCommandlet(){IsEditor=true;IsClient=false;IsServer=false;LogToConsole=true;}
int32 USpatialTwinNavigationTestCommandlet::Main(const FString& Params)
{
    // Never save a game map. CapacityFixture owns its isolated saved test map.
    FString Map,Root,Position;if(!FParse::Value(*Params,TEXT("Map="),Map) || !FParse::Value(*Params,TEXT("SpatialTwinRoot="),Root) || !FParse::Value(*Params,TEXT("Position="),Position,false))return 2;
    if(IFileManager::Get().FileExists(*(Root/TEXT("world.sqlite"))))return 2;
    TArray<FString> Coordinates;Position.ParseIntoArray(Coordinates,TEXT(","));if(Coordinates.Num()!=3)return 3;
    FVector Center;for(int32 I=0;I<3;++I)if(!LexTryParseString(Center[I],*Coordinates[I]) || !FMath::IsFinite(Center[I]))return 3;
    const bool MaskFixture=FParse::Param(*Params,TEXT("MaskFixture"));ANavModifierVolume* TestMask=nullptr;
    const bool ProjectedLinkFixture=FParse::Param(*Params,TEXT("ProjectedLinkFixture")),FillFixture=MaskFixture || FParse::Param(*Params,TEXT("FillFixture"));
    const bool NavIdleFixture=FParse::Param(*Params,TEXT("NavIdleFixture")),SlopeFixture=FParse::Param(*Params,TEXT("SlopeFixture"));
    const bool CapacityFixture=FParse::Param(*Params,TEXT("CapacityFixture"));
    TArray<AStaticMeshActor*> SlopeRamps;
    const bool LinkFixture=CapacityFixture || SlopeFixture || NavIdleFixture || FillFixture || ProjectedLinkFixture || FParse::Param(*Params,TEXT("LinkFixture"));ANavLinkProxy* TestLink=nullptr;AStaticMeshActor* TestPlatform=nullptr;
    if(!GEditor)return 4;
    if(LinkFixture)
    {
        if(FApp::GetProjectName()!=FString(TEXT("SpatialTwinFixture")))return 10;
        GEditor->NewMap(false);
    }
    else if(!FEditorFileUtils::LoadMap(Map,false,true))return 4;
    auto W=GEditor->GetEditorWorldContext().World();
    if(SlopeFixture)
    {
        Map=W->GetPackage()->GetName();auto Original=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));if(!Original)return 350;
        FAssetCompilingManager::Get().FinishAllCompilation();
        for(int32 Row=0;Row<2;++Row)for(int32 Behavior=0;Behavior<4;++Behavior)
        {
            const FString Name=FString::Printf(TEXT("Slope_%d_%d"),Row,Behavior);
            auto Mesh=DuplicateObject<UStaticMesh>(Original,CreatePackage(*(TEXT("/Game/SpatialTwinTests/")+Name)),*Name);
            auto Body=Mesh->GetBodySetup();if(!Body)return 351;
            Body->WalkableSlopeOverride=FWalkableSlopeOverride((EWalkableSlopeBehavior)Behavior,Behavior==1?80.f:5.f);
            auto Ramp=W->SpawnActor<AStaticMeshActor>();Ramp->SetActorLabel(Name);Ramp->GetStaticMeshComponent()->SetStaticMesh(Mesh);
            Ramp->SetActorScale3D(FVector(6,7,.2));Ramp->SetActorRotation(FRotator(Row==0?30:60,0,0));
            Ramp->SetActorLocation(Center+FVector((Behavior-1.5)*1000,(Row-.5)*1400,300));SlopeRamps.Add(Ramp);
        }
        auto Bounds=W->SpawnActor<ANavMeshBoundsVolume>();Bounds->SetActorLocation(Center+FVector(0,0,400));
        auto Builder=NewObject<UCubeBuilder>();Builder->X=5000;Builder->Y=4000;Builder->Z=2000;UActorFactory::CreateBrushForVolumeActor(Bounds,Builder);
        auto Nav=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);if(!Nav){FNavigationSystem::AddNavigationSystemToWorld(*W,FNavigationSystemRunMode::EditorMode);Nav=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);}
        if(!Nav)return 352;Nav->OnNavigationBoundsUpdated(Bounds);Nav->OnWorldInitDone(FNavigationSystemRunMode::EditorMode);
        for(auto Ramp:SlopeRamps)Nav->UpdateActorAndComponentsInNavOctree(*Ramp);Nav->ProcessPendingOctreeUpdates();
    }
    else if(LinkFixture)
    {
        Map=W->GetPackage()->GetName();auto Mesh=LoadObject<UStaticMesh>(nullptr,TEXT("/Engine/BasicShapes/Cube.Cube"));if(!Mesh)return 11;
        FAssetCompilingManager::Get().FinishAllCompilation();
        for(int I=0;I<2;++I){auto Floor=W->SpawnActor<AStaticMeshActor>();Floor->SetActorLabel(FString::Printf(TEXT("LinkPlatform%d"),I));Floor->GetStaticMeshComponent()->SetStaticMesh(Mesh);Floor->SetActorScale3D(FVector(7,10,.5));Floor->SetActorLocation(Center+(FillFixture?FVector(0,0,I*300-25):FVector(I*900,0,-25)));Floor->GetStaticMeshComponent()->bFillCollisionUnderneathForNavmesh=FillFixture && I==1;TestPlatform=Floor;}
        auto Bounds=W->SpawnActor<ANavMeshBoundsVolume>();Bounds->SetActorLocation(Center+FVector(450,0,300));auto Builder=NewObject<UCubeBuilder>();Builder->X=2500;Builder->Y=1800;Builder->Z=1000;UActorFactory::CreateBrushForVolumeActor(Bounds,Builder);
        if(MaskFixture)
        {
            TestMask=W->SpawnActor<ANavModifierVolume>();TestMask->SetActorLabel(TEXT("NativeFillMask"));TestMask->SetActorLocation(Center+FVector(0,0,300));TestMask->SetAreaClass(UNavArea_Default::StaticClass());
            auto MaskProperty=FindFProperty<FBoolProperty>(TestMask->GetClass(),TEXT("bMaskFillCollisionUnderneathForNavmesh"));if(!MaskProperty)return 18;MaskProperty->SetPropertyValue_InContainer(TestMask,true);
            auto MaskBuilder=NewObject<UCubeBuilder>();MaskBuilder->X=700;MaskBuilder->Y=1000;MaskBuilder->Z=1000;UActorFactory::CreateBrushForVolumeActor(TestMask,MaskBuilder);
        }
        if(!FillFixture){TestLink=W->SpawnActor<ANavLinkProxy>();TestLink->SetActorLabel(TEXT("NativeOfflineLink"));TestLink->SetActorLocation(Center+FVector(450,0,0));
        auto& Link=TestLink->PointLinks[0];Link.Left=FVector(-200,0,0);Link.Right=FVector(200,0,0);Link.Direction=ENavLinkDirection::LeftToRight;Link.LeftProjectHeight=0;Link.MaxFallDownLength=0;Link.SnapRadius=100;
        if(ProjectedLinkFixture){TestLink->SetActorLocation(Center+FVector(450,0,100));Link.LeftProjectHeight=200;Link.MaxFallDownLength=200;}}
        auto Nav=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);if(!Nav){FNavigationSystem::AddNavigationSystemToWorld(*W,FNavigationSystemRunMode::EditorMode);Nav=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);}
        if(!Nav)return 12;Nav->OnNavigationBoundsUpdated(Bounds);Nav->OnWorldInitDone(FNavigationSystemRunMode::EditorMode);
        if(TestLink)Nav->UpdateActorAndComponentsInNavOctree(*TestLink);else Nav->UpdateActorAndComponentsInNavOctree(*TestPlatform);Nav->ProcessPendingOctreeUpdates();
        if(TestMask){Nav->UpdateActorAndComponentsInNavOctree(*TestMask);Nav->ProcessPendingOctreeUpdates();}
    }
    auto N=FNavigationSystem::GetCurrent<UNavigationSystemV1>(W);if(!N)return 5;
    auto Anchor=W->SpawnActor<AActor>();auto Scene=NewObject<USceneComponent>(Anchor);Anchor->SetRootComponent(Scene);Scene->RegisterComponent();Anchor->SetActorLocation(Center);
    UNavigationSystemV1::RegisterNavigationInvoker(*Anchor,2500,3500);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinNavigationTest auto=%d locked=%d editorlock=%d initiallock=%d invoker_generation=%d"),N->GetIsAutoUpdateEnabled(),N->IsNavigationBuildingLocked(),N->IsNavigationBuildingLocked(ENavigationBuildLock::NoUpdateInEditor),N->IsNavigationBuildingLocked(ENavigationBuildLock::InitialLock),N->IsActiveTilesGenerationEnabled());
    // Explicit native Build ignores the editor auto-update lock. Its build
    // bounds constrain this disposable probe, never the saved game project.
    const FBox Region(Center-FVector(4000,4000,10000),Center+FVector(4000,4000,10000));N->SetBuildBounds(Region);
    // AsyncLoadLock is released by the native core ticker after compilation,
    // not by ticking navigation alone. Commandlets do not run the editor loop.
    FAssetCompilingManager::Get().FinishAllCompilation();
    for(int32 I=0;I<65;++I){W->Tick(LEVELTICK_TimeOnly,1.f/30);FTSTicker::GetCoreTicker().Tick(1.f/30);++GFrameCounter;}
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinNavigationTest after_native_idle locked=%d invokers=%d"),N->IsNavigationBuildingLocked(),N->GetInvokerLocations().Num());
    if(CapacityFixture)for(TActorIterator<ARecastNavMesh> It(W);It;++It)
    {
        // Build() alone can retain existing tiles. Exercise the same property
        // notification that official editor tools use to recreate the pool.
        auto Property=FindFProperty<FProperty>(It->GetClass(),TEXT("TilePoolSize"));
        if(!Property)return 374;
        It->PreEditChange(Property);It->bFixedTilePoolSize=true;It->TilePoolSize=2;
        FPropertyChangedEvent Event(Property);It->PostEditChangeProperty(Event);
    }
    auto BuildAndWait=[&]()->int32
    {
    N->Build();
    const double Deadline=FPlatformTime::Seconds()+120;int32 Active=0;
    do
    {
        W->Tick(LEVELTICK_TimeOnly,1.f/30);N->Tick(1.f/30);++GFrameCounter;FTaskGraphInterface::Get().ProcessThreadUntilIdle(ENamedThreads::GameThread);Active=0;
        for(TActorIterator<ARecastNavMesh> It(W);It;++It)Active+=It->GetNumActiveTiles();
        if(Active>0 && !N->IsNavigationBuildInProgress())break;
        FPlatformProcess::Sleep(.01f);
    }while(FPlatformTime::Seconds()<Deadline);
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinNavigationTest active_tiles=%d"),Active);
    return N->IsNavigationBuildInProgress()?0:Active;
    };
    const int32 Active=BuildAndWait();if(!Active)return 6;
    if(CapacityFixture)
    {
        if(FPaths::FileExists(FPaths::ProjectContentDir()/TEXT("CapacityFixture.umap")))return 368;
        if(!UEditorLoadingAndSavingUtils::SaveMap(W,TEXT("/Game/CapacityFixture")))return 369;
        auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();if(!S || !S->Rebuild(W))return 370;
        TArray<TSharedPtr<FJsonValue>> Agents;
        for(TActorIterator<ARecastNavMesh> It(W);It;++It)
        {
            auto Row=MakeShared<FJsonObject>();Row->SetStringField(TEXT("path"),It->GetPathName());Row->SetStringField(TEXT("actor_id"),S->ActorId(*It));
            Row->SetStringField(TEXT("agent"),It->GetConfig().Name.IsNone()?It->GetName():It->GetConfig().Name.ToString());
            Row->SetNumberField(TEXT("active_tiles"),It->GetNumActiveTiles());if(It->GetNumActiveTiles()!=2)return 371;
            TArray<TSharedPtr<FJsonValue>> Points;int32 Hits=0,Misses=0;
            for(double X:{-200.,0.,200.,700.,900.,1100.})for(double Y:{-300.,0.,300.})
            {
                FVector P=Center+FVector(X,Y,20);FNavLocation Projected;const bool Found=It->ProjectPoint(P,Projected,FVector(20,20,100));
                auto Point=MakeShared<FJsonObject>();Point->SetArrayField(TEXT("position"),{MakeShared<FJsonValueNumber>(P.X),MakeShared<FJsonValueNumber>(P.Y),MakeShared<FJsonValueNumber>(P.Z)});
                Point->SetBoolField(TEXT("found"),Found);Points.Add(MakeShared<FJsonValueObject>(Point));Found?++Hits:++Misses;
            }
            if(!Hits || !Misses)return 372;
            Row->SetArrayField(TEXT("probes"),Points);Agents.Add(MakeShared<FJsonValueObject>(Row));
        }
        auto Report=MakeShared<FJsonObject>();Report->SetStringField(TEXT("state"),TEXT("READY_SATURATED_FIXTURE"));Report->SetArrayField(TEXT("agents"),Agents);Report->SetStringField(TEXT("native_status"),S->Status());
        FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Report),*(Root/TEXT("native-capacity-fixture.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
        return Agents.IsEmpty()?373:0;
    }
    if(SlopeFixture)
    {
        auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();if(!S || !S->Rebuild(W))return 353;
        TArray<TSharedPtr<FJsonValue>> Rows;
        for(TActorIterator<ARecastNavMesh> It(W);It;++It)for(int32 I=0;I<SlopeRamps.Num();++I)
        {
            auto Ramp=SlopeRamps[I];const FVector Point=Ramp->GetActorTransform().TransformPosition(FVector(0,0,50));
            FNavLocation Projected;const bool Found=It->ProjectPoint(Point,Projected,FVector(20,20,40));
            auto Row=MakeShared<FJsonObject>();Row->SetStringField(TEXT("agent"),It->GetConfig().Name.ToString());Row->SetStringField(TEXT("actor_id"),S->ActorId(Ramp));
            Row->SetNumberField(TEXT("behavior"),I%4);Row->SetNumberField(TEXT("surface_angle"),I<4?30:60);Row->SetBoolField(TEXT("found"),Found);
            TArray<TSharedPtr<FJsonValue>> PointValues;for(int32 Axis=0;Axis<3;++Axis)PointValues.Add(MakeShared<FJsonValueNumber>(Point[Axis]));Row->SetArrayField(TEXT("position"),PointValues);
            if(Found){TArray<TSharedPtr<FJsonValue>> P;for(int32 Axis=0;Axis<3;++Axis)P.Add(MakeShared<FJsonValueNumber>(Projected.Location[Axis]));Row->SetArrayField(TEXT("projected"),P);}
            Rows.Add(MakeShared<FJsonValueObject>(Row));
        }
        auto Evidence=MakeShared<FJsonObject>();Evidence->SetStringField(TEXT("state"),TEXT("OBSERVED_NATIVE"));Evidence->SetStringField(TEXT("engine_version"),FEngineVersion::Current().ToString());Evidence->SetArrayField(TEXT("projections"),Rows);
        FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Evidence),*(S->Database.Root/TEXT("slope-probe.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
        return Rows.Num()>=8?0:354;
    }
    if(NavIdleFixture)
    {
        // Real native queues/ticker, including a blocked interval. No second
        // Build(), dirty-queue reset, synthetic tile event or saved game edits.
        UNavigationSystemV1::SetNavigationAutoUpdateEnabled(true,N);
        N->AddNavigationBuildLock(ENavigationBuildLock::Custom);
        auto S=GEditor->GetEditorSubsystem<USpatialTwinSubsystem>();if(!S || !S->Rebuild(W))return 21;
        const FString FirstScan=S->Database.ReadMetadata(TEXT("navigation_state"));
        auto Pump=[&](double Seconds){const double End=FPlatformTime::Seconds()+Seconds;do{
            W->Tick(LEVELTICK_TimeOnly,1.f/30);N->Tick(1.f/30);FTSTicker::GetCoreTicker().Tick(1.f/30);++GFrameCounter;
            FTaskGraphInterface::Get().ProcessThreadUntilIdle(ENamedThreads::GameThread);FPlatformProcess::Sleep(.01f);
        }while(FPlatformTime::Seconds()<End);};
        Pump(1.5);const bool FirstScanStayedStale=S->Database.ReadMetadata(TEXT("navigation_state"))!=TEXT("AVAILABLE");
        N->RemoveNavigationBuildLock(ENavigationBuildLock::Custom,UNavigationSystemV1::ELockRemovalRebuildAction::NoRebuild);
        Pump(2);const FString Initial=S->Database.ReadMetadata(TEXT("navigation_state"));
        auto NavRecords=[&](){FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE kind='NavRegion' ORDER BY id"));FString Result;
            while(Q.Step()==ESTSQLiteStepResult::Row){FString Source;Q.GetColumnValueByIndex(0,Source);Result+=Source;}return Result;};
        const FString Cached=NavRecords();N->AddNavigationBuildLock(ENavigationBuildLock::Custom);
        bool Ok=S->Rebuild(W);const FString Rescan=S->Database.ReadMetadata(TEXT("navigation_state"));const bool CachedPreserved=!Cached.IsEmpty() && Cached==NavRecords();
        N->RemoveNavigationBuildLock(ENavigationBuildLock::Custom,UNavigationSystemV1::ELockRemovalRebuildAction::NoRebuild);Pump(2);
        auto Events=MakeShared<int32>(0);TArray<TPair<ARecastNavMesh*,FDelegateHandle>> Watches;
        for(TActorIterator<ARecastNavMesh> It(W);It;++It)Watches.Emplace(*It,It->OnNavMeshUpdate.AddLambda([Events](){++*Events;}));
        N->AddNavigationBuildLock(ENavigationBuildLock::Custom);
        auto Probe=W->SpawnActor<AStaticMeshActor>();Probe->SetActorLabel(TEXT("TwinNavIdleProbe"));
        Probe->SetActorLocation(Center+FVector(100000,0,10000));Probe->GetStaticMeshComponent()->SetStaticMesh(TestPlatform->GetStaticMeshComponent()->GetStaticMesh());
        N->UpdateActorAndComponentsInNavOctree(*Probe);S->Dirty(Probe);Ok=S->Sync() && Ok;
        const FString Blocked=S->Database.ReadMetadata(TEXT("navigation_state"));
        Pump(1.5);const int64 LockedRevision=S->Database.Revision;Pump(1.5);
        auto BackgroundTick=[&](){bool Requested=false;for(const auto& D:GEditor->ShouldDisableCPUThrottlingDelegates)if(D.IsBoundToObject(S))Requested|=D.Execute();return Requested;};
        if(BackgroundTick())return 366; // A blocked native build must not force busy idle.
        const bool NoChurn=LockedRevision==S->Database.Revision;
        const bool StayedStale=S->Database.ReadMetadata(TEXT("navigation_state"))!=TEXT("AVAILABLE");
        N->RemoveNavigationBuildLock(ENavigationBuildLock::Custom,UNavigationSystemV1::ELockRemovalRebuildAction::NoRebuild);
        Pump(3);const FString Recovered=S->Database.ReadMetadata(TEXT("navigation_state"));
        const int32 RecoveryEvents=*Events;
        Probe->GetStaticMeshComponent()->SetCanEverAffectNavigation(false);S->Dirty(Probe);Ok=S->Sync() && Ok;Pump(2);
        const FString Disabled=S->Database.ReadMetadata(TEXT("navigation_state"));
        const int64 SettledRevision=S->Database.Revision;Pump(1.5);const bool IdleStable=SettledRevision==S->Database.Revision;
        if(BackgroundTick())return 367;
        // Native work may start without an actor change or generation-finished
        // event. It still needs CPU and must invalidate an idle exported cache.
        const int32 BeforeNativeOnly=*Events;
        N->AddDirtyArea(FBox(Center+FVector(200000,0,0),Center+FVector(200100,100,100)),ENavigationDirtyFlag::All,TEXT("SpatialTwinNativeOnlyProbe"));
        if(!BackgroundTick())return 375;
        N->AddNavigationBuildLock(ENavigationBuildLock::Custom);Pump(1.5);
        const bool NativeOnlyStale=S->Database.ReadMetadata(TEXT("navigation_state"))!=TEXT("AVAILABLE");
        if(!NativeOnlyStale || BackgroundTick())return 376;
        N->RemoveNavigationBuildLock(ENavigationBuildLock::Custom,UNavigationSystemV1::ELockRemovalRebuildAction::NoRebuild);Pump(3);
        const bool NativeOnlyRecovered=S->Database.ReadMetadata(TEXT("navigation_state"))==TEXT("AVAILABLE") && *Events==BeforeNativeOnly && !BackgroundTick();
        if(!NativeOnlyRecovered)return 377;
        for(const auto& Watch:Watches)Watch.Key->OnNavMeshUpdate.Remove(Watch.Value);
        auto Report=MakeShared<FJsonObject>();Report->SetStringField(TEXT("initial"),Initial);Report->SetStringField(TEXT("blocked"),Blocked);
        Report->SetStringField(TEXT("first_scan_locked"),FirstScan);Report->SetBoolField(TEXT("first_scan_stayed_stale"),FirstScanStayedStale);
        Report->SetStringField(TEXT("rescan_locked"),Rescan);Report->SetBoolField(TEXT("cached_navigation_preserved"),CachedPreserved);
        Report->SetBoolField(TEXT("blocked_stays_stale"),StayedStale);Report->SetBoolField(TEXT("blocked_no_revision_churn"),NoChurn);
        Report->SetStringField(TEXT("recovered"),Recovered);Report->SetNumberField(TEXT("recovery_tile_events"),RecoveryEvents);
        Report->SetStringField(TEXT("disabled_navigation"),Disabled);Report->SetBoolField(TEXT("idle_revision_stable"),IdleStable);
        Report->SetStringField(TEXT("native_status"),S->Status());Report->SetNumberField(TEXT("initial_active_tiles"),Active);
        Report->SetBoolField(TEXT("blocked_and_idle_release_background_tick"),true);
        Report->SetBoolField(TEXT("native_only_work_invalidates_and_recovers"),NativeOnlyStale && NativeOnlyRecovered);
        Ok=Ok && FirstScan!=TEXT("AVAILABLE") && FirstScanStayedStale && Rescan!=TEXT("AVAILABLE") && CachedPreserved && Initial==TEXT("AVAILABLE") && Blocked!=TEXT("AVAILABLE") && StayedStale && NoChurn && Recovered==TEXT("AVAILABLE") && RecoveryEvents==0 && Disabled==TEXT("AVAILABLE") && IdleStable;
        Report->SetBoolField(TEXT("passed"),Ok);FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Report),*(Root/TEXT("native-nav-idle-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
        UE_LOG(LogTemp,Display,TEXT("SpatialTwinNavigationIdleTest %s"),*FSpatialTwinDatabase::Json(Report));return Ok?0:22;
    }
    if(ProjectedLinkFixture)
    {
        FHitResult Hit;const FVector Start=Center+FVector(250,0,100);
        const bool Found=W->LineTraceSingleByChannel(Hit,Start,Start-FVector(0,0,200),ECC_WorldStatic,FCollisionQueryParams(SCENE_QUERY_STAT(SpatialTwinProjectionTest),true,TestLink));
        UE_LOG(LogTemp,Display,TEXT("SpatialTwinProjectionTest physics=%d hit=%d impact=%s"),W->GetPhysicsScene()!=nullptr,Found,*Hit.ImpactPoint.ToString());
        if(!Found || !FMath::IsNearlyEqual(Hit.ImpactPoint.Z,Center.Z,.01))return 15;
        N->UpdateActorAndComponentsInNavOctree(*TestLink);N->ProcessPendingOctreeUpdates();if(!BuildAndWait())return 16;
    }
    auto S=NewObject<USpatialTwinSubsystem>();S->MapId=Map;auto Plugin=IPluginManager::Get().FindPlugin(TEXT("UnrealSpatialTwin"));FString Schema;
    if(!Plugin || !FFileHelper::LoadFileToString(Schema,*(Plugin->GetBaseDir()/TEXT("Resources/schema.sql"))) || !S->Database.Open(Root,Schema) || !S->Database.Begin(true,Map))return 7;
    S->Database.Metadata(TEXT("project_id"),FApp::GetProjectName());S->Database.Metadata(TEXT("engine_version"),FEngineVersion::Current().ToString());S->Database.Metadata(TEXT("engine_directory"),FPaths::ConvertRelativePathToFull(FPaths::EngineDir()));S->Database.Metadata(TEXT("native_library"),FPaths::ConvertRelativePathToFull(FModuleManager::Get().GetModuleFilename(TEXT("SpatialTwinCore"))));S->Database.Metadata(TEXT("coverage"),TEXT("READ_ONLY_NAVIGATION_TEST_REGION"));
    S->Database.Metadata(TEXT("navigation_owners_version"),TEXT("2"));S->Database.Metadata(TEXT("collision_bounds_version"),TEXT("2"));
    for(TActorIterator<AActor> It(W);It;++It)if(*It!=Anchor && It->GetComponentsBoundingBox(true).Intersect(Region) && !S->ExportActor(*It))return 8;
    TArray<TSharedPtr<FJsonObject>> LegacyOwners;
    if(FParse::Param(*Params,TEXT("LegacyRasterFixture")))
    {
        TArray<FString> Sources;
        {FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE kind IN ('Component','NavigationInput') AND json_type(source,'$.navigation_modifiers')='array'"));while(Q.Step()==ESTSQLiteStepResult::Row){FString Source;Q.GetColumnValueByIndex(0,Source);Sources.Add(Source);}}
        for(const auto& Source:Sources)
        {
            TSharedPtr<FJsonObject> Before,E;FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Before);FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),E);LegacyOwners.Add(Before);
            for(const TCHAR* Field:{TEXT("navigation_fill_underneath"),TEXT("navigation_filled_convex"),TEXT("navigation_mask_fill_underneath")})E->RemoveField(Field);
            for(auto Value:E->GetArrayField(TEXT("navigation_modifiers")))Value->AsObject()->RemoveField(TEXT("mask_fill_underneath"));
            if(!S->Database.Put(E))return 19;
        }
        if(LegacyOwners.IsEmpty())return 20;
    }
    extern bool ExportSpatialTwinNavigation(UWorld*,FSpatialTwinDatabase&);
    if(!ExportSpatialTwinNavigation(W,S->Database) || !S->Database.Commit(Map,true))return 9;
    for(const auto& Before:LegacyOwners)
    {
        FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Before->GetStringField(TEXT("id")));FString Source;TSharedPtr<FJsonObject> After;
        if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,Source) || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),After) || !FJsonValue::CompareEqual(FJsonValueObject(Before),FJsonValueObject(After)))return 21;
    }
    auto Report=MakeShared<FJsonObject>();Report->SetStringField(TEXT("state"),TEXT("MEASURED"));Report->SetNumberField(TEXT("active_tiles"),Active);TArray<TSharedPtr<FJsonValue>> Samples;
    Report->SetNumberField(TEXT("legacy_rasterization_owners_restored"),LegacyOwners.Num());
    for(TActorIterator<ARecastNavMesh> It(W);It;++It)
    {
        FNavLocation P;const bool Found=It->ProjectPoint(Center,P,It->GetConfig().DefaultQueryExtent);auto R=MakeShared<FJsonObject>();R->SetStringField(TEXT("agent"),It->GetConfig().Name.ToString());R->SetBoolField(TEXT("found"),Found);
        if(Found)
        {
            auto Point=[](const FVector& V)->TArray<TSharedPtr<FJsonValue>>{return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(V.Z)};};
            R->SetArrayField(TEXT("position"),Point(P.Location));FNavLocation End;const FVector RequestedEnd=Center+FVector(LinkFixture?900:300,0,0);
            R->SetArrayField(TEXT("requested_end"),Point(RequestedEnd));R->SetBoolField(TEXT("end_found"),It->ProjectPoint(RequestedEnd,End,It->GetConfig().DefaultQueryExtent));
            if(R->GetBoolField(TEXT("end_found")))
            {
                R->SetArrayField(TEXT("projected_end"),Point(End.Location));FPathFindingQuery Query(Anchor,**It,P.Location,End.Location,It->GetDefaultQueryFilter());Query.bAllowPartialPaths=false;
                auto Path=N->FindPathSync(It->GetConfig(),Query);bool Reachable=Path.IsSuccessful() && Path.Path.IsValid() && !Path.IsPartial();R->SetBoolField(TEXT("reachable"),Reachable);
                if(Reachable){R->SetNumberField(TEXT("path_cost"),Path.Path->GetCost());TArray<TSharedPtr<FJsonValue>> Points;for(const auto& V:Path.Path->GetPathPoints())Points.Add(MakeShared<FJsonValueArray>(Point(V.Location)));R->SetArrayField(TEXT("path"),Points);}
            }
        }
        Samples.Add(MakeShared<FJsonValueObject>(R));
    }
    Report->SetArrayField(TEXT("native_project_point"),Samples);
    if(FillFixture)
    {
        auto Project=[&]()
        {
            TArray<TSharedPtr<FJsonValue>> Values;
            for(TActorIterator<ARecastNavMesh> It(W);It;++It)for(FVector Offset:{FVector(0,0,0),FVector(0,0,300),FVector(900,0,300)})
            {
                FNavLocation P;auto R=MakeShared<FJsonObject>();const FVector Point=Center+Offset;
                R->SetStringField(TEXT("agent"),It->GetConfig().Name.ToString());R->SetArrayField(TEXT("requested"),{MakeShared<FJsonValueNumber>(Point.X),MakeShared<FJsonValueNumber>(Point.Y),MakeShared<FJsonValueNumber>(Point.Z)});
                R->SetBoolField(TEXT("found"),It->ProjectPoint(Point,P,FVector(20,20,20)));Values.Add(MakeShared<FJsonValueObject>(R));
            }
            return Values;
        };
        AActor* Changed=MaskFixture?static_cast<AActor*>(TestMask):static_cast<AActor*>(TestPlatform);
        Report->SetBoolField(TEXT("mask_fixture"),MaskFixture);Report->SetArrayField(TEXT("fill_before"),Project());Report->SetStringField(TEXT("changed_actor_id"),S->ActorId(Changed));
        const FVector Moved=Changed->GetActorLocation()+FVector(900,0,0);Report->SetArrayField(TEXT("changed_position"),{MakeShared<FJsonValueNumber>(Moved.X),MakeShared<FJsonValueNumber>(Moved.Y),MakeShared<FJsonValueNumber>(Moved.Z)});
        Changed->SetActorLocation(Moved);N->UpdateActorAndComponentsInNavOctree(*Changed);N->ProcessPendingOctreeUpdates();if(!BuildAndWait())return 17;
        Report->SetArrayField(TEXT("fill_after"),Project());
    }
    if(TestLink)
    {
        Report->SetStringField(TEXT("link_actor_id"),S->ActorId(TestLink));
        AActor* Changed=ProjectedLinkFixture?static_cast<AActor*>(TestPlatform):static_cast<AActor*>(TestLink);
        Report->SetStringField(TEXT("changed_actor_id"),S->ActorId(Changed));Report->SetBoolField(TEXT("projected_link_fixture"),ProjectedLinkFixture);
        Changed->SetActorLocation(Changed->GetActorLocation()+(ProjectedLinkFixture?FVector(0,0,-100):FVector(0,2000,0)));
        const FVector Moved=Changed->GetActorLocation();Report->SetArrayField(TEXT("link_moved_position"),{MakeShared<FJsonValueNumber>(Moved.X),MakeShared<FJsonValueNumber>(Moved.Y),MakeShared<FJsonValueNumber>(Moved.Z)});
        N->UpdateActorAndComponentsInNavOctree(*Changed);N->ProcessPendingOctreeUpdates();if(!BuildAndWait())return 13;
        TArray<TSharedPtr<FJsonValue>> After;
        for(TActorIterator<ARecastNavMesh> It(W);It;++It)
        {
            FNavLocation A,B;if(!It->ProjectPoint(Center,A,It->GetConfig().DefaultQueryExtent) || !It->ProjectPoint(Center+FVector(900,0,0),B,It->GetConfig().DefaultQueryExtent))return 14;
            FPathFindingQuery Query(Anchor,**It,A.Location,B.Location,It->GetDefaultQueryFilter());Query.bAllowPartialPaths=false;auto Path=N->FindPathSync(It->GetConfig(),Query);
            auto R=MakeShared<FJsonObject>();R->SetStringField(TEXT("agent"),It->GetConfig().Name.ToString());R->SetBoolField(TEXT("reachable"),Path.IsSuccessful() && Path.Path.IsValid() && !Path.IsPartial());After.Add(MakeShared<FJsonValueObject>(R));
        }
        Report->SetArrayField(TEXT("native_after_moving_link"),After);
    }
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Report),*(Root/TEXT("native-navigation-test.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    S->Database.DB.Close();return 0;
}
