"""Read Codex rollout usage without counting cumulative/mirrored events twice."""
from datetime import datetime, timezone
import json
from collections import defaultdict

FIELDS = ('input_tokens', 'cached_input_tokens', 'cache_write_input_tokens',
          'output_tokens', 'reasoning_output_tokens', 'total_tokens')


def timestamp(value):
    if not isinstance(value, str):
        return None
    try:
        dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def counts(value):
    if not isinstance(value, dict) or 'input_tokens' not in value:
        return None
    result = tuple(value.get(k, 0) or 0 for k in FIELDS)
    if any(type(n) is not int or n < 0 for n in result):
        return None
    return result


def record(t, model, project, u):
    i, cached, write, out, reasoning, _ = u
    cached = min(i, cached)
    write = min(i - cached, write)
    return dict(t_utc=t, model=model, project=project, source='codex',
                input_=i - cached - write, output=out, cache_read=cached,
                cache_create=write, reasoning_output=min(out, reasoning))


def _read_file(path):
    """Produce primary requests and fallback counters with context snapshots."""
    owner = None
    owner_time = None
    parent = None
    model = 'unknown'
    project = '(unknown)'
    previous = None
    primaries = []
    fallbacks = []
    try:
        fp = open(path, encoding='utf-8', errors='replace')
    except OSError:
        return primaries, fallbacks
    with fp:
        for line in fp:
            # Rollouts contain large tool outputs; skip unrelated events before
            # decoding their contents. All supported formats carry these tags.
            header = line[:300]
            if not (any(tag in header for tag in ('session_meta', 'turn_context', 'token_usage_record'))
                    or ('event_msg' in header and 'token_count' in header)):
                continue
            try:
                obj = json.loads(line)
            except ValueError:
                continue
            if not isinstance(obj, dict):
                continue
            p = obj.get('payload')
            if not isinstance(p, dict):
                continue
            kind = obj.get('type')
            if kind == 'session_meta':
                if owner is None:
                    owner = p.get('id') or path
                    owner_time = timestamp(p.get('timestamp') or obj.get('timestamp'))
                    parent = p.get('forked_from_id')
                project = p.get('cwd') or project
                continue
            if kind == 'turn_context':
                model = p.get('model') or model
                project = p.get('cwd') or project
                continue
            t = timestamp(obj.get('timestamp'))
            if t is None:
                continue
            thread = parent if parent and owner_time and t < owner_time else (owner or path)
            if kind == 'token_usage_record':
                u = counts(p.get('usage'))
                if u is None:
                    continue
                thread = p.get('thread_id') or thread
                key = ('response', p['response_id']) if p.get('response_id') else (
                    'request', thread, t.isoformat(), u)
                total = counts(p.get('thread_token_usage'))
                primaries.append((key, record(t, p.get('model') or model, project, u),
                                  (thread, total), (thread, t.replace(microsecond=0), u), u))
                continue
            if kind != 'event_msg' or p.get('type') != 'token_count':
                continue
            info = p.get('info')
            if not isinstance(info, dict):
                continue
            total = counts(info.get('total_token_usage'))
            last = counts(info.get('last_token_usage'))
            if total is not None:
                if previous == total:
                    continue
                reset = previous is None or total[0] < previous[0] or total[3] < previous[3]
                delta = (last or total) if reset else tuple(max(0, a - b) for a, b in zip(total, previous))
                previous = total
                key = ('counter', thread, t.isoformat(), total)
            elif last is not None:
                delta = last
                key = ('last', thread, t.isoformat(), last)
            else:
                continue
            fallbacks.append((key, record(t, model, project, delta), (thread, total),
                              (thread, t.replace(microsecond=0), last), delta))
    return primaries, fallbacks


def scan_events(files, *, read_file=None):
    primary = []
    fallback = []
    for path in files:
        requests, counters = (read_file or _read_file)(path)
        primary.extend(requests)
        fallback.extend(counters)
    checkpoints = defaultdict(list)
    for _, rec, c, _, _ in primary:
        if c[1] is not None:
            checkpoints[c].append(rec['t_utc'])
    mirrors = {m for _, _, _, m, _ in primary}
    # Checkpoint values may recur after a counter reset; only nearby paired
    # events are mirrors. Real rollouts write the pair milliseconds apart.
    candidates = primary + [r for r in fallback if r[3] not in mirrors and not any(
        abs((r[1]['t_utc'] - t).total_seconds()) <= 2 for t in checkpoints.get(r[2], []))]
    merged = {}
    for key, rec, _, _, u in candidates:
        prior = merged.get(key)
        if prior:
            old_rec, old_u = prior
            # Merge the inclusive input counter BEFORE splitting cached input;
            # max(non-cached) + max(cached) can invent tokens in partial snapshots.
            u = tuple(max(a, b) for a, b in zip(old_u, u))
            model = old_rec['model'] if old_rec['model'] != 'unknown' else rec['model']
            rec = record(old_rec['t_utc'], model, old_rec['project'], u)
        merged[key] = (rec, u)
    for key, (rec, _) in merged.items():
        if rec['input_'] + rec['cache_read'] + rec['cache_create'] + rec['output']:
            yield key, rec
