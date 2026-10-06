#include "SpatialTwinSQLite.h"
#include "Windows/AllowWindowsPlatformTypes.h"
// SDK 10.0.26100's WinSQLite declarations use this upstream alias without
// declaring it; keep the SDK/library unmodified.
typedef const char* sqlite3_filename;
#include <winsqlite/winsqlite3.h>
#include "Windows/HideWindowsPlatformTypes.h"

bool FSpatialTwinSQLiteDatabase::Open(const TCHAR* Path,ESTSQLiteOpenMode Mode)
{
    if(Handle)return false;
    Error.Reset();RequireTransactionWrites=false;
    int Flags=Mode==ESTSQLiteOpenMode::ReadOnly?SQLITE_OPEN_READONLY:SQLITE_OPEN_READWRITE;
    if(Mode==ESTSQLiteOpenMode::ReadWriteCreate)Flags|=SQLITE_OPEN_CREATE;
    Flags|=SQLITE_OPEN_FULLMUTEX;
    if(sqlite3_open_v2(TCHAR_TO_UTF8(Path),&Handle,Flags,nullptr)!=SQLITE_OK){Error=GetLastError();Close();return false;}
    sqlite3_busy_timeout(Handle,5000);return true;
}
bool FSpatialTwinSQLiteDatabase::Close(){if(!Handle)return true;int Code=sqlite3_close_v2(Handle);if(Code==SQLITE_OK){Handle=nullptr;return true;}Error=GetLastError();return false;}
bool FSpatialTwinSQLiteDatabase::Execute(const TCHAR* SQL)
{
    if(!Handle){Error=TEXT("Database closed");return false;}
    Error.Reset();
    const FString Command=FString(SQL).TrimStartAndEnd();
    if(RequireTransactionWrites && sqlite3_get_autocommit(Handle) && !Command.StartsWith(TEXT("BEGIN")) && !Command.StartsWith(TEXT("ROLLBACK")))
    {Error=TEXT("Canonical write blocked: transaction ended or automatically rolled back");return false;}
    if(sqlite3_exec(Handle,TCHAR_TO_UTF8(SQL),nullptr,nullptr,nullptr)==SQLITE_OK)return true;Error=GetLastError();return false;
}
FString FSpatialTwinSQLiteDatabase::GetLastError() const{return !Error.IsEmpty()?Error:(Handle?FString(UTF8_TO_TCHAR(sqlite3_errmsg(Handle))):TEXT("Database closed"));}
FSpatialTwinSQLiteStatement::FSpatialTwinSQLiteStatement(FSpatialTwinSQLiteDatabase& DB,const TCHAR* SQL):Database(DB)
{
    DB.Error.Reset();
    if(!DB.Handle || sqlite3_prepare_v2(DB.Handle,TCHAR_TO_UTF8(SQL),-1,&Handle,nullptr)!=SQLITE_OK)DB.Error=DB.GetLastError();
}
bool FSpatialTwinSQLiteStatement::Destroy(){if(!Handle)return true;int Code=sqlite3_finalize(Handle);Handle=nullptr;return Code==SQLITE_OK;}
ESTSQLiteStepResult FSpatialTwinSQLiteStatement::Step()
{
    if(!Handle)return ESTSQLiteStepResult::Error;Database.Error.Reset();
    if(Database.RequireTransactionWrites && !sqlite3_stmt_readonly(Handle) && sqlite3_get_autocommit(Database.Handle))
    {Database.Error=TEXT("Canonical write blocked: transaction ended or automatically rolled back");return ESTSQLiteStepResult::Error;}
    int Code=sqlite3_step(Handle);
    if(Code==SQLITE_ROW)return ESTSQLiteStepResult::Row;if(Code==SQLITE_DONE)return ESTSQLiteStepResult::Done;
    Database.Error=Database.GetLastError();return Code==SQLITE_BUSY || Code==SQLITE_LOCKED?ESTSQLiteStepResult::Busy:ESTSQLiteStepResult::Error;
}
bool FSpatialTwinSQLiteStatement::SetBindingValueByIndex(int I,const FString& V){return Handle && sqlite3_bind_text(Handle,I,TCHAR_TO_UTF8(*V),-1,SQLITE_TRANSIENT)==SQLITE_OK;}
bool FSpatialTwinSQLiteStatement::SetBindingValueByIndex(int I,int32 V){return Handle && sqlite3_bind_int(Handle,I,V)==SQLITE_OK;}
bool FSpatialTwinSQLiteStatement::SetBindingValueByIndex(int I,int64 V){return Handle && sqlite3_bind_int64(Handle,I,V)==SQLITE_OK;}
bool FSpatialTwinSQLiteStatement::SetBindingValueByIndex(int I,double V){return Handle && sqlite3_bind_double(Handle,I,V)==SQLITE_OK;}
bool FSpatialTwinSQLiteStatement::SetBindingValueByIndex(int I,decltype(nullptr)){return Handle && sqlite3_bind_null(Handle,I)==SQLITE_OK;}
bool FSpatialTwinSQLiteStatement::GetColumnValueByIndex(int I,FString& V) const
{
    if(!Handle)return false;auto Text=sqlite3_column_text(Handle,I);if(!Text)return false;V=UTF8_TO_TCHAR((const char*)Text);return true;
}
bool FSpatialTwinSQLiteStatement::GetColumnValueByIndex(int I,int32& V) const{if(!Handle)return false;V=sqlite3_column_int(Handle,I);return true;}
bool FSpatialTwinSQLiteStatement::GetColumnValueByIndex(int I,int64& V) const{if(!Handle)return false;V=sqlite3_column_int64(Handle,I);return true;}
bool FSpatialTwinSQLiteStatement::GetColumnValueByIndex(int I,double& V) const{if(!Handle)return false;V=sqlite3_column_double(Handle,I);return true;}
