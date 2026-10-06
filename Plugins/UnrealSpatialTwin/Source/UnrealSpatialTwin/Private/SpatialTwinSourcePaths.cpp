#include "SpatialTwinSourcePaths.h"
#include "Misc/Paths.h"
#include "Misc/PackageName.h"
#if PLATFORM_WINDOWS
#include "Windows/WindowsHWrapper.h"
#endif

FString SpatialTwinSourcePath(const FString& Path)
{
    FString Result=FPaths::ConvertRelativePathToFull(Path);
    FPaths::NormalizeFilename(Result);
#if PLATFORM_WINDOWS
    HANDLE File=CreateFileW(*Result,FILE_READ_ATTRIBUTES,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,
        nullptr,OPEN_EXISTING,FILE_FLAG_BACKUP_SEMANTICS,nullptr);
    if(File!=INVALID_HANDLE_VALUE)
    {
        const DWORD Length=GetFinalPathNameByHandleW(File,nullptr,0,FILE_NAME_NORMALIZED);
        if(Length)
        {
            TArray<WCHAR> Buffer;Buffer.SetNumUninitialized(Length+1);
            const DWORD Written=GetFinalPathNameByHandleW(File,Buffer.GetData(),Buffer.Num(),FILE_NAME_NORMALIZED);
            if(Written && Written<static_cast<DWORD>(Buffer.Num()))
            {
                Result=Buffer.GetData();
                if(Result.StartsWith(TEXT("\\\\?\\UNC\\")))Result=TEXT("\\\\")+Result.Mid(8);
                else Result.RemoveFromStart(TEXT("\\\\?\\"));
            }
        }
        CloseHandle(File);
    }
#endif
    FPaths::NormalizeFilename(Result);return Result;
}

bool SpatialTwinSourcePackage(const FString& Path,FString& Package)
{
    if(FPackageName::TryConvertFilenameToLongPackageName(Path,Package))return true;
    TArray<FString> Roots;FPackageName::QueryRootContentPaths(Roots,true);
    for(const auto& Root:Roots)
    {
        const FString Mounted=FPaths::ConvertRelativePathToFull(FPackageName::LongPackageNameToFilename(Root));
        FString Physical=SpatialTwinSourcePath(Mounted);Physical.RemoveFromEnd(TEXT("/"));Physical+=TEXT("/");
        if(Path.StartsWith(Physical))return FPackageName::TryConvertFilenameToLongPackageName(Mounted/Path.Mid(Physical.Len()),Package);
    }
    return false;
}
