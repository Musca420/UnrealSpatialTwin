#pragma once
#include "CoreMinimal.h"
class dtNavMesh;
SPATIALTWINCORE_API bool STSaveNavmesh(const dtNavMesh* Mesh,const FString& Path);
extern "C" {
    SPATIALTWINCORE_API int STNavigationRasterizationVersion();
    SPATIALTWINCORE_API int STNavigationQuery(const char* Path,const char* Request,char* Output,int Capacity);
    SPATIALTWINCORE_API int STBuildNavigation(const char* Path,const char* Request,const char* OutputPath,char* Output,int Capacity);
}
