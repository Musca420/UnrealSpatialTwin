#include "SpatialTwinSave.h"
#include "SpatialTwinSubsystem.h"
#include "SpatialTwinSourcePaths.h"
#include "SpatialTwinToolset.h"
#include "SpatialTwinSQLite.h"
#include "Editor.h"
#include "FileHelpers.h"
#include "Misc/PackageName.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"
#include "HAL/FileManager.h"
#include "Serialization/JsonSerializer.h"
#include "UObject/Package.h"
#include "UObject/UObjectGlobals.h"

namespace {
struct FSaveFence { TWeakObjectPtr<UPackage> Package; uint64 Serial=0; };
TMap<FString,FSaveFence> SaveFences;
FString SaveSession;
FDelegateHandle MarkedHandle,ModifiedHandle,PropertyHandle,TransactedHandle;
uint64 SaveSerial=0;
void ModifiedPackage(UPackage* Package)
{
    if(Package)if(auto Fence=SaveFences.Find(Package->GetName()))Fence->Serial=++SaveSerial;
}
void ModifiedObject(UObject* Object){if(Object)ModifiedPackage(Object->GetPackage());}
void EnsureSaveTracking()
{
    if(!SaveSession.IsEmpty())return;
    SaveSession=FGuid::NewGuid().ToString(EGuidFormats::Digits);
    MarkedHandle=UPackage::PackageMarkedDirtyEvent.AddLambda([](UPackage* P,bool){ModifiedPackage(P);});
    ModifiedHandle=FCoreUObjectDelegates::OnObjectModified.AddStatic(&ModifiedObject);
    PropertyHandle=FCoreUObjectDelegates::OnObjectPropertyChanged.AddLambda([](UObject* O,FPropertyChangedEvent&){ModifiedObject(O);});
    TransactedHandle=FCoreUObjectDelegates::OnObjectTransacted.AddLambda([](UObject* O,const FTransactionObjectEvent&){ModifiedObject(O);});
}
TSharedPtr<FJsonObject> PackageEvidence(UPackage* Package)
{
    auto Row=MakeShared<FJsonObject>();Row->SetStringField(TEXT("package"),Package->GetName());
    FString Path=FPackageName::LongPackageNameToFilename(Package->GetName(),Package->ContainsMap()?FPackageName::GetMapPackageExtension():FPackageName::GetAssetPackageExtension());
    Path=SpatialTwinSourcePath(Path);Row->SetStringField(TEXT("path"),Path);
    const auto Stat=IFileManager::Get().GetStatData(*Path);
    if(Stat.bIsValid)
    {
        Row->SetStringField(TEXT("signature"),FString::Printf(TEXT("%lld|%lld"),Stat.FileSize,Stat.ModificationTime.GetTicks()));
        TUniquePtr<FArchive> Reader(IFileManager::Get().CreateFileReader(*Path));if(!Reader)return nullptr;
        const auto Hash=FMD5Hash::HashFileFromArchive(Reader.Get());if(Reader->IsError() || !Hash.IsValid())return nullptr;
        Row->SetStringField(TEXT("content_md5"),LexToString(Hash));
    }
    else {Row->SetField(TEXT("signature"),MakeShared<FJsonValueNull>());Row->SetField(TEXT("content_md5"),MakeShared<FJsonValueNull>());}
    return Row;
}
bool SameEvidence(const TSharedPtr<FJsonObject>& A,const TSharedPtr<FJsonObject>& B)
{
    if(!A || !B)return false;
    for(auto Key:{TEXT("package"),TEXT("path"),TEXT("signature"),TEXT("content_md5")})
    {
        auto X=A->TryGetField(Key),Y=B->TryGetField(Key);if(!X || !Y || X->Type!=Y->Type)return false;
        if(X->Type!=EJson::Null && X->AsString()!=Y->AsString())return false;
    }
    return true;
}
}
void SpatialTwinSaveShutdown()
{
    UPackage::PackageMarkedDirtyEvent.Remove(MarkedHandle);
    FCoreUObjectDelegates::OnObjectModified.Remove(ModifiedHandle);
    FCoreUObjectDelegates::OnObjectPropertyChanged.Remove(PropertyHandle);
    FCoreUObjectDelegates::OnObjectTransacted.Remove(TransactedHandle);
    SaveFences.Reset();SaveSession.Reset();SaveSerial=0;
}

FString USpatialTwinToolset::spatial_twin_save_patch(const FString& patch_id,bool resume)
{
    EnsureSaveTracking();auto S=GEditor?GEditor->GetEditorSubsystem<USpatialTwinSubsystem>():nullptr;if(!S)return TEXT("{\"error\":\"Editor unavailable\"}");
    FSpatialTwinSQLiteDatabase Patches;if(!Patches.Open(*(S->Database.Root/TEXT("patches.sqlite")),ESTSQLiteOpenMode::ReadOnly))return TEXT("{\"error\":\"Patch store unavailable\"}");
    FString Operations,State,Receipts,Digest,ProgressJson,ProgressDigest;
    {FSpatialTwinSQLiteStatement Q(Patches,TEXT("SELECT operations,status,receipts,json_extract(validation,'$.operations_digest') FROM patches WHERE id=?"));Q.SetBindingValueByIndex(1,patch_id);if(Q.Step()==ESTSQLiteStepResult::Row){Q.GetColumnValueByIndex(0,Operations);Q.GetColumnValueByIndex(1,State);Q.GetColumnValueByIndex(2,Receipts);Q.GetColumnValueByIndex(3,Digest);}}
    {FSpatialTwinSQLiteStatement Q(Patches,TEXT("SELECT 1 FROM patch_saves WHERE patch_id=?"));Q.SetBindingValueByIndex(1,patch_id);if(Q.Step()==ESTSQLiteStepResult::Row)return TEXT("{\"error\":\"Native save already recorded; recover the receipt, do not repeat saving\"}");}
    {FSpatialTwinSQLiteStatement Q(Patches,TEXT("SELECT operations_digest,result FROM patch_save_progress WHERE patch_id=?"));Q.SetBindingValueByIndex(1,patch_id);if(Q.Step()==ESTSQLiteStepResult::Row){Q.GetColumnValueByIndex(0,ProgressDigest);Q.GetColumnValueByIndex(1,ProgressJson);}}
    Patches.Close();if(State!=TEXT("APPLYING"))return TEXT("{\"error\":\"Patch is not APPLYING\"}");
    TArray<TSharedPtr<FJsonValue>> Ops;if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Operations),Ops))return TEXT("{\"error\":\"Invalid operations\"}");
    TArray<TSharedPtr<FJsonValue>> Confirmations;TSharedPtr<FJsonObject> Resolved;bool SaveSent=false;int64 ConfirmedRevision=-1;
    if(FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Receipts),Confirmations))for(auto Value:Confirmations)
    {
        auto R=Value->AsObject();FString Kind,Id;
        if(R && R->TryGetStringField(TEXT("state"),Kind))
        {
            if(Kind==TEXT("CANONICAL_VERIFIED") && R->HasTypedField<EJson::Object>(TEXT("actor_ids"))){Resolved=R->GetObjectField(TEXT("actor_ids"));ConfirmedRevision=R->GetIntegerField(TEXT("canonical_revision"));}
            if(Kind==TEXT("SENT") && R->TryGetStringField(TEXT("operation_id"),Id) && Id==TEXT("save:")+patch_id)SaveSent=true;
        }
    }
    if(!Resolved)return TEXT("{\"error\":\"Canonical verification receipt missing\"}");
    if(!SaveSent)return TEXT("{\"error\":\"Recorded save dispatch required\"}");
    if(!S->Sync() || S->Database.Revision!=ConfirmedRevision)return TEXT("{\"error\":\"Canonical changed before saving; verify effects again\"}");
    TSet<UPackage*> Scope;TArray<AActor*> Actors;
    for(const auto& Value:Ops)
    {
        auto Op=Value->AsObject();const FString Target=Op->GetStringField(TEXT("target"));FString Source,CanonicalId;
        if(!Resolved->TryGetStringField(Target,CanonicalId))
        {
            if(Op->GetStringField(TEXT("type"))==TEXT("CREATE_ACTOR"))return TEXT("{\"error\":\"Created Actor identity not confirmed\"}");
            CanonicalId=Target;
        }
        FSpatialTwinSQLiteStatement Q(S->Database.DB,TEXT("SELECT source FROM (SELECT source,revision FROM entities WHERE id=? UNION ALL SELECT before_json AS source,revision FROM changes WHERE entity_id=? AND type='DELETE') ORDER BY revision DESC LIMIT 1"));Q.SetBindingValueByIndex(1,CanonicalId);Q.SetBindingValueByIndex(2,CanonicalId);if(Q.Step()==ESTSQLiteStepResult::Row)Q.GetColumnValueByIndex(0,Source);
        TSharedPtr<FJsonObject> Entity;if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Entity))return TEXT("{\"error\":\"Package scope unresolved\"}");
        if(Entity->GetStringField(TEXT("kind"))==TEXT("Instance"))
        {
            FSpatialTwinSQLiteStatement Owner(S->Database.DB,TEXT("SELECT source FROM entities WHERE id=? AND kind='Actor'"));Owner.SetBindingValueByIndex(1,Entity->GetStringField(TEXT("actor_id")));
            if(Owner.Step()!=ESTSQLiteStepResult::Row)return TEXT("{\"error\":\"Instance owner missing\"}");Owner.GetColumnValueByIndex(0,Source);
            if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Source),Entity))return TEXT("{\"error\":\"Instance owner invalid\"}");
        }
        FString Package=Entity->GetStringField(TEXT("package"));if(auto P=FindPackage(nullptr,*Package))Scope.Add(P);else return TEXT("{\"error\":\"Touched package not loaded\"}");
        FString Path;if(Entity->TryGetStringField(TEXT("path"),Path))if(auto A=FindObject<AActor>(nullptr,*Path))Actors.Add(A);
    }
    if(Scope.IsEmpty())return TEXT("{\"error\":\"Empty save scope\"}");
    TSharedPtr<FJsonObject> Progress;TArray<TSharedPtr<FJsonValue>> Rows;
    if(resume)
    {
        if(ProgressDigest!=Digest || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(ProgressJson),Progress) ||
           Progress->GetStringField(TEXT("session"))!=SaveSession || Progress->GetStringField(TEXT("state"))!=TEXT("PARTIAL"))
            return TEXT("{\"error\":\"No terminal partial save in this editor session; refusing an uncertain retry\"}");
        Rows=Progress->GetArrayField(TEXT("packages"));TMap<FString,TSharedPtr<FJsonObject>> Names;
        for(auto V:Rows)Names.Add(V->AsObject()->GetStringField(TEXT("package")),V->AsObject());
        if(Names.Num()!=Scope.Num() || Rows.Num()!=Scope.Num())return TEXT("{\"error\":\"Partial save scope differs\"}");
        // Check the entire scope before any write, including already-saved files.
        for(auto Package:Scope)
        {
            if(!Names.Contains(Package->GetName()))return TEXT("{\"error\":\"Partial save scope differs\"}");
            const auto Fence=SaveFences.Find(Package->GetName());
            {
                auto Row=Names.FindChecked(Package->GetName());const FString Stage=Row->GetStringField(TEXT("state"));
                if(!Fence || Fence->Package.Get()!=Package || Row->GetStringField(TEXT("serial"))!=LexToString(Fence->Serial))return TEXT("{\"error\":\"Package changed after partial save; no automatic saving\"}");
                if(Stage==TEXT("SAVED"))
                {if(Package->IsDirty() || !SameEvidence(Row,PackageEvidence(Package)))return TEXT("{\"error\":\"Previously saved package changed\"}");}
                else if((Stage!=TEXT("PENDING") && Stage!=TEXT("FAILED")) || !Package->IsDirty())return TEXT("{\"error\":\"Package save outcome is uncertain; refusing replay\"}");
            }
        }
    }
    else
    {
        if(!ProgressJson.IsEmpty())return TEXT("{\"error\":\"Save intent already recorded; use explicit save finalization\"}");
        Progress=MakeShared<FJsonObject>();Progress->SetStringField(TEXT("session"),SaveSession);
        TArray<UPackage*> Ordered=Scope.Array();Ordered.Sort([](const UPackage& A,const UPackage& B){return A.GetName()<B.GetName();});
        for(auto Package:Ordered)
        {
            auto& Fence=SaveFences.FindOrAdd(Package->GetName());if(Fence.Package.Get()!=Package){Fence.Package=Package;Fence.Serial=++SaveSerial;}
            auto Row=MakeShared<FJsonObject>();Row->SetStringField(TEXT("package"),Package->GetName());
            Row->SetStringField(TEXT("state"),TEXT("PENDING"));Row->SetStringField(TEXT("serial"),LexToString(Fence.Serial));
            Rows.Add(MakeShared<FJsonValueObject>(Row));
        }
        Progress->SetArrayField(TEXT("packages"),Rows);
    }
    if(!Patches.Open(*(S->Database.Root/TEXT("patches.sqlite")),ESTSQLiteOpenMode::ReadWrite))return TEXT("{\"error\":\"Save progress journal unavailable\"}");
    auto Record=[&]()
    {
        FSpatialTwinSQLiteStatement Q(Patches,TEXT("INSERT INTO patch_save_progress(patch_id,operations_digest,result) SELECT id,?,? FROM patches WHERE id=? AND status IN ('APPLYING','FAILED') AND operations=? AND json_extract(validation,'$.operations_digest')=? ON CONFLICT(patch_id) DO UPDATE SET result=excluded.result WHERE patch_save_progress.operations_digest=excluded.operations_digest RETURNING patch_id"));
        Q.SetBindingValueByIndex(1,Digest);Q.SetBindingValueByIndex(2,FSpatialTwinDatabase::Json(Progress));Q.SetBindingValueByIndex(3,patch_id);Q.SetBindingValueByIndex(4,Operations);Q.SetBindingValueByIndex(5,Digest);
        return Q.Step()==ESTSQLiteStepResult::Row && Q.Step()==ESTSQLiteStepResult::Done;
    };
    auto Partial=[&](const FString& Error)
    {
        Progress->SetStringField(TEXT("state"),TEXT("PARTIAL"));Progress->SetStringField(TEXT("error"),Error);
        if(!Record())return FString(TEXT("{\"error\":\"Partial save progress could not be recorded; uncertain outcome\"}"));
        auto Summary=MakeShared<FJsonObject>();Summary->SetStringField(TEXT("state"),TEXT("PARTIAL"));Summary->SetStringField(TEXT("error"),Error);
        int32 SavedCount=0;for(auto V:Rows)SavedCount+=V->AsObject()->GetStringField(TEXT("state"))==TEXT("SAVED");
        Summary->SetNumberField(TEXT("packages_saved"),SavedCount);Summary->SetNumberField(TEXT("packages_total"),Rows.Num());
        return FSpatialTwinDatabase::Json(Summary);
    };
    auto Checkpoint=[&](const TSharedPtr<FJsonObject>& Row)
    {
        // The full intent/terminal snapshot is O(scope). Per-file progress must
        // not rewrite that array twice per package (quadratic bytes for WP).
        FSpatialTwinSQLiteStatement Q(Patches,TEXT("INSERT INTO patch_save_packages(patch_id,package,result) VALUES(?,?,?) ON CONFLICT(patch_id,package) DO UPDATE SET result=excluded.result"));
        Q.SetBindingValueByIndex(1,patch_id);Q.SetBindingValueByIndex(2,Row->GetStringField(TEXT("package")));Q.SetBindingValueByIndex(3,FSpatialTwinDatabase::Json(Row));
        return Q.Step()==ESTSQLiteStepResult::Done;
    };
    Progress->SetStringField(TEXT("state"),TEXT("STARTED"));Progress->RemoveField(TEXT("error"));
    if(!Record())return TEXT("{\"error\":\"Save intent could not be committed; no package written\"}");
    for(auto V:Rows)
    {
        auto Row=V->AsObject();if(Row->GetStringField(TEXT("state"))==TEXT("SAVED"))continue;
        auto Package=FindPackage(nullptr,*Row->GetStringField(TEXT("package")));if(!Package)return Partial(TEXT("Scoped package unloaded"));auto Fence=SaveFences.Find(Package->GetName());
        if(!Fence || Row->GetStringField(TEXT("serial"))!=LexToString(Fence->Serial))return Partial(TEXT("Package changed during earlier scoped save"));
        Row->SetStringField(TEXT("state"),TEXT("STARTED"));if(!Checkpoint(Row))return TEXT("{\"error\":\"Package save intent not committed\"}");
        // Official noninteractive save API, one owned package at a time. Never SaveAll.
        const bool Saved=UEditorLoadingAndSavingUtils::SavePackages({Package},true);
        Row->SetStringField(TEXT("serial"),LexToString(Fence->Serial));
        if(!Saved || Package->IsDirty())
        {Row->SetStringField(TEXT("state"),Package->IsDirty()?TEXT("FAILED"):TEXT("UNCERTAIN"));return Partial(TEXT("Scoped package save failed"));}
        auto Evidence=PackageEvidence(Package);if(!Evidence){Row->SetStringField(TEXT("state"),TEXT("UNCERTAIN"));return Partial(TEXT("Saved package digest unavailable"));}
        for(const auto& Field:Evidence->Values)Row->SetField(Field.Key,Field.Value);
        Row->SetStringField(TEXT("state"),TEXT("SAVED"));if(!Checkpoint(Row))return TEXT("{\"error\":\"Package saved but progress journal failed\"}");
    }
    for(auto A:Actors)S->Dirty(A);if(!S->Sync())return Partial(TEXT("Saved source synchronization failed"));
    if(!S->Database.Begin(false,S->MapId))return Partial(TEXT("Save metadata transaction failed"));
    for(auto Package:Scope)if(!S->RecordSavedPackage(Package)){S->Database.Rollback();return Partial(TEXT("Saved package signatures failed"));}
    S->Database.Metadata(TEXT("last_patch_saved_revision"),LexToString(S->Database.Revision));
    {FSpatialTwinSQLiteStatement Dirty(S->Database.DB,TEXT("SELECT 1 FROM entities WHERE kind='Actor' AND json_extract(source,'$.package_dirty')=1 LIMIT 1"));if(Dirty.Step()!=ESTSQLiteStepResult::Row)S->Database.Metadata(TEXT("saved_revision"),LexToString(S->Database.Revision));}
    if(!S->Database.Commit(S->MapId,false))return Partial(TEXT("Save metadata commit failed"));
    auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("state"),TEXT("SAVED"));Result->SetNumberField(TEXT("canonical_revision"),S->Database.Revision);
    TArray<TSharedPtr<FJsonValue>> Packages;for(auto V:Rows){auto Row=MakeShared<FJsonObject>();for(auto Key:{TEXT("package"),TEXT("path"),TEXT("signature"),TEXT("content_md5")})Row->SetField(Key,V->AsObject()->TryGetField(Key));Packages.Add(MakeShared<FJsonValueObject>(Row));}
    Result->SetArrayField(TEXT("packages"),Packages);const FString Saved=FSpatialTwinDatabase::Json(Result);
    Patches.Close();
    if(SaveSent)
    {
        // Persist after native saves + canonical commit, before the transport
        // response. Never turn an uncertain earlier save into another SaveAll.
        if(!Patches.Open(*(S->Database.Root/TEXT("patches.sqlite")),ESTSQLiteOpenMode::ReadWrite))return TEXT("{\"error\":\"Packages saved but save journal unavailable\"}");
        FSpatialTwinSQLiteStatement Q(Patches,TEXT("INSERT INTO patch_saves(patch_id,operations_digest,dispatch_id,result) SELECT id,?, ?,? FROM patches WHERE id=? AND status IN ('APPLYING','FAILED') AND operations=? AND json_extract(validation,'$.operations_digest')=? RETURNING patch_id"));
        Q.SetBindingValueByIndex(1,Digest);Q.SetBindingValueByIndex(2,TEXT("save:")+patch_id);Q.SetBindingValueByIndex(3,Saved);Q.SetBindingValueByIndex(4,patch_id);Q.SetBindingValueByIndex(5,Operations);Q.SetBindingValueByIndex(6,Digest);
        if(Q.Step()!=ESTSQLiteStepResult::Row || Q.Step()!=ESTSQLiteStepResult::Done)return TEXT("{\"error\":\"Packages saved but save journal commit failed\"}");
    }
    return Saved;
}
