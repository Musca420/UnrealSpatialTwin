#include "Modules/ModuleManager.h"
#include "SpatialTwinSubsystem.h"
#include "SpatialTwinToolset.h"
#include "SpatialTwinSave.h"
#include "ModelContextProtocolSettings.h"
#include "ToolsetRegistry/UToolsetRegistry.h"
#include "Editor.h"
#include "SpatialTwinSQLite.h"
#include "FileHelpers.h"
#include "Settings/LevelEditorMiscSettings.h"
#include "Misc/ScopeExit.h"
#include "Misc/Parse.h"
#include "Misc/FileHelper.h"
#include "Misc/Paths.h"
#include "Misc/PackageName.h"
#include "Misc/SecureHash.h"
#include "HAL/FileManager.h"
#include "Framework/Docking/TabManager.h"
#include "Widgets/Docking/SDockTab.h"
#include "Widgets/Text/STextBlock.h"
#include "Widgets/Input/SButton.h"
#include "Widgets/SBoxPanel.h"
#include "HAL/PlatformProcess.h"
#include "Interfaces/IPluginManager.h"
#include "Serialization/JsonSerializer.h"

namespace {
USpatialTwinSubsystem* Twin(){return GEditor?GEditor->GetEditorSubsystem<USpatialTwinSubsystem>():nullptr;}
FProcHandle ValidationProcess;
FString ValidationOutput;
int64 ValidationRevision=-1;
}
FString USpatialTwinToolset::spatial_twin_status()
{
    auto S=Twin();TSharedPtr<FJsonObject> Status;
    if(!S || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(S->Status()),Status))return TEXT("{\"error\":\"Editor unavailable\"}");
    Status->SetNumberField(TEXT("patch_result_journal"),1);Status->SetNumberField(TEXT("patch_save_journal"),1);Status->SetNumberField(TEXT("patch_partial_save"),1);Status->SetNumberField(TEXT("instance_transform_version"),1);return FSpatialTwinDatabase::Json(Status);
}
FString USpatialTwinToolset::spatial_twin_sync(){auto S=Twin();if(S)S->Sync();return spatial_twin_status();}
bool USpatialTwinToolset::spatial_twin_transform_instance(const FString& instance_id,const FTransform& expected_transform,const FTransform& transform)
{auto S=Twin();return S && S->TransformInstance(instance_id,expected_transform,transform);}
FString USpatialTwinToolset::spatial_twin_rebuild(){auto S=Twin();if(S)S->Rebuild();return spatial_twin_status();}
FString USpatialTwinToolset::spatial_twin_validate()
{
    auto S=Twin();TSharedPtr<FJsonObject> Status;
    if(!S || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(S->Status()),Status) || !Status->GetBoolField(TEXT("ready")))return TEXT("{\"state\":\"UNAVAILABLE\",\"valid\":false}");
    if(ValidationProcess.IsValid() && !FPlatformProcess::IsProcRunning(ValidationProcess))
    {
        int32 ExitCode=-1;FPlatformProcess::GetProcReturnCode(ValidationProcess,&ExitCode);
        FPlatformProcess::CloseProc(ValidationProcess);ValidationProcess.Reset();
        if(ExitCode!=0 && !FPaths::FileExists(ValidationOutput))return TEXT("{\"state\":\"FAILED\",\"valid\":false,\"error\":\"Validation commandlet exited without a report\"}");
    }
    if(!ValidationProcess.IsValid() && ValidationRevision==S->Database.Revision)
    {
        FString Result;if(FFileHelper::LoadFileToString(Result,*ValidationOutput))return Result;
    }
    if(!ValidationProcess.IsValid())
    {
        ValidationRevision=S->Database.Revision;
        ValidationOutput=S->Database.Root/TEXT("logs")/(TEXT("validation-")+FGuid::NewGuid().ToString(EGuidFormats::Digits)+TEXT(".json"));
        const FString Exe=FPaths::ConvertRelativePathToFull(FPaths::EngineDir()/TEXT("Binaries/Win64/UnrealEditor-Cmd.exe"));
        const FString Args=FString::Printf(TEXT("\"%s\" -run=SpatialTwinValidate -SpatialTwinRoot=\"%s\" -ValidationOutput=\"%s\" -nullrhi -unattended -nosound -nop4 -ddc=InstalledNoZenLocalFallback -DDC-ForceMemoryCache"),*FPaths::GetProjectFilePath(),*S->Database.Root,*ValidationOutput);
        uint32 ProcessId=0;ValidationProcess=FPlatformProcess::CreateProc(*Exe,*Args,true,true,true,&ProcessId,0,nullptr,nullptr);
        if(!ValidationProcess.IsValid())return TEXT("{\"state\":\"FAILED\",\"valid\":false,\"error\":\"Validation commandlet could not start\"}");
    }
    auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("state"),TEXT("RUNNING"));
    Result->SetNumberField(TEXT("requested_revision"),ValidationRevision);Result->SetStringField(TEXT("report_path"),ValidationOutput);
    Result->SetStringField(TEXT("note"),TEXT("Read-only headless integrity audit; poll this tool. Report revision is the audited snapshot, not a freshness guarantee."));
    return FSpatialTwinDatabase::Json(Result);
}
FString USpatialTwinToolset::spatial_twin_cache_asset(const FString& object_path)
{
    auto S=Twin();if(S)S->CacheAsset(object_path);return spatial_twin_status();
}
FString USpatialTwinToolset::spatial_twin_apply_patch(const FString& patch_id,bool save_packages)
{
    FGuid Guid;if(!FGuid::Parse(patch_id,Guid))return TEXT("{\"error\":\"Invalid patch ID\"}");
    auto S=Twin();if(!S)return TEXT("{\"error\":\"Editor unavailable\"}");
    auto Plugin=IPluginManager::Get().FindPlugin(TEXT("UnrealSpatialTwin"));
    FString Root=FPlatformMisc::GetEnvironmentVariable(TEXT("SPATIAL_TWIN_HOME"));if(Root.IsEmpty())Root=Plugin->GetBaseDir()/TEXT("../..");Root=FPaths::ConvertRelativePathToFull(Root);
    FString Python=FPlatformMisc::GetEnvironmentVariable(TEXT("SPATIAL_TWIN_PYTHON"));if(Python.IsEmpty())Python=Root/TEXT("Build/MCP/venv/Scripts/python.exe");
    const FString Script=Root/TEXT("tools/UnrealSpatialTwinMCP/apply_patch.py");
    if(!FPaths::FileExists(Python) || !FPaths::FileExists(Script))return TEXT("{\"error\":\"Install Spatial Twin MCP dependencies in the project first\"}");
    const FString Url=FString::Printf(TEXT("http://127.0.0.1:%u%s"),UE::ModelContextProtocol::GetServerPortNumber(),*UE::ModelContextProtocol::GetServerUrlPath());
    FString Arguments=FString::Printf(TEXT("\"%s\" --root \"%s\" --patch %s --url \"%s\" %s"),*Script,*S->Database.Root,*patch_id,*Url,save_packages?TEXT(""):TEXT("--no-save"));
    uint32 ProcessId=0;auto Process=FPlatformProcess::CreateProc(*Python,*Arguments,true,true,true,&ProcessId,0,*Root,nullptr);
    if(!Process.IsValid())return TEXT("{\"error\":\"Patch executor could not start\"}");FPlatformProcess::CloseProc(Process);
    auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("state"),TEXT("DISPATCHED"));Result->SetStringField(TEXT("patch_id"),patch_id);Result->SetNumberField(TEXT("process_id"),ProcessId);return FSpatialTwinDatabase::Json(Result);
}
FString USpatialTwinToolset::spatial_twin_record_patch_result(const FString& patch_id,const FString& operations_digest,const FString& result_json)
{
    FGuid Guid;TSharedPtr<FJsonObject> Result;bool Complete=false;
    if(!FGuid::Parse(patch_id,Guid) || operations_digest.Len()!=64 || result_json.Len()>4*1024*1024
        || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(result_json),Result)
        || !Result->TryGetBoolField(TEXT("complete"),Complete))return TEXT("{\"error\":\"Invalid batch result\"}");
    auto S=Twin();if(!S)return TEXT("{\"error\":\"Editor unavailable\"}");
    FSpatialTwinSQLiteDatabase DB;
    if(!DB.Open(*(S->Database.Root/TEXT("patches.sqlite")),ESTSQLiteOpenMode::ReadWrite) || !DB.Execute(TEXT("BEGIN IMMEDIATE")))return TEXT("{\"error\":\"Patch journal unavailable\"}");
    ON_SCOPE_EXIT{DB.Execute(TEXT("ROLLBACK"));};
    const FString Dispatch=TEXT("batch:")+patch_id;FString Operations,Receipts;
    {
        FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT operations,receipts FROM patches WHERE id=? AND status IN ('APPLYING','FAILED') AND json_extract(validation,'$.valid')=1 AND json_extract(validation,'$.operations_digest')=?"));
        Q.SetBindingValueByIndex(1,patch_id);Q.SetBindingValueByIndex(2,operations_digest);
        if(Q.Step()!=ESTSQLiteStepResult::Row)return TEXT("{\"error\":\"Patch plan or state changed\"}");
        Q.GetColumnValueByIndex(0,Operations);Q.GetColumnValueByIndex(1,Receipts);
    }
    TArray<TSharedPtr<FJsonValue>> Confirmations,Ops;bool Sent=false;
    if(!FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Receipts),Confirmations)
        || !FJsonSerializer::Deserialize(TJsonReaderFactory<>::Create(Operations),Ops))return TEXT("{\"error\":\"Invalid patch records\"}");
    for(auto V:Confirmations){auto R=V->AsObject();FString Id,State;if(R && R->TryGetStringField(TEXT("operation_id"),Id) && Id==Dispatch
        && R->TryGetStringField(TEXT("state"),State) && (State==TEXT("SENT") || State==TEXT("ACKNOWLEDGED")))Sent=true;}
    if(!Sent)return TEXT("{\"error\":\"Batch was not dispatched\"}");
    TMap<FString,FString> Creates;TSet<FString> Seen;
    for(auto V:Ops){auto Op=V->AsObject();if(Op && Op->GetStringField(TEXT("type"))==TEXT("CREATE_ACTOR"))Creates.Add(Op->GetStringField(TEXT("target")),Op->GetStringField(TEXT("operation_id")));}
    const TArray<TSharedPtr<FJsonValue>>* Rows=nullptr;
    if(Result->HasField(TEXT("created")) && !Result->TryGetArrayField(TEXT("created"),Rows))return TEXT("{\"error\":\"Invalid created records\"}");
    if(Rows)for(auto V:*Rows)
    {
        auto R=V->AsObject();FString Target,Id,Path;const TSharedPtr<FJsonObject>* Actor=nullptr;
        if(!R || !R->TryGetStringField(TEXT("target"),Target) || !R->TryGetStringField(TEXT("operation_id"),Id)
            || !Creates.Contains(Target) || Creates[Target]!=Id || Seen.Contains(Target)
            || !R->TryGetObjectField(TEXT("actor"),Actor) || !(*Actor)->TryGetStringField(TEXT("refPath"),Path) || Path.IsEmpty())return TEXT("{\"error\":\"Creation reference differs from plan\"}");
        Seen.Add(Target);
    }
    // Immutable terminal result; independent of client receipt updates. A process
    // crash before this call still leaves unknown effects and must never replay.
    const FString CanonicalResult=FSpatialTwinDatabase::Json(Result);
    {
        FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT operations_digest,dispatch_id,result FROM patch_results WHERE patch_id=?"));Q.SetBindingValueByIndex(1,patch_id);
        if(Q.Step()==ESTSQLiteStepResult::Row)
        {
            FString Digest,Id,Prior;Q.GetColumnValueByIndex(0,Digest);Q.GetColumnValueByIndex(1,Id);Q.GetColumnValueByIndex(2,Prior);
            if(Digest!=operations_digest || Id!=Dispatch || Prior!=CanonicalResult)return TEXT("{\"error\":\"Conflicting terminal result\"}");
            return TEXT("{\"state\":\"RECORDED\"}");
        }
    }
    {
        FSpatialTwinSQLiteStatement Q(DB,TEXT("INSERT INTO patch_results(patch_id,operations_digest,dispatch_id,result) VALUES(?,?,?,?)"));
        Q.SetBindingValueByIndex(1,patch_id);Q.SetBindingValueByIndex(2,operations_digest);Q.SetBindingValueByIndex(3,Dispatch);Q.SetBindingValueByIndex(4,CanonicalResult);
        if(Q.Step()!=ESTSQLiteStepResult::Done)return TEXT("{\"error\":\"Result journal write failed\"}");
    }
    if(!DB.Execute(TEXT("COMMIT")))return TEXT("{\"error\":\"Result journal commit failed\"}");
    return TEXT("{\"state\":\"RECORDED\"}");
}

class FUnrealSpatialTwinModule : public IModuleInterface
{
public:
    FDelegateHandle Registration;
    virtual void StartupModule() override
    {
        if(IsRunningCommandlet())return;
        if(GEditor)UToolsetRegistry::RegisterToolsetClass(USpatialTwinToolset::StaticClass());
        else Registration=FCoreDelegates::GetOnPostEngineInit().AddLambda([]{UToolsetRegistry::RegisterToolsetClass(USpatialTwinToolset::StaticClass());});
        FGlobalTabmanager::Get()->RegisterNomadTabSpawner(TEXT("SpatialTwin"),FOnSpawnTab::CreateLambda([](const FSpawnTabArgs&)
        {
            return SNew(SDockTab).TabRole(ETabRole::NomadTab)
            [SNew(SVerticalBox)
             +SVerticalBox::Slot().AutoHeight()[SNew(STextBlock).Text_Lambda([]{return FText::FromString(USpatialTwinToolset::spatial_twin_status());})]
             +SVerticalBox::Slot().AutoHeight()[SNew(SButton).Text(FText::FromString(TEXT("Rebuild"))).OnClicked_Lambda([]{USpatialTwinToolset::spatial_twin_rebuild();return FReply::Handled();})]
             +SVerticalBox::Slot().AutoHeight()[SNew(SButton).Text(FText::FromString(TEXT("Validate"))).OnClicked_Lambda([]{USpatialTwinToolset::spatial_twin_validate();return FReply::Handled();})]
             +SVerticalBox::Slot().AutoHeight()[SNew(SButton).Text(FText::FromString(TEXT("Open Logs"))).OnClicked_Lambda([]{FPlatformProcess::ExploreFolder(*(FPaths::ProjectSavedDir()/TEXT("SpatialTwin")));return FReply::Handled();})]];
        })).SetDisplayName(FText::FromString(TEXT("Spatial Twin")));
    }
    virtual void ShutdownModule() override
    {
        SpatialTwinSaveShutdown();
        // The read-only child may finish independently; closing its handle does not terminate it.
        if(ValidationProcess.IsValid()){FPlatformProcess::CloseProc(ValidationProcess);ValidationProcess.Reset();}
        FCoreDelegates::GetOnPostEngineInit().Remove(Registration);
        if(!IsRunningCommandlet())UToolsetRegistry::UnregisterToolsetClass(USpatialTwinToolset::StaticClass());
        if(!IsRunningCommandlet())FGlobalTabmanager::Get()->UnregisterNomadTabSpawner(TEXT("SpatialTwin"));
    }
};
IMPLEMENT_MODULE(FUnrealSpatialTwinModule,UnrealSpatialTwin)

USpatialTwinScanCommandlet::USpatialTwinScanCommandlet(){IsEditor=true;IsClient=false;IsServer=false;LogToConsole=true;}
int32 USpatialTwinScanCommandlet::Main(const FString& Params)
{
    FString Map;if(!FParse::Value(*Params,TEXT("Map="),Map)){UE_LOG(LogTemp,Error,TEXT("SpatialTwinScan requires -Map=/Game/Path/Map"));return 2;}
    // Scanning reads the saved navigation source; it must not regenerate it
    // implicitly while loading or streaming descriptor actors.
    auto Settings=GetMutableDefault<ULevelEditorMiscSettings>();const bool AutoUpdate=Settings->bNavigationAutoUpdate;Settings->bNavigationAutoUpdate=false;
    ON_SCOPE_EXIT{Settings->bNavigationAutoUpdate=AutoUpdate;};
    if(!GEditor || !FEditorFileUtils::LoadMap(Map,false,true))return 1;
    auto S=NewObject<USpatialTwinSubsystem>();bool Ok=S->Rebuild(GEditor->GetEditorWorldContext().World());
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinScan: %s"),*S->Status());S->Database.DB.Close();return Ok?0:1;
}
USpatialTwinValidateCommandlet::USpatialTwinValidateCommandlet(){IsEditor=true;IsClient=false;IsServer=false;LogToConsole=true;}
int32 USpatialTwinValidateCommandlet::Main(const FString& Params)
{
    FString Root=FPaths::ProjectSavedDir()/TEXT("SpatialTwin");FParse::Value(*Params,TEXT("SpatialTwinRoot="),Root);const FString Path=Root/TEXT("world.sqlite");
    if(FParse::Param(*Params,TEXT("UpgradeCollisionBounds")))
    {
        auto Plugin=IPluginManager::Get().FindPlugin(TEXT("UnrealSpatialTwin"));FString Schema,Map;
        if(!FPaths::FileExists(Path) || !Plugin || !FFileHelper::LoadFileToString(Schema,*(Plugin->GetBaseDir()/TEXT("Resources/schema.sql"))))return 1;
        FSpatialTwinDatabase Writer;if(!Writer.Open(FPaths::ConvertRelativePathToFull(Root),Schema)){UE_LOG(LogTemp,Error,TEXT("SpatialTwinUpgrade: %s"),*Writer.Error);return 1;}
        {FSpatialTwinSQLiteStatement Q(Writer.DB,TEXT("SELECT map FROM snapshots WHERE state='READY' ORDER BY revision DESC,timestamp DESC LIMIT 1"));if(Q.Step()!=ESTSQLiteStepResult::Row || !Q.GetColumnValueByIndex(0,Map))return 1;}
        if(!Writer.UpgradeCollisionBounds(Map)){UE_LOG(LogTemp,Error,TEXT("SpatialTwinUpgrade: %s"),*Writer.Error);return 1;}
        UE_LOG(LogTemp,Display,TEXT("SpatialTwinUpgrade: %s"),*Writer.ReadMetadata(TEXT("collision_bounds_upgrade")));
    }
    if(FParse::Param(*Params,TEXT("CheckpointSources")))
    {auto S=NewObject<USpatialTwinSubsystem>();const bool Ok=S->CheckpointSources(FPaths::ConvertRelativePathToFull(Root));UE_LOG(LogTemp,Display,TEXT("SpatialTwinSourceCheckpoint success=%d error=%s"),Ok,*S->Database.Error);S->Database.DB.Close();return Ok?0:1;}
    const double Started=FPlatformTime::Seconds();
    FSpatialTwinSQLiteDatabase DB;if(!DB.Open(*Path,ESTSQLiteOpenMode::ReadOnly) || !DB.Execute(TEXT("BEGIN")))return 1;
    int64 Revision=-1;{FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT value FROM metadata WHERE key='world_revision'"));if(Q.Step()==ESTSQLiteStepResult::Row){FString V;Q.GetColumnValueByIndex(0,V);Revision=FCString::Atoi64(*V);}}
    FSpatialTwinSQLiteStatement S(DB,TEXT("PRAGMA integrity_check"));FString Value;
    if(S.Step()==ESTSQLiteStepResult::Row)S.GetColumnValueByIndex(0,Value);
    S.Destroy();bool Ready=false;
    {FSpatialTwinSQLiteStatement Q(DB,TEXT("SELECT 1 FROM snapshots WHERE state='READY' LIMIT 1"));Ready=Q.Step()==ESTSQLiteStepResult::Row;}
    DB.Execute(TEXT("ROLLBACK"));DB.Close();const bool Valid=Value==TEXT("ok") && Ready && Revision>=0;
    FString Output;if(FParse::Value(*Params,TEXT("ValidationOutput="),Output))
    {
        auto Result=MakeShared<FJsonObject>();Result->SetStringField(TEXT("state"),Valid?TEXT("VALID"):TEXT("INVALID"));Result->SetBoolField(TEXT("valid"),Valid);
        Result->SetStringField(TEXT("integrity"),Value);Result->SetBoolField(TEXT("ready_snapshot"),Ready);Result->SetNumberField(TEXT("canonical_revision"),Revision);
        Result->SetNumberField(TEXT("seconds"),FPlatformTime::Seconds()-Started);Result->SetStringField(TEXT("source"),FParse::Param(*Params,TEXT("UpgradeCollisionBounds"))?TEXT("SpatialTwinValidate cache upgrade and integrity"):TEXT("SpatialTwinValidate read-only commandlet"));
        IFileManager::Get().MakeDirectory(*FPaths::GetPath(Output),true);
        const FString Temp=Output+TEXT(".tmp");if(!FFileHelper::SaveStringToFile(FSpatialTwinDatabase::Json(Result),*Temp,FFileHelper::EEncodingOptions::ForceUTF8WithoutBOM) || !IFileManager::Get().Move(*Output,*Temp,true))return 2;
    }
    UE_LOG(LogTemp,Display,TEXT("SpatialTwinValidate integrity=%s ready=%d revision=%lld"),*Value,Ready,Revision);return Valid?0:1;
}
