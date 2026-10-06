#pragma once
#include "CoreMinimal.h"

struct sqlite3;
struct sqlite3_stmt;
enum class ESTSQLiteOpenMode { ReadOnly, ReadWrite, ReadWriteCreate };
enum class ESTSQLiteStepResult { Error, Busy, Row, Done };

// Windows' native SQLite supplies real cross-process locks and WAL. Unreal's
// unreal-fs VFS deliberately lacks shared memory and granular file locks.
class SPATIALTWINCORE_API FSpatialTwinSQLiteDatabase
{
public:
    bool RequireTransactionWrites=false;
    sqlite3* Handle=nullptr;
    FString Error;
    ~FSpatialTwinSQLiteDatabase(){Close();}
    FSpatialTwinSQLiteDatabase()=default;
    FSpatialTwinSQLiteDatabase(const FSpatialTwinSQLiteDatabase&)=delete;
    FSpatialTwinSQLiteDatabase& operator=(const FSpatialTwinSQLiteDatabase&)=delete;
    bool Open(const TCHAR* Path,ESTSQLiteOpenMode Mode=ESTSQLiteOpenMode::ReadWriteCreate);
    bool Close();
    bool IsValid() const{return Handle!=nullptr;}
    bool Execute(const TCHAR* SQL);
    FString GetLastError() const;
};

class SPATIALTWINCORE_API FSpatialTwinSQLiteStatement
{
    FSpatialTwinSQLiteDatabase& Database;
    sqlite3_stmt* Handle=nullptr;
public:
    FSpatialTwinSQLiteStatement(FSpatialTwinSQLiteDatabase& DB,const TCHAR* SQL);
    ~FSpatialTwinSQLiteStatement(){Destroy();}
    FSpatialTwinSQLiteStatement(const FSpatialTwinSQLiteStatement&)=delete;
    FSpatialTwinSQLiteStatement& operator=(const FSpatialTwinSQLiteStatement&)=delete;
    bool Destroy();
    ESTSQLiteStepResult Step();
    bool SetBindingValueByIndex(int Index,const FString& Value);
    bool SetBindingValueByIndex(int Index,int32 Value);
    bool SetBindingValueByIndex(int Index,int64 Value);
    bool SetBindingValueByIndex(int Index,double Value);
    bool SetBindingValueByIndex(int Index,decltype(nullptr));
    bool GetColumnValueByIndex(int Index,FString& Value) const;
    bool GetColumnValueByIndex(int Index,int32& Value) const;
    bool GetColumnValueByIndex(int Index,int64& Value) const;
    bool GetColumnValueByIndex(int Index,double& Value) const;
};
