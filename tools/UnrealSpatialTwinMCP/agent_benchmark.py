"""Native whole-agent usage accounting; copied verbatim from the qualified runner."""
def usage(events):
    values=[e['usage'] for e in events if e.get('type')=='turn.completed' and e.get('usage')]
    if not values:raise ValueError('No native completed-turn usage; never estimate it from text')
    result={k:sum(v[k] for v in values) if all(k in v for v in values) else None
            for k in ('input_tokens','cached_input_tokens','cache_write_input_tokens','output_tokens','reasoning_output_tokens')}
    if any(type(result[k]) is not int or result[k]<0 for k in ('input_tokens','output_tokens')):raise ValueError('Missing/invalid native input or output counters')
    result['total_tokens']=result['input_tokens']+result['output_tokens']
    result['uncached_input_tokens']=result['input_tokens']-result['cached_input_tokens'] if result['cached_input_tokens'] is not None else None
    result['turns']=len(values)
    return result
