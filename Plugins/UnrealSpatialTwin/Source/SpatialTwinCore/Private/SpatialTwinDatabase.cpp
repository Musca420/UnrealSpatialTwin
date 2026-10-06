#include "SpatialTwinDatabase.h"
#include "SpatialTwinSQLite.h"
#include "Serialization/JsonSerializer.h"
#include "Policies/CondensedJsonPrintPolicy.h"
#include "Misc/Paths.h"
#include "Misc/FileHelper.h"
#include "Misc/SecureHash.h"
#include "HAL/FileManager.h"
#include "Modules/ModuleManager.h"
#include "Windows/WindowsHWrapper.h"

IMPLEMENT_MODULE(FDefaultModuleImpl, SpatialTwinCore)

FSpatialTwinDatabase::~FSpatialTwinDatabase()
{
    Close();
}
void FSpatialTwinDatabase::Close()
{
    if(DB.IsValid()){DB.Execute(TEXT("ROLLBACK"));DB.Close();}
    if(WriterLease){CloseHandle(WriterLease);WriterLease=nullptr;}
    DB.RequireTransactionWrites=false;
}

FString FSpatialTwinDatabase::Json(const TSharedPtr<FJsonObject>& Value)
{
    FString Result;
    FJsonSerializer::Serialize(Value.ToSharedRef(), TJsonWriterFactory<TCHAR,TCondensedJsonPrintPolicy<TCHAR>>::Create(&Result));
    return Result;
}
bool FSpatialTwinDatabase::Exec(const FString& Sql)
{
    if (DB.Execute(*Sql)) return true;
    Error=DB.GetLastError(); return false;
}
bool FSpatialTwinDatabase::Open(const FString& Directory,const FString& Schema)
{
    Root=Directory; IFileManager::Get().MakeDirectory(*Root,true);
    if(!WriterLease)
    {
        // A process-lifetime OS lease keeps a headless scanner from replacing
        // a live editor's unsaved canonical state. Readers remain independent.
        auto Lease=CreateFileW(*(Root/TEXT("canonical-writer.lock")),GENERIC_READ|GENERIC_WRITE,0,nullptr,OPEN_ALWAYS,FILE_ATTRIBUTE_NORMAL,nullptr);
        if(Lease==INVALID_HANDLE_VALUE){Error=TEXT("Canonical writer already active or Twin directory not writable; use a separate scan root");return false;}
        WriterLease=Lease;
    }
    if (!DB.Open(*(Root/TEXT("world.sqlite")))) { Error=DB.GetLastError(); return false; }
    TArray<FString> Statements;Schema.ParseIntoArray(Statements,TEXT("-- @statement"),true);
    for(int32 I=0;I<Statements.Num();++I)
    {
        if(I==3 && !Exec(TEXT("BEGIN IMMEDIATE")))return false;
        if(!Exec(Statements[I])){DB.Execute(TEXT("ROLLBACK"));return false;}
    }
    if(!Exec(TEXT("COMMIT")))return false;
    {FSpatialTwinSQLiteStatement Journal(DB,TEXT("PRAGMA journal_mode"));FString Mode;if(Journal.Step()!=ESTSQLiteStepResult::Row || !Journal.GetColumnValueByIndex(0,Mode) || Mode!=TEXT("wal")){Error=TEXT("Canonical database requires WAL support");return false;}}
    if (ReadMetadata(TEXT("schema_version"))!=TEXT("1")) { Error=TEXT("Unsupported schema"); return false; }
    Revision=FCString::Atoi64(*ReadMetadata(TEXT("world_revision")));
    DB.RequireTransactionWrites=true;
    return true;
}
FString FSpatialTwinDatabase::ReadMetadata(const FString& Key)
{
    FSpatialTwinSQLiteStatement S(DB,TEXT("SELECT value FROM metadata WHERE key=?"));
    S.SetBindingValueByIndex(1,Key); FString Value;
    if (S.Step()==ESTSQLiteStepResult::Row) S.GetColumnValueByIndex(0,Value);
    return Value;
}
bool FSpatialTwinDatabase::Metadata(const FString& Key,const FString& Value)
{
    FSpatialTwinSQLiteStatement S(DB,TEXT("INSERT INTO metadata(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value"));
    S.SetBindingValueByIndex(1,Key); S.SetBindingValueByIndex(2,Value);
    if (S.Step()==ESTSQLiteStepResult::Done) return true;
    Error=DB.GetLastError(); return false;
}
bool FSpatialTwinDatabase::Begin(bool bFull,const FString& Map)
{
    BuildingMap=Map;
    Generation=FGuid::NewGuid().ToString(EGuidFormats::Digits); bChanged=false; Error.Reset();
    if (!Exec(TEXT("BEGIN IMMEDIATE"))) return false;
    RelationBaseline.Reset();
    ExistingRelations.Reset();
    OriginalEntityRevisions.Reset();
    if(bFull)
    {
        TArray<FString> Sources;{FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT DISTINCT source FROM relationships"));while(Q.Step()==ESTSQLiteStepResult::Row){FString V;Q.GetColumnValueByIndex(0,V);Sources.Add(V);}}
        for(auto Source:Sources)TrackRelations(Source);if(!Exec(TEXT("DELETE FROM relationships"))){Rollback();return false;}
    }
    FSpatialTwinSQLiteStatement S(DB,TEXT("INSERT INTO snapshots VALUES(?,?,?,?,?)"));
    S.SetBindingValueByIndex(1,Generation); S.SetBindingValueByIndex(2,Revision+1);
    S.SetBindingValueByIndex(3,FString(TEXT("BUILDING"))); S.SetBindingValueByIndex(4,FDateTime::UtcNow().ToIso8601()); S.SetBindingValueByIndex(5,Map);
    return S.Step()==ESTSQLiteStepResult::Done;
}
bool FSpatialTwinDatabase::Put(const TSharedPtr<FJsonObject>& E)
{
    const FString Id=E->GetStringField(TEXT("id")),Source=Json(E);
    FString Previous,PreviousGeneration;int64 PreviousRevision=0;
    {
        FSpatialTwinSQLiteStatement Old(DB,TEXT("SELECT source,generation,revision FROM entities WHERE id=?")); Old.SetBindingValueByIndex(1,Id);
        if (Old.Step()==ESTSQLiteStepResult::Row){Old.GetColumnValueByIndex(0,Previous);Old.GetColumnValueByIndex(1,PreviousGeneration);Old.GetColumnValueByIndex(2,PreviousRevision);}
    }
    if(Source==Previous)
    {
        if(PreviousGeneration==Generation)return true;
        FSpatialTwinSQLiteStatement Seen(DB,TEXT("UPDATE entities SET generation=? WHERE id=?"));Seen.SetBindingValueByIndex(1,Generation);Seen.SetBindingValueByIndex(2,Id);
        if(Seen.Step()==ESTSQLiteStepResult::Done)return true;Error=DB.GetLastError();return false;
    }
    if (Source!=Previous)
    {
        if(!OriginalEntityRevisions.Contains(Id))OriginalEntityRevisions.Add(Id,PreviousRevision);
        FSpatialTwinSQLiteStatement Change(DB,TEXT("INSERT INTO changes VALUES(?,?,?,?,?) ON CONFLICT(revision,entity_id) DO UPDATE SET after_json=excluded.after_json"));
        Change.SetBindingValueByIndex(1,Revision+1); Change.SetBindingValueByIndex(2,Id);
        FString Type=Previous.IsEmpty()?TEXT("CREATE"):TEXT("PROPERTY");
        TSharedPtr<FJsonObject> Before;
        if (!Previous.IsEmpty() && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Previous),Before))
        {
            auto A=Before->TryGetField(TEXT("transform")), B=E->TryGetField(TEXT("transform"));
            if (A.IsValid() && B.IsValid() && !FJsonValue::CompareEqual(*A,*B)) Type=TEXT("TRANSFORM");
            else
            {
                auto OldGeometry=Before->TryGetField(TEXT("geometry_hash")),NewGeometry=E->TryGetField(TEXT("geometry_hash"));
                if(OldGeometry.IsValid() && NewGeometry.IsValid() && !FJsonValue::CompareEqual(*OldGeometry,*NewGeometry))Type=TEXT("GEOMETRY");
                else{FString Kind=E->GetStringField(TEXT("kind"));if(Kind==TEXT("Component") || Kind==TEXT("Instance"))Type=TEXT("COMPONENT");else if(Kind==TEXT("Asset") || Kind==TEXT("StaticMesh") || Kind==TEXT("Material"))Type=TEXT("ASSET");}
            }
        }
        Change.SetBindingValueByIndex(3,Type);
        if (Previous.IsEmpty()) Change.SetBindingValueByIndex(4,nullptr); else Change.SetBindingValueByIndex(4,Previous);
        Change.SetBindingValueByIndex(5,Source);
        if (Change.Step()!=ESTSQLiteStepResult::Done) { Error=DB.GetLastError(); return false; }
        bChanged=true;
    }
    FSpatialTwinSQLiteStatement S(DB,TEXT("INSERT INTO entities(id,kind,parent_id,actor_id,asset_id,label,class,path,x0,x1,y0,y1,z0,z1,source,generation,revision) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET kind=excluded.kind,parent_id=excluded.parent_id,actor_id=excluded.actor_id,asset_id=excluded.asset_id,label=excluded.label,class=excluded.class,path=excluded.path,x0=excluded.x0,x1=excluded.x1,y0=excluded.y0,y1=excluded.y1,z0=excluded.z0,z1=excluded.z1,source=excluded.source,generation=excluded.generation,revision=excluded.revision"));
    const TCHAR* Fields[]={TEXT("id"),TEXT("kind"),TEXT("parent_id"),TEXT("actor_id"),TEXT("asset_id"),TEXT("label"),TEXT("class"),TEXT("path")};
    for (int32 I=0;I<8;++I) { FString V; if (E->TryGetStringField(Fields[I],V)) S.SetBindingValueByIndex(I+1,V); else S.SetBindingValueByIndex(I+1,nullptr); }
    const TArray<TSharedPtr<FJsonValue>>* Box=nullptr;
    if ((E->TryGetArrayField(TEXT("bounds"),Box) || E->TryGetArrayField(TEXT("collision_bounds"),Box)) && Box->Num()==2)
    {
        auto Lo=(*Box)[0]->AsArray(),Hi=(*Box)[1]->AsArray();
        const TArray<TSharedPtr<FJsonValue>>* Collision=nullptr;E->TryGetArrayField(TEXT("collision_bounds"),Collision);
        for(int32 I=0;I<3;++I)
        {
            double Min=Lo[I]->AsNumber(),Max=Hi[I]->AsNumber();
            if(Collision && Collision->Num()==2){Min=FMath::Min(Min,(*Collision)[0]->AsArray()[I]->AsNumber());Max=FMath::Max(Max,(*Collision)[1]->AsArray()[I]->AsNumber());}
            S.SetBindingValueByIndex(9+I*2,Min); S.SetBindingValueByIndex(10+I*2,Max);
        }
    }
    else for(int32 I=9;I<=14;++I) S.SetBindingValueByIndex(I,nullptr);
    S.SetBindingValueByIndex(15,Source); S.SetBindingValueByIndex(16,Generation); S.SetBindingValueByIndex(17,Revision+(Source!=Previous));
    if (S.Step()!=ESTSQLiteStepResult::Done) { Error=DB.GetLastError(); return false; }
    return true;
}
bool FSpatialTwinDatabase::Relation(const FString& From,const FString& To,const FString& Kind)
{
    TrackRelation(From,To,Kind);
    FSpatialTwinSQLiteStatement S(DB,TEXT("INSERT OR IGNORE INTO relationships VALUES(?,?,?)"));
    S.SetBindingValueByIndex(1,From); S.SetBindingValueByIndex(2,To); S.SetBindingValueByIndex(3,Kind);
    if(S.Step()==ESTSQLiteStepResult::Done)return true;Error=DB.GetLastError();return false;
}
bool FSpatialTwinDatabase::Geometry(const FString& Hash,const FString& Path,const FString& Meta)
{
    FSpatialTwinSQLiteStatement S(DB,TEXT("INSERT OR IGNORE INTO geometry VALUES(?,?,?)"));
    S.SetBindingValueByIndex(1,Hash); S.SetBindingValueByIndex(2,Path); S.SetBindingValueByIndex(3,Meta);
    if(S.Step()==ESTSQLiteStepResult::Done)return true;Error=DB.GetLastError();return false;
}
void FSpatialTwinDatabase::TrackRelation(const FString& Source,const FString& Target,const FString& Kind)
{
    auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("source"),Source);J->SetStringField(TEXT("target"),Target);J->SetStringField(TEXT("relationship"),Kind);J->SetStringField(TEXT("kind"),TEXT("Relationship"));
    const FString Id=TEXT("relationship:")+FMD5::HashAnsiString(*Json(J));if(RelationBaseline.Contains(Id))return;
    J->SetStringField(TEXT("id"),Id);RelationBaseline.Add(Id,J);
    FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT 1 FROM relationships WHERE source=? AND target=? AND kind=?"));Q.SetBindingValueByIndex(1,Source);Q.SetBindingValueByIndex(2,Target);Q.SetBindingValueByIndex(3,Kind);
    auto Step=Q.Step();if(Step==ESTSQLiteStepResult::Row)ExistingRelations.Add(Id);else if(Step!=ESTSQLiteStepResult::Done)Error=DB.GetLastError();
}
void FSpatialTwinDatabase::TrackRelations(const FString& Source)
{
    FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT target,kind FROM relationships WHERE source=?"));Q.SetBindingValueByIndex(1,Source);
    while(Q.Step()==ESTSQLiteStepResult::Row){FString Target,Kind;Q.GetColumnValueByIndex(0,Target);Q.GetColumnValueByIndex(1,Kind);TrackRelation(Source,Target,Kind);}
}
bool FSpatialTwinDatabase::Delete(const FString& Id)
{
    {FSpatialTwinSQLiteStatement Exists(DB,TEXT("SELECT 1 FROM entities WHERE id=?"));Exists.SetBindingValueByIndex(1,Id);if(Exists.Step()!=ESTSQLiteStepResult::Row)return true;}
    {FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT source,target,kind FROM relationships WHERE source=? OR target=?"));Q.SetBindingValueByIndex(1,Id);Q.SetBindingValueByIndex(2,Id);while(Q.Step()==ESTSQLiteStepResult::Row){FString Source,Target,Kind;Q.GetColumnValueByIndex(0,Source);Q.GetColumnValueByIndex(1,Target);Q.GetColumnValueByIndex(2,Kind);TrackRelation(Source,Target,Kind);}}
    FSpatialTwinSQLiteStatement C(DB,TEXT("INSERT INTO changes SELECT ?,id,'DELETE',source,NULL FROM entities WHERE id=? ON CONFLICT(revision,entity_id) DO UPDATE SET after_json=NULL,type='DELETE'"));
    C.SetBindingValueByIndex(1,Revision+1); C.SetBindingValueByIndex(2,Id);
    if (C.Step()!=ESTSQLiteStepResult::Done) return false;
    FSpatialTwinSQLiteStatement S(DB,TEXT("DELETE FROM entities WHERE id=?")); S.SetBindingValueByIndex(1,Id); if (S.Step()!=ESTSQLiteStepResult::Done) return false;
    FSpatialTwinSQLiteStatement R(DB,TEXT("DELETE FROM relationships WHERE source=? OR target=?")); R.SetBindingValueByIndex(1,Id);R.SetBindingValueByIndex(2,Id);
    bChanged=true; return R.Step()==ESTSQLiteStepResult::Done;
}
bool FSpatialTwinDatabase::DeleteActor(const FString& Id)
{
    TArray<FString> Ids;
    { FSpatialTwinSQLiteStatement S(DB,TEXT("SELECT id FROM entities WHERE id=? OR actor_id=?")); S.SetBindingValueByIndex(1,Id);S.SetBindingValueByIndex(2,Id);
      while(S.Step()==ESTSQLiteStepResult::Row) { FString V;S.GetColumnValueByIndex(0,V);Ids.Add(V); } }
    for(const FString& V:Ids) if(!Delete(V)) return false;
    return true;
}
bool FSpatialTwinDatabase::PruneActor(const FString& Id)
{
    TArray<FString> Stale;
    { FSpatialTwinSQLiteStatement S(DB,TEXT("SELECT id FROM entities WHERE actor_id=? AND generation<>?"));
      S.SetBindingValueByIndex(1,Id); S.SetBindingValueByIndex(2,Generation);
      while(S.Step()==ESTSQLiteStepResult::Row){FString V;S.GetColumnValueByIndex(0,V);Stale.Add(V);} }
    for(const auto& V:Stale)if(!Delete(V))return false;
    return true;
}
bool FSpatialTwinDatabase::ResetActorRelations(const FString& Id)
{
    {FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT source,target,kind FROM relationships WHERE (source=? AND kind<>'IN_PARTITION_CELL') OR source IN (SELECT id FROM entities WHERE actor_id=?) OR (target=? AND kind='CONTAINS')"));Q.SetBindingValueByIndex(1,Id);Q.SetBindingValueByIndex(2,Id);Q.SetBindingValueByIndex(3,Id);while(Q.Step()==ESTSQLiteStepResult::Row){FString Source,Target,Kind;Q.GetColumnValueByIndex(0,Source);Q.GetColumnValueByIndex(1,Target);Q.GetColumnValueByIndex(2,Kind);TrackRelation(Source,Target,Kind);}}
    FSpatialTwinSQLiteStatement S(DB,TEXT("DELETE FROM relationships WHERE (source=? AND kind<>'IN_PARTITION_CELL') OR source IN (SELECT id FROM entities WHERE actor_id=?) OR (target=? AND kind='CONTAINS')"));
    S.SetBindingValueByIndex(1,Id);S.SetBindingValueByIndex(2,Id);
    S.SetBindingValueByIndex(3,Id);
    return S.Step()==ESTSQLiteStepResult::Done;
}
bool FSpatialTwinDatabase::Commit(const FString& Map,bool bFull)
{
    if (bFull)
    {
        TArray<FString> Stale;
        { FSpatialTwinSQLiteStatement S(DB,TEXT("SELECT id FROM entities WHERE generation<>?")); S.SetBindingValueByIndex(1,Generation);
          while(S.Step()==ESTSQLiteStepResult::Row) { FString V;S.GetColumnValueByIndex(0,V);Stale.Add(V); } }
        for(const auto& V:Stale) if(!Delete(V)) { Rollback();return false; }
    }
    if(!Error.IsEmpty()){Rollback();return false;}
    for(const auto& Edge:RelationBaseline)
    {
        const auto& J=Edge.Value;FSpatialTwinSQLiteStatement Exists(DB,TEXT("SELECT 1 FROM relationships WHERE source=? AND target=? AND kind=?"));Exists.SetBindingValueByIndex(1,J->GetStringField(TEXT("source")));Exists.SetBindingValueByIndex(2,J->GetStringField(TEXT("target")));Exists.SetBindingValueByIndex(3,J->GetStringField(TEXT("relationship")));
        auto Step=Exists.Step();if(Step!=ESTSQLiteStepResult::Row && Step!=ESTSQLiteStepResult::Done){Error=DB.GetLastError();Rollback();return false;}
        const bool Before=ExistingRelations.Contains(Edge.Key),After=Step==ESTSQLiteStepResult::Row;if(Before==After)continue;
        const FString Value=Json(J);FSpatialTwinSQLiteStatement Q(DB,TEXT("INSERT INTO changes VALUES(?,?,'RELATION',?,?)"));Q.SetBindingValueByIndex(1,Revision+1);Q.SetBindingValueByIndex(2,Edge.Key);if(Before)Q.SetBindingValueByIndex(3,Value);else Q.SetBindingValueByIndex(3,nullptr);if(After)Q.SetBindingValueByIndex(4,Value);else Q.SetBindingValueByIndex(4,nullptr);
        if(Q.Step()!=ESTSQLiteStepResult::Done){Error=DB.GetLastError();Rollback();return false;}bChanged=true;
    }
    // Extractors can enrich an entity more than once in a transaction. Only
    // the committed before/after state is a canonical change.
    {TArray<FString> NoOps;FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT entity_id FROM changes WHERE revision=? AND before_json IS after_json"));Q.SetBindingValueByIndex(1,Revision+1);
     while(Q.Step()==ESTSQLiteStepResult::Row){FString Id;Q.GetColumnValueByIndex(0,Id);NoOps.Add(Id);}
     for(auto Id:NoOps)if(auto Original=OriginalEntityRevisions.Find(Id)){FSpatialTwinSQLiteStatement Restore(DB,TEXT("UPDATE entities SET revision=? WHERE id=?"));Restore.SetBindingValueByIndex(1,*Original);Restore.SetBindingValueByIndex(2,Id);if(Restore.Step()!=ESTSQLiteStepResult::Done){Error=DB.GetLastError();Rollback();return false;}}}
    {FSpatialTwinSQLiteStatement Remove(DB,TEXT("DELETE FROM changes WHERE revision=? AND before_json IS after_json"));Remove.SetBindingValueByIndex(1,Revision+1);if(Remove.Step()!=ESTSQLiteStepResult::Done){Error=DB.GetLastError();Rollback();return false;}}
    {FSpatialTwinSQLiteStatement Changed(DB,TEXT("SELECT 1 FROM changes WHERE revision=? LIMIT 1"));Changed.SetBindingValueByIndex(1,Revision+1);bChanged=Changed.Step()==ESTSQLiteStepResult::Row;}
    if(bChanged) ++Revision;
    if(!Metadata(TEXT("world_revision"),LexToString(Revision)) || !Metadata(TEXT("last_sync"),FDateTime::UtcNow().ToIso8601())) { Rollback(); return false; }
    FSpatialTwinSQLiteStatement S(DB,TEXT("UPDATE snapshots SET state='READY',revision=?,timestamp=? WHERE id=?"));
    S.SetBindingValueByIndex(1,Revision);S.SetBindingValueByIndex(2,ReadMetadata(TEXT("last_sync")));S.SetBindingValueByIndex(3,Generation);
    if(S.Step()!=ESTSQLiteStepResult::Done || !Exec(TEXT("COMMIT"))) { Rollback();return false; }
    auto Manifest=MakeShared<FJsonObject>();Manifest->SetNumberField(TEXT("schema_version"),1);Manifest->SetNumberField(TEXT("canonical_revision"),Revision);Manifest->SetStringField(TEXT("snapshot_id"),Generation);Manifest->SetStringField(TEXT("state"),TEXT("READY"));Manifest->SetStringField(TEXT("map"),Map);Manifest->SetStringField(TEXT("last_sync"),ReadMetadata(TEXT("last_sync")));
    const FString Path=Root/TEXT("manifest.json");if(FFileHelper::SaveStringToFile(Json(Manifest),*(Path+TEXT(".tmp")),FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM))IFileManager::Get().Move(*Path,*(Path+TEXT(".tmp")),true);
    return true;
}
void FSpatialTwinDatabase::Rollback()
{
    const FString Failure=Error;
    DB.Execute(TEXT("ROLLBACK"));Revision=FCString::Atoi64(*ReadMetadata(TEXT("world_revision")));
    // The previous READY state remains intact. Record failure in a separate
    // small transaction; lack of disk space must never conceal the rollback.
    if(!Generation.IsEmpty() && DB.Execute(TEXT("BEGIN IMMEDIATE")))
    {
        FSpatialTwinSQLiteStatement S(DB,TEXT("INSERT OR IGNORE INTO snapshots VALUES(?,?,'INVALID',?,?)"));
        S.SetBindingValueByIndex(1,Generation);S.SetBindingValueByIndex(2,Revision);S.SetBindingValueByIndex(3,FDateTime::UtcNow().ToIso8601());S.SetBindingValueByIndex(4,BuildingMap);
        if(S.Step()==ESTSQLiteStepResult::Done && Metadata(TEXT("last_failed_sync"),Failure))DB.Execute(TEXT("COMMIT"));else DB.Execute(TEXT("ROLLBACK"));
    }
    Error=Failure;
}
