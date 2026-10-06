"""Public MCP input contracts; canonical/Shadow checks still decide validity."""
from typing import Annotated, Any, Literal
from typing_extensions import NotRequired, TypedDict
from pydantic import ConfigDict, Field, RootModel, with_config

Number = Annotated[float, Field(strict=True, allow_inf_nan=False)]
Vector2 = Annotated[list[Number], Field(min_length=2, max_length=2, description='Footprint offset [x,y] in cm.')]
Vector3 = Annotated[list[Number], Field(min_length=3, max_length=3)]
Quaternion = Annotated[list[Number], Field(min_length=4, max_length=4, description='Unit quaternion [x,y,z,w], not Euler angles.')]
WorldSelection = Literal['canonical','shadow']
RayHitField = Literal['distance','actor_id','component_id','instance_id','position','normal','triangle','primitive']

@with_config(ConfigDict(extra='forbid'))
class ReadQuery(TypedDict):
    tool: Annotated[str, Field(description='Exact registered read name, for example world_search or world_region.')]
    arguments: NotRequired[Annotated[dict, Field(description='That read handler\'s exact parameters; not a top-level world_read field.')]]
    purpose: NotRequired[str]

@with_config(ConfigDict(extra='allow'))
class ToolContract(TypedDict):
    name: str
    description: str
    inputSchema: dict
    outputSchema: NotRequired[dict]

@with_config(ConfigDict(extra='allow'))
class ReadResult(TypedDict):
    tool: str
    data: dict
    next_query: NotRequired[Annotated[ReadQuery, Field(description='Continue this exact query; belongs to the result row, not data or the original batch.')]]

@with_config(ConfigDict(extra='allow'))
class ReadBatch(TypedDict):
    state: Literal['READY','CONFLICTED']
    status: dict
    results: NotRequired[list[ReadResult]]
    tool_schemas: NotRequired[Annotated[list[ToolContract], Field(description='Requested contracts as an array; find by name. Not schemas[tool] or a dictionary.')]]
    tool_schemas_reference: NotRequired[str]
    results_reference: NotRequired[str]

class ReadBatchOutput(RootModel[ReadBatch]):
    pass

Bounds3 = Annotated[list[Vector3], Field(min_length=2, max_length=2,
    description='World AABB [[minX,minY,minZ],[maxX,maxY,maxZ]] in cm, not a min/max object.')]

@with_config(ConfigDict(extra='allow'))
class SourceEntity(TypedDict):
    id: str
    kind: str
    bounds: NotRequired[Bounds3 | None]

@with_config(ConfigDict(extra='allow'))
class EntityPage(TypedDict):
    revision: int
    entities: list[SourceEntity]
    returned: int
    cursor: str | None

class GroundSupport(TypedDict):
    state: Literal['PROVEN']
    minimum_normal_z: Annotated[float, Field(description='Minimum support normal Z; compare to cos(max_tilt_degrees). There is no separate uprightness object.')]
    maximum_height_difference: Annotated[float, Field(description='Maximum sampled support height range in cm; compare to requested max_height_difference.')]

@with_config(ConfigDict(extra='allow'))
class PatchValidation(TypedDict):
    valid: bool
    base_revision: int
    errors_count: int
    new_collisions_count: int
    errors: list[dict]
    navigation: Annotated[dict[str,dict], Field(description='Named diagnostics, not an aggregate valid/state field: each configured agent must have state READY; availability.state NOT_CONFIGURED explicitly means this map has no navigation. UNKNOWN/UNAVAILABLE is not approval; do not invent navigation.valid.')]

@with_config(ConfigDict(extra='allow'))
class GroundPlan(TypedDict):
    status: Annotated[Literal['BLOCKED','DRAFT','VALIDATED','INVALID','CONFLICTED'],
        Field(description='Use status, not state. BLOCKED creates no patch. Compact success has accepted_count, not accepted.')]
    base_revision: int
    id: NotRequired[Annotated[str, Field(description='Prepared patch identity. Pass this value as patch_id to the live executor; this response has id, not patch_id.')]]
    accepted_count: NotRequired[int]
    continuous_support: NotRequired[GroundSupport]
    validation: NotRequired[Annotated[PatchValidation, Field(description='Strict checks live here: valid, errors_count, new_collisions_count, errors and navigation. Not top-level plan.errors or plan.new_collisions.')]]
    rejected: NotRequired[Annotated[list[dict], Field(description='Rejected entries; a successful complete plan has []. There is no rejected_count field.')]]
    operations: NotRequired[list['PatchOperation']]

@with_config(ConfigDict(extra='forbid'))
class RayRequest(TypedDict):
    origin: Vector3
    direction: Vector3
    max_distance: Annotated[float, Field(strict=True, allow_inf_nan=False, ge=0)]

@with_config(ConfigDict(extra='forbid'))
class WorldTransform(TypedDict):
    position: Vector3
    rotation: Quaternion
    scale: Vector3

@with_config(ConfigDict(extra='forbid'))
class SpawnComponentProperties(TypedDict):
    bCanEverAffectNavigation: NotRequired[Annotated[bool, Field(strict=True)]]

def operation_value_schema(schema):
    # SET_PROPERTY keeps arbitrary typed JSON; spatial operations advertise
    # their exact vector shape without duplicating the entire operation schema.
    schema['allOf']=[{
        'if':{'properties':{'type':{'const':operation}},'required':['type']},
        'then':{'required':['value'],'properties':{'value':{
            'type':'array','items':{'type':'number'},'minItems':size,'maxItems':size}}}
    } for operation,size in (('MOVE_ACTOR',3),('SCALE_ACTOR',3),('ROTATE_ACTOR',4))]


PatchOperation = with_config(ConfigDict(extra='forbid',json_schema_extra=operation_value_schema))(TypedDict('PatchOperation', {
    'type': Literal['CREATE_ACTOR','DELETE_ACTOR','MOVE_ACTOR','ROTATE_ACTOR','SCALE_ACTOR','SET_PROPERTY','ATTACH','DETACH','CHANGE_ASSET','SET_INSTANCE_TRANSFORM'],
    'target': Annotated[str, Field(min_length=1, description='Persistent entity ID; CREATE_ACTOR uses a new temporary ID referenced by later operations.')],
    'operation_id': NotRequired[Annotated[str, Field(min_length=1)]],
    'class': NotRequired[Annotated[str, Field(description='Required for CREATE_ACTOR: exact exported Unreal class from source field class.')]],
    'asset': NotRequired[Annotated[str, Field(description='StaticMesh entity ID (asset:/...), for CREATE_ACTOR mesh or required CHANGE_ASSET. Not a package ID.')]],
    'transform': NotRequired[Annotated[WorldTransform, Field(description='World pose in cm; CREATE_ACTOR or required SET_INSTANCE_TRANSFORM.')]],
    'value': NotRequired[Annotated[Any, Field(description='Required MOVE_ACTOR/SCALE_ACTOR: [x,y,z]; ROTATE_ACTOR: unit quaternion [x,y,z,w]; SET_PROPERTY: typed JSON value.')]],
    'property': NotRequired[Annotated[str, Field(description='Required SET_PROPERTY: exported editable property name.')]],
    'parent': NotRequired[Annotated[str, Field(description='Required ATTACH: persistent Actor ID or earlier temporary CREATE target.')]],
    'component_id': NotRequired[Annotated[str, Field(description='CHANGE_ASSET component selector when the Actor has multiple mesh components.')]],
    'label': NotRequired[str],
    'component_properties': NotRequired[SpawnComponentProperties],
}))

# RootModel preserves optional absence and project source fields. FastMCP's
# direct TypedDict conversion drops extras and inserts absent fields as null.
class EntityPageOutput(RootModel[EntityPage]):
    pass

class GroundPlanOutput(RootModel[GroundPlan]):
    pass
