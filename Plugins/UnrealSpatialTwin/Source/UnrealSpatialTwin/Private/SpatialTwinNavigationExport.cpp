#include "SpatialTwinDatabase.h"
#include "SpatialTwinNavigation.h"
#include "EngineUtils.h"
#include "NavMesh/RecastNavMesh.h"
#include "NavMesh/RecastNavMeshGenerator.h"
#include "Detour/DetourNavMesh.h"
#include "Misc/FileHelper.h"
#include "Misc/SecureHash.h"
#include "Misc/EngineVersion.h"
#include "HAL/FileManager.h"
#include "AI/Navigation/NavQueryFilter.h"
#include "NavAreas/NavArea.h"
#include "NavigationSystem.h"
#include "NavigationOctree.h"

bool SpatialTwinNavigationPending(UNavigationSystemV1* Nav)
{
    return Nav && (Nav->IsNavigationOctreeLocked() || Nav->IsNavigationBuildingLocked() ||
        Nav->IsNavigationBuildInProgress() || Nav->HasDirtyAreasQueued() ||
        !Nav->PendingNavBoundsUpdates.IsEmpty() || !Nav->NavDataRegistrationQueue.IsEmpty());
}

bool ExportSpatialTwinNavigation(UWorld* World,FSpatialTwinDatabase& DB)
{
    IFileManager::Get().MakeDirectory(*(DB.Root/TEXT("navigation")),true);
    bool Configured=false,Exported=false;
    // Audit actual octree owners, including custom sub-elements. Bounds let an
    // offline rebuild reject only affected incomplete regions; cap diagnostics.
    TArray<TSharedPtr<FJsonValue>> InputGaps;int32 InputGapCount=0,FillMaskCount=0;bool EnrichmentOk=true;
    auto Navigation=FNavigationSystem::GetCurrent<UNavigationSystemV1>(World);
    if(Navigation && !Navigation->IsNavigationOctreeLocked())Navigation->ProcessPendingOctreeUpdates();
    if(TActorIterator<ARecastNavMesh>(World) && SpatialTwinNavigationPending(Navigation))
    {
        // Full scans and direct exports use the same readiness gate as Sync.
        // Retain the last cache across full-scan generation cleanup, but never
        // expose it as current while native work is blocked or unfinished.
        FSpatialTwinSQLiteStatement Keep(DB.DB,TEXT("UPDATE entities SET generation=? WHERE kind='NavRegion'"));Keep.SetBindingValueByIndex(1,DB.Generation);
        return Keep.Step()==ESTSQLiteStepResult::Done && DB.Metadata(TEXT("navigation_state"),TEXT("BUILDING"));
    }
    const auto Octree=Navigation?Navigation->GetNavOctree():nullptr;
    if(Octree)Octree->FindAllElements([&](const FNavigationOctreeElement& Element)
    {
        if(Element.Data->Modifiers.GetMaskFillCollisionUnderneathForNavmesh())++FillMaskCount;
        auto Source=Element.GetSourceElement();auto Object=Source->GetWeakUObject().Get();bool Present=false;
        if(Object && Source->GetHandle()==FNavigationElementHandle(Object))
        {
            const bool Fill=Element.Data->Modifiers.GetFillCollisionUnderneathForNavmesh(),Convex=Element.Data->Modifiers.GetRasterizeAsFilledConvexVolume(),Mask=Element.Data->Modifiers.GetMaskFillCollisionUnderneathForNavmesh();
            // Enrich only cached metadata from the authoritative octree. The
            // indexed lookup returns no JSON for already-current owners and
            // never gathers geometry or rewrites thousands of ISM instances.
            FString Cached;
            {
                FSpatialTwinSQLiteStatement Q(DB.DB,TEXT("SELECT CASE WHEN json_extract(source,'$.navigation_fill_underneath') IS ?2 AND json_extract(source,'$.navigation_filled_convex') IS ?3 AND json_extract(source,'$.navigation_mask_fill_underneath') IS ?4 AND NOT EXISTS(SELECT 1 FROM json_each(source,'$.navigation_modifiers') WHERE json_extract(value,'$.mask_fill_underneath') IS NOT ?4) THEN NULL ELSE source END FROM entities WHERE path=?1 AND kind IN ('Component','NavigationInput') AND json_type(source,'$.navigation_modifiers')='array' LIMIT 1"));
                Q.SetBindingValueByIndex(1,Object->GetPathName());Q.SetBindingValueByIndex(2,int32(Fill));Q.SetBindingValueByIndex(3,int32(Convex));Q.SetBindingValueByIndex(4,int32(Mask));Present=Q.Step()==ESTSQLiteStepResult::Row;
                if(Present)Q.GetColumnValueByIndex(0,Cached);
            }
            if(Present && !Cached.IsEmpty())
            {
                TSharedPtr<FJsonObject> E;
                if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Cached),E)){EnrichmentOk=false;return;}
                E->SetBoolField(TEXT("navigation_fill_underneath"),Fill);E->SetBoolField(TEXT("navigation_filled_convex"),Convex);E->SetBoolField(TEXT("navigation_mask_fill_underneath"),Mask);
                for(auto Value:E->GetArrayField(TEXT("navigation_modifiers")))Value->AsObject()->SetBoolField(TEXT("mask_fill_underneath"),Mask);
                EnrichmentOk=DB.Put(E) && EnrichmentOk;
            }
        }
        if(Present)return;
        ++InputGapCount;if(InputGaps.Num()>=256)return;
        auto Gap=MakeShared<FJsonObject>();Gap->SetStringField(TEXT("object_path"),Object?Object->GetPathName():TEXT("unavailable"));
        const auto Box=Element.Bounds.GetBox();auto Vector=[](FVector V)->TArray<TSharedPtr<FJsonValue>>{return {MakeShared<FJsonValueNumber>(V.X),MakeShared<FJsonValueNumber>(V.Y),MakeShared<FJsonValueNumber>(V.Z)};};
        Gap->SetArrayField(TEXT("bounds"),{MakeShared<FJsonValueArray>(Vector(Box.Min)),MakeShared<FJsonValueArray>(Vector(Box.Max))});
        InputGaps.Add(MakeShared<FJsonValueObject>(Gap));
    });
    if(!EnrichmentOk)return false;
    for(TActorIterator<ARecastNavMesh> It(World);It;++It)
    {
        auto A=*It;Configured=true;const dtNavMesh* M=A->GetRecastMesh();if(!M)continue;Exported|=A->GetNumActiveTiles()>0;
        FString Agent=A->GetConfig().Name.IsNone()?A->GetName():A->GetConfig().Name.ToString(),Temp=DB.Root/TEXT("navigation/build.stn");
        if(!STSaveNavmesh(M,Temp))return false;TArray<uint8> Bytes;FFileHelper::LoadFileToArray(Bytes,*Temp);FSHAHash Hash;FSHA1::HashBuffer(Bytes.GetData(),Bytes.Num(),Hash.Hash);
        FString Rel=TEXT("navigation/")+Hash.ToString().ToLower()+TEXT(".stn"),Final=DB.Root/Rel;
        if(!IFileManager::Get().Move(*Final,*Temp,true))return false;
        auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("id"),TEXT("nav:")+Agent);J->SetStringField(TEXT("kind"),TEXT("NavRegion"));J->SetStringField(TEXT("path"),Rel);J->SetStringField(TEXT("agent"),Agent);J->SetStringField(TEXT("coverage"),TEXT("exported_active_tiles"));J->SetNumberField(TEXT("active_tiles"),A->GetNumActiveTiles());
        J->SetStringField(TEXT("source_object_path"),A->GetPathName());J->SetStringField(TEXT("source_package"),A->GetPackage()->GetName());
        {FSpatialTwinSQLiteStatement Q(DB.DB,TEXT("SELECT id FROM entities WHERE path=? AND kind='Actor'"));Q.SetBindingValueByIndex(1,A->GetPathName());FString Id;if(Q.Step()==ESTSQLiteStepResult::Row && Q.GetColumnValueByIndex(0,Id))J->SetStringField(TEXT("source_actor_id"),Id);}
        auto Capacity=MakeShared<FJsonObject>();Capacity->SetBoolField(TEXT("fixed_tile_pool"),A->bFixedTilePoolSize);Capacity->SetNumberField(TEXT("tile_pool_size"),A->TilePoolSize);Capacity->SetNumberField(TEXT("tile_number_hard_limit"),A->TileNumberHardLimit);J->SetObjectField(TEXT("capacity_settings"),Capacity);
        J->SetStringField(TEXT("input_coverage"),Octree?TEXT("octree_audited"):TEXT("octree_unavailable"));
        // This shipping build ignores ModifyWalkableFloorZ's return value in
        // Recast rasterization. Preserve source overrides, but match actual
        // navigation, not CharacterMovement's different slope semantics.
        // Other builds stay unknown until their native fixture is verified.
        if(FEngineVersion::Current().ToString()==TEXT("5.8.2-56702186+++UE5+Release-5.8"))
            J->SetStringField(TEXT("walkable_slope_policy"),TEXT("ue5.8.2-56702186-global-agent-slope"));
        J->SetNumberField(TEXT("input_gap_count"),InputGapCount);J->SetArrayField(TEXT("input_gaps"),InputGaps);
        if(Octree){J->SetNumberField(TEXT("fill_underneath_mask_count"),FillMaskCount);J->SetNumberField(TEXT("rasterization_mask_version"),1);}
        auto S=MakeShared<FJsonObject>();S->SetNumberField(TEXT("cell_size"),A->GetCellSize(ENavigationDataResolution::Default));S->SetNumberField(TEXT("cell_height"),A->GetCellHeight(ENavigationDataResolution::Default));S->SetNumberField(TEXT("height"),A->GetConfig().AgentHeight);S->SetNumberField(TEXT("radius"),A->GetConfig().AgentRadius);S->SetNumberField(TEXT("climb"),A->GetAgentMaxStepHeight(ENavigationDataResolution::Default));S->SetNumberField(TEXT("slope"),A->AgentMaxSlope);S->SetNumberField(TEXT("simplification_error"),A->MaxSimplificationError);
        J->SetObjectField(TEXT("settings"),S);
        S->SetNumberField(TEXT("agent_index"),Navigation?Navigation->GetSupportedAgentIndex(A):0);
        J->SetNumberField(TEXT("off_mesh_link_flag"),ARecastNavMesh::GetNavLinkFlag());
        auto Params=M->getParams();auto P=MakeShared<FJsonObject>();P->SetArrayField(TEXT("origin"),{MakeShared<FJsonValueNumber>(Params->orig[0]),MakeShared<FJsonValueNumber>(Params->orig[1]),MakeShared<FJsonValueNumber>(Params->orig[2])});P->SetNumberField(TEXT("tile_width"),Params->tileWidth);P->SetNumberField(TEXT("tile_height"),Params->tileHeight);P->SetNumberField(TEXT("max_tiles"),Params->maxTiles);P->SetNumberField(TEXT("max_polys"),Params->maxPolys);J->SetObjectField(TEXT("detour_parameters"),P);
        P->SetNumberField(TEXT("walkable_height"),Params->walkableHeight);P->SetNumberField(TEXT("walkable_radius"),Params->walkableRadius);P->SetNumberField(TEXT("walkable_climb"),Params->walkableClimb);
        TArray<TSharedPtr<FJsonValue>> Quantization;for(auto Resolution:Params->resolutionParams)Quantization.Add(MakeShared<FJsonValueNumber>(Resolution.bvQuantFactor));P->SetArrayField(TEXT("resolution_quantization"),Quantization);J->SetNumberField(TEXT("cache_version"),2);
        S->SetNumberField(TEXT("min_region_area"),A->MinRegionArea);S->SetNumberField(TEXT("merge_region_size"),A->MergeRegionSize);S->SetNumberField(TEXT("region_partitioning"),(int32)A->RegionPartitioning.GetValue());S->SetNumberField(TEXT("layer_partitioning"),(int32)A->LayerPartitioning.GetValue());S->SetBoolField(TEXT("voxel_filtering"),A->bPerformVoxelFiltering);S->SetBoolField(TEXT("filter_low_spans"),A->bFilterLowSpanSequences);S->SetBoolField(TEXT("mark_low_height_areas"),A->bMarkLowHeightAreas);
        S->SetNumberField(TEXT("layer_chunk_splits"),A->LayerChunkSplits);S->SetNumberField(TEXT("region_chunk_splits"),A->RegionChunkSplits);S->SetNumberField(TEXT("simplification_elevation_ratio"),A->SimplificationElevationRatio);S->SetNumberField(TEXT("ledge_slope_filter"),(int32)A->LedgeSlopeFilterMode);S->SetBoolField(TEXT("filter_low_from_cache"),A->bFilterLowSpanFromTileCache);
        const FVector Extent=A->GetConfig().DefaultQueryExtent;J->SetArrayField(TEXT("query_extent"),{MakeShared<FJsonValueNumber>(Extent.X),MakeShared<FJsonValueNumber>(Extent.Y),MakeShared<FJsonValueNumber>(Extent.Z)});
        TArray<FBox> Bounds;auto NavSys=FNavigationSystem::GetCurrent<UNavigationSystemV1>(World);if(NavSys)NavSys->GetNavigationBoundsForNavData(*A,Bounds);TArray<TSharedPtr<FJsonValue>> Boxes;
        for(const auto& Box:Bounds){Boxes.Add(MakeShared<FJsonValueArray>(TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueArray>(TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueNumber>(Box.Min.X),MakeShared<FJsonValueNumber>(Box.Min.Y),MakeShared<FJsonValueNumber>(Box.Min.Z)}),MakeShared<FJsonValueArray>(TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueNumber>(Box.Max.X),MakeShared<FJsonValueNumber>(Box.Max.Y),MakeShared<FJsonValueNumber>(Box.Max.Z)})}));}J->SetArrayField(TEXT("navigation_bounds"),Boxes);
        auto Filters=MakeShared<FJsonObject>();auto F=A->GetDefaultQueryFilter();float Costs[64],Fixed[64];F->GetAllAreaCosts(Costs,Fixed,64);TArray<TSharedPtr<FJsonValue>> C,FC;
        for(int I=0;I<64;++I){C.Add(MakeShared<FJsonValueNumber>(Costs[I]));FC.Add(MakeShared<FJsonValueNumber>(Fixed[I]));}
        Filters->SetArrayField(TEXT("costs"),C);Filters->SetArrayField(TEXT("fixed_costs"),FC);Filters->SetNumberField(TEXT("include_flags"),F->GetIncludeFlags());Filters->SetNumberField(TEXT("exclude_flags"),F->GetExcludeFlags());J->SetObjectField(TEXT("filter"),Filters);
        // Match FPImplRecastNavMesh::OnAreaCostChanged, including equal-score
        // ordering. Detour uses this native default order when snapping links.
        struct FLinkCost{double Score;int32 Index;bool operator<(const FLinkCost& Other)const{return Score<Other.Score;}};
        TArray<FLinkCost> Sorted;for(int32 I=0;I<64;++I)Sorted.Add({double(Costs[I])+double(Fixed[I]),I});Sorted.Sort();
        int32 Order[64];for(int32 I=0;I<64;++I)Order[Sorted[I].Index]=I;
        TArray<TSharedPtr<FJsonValue>> LinkOrder;for(int32 Value:Order)LinkOrder.Add(MakeShared<FJsonValueNumber>(Value));J->SetArrayField(TEXT("link_area_order"),LinkOrder);
        auto AreaClasses=MakeShared<FJsonObject>();TArray<FSupportedAreaData> Areas;A->GetSupportedAreas(Areas);for(const auto& Area:Areas)if(Area.AreaClass){auto Data=MakeShared<FJsonObject>();Data->SetNumberField(TEXT("id"),Area.AreaID);Data->SetNumberField(TEXT("flags"),Area.AreaClass->GetDefaultObject<UNavArea>()->GetAreaFlags());AreaClasses->SetObjectField(Area.AreaClass->GetPathName(),Data);}J->SetObjectField(TEXT("areas"),AreaClasses);
        TArray<TSharedPtr<FJsonValue>> Tiles;for(int I=0;I<M->getMaxTiles();++I){auto T=M->getTile(I);if(!T || !T->header)continue;auto E=MakeShared<FJsonObject>();E->SetNumberField(TEXT("x"),T->header->x);E->SetNumberField(TEXT("y"),T->header->y);E->SetNumberField(TEXT("layer"),T->header->layer);E->SetNumberField(TEXT("minimum_height"),T->header->bmin[1]);E->SetNumberField(TEXT("maximum_height"),T->header->bmax[1]);Tiles.Add(MakeShared<FJsonValueObject>(E));}J->SetArrayField(TEXT("tiles"),Tiles);
        if(!DB.Put(J))return false;
    }
    TArray<FString> Removed;
    {FSpatialTwinSQLiteStatement Q(DB.DB,TEXT("SELECT id FROM entities WHERE kind='NavRegion' AND generation<>?"));Q.SetBindingValueByIndex(1,DB.Generation);while(Q.Step()==ESTSQLiteStepResult::Row){FString Id;Q.GetColumnValueByIndex(0,Id);Removed.Add(Id);}}
    for(const auto& Id:Removed)if(!DB.Delete(Id))return false;
    return DB.Metadata(TEXT("navigation_state"),Exported?TEXT("AVAILABLE"):(Configured?TEXT("UNBUILT"):TEXT("NOT_CONFIGURED")));
}
