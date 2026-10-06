#pragma once
#include "CoreMinimal.h"
#include "Dom/JsonObject.h"
#include "SpatialTwinSQLite.h"

class SPATIALTWINCORE_API FSpatialTwinDatabase
{
public:
    ~FSpatialTwinDatabase();
    void* WriterLease=nullptr;
    FSpatialTwinSQLiteDatabase DB;
    FString Root, Generation, Error, BuildingMap;
    int64 Revision = 0;
    bool bChanged = false;
    TMap<FString,TSharedPtr<FJsonObject>> RelationBaseline;
    TSet<FString> ExistingRelations;
    TMap<FString,int64> OriginalEntityRevisions;
    void TrackRelations(const FString& Source);
    void TrackRelation(const FString& Source,const FString& Target,const FString& Kind);
    bool Open(const FString& Directory, const FString& Schema);
    bool UpgradeCollisionBounds(const FString& Map);
    void Close();
    bool Begin(bool bFull, const FString& Map);
    bool Put(const TSharedPtr<FJsonObject>& Entity);
    bool Delete(const FString& Id);
    bool DeleteActor(const FString& Id);
    bool PruneActor(const FString& Id);
    bool ResetActorRelations(const FString& Id);
    bool Commit(const FString& Map, bool bFull);
    void Rollback();
    bool Exec(const FString& Sql);
    bool Metadata(const FString& Key, const FString& Value);
    FString ReadMetadata(const FString& Key);
    bool Relation(const FString& From, const FString& To, const FString& Kind);
    bool Geometry(const FString& Hash, const FString& Path, const FString& Meta);
    static FString Json(const TSharedPtr<FJsonObject>& Value);
};
