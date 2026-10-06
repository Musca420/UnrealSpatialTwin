#include "SpatialTwinSubsystem.h"
#include "Editor.h"
#include "WorldPartition/WorldPartition.h"
#include "WorldPartition/WorldPartitionRuntimeHash.h"
#include "WorldPartition/WorldPartitionStreamingPolicy.h"
#include "WorldPartition/WorldPartitionRuntimeLevelStreamingCell.h"
#include "WorldPartition/WorldPartitionStreamingGeneration.h"
#include "UObject/StrongObjectPtr.h"
#include "UObject/UnrealType.h"
#include "Misc/FileHelper.h"

bool USpatialTwinSubsystem::RefreshPartition()
{
    if(!bReady || bScanning || GEditor->PlayWorld)return false;
    auto World=GEditor->GetEditorWorldContext().World();auto WP=World?World->GetWorldPartition():nullptr;
    if(!World || World->GetOutermost()->GetName()!=MapId)return false;
    if(!WP || !WP->RuntimeHash){bPartitionDirty=false;return true;}
    const double Started=FPlatformTime::Seconds();bScanning=true;
    // Generate engine-derived layout on a private transient hash. Never replace
    // or flush the editor's runtime hash/streaming policy, or export actor geometry.
    TStrongObjectPtr<UWorldPartitionRuntimeHash> Hash(DuplicateObject<UWorldPartitionRuntimeHash>(WP->RuntimeHash,WP,NAME_None));
    Hash->SetFlags(RF_Transient);Hash->FlushStreamingContent();
    auto Property=FindFProperty<FClassProperty>(WP->GetClass(),TEXT("WorldPartitionStreamingPolicyClass"));
    UClass* PolicyClass=Property?Cast<UClass>(Property->GetObjectPropertyValue_InContainer(WP)):nullptr;
    if(!PolicyClass || PolicyClass->HasAnyClassFlags(CLASS_Abstract))
    {Database.Error=TEXT("Native streaming policy class unavailable");bScanning=false;bPartitionDirty=false;return false;}
    TStrongObjectPtr<UWorldPartitionStreamingPolicy> Policy(NewObject<UWorldPartitionStreamingPolicy>(WP,PolicyClass,NAME_None,RF_Transient));
    UWorldPartition::FGenerateStreamingParams Params;UWorldPartition::FGenerateStreamingContext Output;
    Params.SetContainerInstanceCollection(*WP,FStreamingGenerationContainerInstanceCollection::ECollectionType::BaseAndEDLs).SetOutputLogType(TEXT(""));
    auto Context=WP->GenerateStreamingGenerationContext(Params,Output);
    bool Ok=Context && Hash->GenerateStreaming(Policy.Get(),Context.Get(),nullptr);
    const double Generated=FPlatformTime::Seconds();
    int32 Cells=0,Members=0,RetainedMembers=0,AddedMembers=0,RemovedMembers=0;
    if(Ok)Ok=Database.Begin(false,MapId);
    if(Ok)
    {
        TMap<FString,TSet<FString>> Previous;
        {FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT source,target FROM relationships WHERE kind='IN_PARTITION_CELL'"));
         ESTSQLiteStepResult Step;while((Step=Q.Step())==ESTSQLiteStepResult::Row){FString A,B;Q.GetColumnValueByIndex(0,A);Q.GetColumnValueByIndex(1,B);Previous.FindOrAdd(A).Add(B);}
         if(Step!=ESTSQLiteStepResult::Done){Database.Error=Database.DB.GetLastError();Ok=false;}}
        Hash->ForEachStreamingCells([&](const UWorldPartitionRuntimeCell* Cell)
        {
            if(!Ok)return false;++Cells;
            const FString Id=MapId+TEXT(":cell:")+Cell->GetGuid().ToString(EGuidFormats::Digits);
            auto E=MakeShared<FJsonObject>();E->SetStringField(TEXT("id"),Id);E->SetStringField(TEXT("kind"),TEXT("WorldPartitionCell"));E->SetStringField(TEXT("parent_id"),MapId);
            E->SetStringField(TEXT("label"),Cell->GetDebugName());E->SetStringField(TEXT("level_package"),Cell->GetLevelPackageName().ToString());
            E->SetStringField(TEXT("source_type"),TEXT("native_descriptor_generation"));E->SetStringField(TEXT("class"),Cell->GetClass()->GetPathName());
            const FBox B=Cell->GetStreamingBounds();if(B.IsValid)
            {auto V=[](const FVector& P){return TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueNumber>(P.X),MakeShared<FJsonValueNumber>(P.Y),MakeShared<FJsonValueNumber>(P.Z)};};E->SetArrayField(TEXT("bounds"),{MakeShared<FJsonValueArray>(V(B.Min)),MakeShared<FJsonValueArray>(V(B.Max))});}
            Ok=Database.Put(E) && Database.Relation(MapId,Id,TEXT("CONTAINS"));
            if(auto Level=Cast<UWorldPartitionRuntimeLevelStreamingCell>(Cell))for(const auto& Mapping:Level->GetPackages())
            {
                const FString Actor=MapId+TEXT(":actor:")+Mapping.ActorInstanceGuid.ToString(EGuidFormats::Digits);
                // Unchanged membership needs no SQL write or revision tracking.
                if(auto Existing=Previous.Find(Actor);Existing && Existing->Remove(Id)){++Members;++RetainedMembers;continue;}
                FSpatialTwinSQLiteStatement Exists(Database.DB,TEXT("SELECT 1 FROM entities WHERE id=? AND kind='Actor'"));Exists.SetBindingValueByIndex(1,Actor);
                auto Step=Exists.Step();
                if(Step==ESTSQLiteStepResult::Row){Ok=Database.Relation(Actor,Id,TEXT("IN_PARTITION_CELL")) && Ok;++Members;++AddedMembers;}
                else if(Step!=ESTSQLiteStepResult::Done){Database.Error=Database.DB.GetLastError();Ok=false;}
            }
            return Ok;
        });
        for(const auto& Entry:Previous)for(const auto& Cell:Entry.Value)
        {
            Database.TrackRelation(Entry.Key,Cell,TEXT("IN_PARTITION_CELL"));
            FSpatialTwinSQLiteStatement D(Database.DB,TEXT("DELETE FROM relationships WHERE source=? AND target=? AND kind='IN_PARTITION_CELL'"));
            D.SetBindingValueByIndex(1,Entry.Key);D.SetBindingValueByIndex(2,Cell);Ok=(D.Step()==ESTSQLiteStepResult::Done) && Ok;++RemovedMembers;
        }
        TArray<FString> Stale;{FSpatialTwinSQLiteStatement Q(Database.DB,TEXT("SELECT id FROM entities WHERE kind='WorldPartitionCell' AND generation<>?"));Q.SetBindingValueByIndex(1,Database.Generation);while(Q.Step()==ESTSQLiteStepResult::Row){FString Id;Q.GetColumnValueByIndex(0,Id);Stale.Add(Id);}}
        for(const auto& Id:Stale)Ok=Database.Delete(Id) && Ok;
        // Public UE generation uses saved descriptors, unlike PIE's private
        // unsaved-actor path. Expose that limit instead of certifying unsaved cells.
        FSpatialTwinSQLiteStatement Dirty(Database.DB,TEXT("SELECT 1 FROM entities WHERE kind='Actor' AND json_extract(source,'$.package_dirty')=1 LIMIT 1"));
        const bool Unsaved=Dirty.Step()==ESTSQLiteStepResult::Row;
        Ok=Database.Metadata(TEXT("partition_cell_state"),Unsaved?TEXT("CURRENT_SAVED_DESCRIPTORS"):TEXT("CURRENT")) && Ok;
        if(Ok)Ok=Database.Commit(MapId,false);else Database.Rollback();
    }
    if(!Ok && Database.Error.IsEmpty())Database.Error=TEXT("Native partition layout generation failed");
    // A failure is explicit; don't block the editor by retrying every tick.
    bPartitionDirty=false;bScanning=false;
    auto Metrics=MakeShared<FJsonObject>();Metrics->SetBoolField(TEXT("success"),Ok);Metrics->SetNumberField(TEXT("seconds"),FPlatformTime::Seconds()-Started);
    Metrics->SetNumberField(TEXT("cells"),Cells);Metrics->SetNumberField(TEXT("members"),Members);Metrics->SetNumberField(TEXT("revision"),Database.Revision);Metrics->SetBoolField(TEXT("actor_geometry_rescan"),false);
    Metrics->SetNumberField(TEXT("generation_seconds"),Generated-Started);Metrics->SetNumberField(TEXT("database_seconds"),FPlatformTime::Seconds()-Generated);
    Metrics->SetNumberField(TEXT("retained_members"),RetainedMembers);Metrics->SetNumberField(TEXT("added_members"),AddedMembers);Metrics->SetNumberField(TEXT("removed_members"),RemovedMembers);
    FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Metrics),*(Database.Root/TEXT("last_partition_metrics.json")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM);
    return Ok;
}
