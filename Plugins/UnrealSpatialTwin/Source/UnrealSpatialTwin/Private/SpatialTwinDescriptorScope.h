#pragma once
#include "Editor.h"
#include "Engine/Level.h"
#include "LevelInstance/LevelInstanceSubsystem.h"
#include "LevelInstance/LevelInstanceInterface.h"
#include "WorldPartition/ActorDescContainerInstance.h"
#include "WorldPartition/WorldPartitionHandle.h"

// Child-container descriptors address source packages, not the transformed live
// copies. Resolve through Unreal's Level Instance loader, once per container.
class FSpatialTwinDescriptorScope
{
public:
    FSpatialTwinDescriptorScope(UWorld* InWorld,bool& InScanning):World(InWorld),Scanning(InScanning){}
    ~FSpatialTwinDescriptorScope()
    {
        TGuardValue<bool> Guard(Scanning,true);
        if(auto Subsystem=World->GetSubsystem<ULevelInstanceSubsystem>())
            for(int32 I=Loaded.Num()-1;I>=0;--I)if(auto Actor=Loaded[I].Get())
                if(auto Instance=Cast<ILevelInstanceInterface>(Actor))Subsystem->BlockUnloadLevelInstance(Instance);
        References.Reset();
    }
    AActor* Resolve(UActorDescContainerInstance* Container,const FGuid& Guid)
    {
        if(!Container->GetParentContainerInstance())
        {
            if(auto D=Container->GetActorDescInstance(Guid))
                if(auto Existing=D->GetActor(false);IsValid(Existing) && !Existing->IsActorBeingDestroyed() && Existing->GetLevel()->Actors.Contains(Existing))return Existing;
            References.Emplace(Container,Guid);return References.Last().GetActor();
        }
        auto Found=Actors.Find(Container);
        if(!Found)
        {
            auto Parent=const_cast<UActorDescContainerInstance*>(Container->GetParentContainerInstance());
            auto Owner=Resolve(Parent,Container->GetContainerActorGuid());
            if(!IsValid(Owner) || Owner->IsActorBeingDestroyed())return nullptr;
            auto Instance=Cast<ILevelInstanceInterface>(Owner);
            auto Subsystem=World->GetSubsystem<ULevelInstanceSubsystem>();
            if(!Instance || !Subsystem)return nullptr;
            if(!Subsystem->IsLoaded(Instance))
            {
                Loaded.Add(Owner);Subsystem->BlockLoadLevelInstance(Instance);
            }
            auto Level=Subsystem->GetLevelInstanceLevel(Instance);if(!Level)return nullptr;
            auto& Entries=Actors.Add(Container);
            for(auto Actor:Level->Actors)if(IsValid(Actor))Entries.Add(Actor->GetActorGuid(),Actor);
            Found=&Entries;
        }
        auto Actor=Found->Find(Guid);return Actor?Actor->Get():nullptr;
    }
private:
    UWorld* World;
    bool& Scanning;
    TArray<FWorldPartitionReference> References;
    TArray<TWeakObjectPtr<AActor>> Loaded;
    TMap<UActorDescContainerInstance*,TMap<FGuid,TWeakObjectPtr<AActor>>> Actors;
};
