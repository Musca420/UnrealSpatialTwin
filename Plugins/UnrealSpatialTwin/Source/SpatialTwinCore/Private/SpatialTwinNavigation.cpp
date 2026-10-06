#include "SpatialTwinNavigation.h"
#include <memory>
#include <fstream>
#include <filesystem>
#include <mutex>
#include "SpatialTwinDatabase.h"
#include "Detour/DetourNavMesh.h"
#include "Detour/DetourAlloc.h"
#include "Detour/DetourCommon.h"
#include "Detour/DetourNavMeshQuery.h"
#include "Detour/DetourNavMeshBuilder.h"
#include "DetourTileCache/DetourTileCacheBuilder.h"
#include "Recast/Recast.h"
#include "Recast/RecastAlloc.h"
#include "Misc/FileHelper.h"
#include "Serialization/MemoryReader.h"
#include "Serialization/MemoryWriter.h"
#include "Serialization/JsonSerializer.h"
#include "Misc/SecureHash.h"
#include "Misc/Paths.h"

namespace
{
int Result(const TSharedPtr<FJsonObject>& J,char* Out,int Capacity)
{
    FTCHARToUTF8 Text(*FSpatialTwinDatabase::Json(J));if(Capacity<=Text.Length())return -Text.Length()-1;
    FMemory::Memcpy(Out,Text.Get(),Text.Length());Out[Text.Length()]=0;return Text.Length();
}
int NavError(const FString& Message,char* Out,int Capacity){auto J=MakeShared<FJsonObject>();J->SetStringField(TEXT("state"),TEXT("UNKNOWN"));J->SetStringField(TEXT("error"),Message);return Result(J,Out,Capacity);}
bool Parse(const char* Raw,TSharedPtr<FJsonObject>& J){return Raw && FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(UTF8_TO_TCHAR(Raw)),J) && J.IsValid();}
bool Numbers(const TSharedPtr<FJsonValue>& Value,int Count)
{
    if(!Value || Value->Type!=EJson::Array)return false;const auto& A=Value->AsArray();if(A.Num()!=Count)return false;
    for(auto V:A){double D;if(!V->TryGetNumber(D) || !FMath::IsFinite(D))return false;}return true;
}
bool Number(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,double& Value){return J->TryGetNumberField(Key,Value) && FMath::IsFinite(Value);}
double Setting(const TSharedPtr<FJsonObject>& J,const TCHAR* Key,double Default){double Value;return Number(J,Key,Value)?Value:Default;}
void Params(FArchive& Ar,dtNavMeshParams& P)
{
    for(int I=0;I<3;++I)Ar<<P.orig[I];Ar<<P.tileWidth<<P.tileHeight<<P.maxTiles<<P.maxPolys;
    Ar<<P.walkableHeight<<P.walkableRadius<<P.walkableClimb;
    for(auto& Resolution:P.resolutionParams)Ar<<Resolution.bvQuantFactor;
}
bool ValidTile(const unsigned char* Data,int Size,int MaxPolys)
{
    if(Size<(int)sizeof(dtMeshHeader))return false;const auto& H=*(const dtMeshHeader*)Data;
    if(H.version!=DT_NAVMESH_VERSION || !H.polyCount || H.polyCount>MaxPolys || H.vertCount<3 || !H.maxLinkCount ||
       H.resolution>=DT_RESOLUTION_COUNT || H.offMeshBase>H.polyCount || H.detailMeshCount!=H.offMeshBase)return false;
#if WITH_NAVMESH_SEGMENT_LINKS
    // Experimental segment links require native regeneration; fail closed offline.
    if(H.offMeshSegConCount)return false;
#endif
    if(H.offMeshBase+H.offMeshConCount!=H.polyCount)return false;
    for(int I=0;I<3;++I)if(!FMath::IsFinite(H.bmin[I]) || !FMath::IsFinite(H.bmax[I]) || H.bmin[I]>H.bmax[I])return false;
    int Offset=dtAlign(sizeof(dtMeshHeader));bool Valid=true;
    auto Take=[&](int Count,int Stride)->const unsigned char*
    {
        int Length=dtAlign(Count*Stride);if(Length>Size-Offset){Valid=false;return nullptr;}
        auto* Result=Data+Offset;Offset+=Length;return Result;
    };
    auto* Vertices=(const dtTileVert*)Take(H.vertCount,sizeof(dtTileVert));
    auto* Polys=(const dtPoly*)Take(H.polyCount,sizeof(dtPoly));Take(H.maxLinkCount,sizeof(dtLink));
    auto* Details=(const dtPolyDetail*)Take(H.detailMeshCount,sizeof(dtPolyDetail));
    auto* DetailVertices=(const dtTileVert*)Take(H.detailVertCount,sizeof(dtTileVert));
    auto* Triangles=Take(H.detailTriCount,4);auto* Nodes=(const dtBVNode*)Take(H.bvNodeCount,sizeof(dtBVNode));
    auto* Connections=(const dtOffMeshConnection*)Take(H.offMeshConCount,sizeof(dtOffMeshConnection));
#if WITH_NAVMESH_SEGMENT_LINKS
    Take(H.offMeshSegConCount,sizeof(dtOffMeshSegmentConnection));
#endif
#if WITH_NAVMESH_CLUSTER_LINKS
    auto* Clusters=(const dtCluster*)Take(H.clusterCount,sizeof(dtCluster));
    auto* PolyClusters=(const unsigned short*)Take(H.offMeshBase,sizeof(unsigned short));
#endif
    if(!Valid || Offset!=Size)return false;
    auto Finite=[](const dtTileVert& V){return FMath::IsFinite(V.x) && FMath::IsFinite(V.y) && FMath::IsFinite(V.z);};
    for(int I=0;I<H.vertCount;++I)if(!Finite(Vertices[I]))return false;
    for(int I=0;I<H.detailVertCount;++I)if(!Finite(DetailVertices[I]))return false;
    for(int I=0;I<H.polyCount;++I)
    {
        const auto& P=Polys[I];bool Ground=I<H.offMeshBase;
        if(P.getType()!=(Ground?DT_POLYTYPE_GROUND:DT_POLYTYPE_OFFMESH_POINT) ||
           P.vertCount<(Ground?3:2) || P.vertCount>(Ground?DT_VERTS_PER_POLYGON:2))return false;
        for(int K=0;K<P.vertCount;++K)
        {
            if(P.verts[K]>=H.vertCount)return false;
            if(P.neis[K]&DT_EXT_LINK){if((P.neis[K]&~DT_EXT_LINK)>7)return false;}
            else if(P.neis[K]>H.offMeshBase)return false;
        }
        if(Ground)
        {
            const auto& D=Details[I];if(D.vertBase+D.vertCount>H.detailVertCount || D.triBase+D.triCount>H.detailTriCount)return false;
            for(int T=0;T<D.triCount;++T)for(int K=0;K<3;++K)if(Triangles[(D.triBase+T)*4+K]>=P.vertCount+D.vertCount)return false;
#if WITH_NAVMESH_CLUSTER_LINKS
            if(H.clusterCount && PolyClusters[I]>=H.clusterCount)return false;
#endif
        }
    }
    for(int I=0;I<H.bvNodeCount;++I)
    {
        const auto& N=Nodes[I];if(N.i>=H.offMeshBase || (N.i<0 && -(int64)N.i>H.bvNodeCount-I))return false;
        for(int K=0;K<3;++K)if(N.bmin[K]>N.bmax[K])return false;
    }
    for(int I=0;I<H.offMeshConCount;++I)
    {
        const auto& C=Connections[I];if(C.poly!=H.offMeshBase+I || !FMath::IsFinite(C.rad) || C.rad<0 || !FMath::IsFinite(C.height) || (C.side>7 && C.side!=DT_CONNECTION_INTERNAL))return false;
        for(auto V:C.pos)if(!FMath::IsFinite(V))return false;
    }
#if WITH_NAVMESH_CLUSTER_LINKS
    for(int I=0;I<H.clusterCount;++I)for(auto V:Clusters[I].center)if(!FMath::IsFinite(V))return false;
#endif
    return true;
}
bool LinkOrder(const TSharedPtr<FJsonObject>& J,TArray<uint8>& Order)
{
    if(!J->HasField(TEXT("link_area_order")))return true;
    const TArray<TSharedPtr<FJsonValue>>* Values=nullptr;if(!J->TryGetArrayField(TEXT("link_area_order"),Values) || Values->Num()!=DT_MAX_AREAS)return false;
    TSet<int32> Seen;for(auto V:*Values){double D;if(!V->TryGetNumber(D) || !FMath::IsFinite(D) || D<0 || D>=DT_MAX_AREAS || D!=FMath::FloorToDouble(D) || Seen.Contains(int32(D)))return false;Seen.Add(int32(D));Order.Add(uint8(D));}return true;
}
bool HasCostSnappedLinks(const dtNavMesh* M)
{
    for(int I=0;I<M->getMaxTiles();++I){auto T=M->getTile(I);if(T->header)for(int K=0;K<T->header->offMeshConCount;++K)if(T->offMeshCons[K].getSnapToCheapestArea())return true;}return false;
}
dtNavMesh* Load(const FString& Path,const TArray<uint8>& Order,const FString& ExpectedDigest=FString())
{
    std::ifstream File(std::filesystem::path(*Path),std::ios::binary|std::ios::ate);if(!File)return nullptr;
    const auto Size=File.tellg();if(Size<108 || Size>INT32_MAX)return nullptr;TArray<uint8> Bytes;Bytes.SetNumUninitialized((int32)Size);File.seekg(0);if(!File.read((char*)Bytes.GetData(),Size))return nullptr;FMemoryReader R(Bytes);
    if(!ExpectedDigest.IsEmpty()){FSHAHash Hash;FSHA1::HashBuffer(Bytes.GetData(),Bytes.Num(),Hash.Hash);if(Hash.ToString().ToLower()!=ExpectedDigest)return nullptr;}
    uint32 Magic=0,Version=0;R<<Magic<<Version;if(Magic!=0x314E5453 || Version!=2)return nullptr;
    dtNavMeshParams P{};Params(R,P);if(P.maxTiles<=0 || P.maxTiles>1048576 || P.maxPolys<=0 || R.IsError())return nullptr;
    for(auto V:P.orig)if(!FMath::IsFinite(V))return nullptr;
    for(auto V:{P.tileWidth,P.tileHeight,P.walkableHeight})if(!FMath::IsFinite(V) || V<=0)return nullptr;
    for(auto V:{P.walkableRadius,P.walkableClimb})if(!FMath::IsFinite(V) || V<0)return nullptr;
    for(auto Resolution:P.resolutionParams)if(!FMath::IsFinite(Resolution.bvQuantFactor) || Resolution.bvQuantFactor<=0)return nullptr;
    dtNavMesh* M=dtAllocNavMesh();if(!M || dtStatusFailed(M->init(&P))){dtFreeNavMesh(M);return nullptr;}
    M->setDeferOffMeshLinking(true);
    if(Order.Num()){uint8 Costs[DT_MAX_AREAS];FMemory::Memcpy(Costs,Order.GetData(),DT_MAX_AREAS);M->applyAreaCostOrder(Costs);}
    int32 Count;R<<Count;if(Count<0 || Count>P.maxTiles){dtFreeNavMesh(M);return nullptr;}
    for(int I=0;I<Count;++I)
    {
        if(R.TotalSize()-R.Tell()<12){dtFreeNavMesh(M);return nullptr;}uint64 Ref;int32 N;R<<Ref<<N;if(N<(int32)sizeof(dtMeshHeader) || N>R.TotalSize()-R.Tell()){dtFreeNavMesh(M);return nullptr;}
        auto* D=(unsigned char*)dtAlloc(N,DT_ALLOC_PERM_TILE_DATA);if(!D){dtFreeNavMesh(M);return nullptr;}R.Serialize(D,N);
        if(!ValidTile(D,N,P.maxPolys) || dtStatusFailed(M->addTile(D,N,DT_TILE_FREE_DATA,Ref,nullptr))){dtFree(D,DT_ALLOC_PERM_TILE_DATA);dtFreeNavMesh(M);return nullptr;}
    }
    if(R.IsError() || R.Tell()!=R.TotalSize()){dtFreeNavMesh(M);return nullptr;}M->connectDeferredOffMeshLinks();return M;
}
std::shared_ptr<dtNavMesh> ReadNavigation(const FString& Path,const TArray<uint8>& Order,bool& Hit)
{
    // Canonical exports and published Shadow files are content-addressed. Keep
    // at most two immutable meshes; mutable/legacy filenames remain uncached.
    struct Entry{FString Path;TArray<uint8> Order;std::filesystem::file_time_type Stamp;uintmax_t Size;std::shared_ptr<dtNavMesh> Mesh;};
    static std::mutex Mutex;static TArray<Entry> Entries;
    Hit=false;FString Digest=FPaths::GetBaseFilename(Path);
    bool Addressed=Digest.Len()==40;for(TCHAR C:Digest)Addressed&=(C>='0' && C<='9') || (C>='a' && C<='f');
    if(!Addressed)return {Load(Path,Order),dtFreeNavMesh};
    std::error_code Error;const std::filesystem::path File(*Path);
    const auto Stamp=std::filesystem::last_write_time(File,Error);if(Error)return {};
    const auto Size=std::filesystem::file_size(File,Error);if(Error)return {};
    std::lock_guard<std::mutex> Lock(Mutex);
    for(int I=0;I<Entries.Num();++I)if(Entries[I].Path==Path && Entries[I].Order==Order)
    {
        auto E=Entries[I];Entries.RemoveAt(I);
        if(E.Stamp==Stamp && E.Size==Size){Entries.Add(E);Hit=true;return E.Mesh;}break;
    }
    std::shared_ptr<dtNavMesh> Mesh(Load(Path,Order,Digest),dtFreeNavMesh);
    if(!Mesh)return {};
    // Do not cache a file that changed while it was being read/validated.
    if(std::filesystem::last_write_time(File,Error)!=Stamp || Error || std::filesystem::file_size(File,Error)!=Size || Error)return {};
    // ponytail: two meshes <=64MiB each; larger worlds still query correctly
    // without retention. A measured working-set budget can replace this cap.
    if(Size<=64ull*1024*1024 && Mesh->getMaxTiles()<=65536)
    {if(Entries.Num()==2)Entries.RemoveAt(0);Entries.Add({Path,Order,Stamp,Size,Mesh});}
    return Mesh;
}
bool Position(const TSharedPtr<FJsonObject>& J,const FString& Name,dtReal* P)
{
    const TArray<TSharedPtr<FJsonValue>>* A=nullptr;if(!J->TryGetArrayField(Name,A) || A->Num()!=3)return false;
    if(!Numbers(J->TryGetField(Name),3))return false;
    P[0]=-(*A)[0]->AsNumber();P[1]=(*A)[2]->AsNumber();P[2]=-(*A)[1]->AsNumber();return true;
}
TArray<TSharedPtr<FJsonValue>> UnrealPoint(const dtReal* P){return {MakeShared<FJsonValueNumber>(-P[0]),MakeShared<FJsonValueNumber>(-P[2]),MakeShared<FJsonValueNumber>(P[1])};}

bool ValidBuild(const TSharedPtr<FJsonObject>& J)
{
    const TSharedPtr<FJsonObject>* S=nullptr;const TArray<TSharedPtr<FJsonValue>>* Tiles=nullptr;
    if(!J->TryGetObjectField(TEXT("settings"),S) || !J->TryGetArrayField(TEXT("tiles"),Tiles) || Tiles->Num()>65536)return false;
    for(auto Key:{TEXT("cell_size"),TEXT("cell_height"),TEXT("height"),TEXT("radius"),TEXT("climb"),TEXT("slope"),TEXT("simplification_error")}){double V;if(!Number(*S,Key,V) || V<0 || V>1e6)return false;}
    for(const auto& Pair:(*S)->Values)if(Pair.Value->Type==EJson::Number && !FMath::IsFinite(Pair.Value->AsNumber()))return false;
    TSet<FIntPoint> Coordinates;
    for(auto TV:*Tiles)
    {
        const TSharedPtr<FJsonObject>* T=nullptr;if(!TV->TryGetObject(T))return false;
        for(auto Key:{TEXT("x"),TEXT("y"),TEXT("layer")}){double V;if(!Number(*T,Key,V) || V!=FMath::FloorToDouble(V) || FMath::Abs(V)>INT32_MAX)return false;}
        const FIntPoint Coordinate((*T)->GetIntegerField(TEXT("x")),(*T)->GetIntegerField(TEXT("y")));
        if(Coordinates.Contains(Coordinate))return false;Coordinates.Add(Coordinate);
        double Low,High;if(!Number(*T,TEXT("minimum_height"),Low) || !Number(*T,TEXT("maximum_height"),High) || High<=Low)return false;
        const TArray<TSharedPtr<FJsonValue>> *V=nullptr,*Triangles=nullptr;
        if(!(*T)->TryGetArrayField(TEXT("vertices"),V) || !(*T)->TryGetArrayField(TEXT("triangles"),Triangles) || V->Num()>4000000 || Triangles->Num()>4000000)return false;
        for(auto P:*V)if(!Numbers(P,3))return false;
        for(auto Triangle:*Triangles){if(!Numbers(Triangle,3))return false;for(auto I:Triangle->AsArray())if(I->AsNumber()<0 || I->AsNumber()>=V->Num() || I->AsNumber()!=FMath::FloorToDouble(I->AsNumber()))return false;}
        const TArray<TSharedPtr<FJsonValue>>* Groups=nullptr;
        if((*T)->HasField(TEXT("rasterization_groups")))
        {
            if(!(*T)->TryGetArrayField(TEXT("rasterization_groups"),Groups) || Groups->Num()>Triangles->Num())return false;
            int64 End=0;
            for(auto Value:*Groups)
            {
                const TSharedPtr<FJsonObject>* G=nullptr;double First,Count,Flags;
                if(!Value->TryGetObject(G) || !Number(*G,TEXT("first_triangle"),First) || First!=End ||
                   !Number(*G,TEXT("triangle_count"),Count) || Count<1 || Count!=FMath::FloorToDouble(Count) || Count>Triangles->Num()-End ||
                   !Number(*G,TEXT("flags"),Flags) || Flags<0 || Flags>3 || Flags!=FMath::FloorToDouble(Flags))return false;
                End+=int64(Count);
            }
            if(End!=Triangles->Num())return false;
        }
        const TArray<TSharedPtr<FJsonValue>>* Bounds=nullptr;if((*T)->TryGetArrayField(TEXT("navigation_bounds"),Bounds))for(auto B:*Bounds){if(B->Type!=EJson::Array || B->AsArray().Num()!=2 || !Numbers(B->AsArray()[0],3) || !Numbers(B->AsArray()[1],3))return false;}
        const TArray<TSharedPtr<FJsonValue>>* Modifiers=nullptr;
        if((*T)->TryGetArrayField(TEXT("modifiers"),Modifiers))for(auto MV:*Modifiers)
        {
            const TSharedPtr<FJsonObject>* M=nullptr;if(!MV->TryGetObject(M))return false;
            if((*M)->HasField(TEXT("mask_fill_underneath")) && !(*M)->HasTypedField<EJson::Boolean>(TEXT("mask_fill_underneath")))return false;
            double Shape,Mode,Area;if(!Number(*M,TEXT("shape"),Shape) || Shape<1 || Shape>3 || Shape!=FMath::FloorToDouble(Shape) || !Number(*M,TEXT("mode"),Mode) || Mode<0 || Mode>3 || Mode!=FMath::FloorToDouble(Mode) || !Number(*M,TEXT("area_id"),Area) || Area<0 || Area>=DT_MAX_AREAS)return false;
            double Replace;if(Mode==1 && (!Number(*M,TEXT("replace_area_id"),Replace) || Replace<0 || Replace>=DT_MAX_AREAS))return false;
            const TArray<TSharedPtr<FJsonValue>>* B=nullptr;if(!(*M)->TryGetArrayField(TEXT("bounds"),B) || B->Num()!=2 || !Numbers((*B)[0],3) || !Numbers((*B)[1],3))return false;
            if(Shape==1){double Radius;if(!Numbers((*M)->TryGetField(TEXT("origin")),3) || !Number(*M,TEXT("radius"),Radius) || Radius<0)return false;}
            if(Shape==3){const TArray<TSharedPtr<FJsonValue>>* Points=nullptr;if(!(*M)->TryGetArrayField(TEXT("points"),Points) || Points->Num()<3 || Points->Num()>65536)return false;for(auto Point:*Points)if(!Numbers(Point,3))return false;}
        }
        const TSharedPtr<FJsonObject>* Areas=nullptr;
        if((*T)->TryGetObjectField(TEXT("areas"),Areas))for(const auto& Pair:(*Areas)->Values){const TSharedPtr<FJsonObject>* A=nullptr;double Id,Flags;if(!Pair.Value->TryGetObject(A) || !Number(*A,TEXT("id"),Id) || Id<0 || Id>=DT_MAX_AREAS || !Number(*A,TEXT("flags"),Flags) || Flags<0 || Flags>65535)return false;}
        const TArray<TSharedPtr<FJsonValue>>* Links=nullptr;
        if((*T)->HasField(TEXT("links")))
        {
            if(!(*T)->TryGetArrayField(TEXT("links"),Links) || Links->Num()>65535)return false;
            for(auto Value:*Links)
            {
                const TSharedPtr<FJsonObject>* Link=nullptr;if(!Value->TryGetObject(Link))return false;
                double Radius,Height,Area,Flags;bool Both;FString Id;
                if(!Numbers((*Link)->TryGetField(TEXT("start")),3) || !Numbers((*Link)->TryGetField(TEXT("end")),3) ||
                   !Number(*Link,TEXT("radius"),Radius) || Radius<0 || !Number(*Link,TEXT("height"),Height) || Height<0 ||
                   !Number(*Link,TEXT("area_id"),Area) || Area<0 || Area>=DT_MAX_AREAS || Area!=FMath::FloorToDouble(Area) ||
                   !Number(*Link,TEXT("flags"),Flags) || Flags<0 || Flags>65535 || Flags!=FMath::FloorToDouble(Flags) ||
                   !(*Link)->TryGetBoolField(TEXT("bidirectional"),Both) || !(*Link)->TryGetStringField(TEXT("user_id"),Id) || Id.IsEmpty())return false;
                for(TCHAR C:Id)if(C<'0' || C>'9')return false;
                for(auto Key:{TEXT("reversed"),TEXT("snap_to_cheapest_area"),TEXT("generated")})if((*Link)->HasField(Key) && !(*Link)->HasTypedField<EJson::Boolean>(Key))return false;
                if(Id.Len()>20 || (Id.Len()==20 && Id.Compare(TEXT("18446744073709551615"))>0))return false;
            }
        }
    }
    return true;
}

TArray<int> RasterizationMasks(const TSharedPtr<FJsonObject>& Tile,const rcHeightfield& HF,const rcConfig& C)
{
    TArray<int> Masks;const TArray<TSharedPtr<FJsonValue>>* Modifiers=nullptr;
    if(!Tile->TryGetArrayField(TEXT("modifiers"),Modifiers))return Masks;
    for(auto Value:*Modifiers)
    {
        auto M=Value->AsObject();bool Enabled=false;if(!M->TryGetBoolField(TEXT("mask_fill_underneath"),Enabled) || !Enabled)continue;
        int Shape=M->GetIntegerField(TEXT("shape"));if(Shape!=2 && Shape!=3)continue; // Native cylinder masks are ignored.
        auto Bounds=M->GetArrayField(TEXT("bounds")),Low=Bounds[0]->AsArray(),High=Bounds[1]->AsArray();
        rcReal Min[3]={-High[0]->AsNumber(),Low[2]->AsNumber(),-High[1]->AsNumber()},Max[3]={-Low[0]->AsNumber(),High[2]->AsNumber(),-Low[1]->AsNumber()};
        if(Max[0]<C.bmin[0]+C.borderSize.low*C.cs || Min[0]>C.bmax[0]-C.borderSize.high*C.cs ||
           Max[2]<C.bmin[2]+C.borderSize.low*C.cs || Min[2]>C.bmax[2]-C.borderSize.high*C.cs || Max[1]<C.bmin[1] || Min[1]>C.bmax[1])continue;
        TArray<FVector2D> Points;
        if(Shape==3)
        {
            Min[0]=Min[2]=DBL_MAX;Max[0]=Max[2]=-DBL_MAX;
            for(auto Point:M->GetArrayField(TEXT("points")))
            {
                auto P=Point->AsArray();FVector2D V(-P[0]->AsNumber(),-P[1]->AsNumber());Points.Add(V);
                Min[0]=FMath::Min(Min[0],V.X);Min[2]=FMath::Min(Min[2],V.Y);Max[0]=FMath::Max(Max[0],V.X);Max[2]=FMath::Max(Max[2],V.Y);
            }
        }
        // Unreal uses floor for box masks and truncation for convex masks.
        auto Cell=[&](double V,int Axis){double F=FMath::Clamp((V-HF.bmin[Axis])/HF.cs,-1.,double(FMath::Max(HF.width,HF.height)));return Shape==2?FMath::FloorToInt(F):FMath::TruncToInt(F);};
        int X0=FMath::Max(0,Cell(Min[0],0)),X1=FMath::Min(HF.width-1,Cell(Max[0],0));
        int Y0=FMath::Max(0,Cell(Min[2],2)),Y1=FMath::Min(HF.height-1,Cell(Max[2],2));
        if(Masks.IsEmpty())Masks.Init(-1,HF.width*HF.height);
        for(int Y=Y0;Y<=Y1;++Y)for(int X=X0;X<=X1;++X)
        {
            bool Inside=Shape==2;
            if(Shape==3)
            {
                FVector2D P(HF.bmin[0]+(X+.5)*HF.cs,HF.bmin[2]+(Y+.5)*HF.cs);
                for(int I=0,J=Points.Num()-1;I<Points.Num();J=I++)
                    if((Points[I].Y>P.Y)!=(Points[J].Y>P.Y) && P.X<(Points[J].X-Points[I].X)*(P.Y-Points[I].Y)/(Points[J].Y-Points[I].Y)+Points[I].X)Inside=!Inside;
            }
            if(Inside)Masks[X+Y*HF.width]&=~RC_PROJECT_TO_BOTTOM;
        }
    }
    return Masks;
}

bool ApplyModifiers(dtTileCacheLayer& Layer,const TSharedPtr<FJsonObject>& Tile,const rcConfig& C,double Height,bool LowPass)
{
    const TArray<TSharedPtr<FJsonValue>>* Modifiers=nullptr;if(!Tile->TryGetArrayField(TEXT("modifiers"),Modifiers))return true;
    for(auto Value:*Modifiers)
    {
        auto M=Value->AsObject();int Shape=M->GetIntegerField(TEXT("shape")),Mode=M->GetIntegerField(TEXT("mode")),Area=M->GetIntegerField(TEXT("area_id"));if((Mode>=2)!=LowPass)continue;
        int Replace=Mode==3?DT_MAX_AREAS-2:(Mode==1?M->GetIntegerField(TEXT("replace_area_id")):0);Mode=Mode==1 || Mode==3;
        auto B=M->GetArrayField(TEXT("bounds"));double Bottom=B[0]->AsArray()[2]->AsNumber(),Top=B[1]->AsArray()[2]->AsNumber();bool Flag=false;
        if(M->TryGetBoolField(TEXT("include_agent_height"),Flag) && Flag)Bottom-=Height;
        if(M->TryGetBoolField(TEXT("expand_top"),Flag) && Flag)Top+=C.ch;
        dtStatus Status=DT_FAILURE;
        if(Shape==2)
        {
            auto Min=B[0]->AsArray(),Max=B[1]->AsArray();dtReal Center[3]={-(Min[0]->AsNumber()+Max[0]->AsNumber())*.5,(Bottom+Top)*.5,-(Min[1]->AsNumber()+Max[1]->AsNumber())*.5};
            dtReal Extent[3]={(Max[0]->AsNumber()-Min[0]->AsNumber())*.5,(Top-Bottom)*.5,(Max[1]->AsNumber()-Min[1]->AsNumber())*.5};
            Status=Mode?dtReplaceBoxArea(Layer,Layer.header->bmin,C.cs,C.ch,Center,Extent,Area,Replace):dtMarkBoxArea(Layer,Layer.header->bmin,C.cs,C.ch,Center,Extent,Area);
        }
        else if(Shape==1)
        {
            dtReal P[3];Position(M,TEXT("origin"),P);P[1]=Bottom;double R=M->GetNumberField(TEXT("radius"));
            Status=Mode?dtReplaceCylinderArea(Layer,Layer.header->bmin,C.cs,C.ch,P,R,Top-Bottom,Area,Replace):dtMarkCylinderArea(Layer,Layer.header->bmin,C.cs,C.ch,P,R,Top-Bottom,Area);
        }
        else
        {
            TArray<dtReal> Points;for(auto V:M->GetArrayField(TEXT("points"))){auto P=V->AsArray();Points.Add(-P[0]->AsNumber());Points.Add(P[2]->AsNumber());Points.Add(-P[1]->AsNumber());}
            Status=Mode?dtReplaceConvexArea(Layer,Layer.header->bmin,C.cs,C.ch,Points.GetData(),Points.Num()/3,Bottom,Top,Area,Replace):dtMarkConvexArea(Layer,Layer.header->bmin,C.cs,C.ch,Points.GetData(),Points.Num()/3,Bottom,Top,Area);
        }
        if(dtStatusFailed(Status))return false;
    }
    return true;
}

// Use Unreal's exported layered tile-cache pipeline, preserving all elevations.
bool PublishLayers(dtNavMesh* Mesh,rcContext& Context,rcCompactHeightfield& Compact,const rcConfig& C,
                   const TSharedPtr<FJsonObject>& Settings,const TSharedPtr<FJsonObject>& Tile,double Height,double Radius,double Climb,FString& Error)
{
    std::unique_ptr<rcHeightfieldLayerSet,decltype(&rcFreeHeightfieldLayerSet)> Layers(rcAllocHeightfieldLayerSet(),rcFreeHeightfieldLayerSet);
    if(!Layers)return false;
    int Partition=Setting(Settings,TEXT("layer_partitioning"),1),Chunks=FMath::Max(1,C.tileSize/(int)FMath::Max(1.,Setting(Settings,TEXT("layer_chunk_splits"),2)));
    bool Ok=Partition==0?rcBuildHeightfieldLayersMonotone(&Context,Compact,C.borderSize,C.walkableHeight,*Layers):
        Partition==1?(rcBuildDistanceField(&Context,Compact) && rcBuildHeightfieldLayers(&Context,Compact,C.borderSize,C.walkableHeight,*Layers)):
        rcBuildHeightfieldLayersChunky(&Context,Compact,C.borderSize,C.walkableHeight,Chunks,*Layers);
    if(!Ok)return false;
    int X=Tile->GetIntegerField(TEXT("x")),Y=Tile->GetIntegerField(TEXT("y"));
    dtTileCacheAlloc Alloc;dtTileCacheLogContext Log;
    for(int L=0;L<Layers->nlayers;++L)
    {
        auto& Source=Layers->layers[L];dtTileCacheLayerHeader Header{};Header.version=DT_TILECACHE_VERSION;Header.tx=X;Header.ty=Y;Header.tlayer=L;
        Header.width=Source.width;Header.height=Source.height;Header.minx=Source.minx;Header.maxx=Source.maxx;Header.miny=Source.miny;Header.maxy=Source.maxy;
        FMemory::Memcpy(Header.bmin,Source.bmin,sizeof(Header.bmin));FMemory::Memcpy(Header.bmax,Source.bmax,sizeof(Header.bmax));
        TArray<uint16> Regions;Regions.SetNumZeroed(Source.width*Source.height);dtTileCacheLayer Layer{&Header,0,Source.heights,Source.areas,Source.cons,Regions.GetData()};
        bool Low=false;Settings->TryGetBoolField(TEXT("mark_low_height_areas"),Low);
        if(Low){if(!ApplyModifiers(Layer,Tile,C,Height,true))return false;dtReplaceArea(Layer,0,DT_MAX_AREAS-2);}
        if(!ApplyModifiers(Layer,Tile,C,Height,false))return false;
        auto FreeContours=[&](dtTileCacheContourSet* P){dtFreeTileCacheContourSet(&Alloc,P);};auto FreePolys=[&](dtTileCachePolyMesh* P){dtFreeTileCachePolyMesh(&Alloc,P);};auto FreeDetail=[&](dtTileCachePolyMeshDetail* P){dtFreeTileCachePolyMeshDetail(&Alloc,P);};auto FreeDistance=[&](dtTileCacheDistanceField* P){dtFreeTileCacheDistanceField(&Alloc,P);};
        std::unique_ptr<dtTileCacheContourSet,decltype(FreeContours)> Contours(dtAllocTileCacheContourSet(&Alloc),FreeContours);
        std::unique_ptr<dtTileCachePolyMesh,decltype(FreePolys)> Polys(dtAllocTileCachePolyMesh(&Alloc),FreePolys);
        std::unique_ptr<dtTileCachePolyMeshDetail,decltype(FreeDetail)> Detail(dtAllocTileCachePolyMeshDetail(&Alloc),FreeDetail);
        std::unique_ptr<dtTileCacheDistanceField,decltype(FreeDistance)> Distance(dtAllocTileCacheDistanceField(&Alloc),FreeDistance);
        if(!Contours || !Polys || !Detail || !Distance)return false;
        int RegionsPartition=Setting(Settings,TEXT("region_partitioning"),1);dtStatus Status;
        if(RegionsPartition==0)Status=dtBuildTileCacheRegionsMonotone(&Alloc,C.minRegionArea,C.mergeRegionArea,Layer);
        else if(RegionsPartition==1){Status=dtBuildTileCacheDistanceField(&Alloc,Layer,*Distance);if(dtStatusSucceed(Status))Status=dtBuildTileCacheRegions(&Alloc,C.minRegionArea,C.mergeRegionArea,Layer,*Distance);}
        else Status=dtBuildTileCacheRegionsChunky(&Alloc,C.minRegionArea,C.mergeRegionArea,Layer,FMath::Max(1,C.tileSize/(int)FMath::Max(1.,Setting(Settings,TEXT("region_chunk_splits"),2))));
        if(dtStatusFailed(Status))return false;if(!Layer.regCount)continue;
#if WITH_NAVMESH_CLUSTER_LINKS
        auto FreeClusters=[&](dtTileCacheClusterSet* P){dtFreeTileCacheClusterSet(&Alloc,P);};std::unique_ptr<dtTileCacheClusterSet,decltype(FreeClusters)> Clusters(dtAllocTileCacheClusterSet(&Alloc),FreeClusters);if(!Clusters)return false;
        Status=dtBuildTileCacheContours(&Alloc,Layer,C.walkableClimb,C.maxSimplificationError,Setting(Settings,TEXT("simplification_elevation_ratio"),1),C.cs,C.ch,*Contours,*Clusters);
#else
        Status=dtBuildTileCacheContours(&Alloc,Layer,C.walkableClimb,C.maxSimplificationError,Setting(Settings,TEXT("simplification_elevation_ratio"),1),C.cs,C.ch,*Contours);
#endif
        if(dtStatusFailed(Status))return false;if(!Contours->nconts)continue;
        if(dtStatusFailed(dtBuildTileCachePolyMesh(&Alloc,&Log,*Contours,*Polys,C.walkableClimb)))return false;if(!Polys->npolys)continue;
        if(dtStatusFailed(dtBuildTileCachePolyMeshDetail(&Alloc,C.cs,C.ch,C.detailSampleDist,C.detailSampleMaxError,Layer,*Polys,*Detail)))return false;
#if WITH_NAVMESH_CLUSTER_LINKS
        if(dtStatusFailed(dtBuildTileCacheClusters(&Alloc,*Clusters,*Polys)))return false;
#endif
        for(int I=0;I<Polys->npolys;++I){Polys->flags[I]=1;if(Tile->HasTypedField<EJson::Object>(TEXT("areas")))for(const auto& A:Tile->GetObjectField(TEXT("areas"))->Values){auto Data=A.Value->AsObject();if(Data->GetIntegerField(TEXT("id"))==Polys->areas[I])Polys->flags[I]=Data->GetIntegerField(TEXT("flags"));}}
        dtNavMeshCreateParams P{};P.verts=Polys->verts;P.vertCount=Polys->nverts;P.polys=Polys->polys;P.polyAreas=Polys->areas;P.polyFlags=Polys->flags;P.polyCount=Polys->npolys;P.nvp=Polys->nvp;
        P.detailMeshes=Detail->meshes;P.detailVerts=Detail->verts;P.detailVertsCount=Detail->nverts;P.detailTris=Detail->tris;P.detailTriCount=Detail->ntris;
        P.tileX=X;P.tileY=Y;P.tileLayer=L;FMemory::Memcpy(P.bmin,Header.bmin,sizeof(P.bmin));FMemory::Memcpy(P.bmax,Header.bmax,sizeof(P.bmax));P.walkableHeight=Height;P.walkableRadius=Radius;P.walkableClimb=Climb;P.cs=C.cs;P.ch=C.ch;P.buildBvTree=Polys->npolys>16;
        TArray<dtOffMeshLinkCreateParams> Links;const TArray<TSharedPtr<FJsonValue>>* LinkValues=nullptr;
        if(Tile->TryGetArrayField(TEXT("links"),LinkValues))for(auto Value:*LinkValues)
        {
            auto Link=Value->AsObject();dtOffMeshLinkCreateParams Params{};
            Position(Link,TEXT("start"),Params.vertsA0);Position(Link,TEXT("end"),Params.vertsB0);
            Params.snapRadius=Link->GetNumberField(TEXT("radius"));Params.snapHeight=Link->GetNumberField(TEXT("height"));
            Params.area=Link->GetIntegerField(TEXT("area_id"));Params.polyFlag=Link->GetIntegerField(TEXT("flags"));
            Params.type=DT_OFFMESH_CON_POINT|(Link->GetBoolField(TEXT("bidirectional"))?DT_OFFMESH_CON_BIDIR:0);
            bool Flag=false;if(Link->TryGetBoolField(TEXT("reversed"),Flag) && Flag)Params.type|=DT_OFFMESH_CON_REVERSED;
            if(Link->TryGetBoolField(TEXT("snap_to_cheapest_area"),Flag) && Flag)Params.type|=DT_OFFMESH_CON_CHEAPAREA;
            if(Link->TryGetBoolField(TEXT("generated"),Flag) && Flag)Params.type|=DT_OFFMESH_CON_GENERATED;
            Params.userID=FCString::Strtoui64(*Link->GetStringField(TEXT("user_id")),nullptr,10);Links.Add(Params);
        }
        P.offMeshCons=Links.GetData();P.offMeshConCount=Links.Num();
#if WITH_NAVMESH_CLUSTER_LINKS
        P.clusterCount=Clusters->nclusters;P.polyClusters=Clusters->polyMap;
#endif
        unsigned char* Data=nullptr;int Size=0;if(!dtCreateNavMeshData(&P,&Data,&Size))return false;
        const dtStatus Added=Mesh->addTile(Data,Size,DT_TILE_FREE_DATA,0,nullptr);
        if(dtStatusFailed(Added))
        {
            dtFree(Data,DT_ALLOC_PERM_TILE_DATA);
            Error=FString::Printf(TEXT("Detour addTile failed at (%d,%d,%d): status=0x%08x, capacity=%d"),X,Y,L,Added,Mesh->getMaxTiles());
            if(Added&DT_OUT_OF_MEMORY)
            {
                int Active=0;const dtNavMesh* Read=Mesh;
                for(int I=0;I<Read->getMaxTiles();++I)if(Read->getTile(I)->header)++Active;
                if(Active==Mesh->getMaxTiles())Error=FString::Printf(TEXT("Navigation tile pool exhausted at (%d,%d,%d): %d/%d; native capacity/coverage requires refresh"),X,Y,L,Active,Mesh->getMaxTiles());
            }
            return false;
        }
    }
    return true;
}
}
bool STSaveNavmesh(const dtNavMesh* M,const FString& Path)
{
    if(!M || !M->getParams())return false;TArray<uint8> Bytes;FMemoryWriter W(Bytes);uint32 Magic=0x314E5453,Version=2;W<<Magic<<Version;
    dtNavMeshParams P=*M->getParams();Params(W,P);int32 Count=0;
    for(int I=0;I<M->getMaxTiles();++I){auto T=M->getTile(I);if(T && T->header && T->dataSize>0)++Count;}
    W<<Count;for(int I=0;I<M->getMaxTiles();++I){auto T=M->getTile(I);if(!T || !T->header || T->dataSize<=0)continue;uint64 Ref=M->getTileRef(T);int32 N=T->dataSize;W<<Ref<<N;W.Serialize(T->data,N);}
    std::ofstream File(std::filesystem::path(*Path),std::ios::binary|std::ios::trunc);return File && bool(File.write((const char*)Bytes.GetData(),Bytes.Num()));
}
extern "C" int STNavigationRasterizationVersion(){return 2;}
extern "C" int STNavigationQuery(const char* Path,const char* Request,char* Output,int Capacity)
{
    TSharedPtr<FJsonObject> J;if(!Parse(Request,J))return NavError(TEXT("Invalid request"),Output,Capacity);
    FString Mode;if(!J->TryGetStringField(TEXT("mode"),Mode) || (Mode!=TEXT("path") && Mode!=TEXT("nearest") && Mode!=TEXT("navigable") && Mode!=TEXT("status")))return NavError(TEXT("Invalid query mode"),Output,Capacity);
    dtReal A[3],B[3],Extent[3]={80,250,80},PA[3],PB[3];if(Mode!=TEXT("status") && !Position(J,TEXT("start"),A))return NavError(TEXT("Invalid start"),Output,Capacity);
    if(J->HasField(TEXT("extent"))){if(!Numbers(J->TryGetField(TEXT("extent")),3))return NavError(TEXT("Invalid query extent"),Output,Capacity);auto E=J->GetArrayField(TEXT("extent"));Extent[0]=E[0]->AsNumber();Extent[1]=E[2]->AsNumber();Extent[2]=E[1]->AsNumber();for(auto V:Extent)if(V<=0)return NavError(TEXT("Invalid query extent"),Output,Capacity);}
    TArray<uint8> Order;if(!LinkOrder(J,Order))return NavError(TEXT("Invalid native link area order"),Output,Capacity);
    bool CacheHit=false;auto Mesh=ReadNavigation(UTF8_TO_TCHAR(Path),Order,CacheHit);dtNavMesh* M=Mesh.get();if(!M)return NavError(TEXT("Invalid or missing navigation cache"),Output,Capacity);
    if(Mode!=TEXT("status") && Order.IsEmpty() && HasCostSnappedLinks(M))return NavError(TEXT("Native link area order required to reproduce cost-based snapping"),Output,Capacity);
    if(Mode==TEXT("status")){auto R=MakeShared<FJsonObject>();int Active=0;const dtNavMesh* Read=M;for(int I=0;I<Read->getMaxTiles();++I)if(Read->getTile(I)->header)++Active;R->SetStringField(TEXT("state"),Active?TEXT("READY"):TEXT("UNKNOWN"));R->SetNumberField(TEXT("active_tiles"),Active);R->SetNumberField(TEXT("tile_capacity"),M->getMaxTiles());R->SetBoolField(TEXT("tile_pool_full"),Active==M->getMaxTiles());R->SetNumberField(TEXT("cache_version"),2);R->SetBoolField(TEXT("cache_hit"),CacheHit);return Result(R,Output,Capacity);}
    struct RuntimeLinks:dtQuerySpecialLinkFilter
    {
        mutable TSet<uint64> Unknown;
        virtual bool isLinkAllowed(const unsigned long long int Id)const override {Unknown.Add(Id);return false;}
    } LinkFilter;
    dtNavMeshQuery Q;dtQueryFilter Filter;if(dtStatusFailed(Q.init(M,65536,&LinkFilter)))return NavError(TEXT("Navigation query allocation failed"),Output,Capacity);dtPolyRef RA=0,RB=0;
    if(J->HasField(TEXT("filter")))
    {
        const TSharedPtr<FJsonObject>* F=nullptr;if(!J->TryGetObjectField(TEXT("filter"),F)){return NavError(TEXT("Invalid filter"),Output,Capacity);}
        for(auto Key:{TEXT("include_flags"),TEXT("exclude_flags")})if((*F)->HasField(Key))
        {
            double V;if(!Number(*F,Key,V) || V<0 || V>65535 || V!=FMath::FloorToDouble(V)){return NavError(TEXT("Invalid filter flags"),Output,Capacity);}
            if(FString(Key)==TEXT("include_flags"))Filter.setIncludeFlags((uint16)V);else Filter.setExcludeFlags((uint16)V);
        }
        for(auto Key:{TEXT("costs"),TEXT("fixed_costs")})if((*F)->HasField(Key))
        {
            const TArray<TSharedPtr<FJsonValue>>* Costs=nullptr;if(!(*F)->TryGetArrayField(Key,Costs) || Costs->Num()>DT_MAX_AREAS){return NavError(TEXT("Invalid filter costs"),Output,Capacity);}
            for(int I=0;I<Costs->Num();++I){double V;if(!(*Costs)[I]->TryGetNumber(V) || !FMath::IsFinite(V) || V<0 || V>FLT_MAX){return NavError(TEXT("Invalid area cost"),Output,Capacity);}if(FString(Key)==TEXT("costs"))Filter.setAreaCost(I,V);else Filter.setAreaFixedCost(I,V);}
        }
    }
    Q.findNearestPoly(A,Extent,&Filter,&RA,PA);auto R=MakeShared<FJsonObject>();R->SetStringField(TEXT("state"),RA?TEXT("READY"):TEXT("UNKNOWN"));
    R->SetBoolField(TEXT("cache_hit"),CacheHit);R->SetBoolField(TEXT("navigable"),RA && FMath::Abs(A[0]-PA[0])<1 && FMath::Abs(A[2]-PA[2])<1 && FMath::Abs(A[1]-PA[1])<10);
    if(RA)R->SetArrayField(TEXT("position"),UnrealPoint(PA));
    if(Mode==TEXT("path") && RA)
    {
        if(!Position(J,TEXT("end"),B)){return NavError(TEXT("Invalid end"),Output,Capacity);}
        R->SetArrayField(TEXT("requested_start"),UnrealPoint(A));R->SetArrayField(TEXT("requested_end"),UnrealPoint(B));
        R->SetArrayField(TEXT("projected_start"),UnrealPoint(PA));
        Q.findNearestPoly(B,Extent,&Filter,&RB,PB);bool Reachable=false;TArray<TSharedPtr<FJsonValue>> Points;
        if(RB)
        {
            R->SetArrayField(TEXT("projected_end"),UnrealPoint(PB));
            R->SetNumberField(TEXT("end_projection_distance_cm"),FMath::Sqrt(dtVdistSqr(B,PB)));
            dtQueryResult Corridor;dtReal Cost=0;dtStatus Status=Q.findPath(RA,RB,PA,PB,DBL_MAX,&Filter,Corridor,&Cost);
            Reachable=dtStatusSucceed(Status) && Corridor.size()>0 && Corridor.getRef(Corridor.size()-1)==RB;
            if(Reachable)
            {
                // Detour returns early without a cost for one polygon. Match
                // Unreal's CalcSegmentCostOnPoly (travel cost, no entry charge).
                if(Corridor.size()==1){unsigned char Area=0;M->getPolyArea(RA,&Area);Cost=Filter.getAreaCost(Area)*FMath::Sqrt(dtVdistSqr(PA,PB));}
                TArray<dtPolyRef> Refs;for(int I=0;I<Corridor.size();++I)Refs.Add(Corridor.getRef(I));dtQueryResult Straight;
                if(dtStatusSucceed(Q.findStraightPath(PA,PB,Refs.GetData(),Refs.Num(),Straight)))for(int I=0;I<Straight.size();++I)Points.Add(MakeShared<FJsonValueArray>(UnrealPoint(Straight.getPos(I))));
                if(Points.IsEmpty()){Reachable=false;R->SetStringField(TEXT("state"),TEXT("UNKNOWN"));R->SetStringField(TEXT("reason"),TEXT("Path corridor could not produce a straight path"));}
                R->SetNumberField(TEXT("cost"),Cost);
            }
            if(!Reachable && ((Status&DT_OUT_OF_NODES)!=0 || LinkFilter.Unknown.Num()))
            {R->SetStringField(TEXT("state"),TEXT("UNKNOWN"));R->SetStringField(TEXT("reason"),LinkFilter.Unknown.Num()?TEXT("No confirmed path without runtime-controlled navigation links"):TEXT("Navigation search node budget exhausted"));}
        }
        R->SetBoolField(TEXT("reachable"),Reachable);R->SetArrayField(TEXT("path"),Points);if(!RB)R->SetStringField(TEXT("state"),TEXT("UNKNOWN"));
        if(LinkFilter.Unknown.Num()){TArray<TSharedPtr<FJsonValue>> Ids;for(uint64 Id:LinkFilter.Unknown)if(Ids.Num()<32)Ids.Add(MakeShared<FJsonValueString>(LexToString(Id)));R->SetArrayField(TEXT("runtime_links_excluded"),Ids);R->SetNumberField(TEXT("runtime_links_excluded_count"),LinkFilter.Unknown.Num());}
    }
    return Result(R,Output,Capacity);
}
extern "C" int STBuildNavigation(const char* Path,const char* Request,const char* OutputPath,char* Output,int Capacity)
{
    TSharedPtr<FJsonObject> J;if(!Parse(Request,J) || !ValidBuild(J))return NavError(TEXT("Invalid build request"),Output,Capacity);
    auto Settings=J->GetObjectField(TEXT("settings"));rcConfig C{};
    C.cs=Settings->GetNumberField(TEXT("cell_size"));C.ch=Settings->GetNumberField(TEXT("cell_height"));C.walkableSlopeAngle=Settings->GetNumberField(TEXT("slope"));
    double Height=Settings->GetNumberField(TEXT("height")),Radius=Settings->GetNumberField(TEXT("radius")),Climb=Settings->GetNumberField(TEXT("climb"));
    if(C.cs<=0 || C.ch<=0 || Height<=0 || Radius<0 || Climb<0)return NavError(TEXT("Invalid build settings"),Output,Capacity);
    bool Low=false,FilterLow=false,Sequences=false;Settings->TryGetBoolField(TEXT("mark_low_height_areas"),Low);Settings->TryGetBoolField(TEXT("filter_low_from_cache"),FilterLow);Settings->TryGetBoolField(TEXT("filter_low_spans"),Sequences);
    int RealHeight=FMath::CeilToInt(Height/C.ch);C.walkableHeight=Low?1:RealHeight;C.walkableClimb=FMath::CeilToInt(Climb/C.ch);C.walkableRadius=FMath::CeilToInt(Radius/C.cs);C.borderSize={C.walkableRadius+3,C.walkableRadius+3};
    C.maxVertsPerPoly=6;C.maxSimplificationError=Settings->GetNumberField(TEXT("simplification_error"));C.minRegionArea=FMath::Square(Setting(Settings,TEXT("min_region_area"),0)/C.cs);C.mergeRegionArea=FMath::Square(Setting(Settings,TEXT("merge_region_size"),0)/C.cs);C.detailSampleDist=600;C.detailSampleMaxError=1;
    TArray<uint8> Order;if(!LinkOrder(J,Order))return NavError(TEXT("Invalid native link area order"),Output,Capacity);
    dtNavMesh* M=Load(UTF8_TO_TCHAR(Path),Order);if(!M)return NavError(TEXT("Baseline navmesh required"),Output,Capacity);
    if(Order.IsEmpty() && HasCostSnappedLinks(M)){dtFreeNavMesh(M);return NavError(TEXT("Native link area order required before rebuilding"),Output,Capacity);}
    const TArray<TSharedPtr<FJsonValue>>& Tiles=J->GetArrayField(TEXT("tiles"));rcContext Context(false);
    if(Order.IsEmpty())for(auto Value:Tiles)
    {
        const TArray<TSharedPtr<FJsonValue>>* Links=nullptr;if(Value->AsObject()->TryGetArrayField(TEXT("links"),Links))for(auto L:*Links)
        {bool Cheapest=false;if(L->AsObject()->TryGetBoolField(TEXT("snap_to_cheapest_area"),Cheapest) && Cheapest){dtFreeNavMesh(M);return NavError(TEXT("Native link area order required before rebuilding"),Output,Capacity);}}
    }
    // Remove the complete replacement set once, on the private loaded copy.
    // Otherwise a growing coordinate can overflow before a shrinking one frees
    // space, and every coordinate performs a whole-pool scan.
    TSet<FIntPoint> Replaced;
    for(const auto& Value:Tiles){auto Tile=Value->AsObject();Replaced.Add(FIntPoint(Tile->GetIntegerField(TEXT("x")),Tile->GetIntegerField(TEXT("y"))));}
    TArray<dtTileRef> Old;const dtNavMesh* Read=M;
    for(int I=0;I<Read->getMaxTiles();++I){auto Tile=Read->getTile(I);if(Tile->header && Replaced.Contains(FIntPoint(Tile->header->x,Tile->header->y)))Old.Add(M->getTileRef(Tile));}
    for(auto Ref:Old)if(dtStatusFailed(M->removeTile(Ref,nullptr,nullptr))){dtFreeNavMesh(M);return NavError(TEXT("Navigation tile removal failed"),Output,Capacity);}
    for(const auto& TileValue:Tiles)
    {
        auto Tile=TileValue->AsObject();int X=Tile->GetIntegerField(TEXT("x")),Y=Tile->GetIntegerField(TEXT("y")),Layer=Tile->GetIntegerField(TEXT("layer"));
        auto Params=M->getParams();C.tileSize=FMath::TruncToInt(Params->tileWidth/C.cs);C.width=C.tileSize+(C.borderSize.low+C.borderSize.high);C.height=C.width;
        if(C.width<=0 || C.width>8192){dtFreeNavMesh(M);return NavError(TEXT("Tile voxel budget exceeded"),Output,Capacity);}
        C.bmin[0]=Params->orig[0]+X*Params->tileWidth-C.borderSize.low*C.cs;C.bmax[0]=C.bmin[0]+C.width*C.cs;
        C.bmin[2]=Params->orig[2]+Y*Params->tileHeight-C.borderSize.low*C.cs;C.bmax[2]=C.bmin[2]+C.height*C.cs;
        C.bmin[1]=Tile->GetNumberField(TEXT("minimum_height"));C.bmax[1]=Tile->GetNumberField(TEXT("maximum_height"));
        TArray<rcReal> Vertices;TArray<int> Indices;
        for(const auto& V:Tile->GetArrayField(TEXT("vertices"))) {auto A=V->AsArray();Vertices.Add(-A[0]->AsNumber());Vertices.Add(A[2]->AsNumber());Vertices.Add(-A[1]->AsNumber());}
        for(const auto& V:Tile->GetArrayField(TEXT("triangles")))for(const auto& I:V->AsArray())Indices.Add(I->AsNumber());
        std::unique_ptr<rcHeightfield,decltype(&rcFreeHeightField)> HF(rcAllocHeightfield(),rcFreeHeightField);
        std::unique_ptr<rcCompactHeightfield,decltype(&rcFreeCompactHeightfield)> CH(rcAllocCompactHeightfield(),rcFreeCompactHeightfield);
        const TArray<TSharedPtr<FJsonValue>>* Groups=nullptr;bool FilledConvex=false;
        if(Tile->TryGetArrayField(TEXT("rasterization_groups"),Groups))for(auto Value:*Groups)FilledConvex|=(Value->AsObject()->GetIntegerField(TEXT("flags"))&RC_RASTERIZE_AS_FILLED_CONVEX)!=0;
        if(!HF || !CH || !rcCreateHeightfield(&Context,*HF,C.width,C.height,C.bmin,C.bmax,C.cs,C.ch,FilledConvex)) {dtFreeNavMesh(M);return NavError(TEXT("Heightfield allocation failed"),Output,Capacity);}
        auto Masks=RasterizationMasks(Tile,*HF,C);const int* MaskData=Masks.IsEmpty()?nullptr:Masks.GetData();
        TArray<unsigned char> Areas;Areas.SetNumZeroed(Indices.Num()/3);rcMarkWalkableTriangles(&Context,C.walkableSlopeAngle,Vertices.GetData(),Vertices.Num()/3,Indices.GetData(),Areas.Num(),Areas.GetData());
        if(Groups)
        {
            for(auto Value:*Groups)
            {
                auto Group=Value->AsObject();int First=Group->GetIntegerField(TEXT("first_triangle")),Count=Group->GetIntegerField(TEXT("triangle_count"));
                rcReal Min[3]={DBL_MAX,DBL_MAX,DBL_MAX},Max[3]={-DBL_MAX,-DBL_MAX,-DBL_MAX};
                for(int I=First*3;I<(First+Count)*3;++I){rcVmin(Min,Vertices.GetData()+Indices[I]*3);rcVmax(Max,Vertices.GetData()+Indices[I]*3);}
                rcRasterizeTriangles(&Context,Vertices.GetData(),Vertices.Num()/3,Indices.GetData()+First*3,Areas.GetData()+First,Count,*HF,C.walkableClimb,(rcRasterizationFlags)Group->GetIntegerField(TEXT("flags")),MaskData,Min,Max);
            }
        }
        else rcRasterizeTriangles(&Context,Vertices.GetData(),Vertices.Num()/3,Indices.GetData(),Areas.GetData(),Areas.Num(),*HF,C.walkableClimb);
        bool Voxel=false;Settings->TryGetBoolField(TEXT("voxel_filtering"),Voxel);const TArray<TSharedPtr<FJsonValue>>* BoundValues=nullptr;
        if(Voxel && Tile->TryGetArrayField(TEXT("navigation_bounds"),BoundValues) && !BoundValues->IsEmpty())
        {
            TArray<FBox> Bounds;for(auto Value:*BoundValues){auto B=Value->AsArray();auto Min=B[0]->AsArray(),Max=B[1]->AsArray();Bounds.Add(FBox(FVector(Min[0]->AsNumber(),Min[1]->AsNumber(),Min[2]->AsNumber()),FVector(Max[0]->AsNumber(),Max[1]->AsNumber(),Max[2]->AsNumber())).ExpandBy(C.walkableRadius*C.cs));}
            for(int Row=0;Row<HF->height;++Row)for(int Column=0;Column<HF->width;++Column)for(rcSpan* Span=HF->spans[Column+Row*HF->width];Span;Span=Span->next)
            {
                if(Span->data.area!=RC_WALKABLE_AREA)continue;double PX=-(HF->bmin[0]+Column*C.cs),PY=-(HF->bmin[2]+Row*C.cs);
                FVector Min(PX-C.cs,PY-C.cs,HF->bmin[1]+Span->data.smin*C.ch),Max(PX,PY,HF->bmin[1]+Span->data.smax*C.ch);bool Inside=false;
                for(auto B:Bounds)if(B.IsInside(Min) || B.IsInside(Max)){Inside=true;break;}if(!Inside)Span->data.area=RC_NULL_AREA;
            }
        }
        rcFilterLowHangingWalkableObstacles(&Context,C.walkableClimb,*HF);rcFilterLedgeSpans(&Context,C.walkableHeight,C.walkableClimb,(rcNeighborSlopeFilterMode)(int)Setting(Settings,TEXT("ledge_slope_filter"),0),C.cs*FMath::Tan(FMath::DegreesToRadians(C.walkableSlopeAngle)),C.ch,*HF);
        if(!Low)rcFilterWalkableLowHeightSpans(&Context,C.walkableHeight,*HF);else if(FilterLow){if(Sequences)rcFilterWalkableLowHeightSpansSequences(&Context,RealHeight,*HF);else rcFilterWalkableLowHeightSpans(&Context,RealHeight,*HF);}
        if(!rcGetHeightFieldSpanCount(&Context,*HF))continue;
        bool Ok=rcBuildCompactHeightfield(&Context,C.walkableHeight,C.walkableClimb,*HF,*CH);
        if(Ok){if(C.walkableRadius>0)Ok=Low?rcErodeWalkableAndLowAreas(&Context,C.walkableRadius,RealHeight,DT_MAX_AREAS-2,Sequences?(RC_LOW_FILTER_POST_PROCESS | (FilterLow?0:RC_LOW_FILTER_SEED_SPANS)):0,*CH):rcErodeWalkableArea(&Context,C.walkableRadius,*CH);else if(Low)Ok=rcMarkLowAreas(&Context,RealHeight,DT_MAX_AREAS-2,*CH);}
        FString Error=FString::Printf(TEXT("Layered Recast build failed at (%d,%d)"),X,Y);
        if(Ok)Ok=PublishLayers(M,Context,*CH,C,Settings,Tile,Height,Radius,Climb,Error);
        if(!Ok){dtFreeNavMesh(M);return NavError(Error,Output,Capacity);}

    }
    bool Saved=STSaveNavmesh(M,UTF8_TO_TCHAR(OutputPath));dtFreeNavMesh(M);
    if(!Saved)return NavError(TEXT("Navigation save failed"),Output,Capacity);
    auto R=MakeShared<FJsonObject>();R->SetStringField(TEXT("state"),TEXT("READY"));R->SetNumberField(TEXT("rebuilt_tiles"),Tiles.Num());return Result(R,Output,Capacity);
}
