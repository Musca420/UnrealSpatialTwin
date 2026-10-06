#include "SpatialTwinDatabase.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/SecureHash.h"

namespace
{
bool Numbers(const TSharedPtr<FJsonValue>& Value,int Count)
{
    if(!Value || Value->Type!=EJson::Array || Value->AsArray().Num()!=Count)return false;
    for(auto V:Value->AsArray()){double N;if(!V->TryGetNumber(N) || !FMath::IsFinite(N))return false;}return true;
}
bool ValidTransform(const TSharedPtr<FJsonObject>& T)
{
    return T && Numbers(T->TryGetField(TEXT("position")),3) && Numbers(T->TryGetField(TEXT("scale")),3) && Numbers(T->TryGetField(TEXT("rotation")),4);
}
FVector Vector(const TSharedPtr<FJsonValue>& V)
{
    const auto& A=V->AsArray();return FVector(A[0]->AsNumber(),A[1]->AsNumber(),A[2]->AsNumber());
}
FTransform Transform(const TSharedPtr<FJsonObject>& J)
{
    if(!J)return FTransform::Identity;
    const auto& Q=J->GetArrayField(TEXT("rotation"));
    return FTransform(FQuat(Q[0]->AsNumber(),Q[1]->AsNumber(),Q[2]->AsNumber(),Q[3]->AsNumber()),Vector(J->TryGetField(TEXT("position"))),Vector(J->TryGetField(TEXT("scale"))));
}
FBox Box(const TSharedPtr<FJsonObject>& J,const TCHAR* Key)
{
    const TArray<TSharedPtr<FJsonValue>>* A=nullptr;
    return J->TryGetArrayField(Key,A) && A->Num()==2 && Numbers((*A)[0],3) && Numbers((*A)[1],3)?FBox(Vector((*A)[0]),Vector((*A)[1])):FBox(ForceInit);
}
void SetBox(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,const FBox& B)
{
    auto V=[](FVector P){return MakeShared<FJsonValueArray>(TArray<TSharedPtr<FJsonValue>>{MakeShared<FJsonValueNumber>(P.X),MakeShared<FJsonValueNumber>(P.Y),MakeShared<FJsonValueNumber>(P.Z)});};
    J->SetArrayField(Key,{V(B.Min),V(B.Max)});
}
bool Contains(const FBox& Outer,const FBox& Inner)
{
    return Outer.IsValid && Outer.IsInsideOrOn(Inner.Min) && Outer.IsInsideOrOn(Inner.Max);
}
}

bool FSpatialTwinDatabase::UpgradeCollisionBounds(const FString& Map)
{
    if(ReadMetadata(TEXT("collision_bounds_version"))==TEXT("2"))return true;
    if(!Begin(false,Map))return false;
    const double Started=FPlatformTime::Seconds();int64 Examined=0,Updated=0;
    TMap<FString,FBox> GeometryBounds,BodyBounds,ActorBounds;
    auto GeometryBox=[&](const FString& Hash,FBox& Result)->bool
    {
        if(auto Cached=GeometryBounds.Find(Hash)){Result=*Cached;return true;}
        FString Relative;FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT path FROM geometry WHERE hash=?"));Q.SetBindingValueByIndex(1,Hash);
        if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,Relative)){Error=TEXT("Missing collision geometry during upgrade: ")+Hash;return false;}
        const FString File=FPaths::ConvertRelativePathToFull(Root/Relative);
        if(!FPaths::IsUnderDirectory(File,FPaths::ConvertRelativePathToFull(Root))){Error=TEXT("Geometry path escapes Twin");return false;}
        TArray<uint8> Data;if(!FFileHelper::LoadFileToArray(Data,*File) || Data.Num()<16 || FMemory::Memcmp(Data.GetData(),"STG1",4)){Error=TEXT("Invalid cached collision geometry");return false;}
        FSHAHash Actual;FSHA1::HashBuffer(Data.GetData(),Data.Num(),Actual.Hash);
        if(Actual.ToString().ToLower()!=Hash){Error=TEXT("Cached collision geometry digest mismatch: ")+Hash;return false;}
        uint32 Version,NV,NT;FMemory::Memcpy(&Version,Data.GetData()+4,4);FMemory::Memcpy(&NV,Data.GetData()+8,4);FMemory::Memcpy(&NT,Data.GetData()+12,4);
        if(Version!=1 || 16ull+24ull*NV+12ull*NT!=(uint64)Data.Num()){Error=TEXT("Invalid cached collision geometry size");return false;}
        Result=FBox(ForceInit);for(uint32 I=0;I<NV;++I){double P[3];FMemory::Memcpy(P,Data.GetData()+16+24ull*I,24);FVector V(P[0],P[1],P[2]);if(V.ContainsNaN()){Error=TEXT("Nonfinite cached collision vertex");return false;}Result+=V;}
        GeometryBounds.Add(Hash,Result);return true;
    };
    FString After;
    for(;;)
    {
        // One streaming pass for this format upgrade, never an editor rescan or
        // per-query pass. Geometry/shape bounds are deduplicated across instances.
        TArray<TSharedPtr<FJsonObject>> Batch;
        // Pin the unique ID range index: entity_kind otherwise sorts all remaining
        // instances for every page, becoming quadratic on a large existing Twin.
        {FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT source FROM entities INDEXED BY sqlite_autoindex_entities_1 WHERE id>? AND kind IN ('StaticMesh','Component','Instance') ORDER BY id LIMIT 512"));Q.SetBindingValueByIndex(1,After);
         while(Q.Step()==ESTSQLiteStepResult::Row){FString Raw;Q.GetColumnValueByIndex(0,Raw);TSharedPtr<FJsonObject> E;if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Raw),E)){Error=TEXT("Invalid entity during upgrade");break;}Batch.Add(E);}}
        if(!Error.IsEmpty()){Rollback();return false;}if(Batch.IsEmpty())break;
        for(const auto& E:Batch)
        {
            After=E->GetStringField(TEXT("id"));++Examined;
            if(E->HasField(TEXT("collision_local_bounds")))continue;
            const bool Asset=E->GetStringField(TEXT("kind"))==TEXT("StaticMesh");
            const TSharedPtr<FJsonObject>* Collision=nullptr;if(!Asset && !E->TryGetObjectField(TEXT("collision"),Collision))continue;
            auto Body=Asset?E:*Collision;const FString Key=Json(Body);FBox Local(ForceInit);
            if(auto Cached=BodyBounds.Find(Key))Local=*Cached;
            else
            {
                for(auto Field:Asset?TArray<FString>{TEXT("collision_simple"),TEXT("collision_complex")}:TArray<FString>{TEXT("simple"),TEXT("complex")})
                {const TArray<TSharedPtr<FJsonValue>>* Values=nullptr;if(Body->TryGetArrayField(Field,Values))for(const auto& Hash:*Values){FBox B(ForceInit);if(!GeometryBox(Hash->AsString(),B)){Rollback();return false;}Local+=B;}}
                const TArray<TSharedPtr<FJsonValue>>* Shapes=nullptr;
                if(Body->TryGetArrayField(Asset?TEXT("collision_shapes"):TEXT("shapes"),Shapes))for(auto Value:*Shapes)
                {
                    const TSharedPtr<FJsonObject>* Object=nullptr;if(!Value->TryGetObject(Object)){Error=TEXT("Invalid cached shape");Rollback();return false;}
                    auto Shape=*Object;FString Type;if(!Shape->TryGetStringField(TEXT("type"),Type)){Error=TEXT("Missing cached shape type");Rollback();return false;}
                    const TSharedPtr<FJsonObject>* T=nullptr;
                    if(Shape->HasField(TEXT("transform")) && (!Shape->TryGetObjectField(TEXT("transform"),T) || !ValidTransform(*T))){Error=TEXT("Invalid cached shape transform");Rollback();return false;}
                    double Radius=0,Length=0;
                    if((Type==TEXT("box") && !Numbers(Shape->TryGetField(TEXT("extent")),3)) ||
                       (Type==TEXT("sphere") && !Numbers(Shape->TryGetField(TEXT("center")),3)) ||
                       ((Type==TEXT("sphere") || Type==TEXT("capsule")) && (!Shape->TryGetNumberField(TEXT("radius"),Radius) || !FMath::IsFinite(Radius) || Radius<0)) ||
                       (Type==TEXT("capsule") && (!Shape->TryGetNumberField(TEXT("length"),Length) || !FMath::IsFinite(Length) || Length<0)))
                    {Error=TEXT("Invalid cached shape dimensions");Rollback();return false;}
                    const FTransform Pose=Transform(T?*T:nullptr);
                    if(Type==TEXT("box")){const FVector Extent=Vector(Shape->TryGetField(TEXT("extent")));Local+=FBox(-Extent,Extent).TransformBy(Pose);}
                    else if(Type==TEXT("sphere")){const FVector Center=Vector(Shape->TryGetField(TEXT("center"))),Extent(Shape->GetNumberField(TEXT("radius")));Local+=FBox(Center-Extent,Center+Extent);}
                    else if(Type==TEXT("capsule")){const FVector Extent(Radius,Radius,Radius+Length*.5);Local+=FBox(-Extent,Extent).TransformBy(Pose);}
                    else {Error=TEXT("Unrecognized cached collision shape: ")+Type;Rollback();return false;}
                }
                BodyBounds.Add(Key,Local);
            }
            if(!Local.IsValid)continue;
            const FBox Visual=Box(E,TEXT("local_bounds"));
            // If the old visual box already contains collision, existing broad-
            // phase and Shadow remain conservative; avoid rewriting huge worlds.
            if(Contains(Visual,Local))continue;
            SetBox(E,TEXT("collision_local_bounds"),Local);
            E->SetStringField(TEXT("collision_bounds_provenance"),TEXT("derived_from_exported_collision_v2"));
            if(!Asset)
            {
                const TSharedPtr<FJsonObject>* EntityPose=nullptr;
                if(!E->TryGetObjectField(TEXT("transform"),EntityPose) || !ValidTransform(*EntityPose)){Error=TEXT("Invalid cached collision transform: ")+After;Rollback();return false;}
                const FBox Physical=Local.TransformBy(Transform(*EntityPose));SetBox(E,TEXT("collision_bounds"),Physical);
                FString Actor;if(E->TryGetStringField(TEXT("actor_id"),Actor))
                {if(auto Existing=ActorBounds.Find(Actor))*Existing+=Physical;else ActorBounds.Add(Actor,Physical);}
            }
            if(!Put(E)){Rollback();return false;}++Updated;
        }
    }
    for(const auto& Pair:ActorBounds)
    {
        FString Raw;FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT source FROM entities WHERE id=?"));Q.SetBindingValueByIndex(1,Pair.Key);
        if(Q.Step()!=ESTSQLiteStepResult::Row)continue;Q.GetColumnValueByIndex(0,Raw);TSharedPtr<FJsonObject> E;
        if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Raw),E)){Error=TEXT("Invalid actor during upgrade");Rollback();return false;}
        FBox Physical=Pair.Value;Physical+=Box(E,TEXT("collision_bounds"));
        SetBox(E,TEXT("collision_bounds"),Physical);E->SetStringField(TEXT("collision_bounds_provenance"),TEXT("derived_from_exported_collision_v2"));
        if(!Put(E)){Rollback();return false;}++Updated;
    }
    auto Metrics=MakeShared<FJsonObject>();Metrics->SetNumberField(TEXT("examined"),Examined);Metrics->SetNumberField(TEXT("updated"),Updated);Metrics->SetNumberField(TEXT("geometry_files"),GeometryBounds.Num());Metrics->SetNumberField(TEXT("seconds"),FPlatformTime::Seconds()-Started);Metrics->SetBoolField(TEXT("editor_objects_loaded"),false);
    if(!Metadata(TEXT("collision_bounds_version"),TEXT("2")) || !Metadata(TEXT("collision_bounds_upgrade"),Json(Metrics))){Rollback();return false;}
    return Commit(Map,false);
}
