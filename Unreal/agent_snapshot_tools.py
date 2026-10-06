"""Read-only snapshot executed by native ProgrammaticToolset, not by CPython."""
import json

SCENE = 'editor_toolset.toolsets.scene.SceneTools'
ACTOR = 'editor_toolset.toolsets.actor.ActorTools'
OBJECT = 'editor_toolset.toolsets.object.ObjectTools'
EDITOR = 'EditorToolset.EditorAppToolset'


def call(toolset, method, args):
    return execute_tool(toolset + '.' + method, json.dumps(args))['returnValue']


def run():
    query = QUERY
    pie = call(EDITOR, 'IsPIERunning', {})
    find = {'name': query['name'], 'tag': '', 'collision_channels': []}
    if query['actor_type']:
        find['actor_type'] = query['actor_type']
    actors = call(SCENE, 'find_actors', find)
    rows = []
    for actor in actors[:query['limit']]:
        schema = json.loads(call(OBJECT, 'list_properties', {'instance': actor})) if query['properties'] else {}
        names = {key.lower(): key for key in schema}
        available = [names[p.lower()] for p in query['properties'] if p.lower() in names]
        rows.append({
            'actor': actor,
            'label': call(ACTOR, 'get_label', {'actor': actor}),
            'class': call(OBJECT, 'get_class', {'instance': actor}),
            'transform': call(ACTOR, 'get_actor_transform', {'actor': actor}),
            'properties': json.loads(call(OBJECT, 'get_properties', {'instance': actor, 'properties': available})) if available else {},
            'unavailable_properties': [p for p in query['properties'] if p.lower() not in names],
        })
    return {
        'context': 'PIE' if pie else 'EDITOR',
        'editor_level': call(SCENE, 'get_current_level', {}),
        'pie_running': pie,
        'editor_camera': call(EDITOR, 'GetCameraTransform', {}),
        'selected_editor_actors': call(EDITOR, 'GetSelectedActors', {}),
        'query': query, 'matched': len(actors), 'returned': len(rows), 'actors': rows,
        'limits': ['Only reflected properties are readable.',
                   'Editor level/camera/selection are not player state.',
                   'Missing fields are unavailable, not false or zero.'],
    }
