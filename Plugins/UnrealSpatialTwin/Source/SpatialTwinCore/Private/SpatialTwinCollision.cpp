#include "CoreMinimal.h"
#include "SpatialTwinDatabase.h"
#include "Chaos/GJK.h"
#include "Serialization/JsonSerializer.h"

namespace
{
struct SupportShape
{
    TArray<FVector> Vertices;
    double Margin=0;
    float GetMarginf() const{return Margin;}
    FVector SupportCore(const FVector& Direction,double,Chaos::FReal* Delta,int32& Index) const
    {
        if(Delta)*Delta=0;Index=0;double Maximum=-DBL_MAX;
        for(int32 I=0;I<Vertices.Num();++I){double D=FVector::DotProduct(Direction,Vertices[I]);if(D>Maximum){Maximum=D;Index=I;}}
        return Vertices[Index];
    }
};
bool Vector(const TSharedPtr<FJsonValue>& Value,FVector& V)
{
    const TArray<TSharedPtr<FJsonValue>>* A=nullptr;if(!Value || !Value->TryGetArray(A) || A->Num()!=3)return false;
    double N[3];for(int I=0;I<3;++I)if(!(*A)[I]->TryGetNumber(N[I]) || !FMath::IsFinite(N[I]))return false;V=FVector(N[0],N[1],N[2]);return true;
}
bool Shapes(const TSharedPtr<FJsonObject>& Request,const FString& Key,const FVector& Origin,TArray<SupportShape>& Out)
{
    const TArray<TSharedPtr<FJsonValue>>* A=nullptr;if(!Request->TryGetArrayField(Key,A) || A->Num()>100000)return false;
    for(auto Value:*A)
    {
        auto Object=Value->AsObject();if(!Object)return false;SupportShape Shape;
        if(!Object->TryGetNumberField(TEXT("margin"),Shape.Margin) || !FMath::IsFinite(Shape.Margin) || Shape.Margin<0)return false;
        const TArray<TSharedPtr<FJsonValue>>* Vertices=nullptr;if(!Object->TryGetArrayField(TEXT("vertices"),Vertices) || Vertices->IsEmpty() || Vertices->Num()>65536)return false;
        for(auto Vertex:*Vertices){FVector V;if(!Vector(Vertex,V))return false;Shape.Vertices.Add(V-Origin);}Out.Add(MoveTemp(Shape));
    }
    return true;
}
}
extern "C" SPATIALTWINCORE_API int STCollisionQuery(const char* Request,char* Output,int Capacity)
{
    if(!Request || !Output || Capacity<=0)return 0;
    TSharedPtr<FJsonObject> J;auto Result=MakeShared<FJsonObject>();FVector Origin;
    TArray<SupportShape> A,B;
    if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(UTF8_TO_TCHAR(Request)),J) || !Vector(J->TryGetField(TEXT("origin")),Origin) || !Shapes(J,TEXT("a"),Origin,A) || !Shapes(J,TEXT("b"),Origin,B))
    {Result->SetStringField(TEXT("state"),TEXT("UNKNOWN"));Result->SetStringField(TEXT("error"),TEXT("Invalid collision request"));}
    else
    {
        double Maximum=0;FVector Normal=FVector::ZeroVector;int32 ShapeA=-1,ShapeB=-1;
        for(int32 I=0;I<A.Num();++I)for(int32 K=0;K<B.Num();++K)
        {
            Chaos::FReal Depth=0;Chaos::FVec3 PA,PB,N;int32 VA,VB;
            if(Chaos::GJKPenetration<false,Chaos::FReal>(A[I],B[K],Chaos::FRigidTransform3::Identity,Depth,PA,PB,N,VA,VB) && Depth>Maximum){Maximum=Depth;Normal=N;ShapeA=I;ShapeB=K;}
        }
        Result->SetStringField(TEXT("state"),TEXT("READY"));Result->SetNumberField(TEXT("penetration"),Maximum);Result->SetBoolField(TEXT("overlap"),Maximum>0.01);Result->SetNumberField(TEXT("shape_a"),ShapeA);Result->SetNumberField(TEXT("shape_b"),ShapeB);
        Result->SetArrayField(TEXT("normal"),{MakeShared<FJsonValueNumber>(Normal.X),MakeShared<FJsonValueNumber>(Normal.Y),MakeShared<FJsonValueNumber>(Normal.Z)});
    }
    FTCHARToUTF8 Text(*FSpatialTwinDatabase::Json(Result));if(Capacity<=Text.Length())return -Text.Length()-1;FMemory::Memcpy(Output,Text.Get(),Text.Length());Output[Text.Length()]=0;return Text.Length();
}
