"""Streaming paired observation variants; labels never enter analyze()."""

from copy import deepcopy
from dataclasses import asdict, replace
from hashlib import sha256
import json
from pathlib import Path

from ..analysis import analyze
from ..analysis.output import debug_json
from ..contracts import Action, request_from_dict
from ..models import valid_seat
from .observer import ObservationConfig, _observe


DEFAULT_VARIANTS = {
    'complete': ObservationConfig(),
    'midgame': ObservationConfig(midgame=True),
    'discard_kind_unknown': ObservationConfig(discard_kind_rate=1),
    'public_missing': ObservationConfig(public_code_rate=0.25),
    'mixed': ObservationConfig(midgame=True, public_code_rate=0.25, discard_kind_rate=0.5),
}


def stable_seed(*parts):
    return int(sha256(json.dumps(parts, ensure_ascii=False, sort_keys=True).encode()).hexdigest()[:16], 16)


def variants(sample, configs=None, *, seed=0):
    """One source sample in, independent variants out, bounded by one sample."""
    configs = DEFAULT_VARIANTS if configs is None else configs
    request = sample['input']
    if isinstance(request, dict): request = request_from_dict(request)
    identity = [sample['source_id'], sample['hand_index'], sample['observer'], sample['event_count']]
    if (not isinstance(identity[0], str) or not identity[0]
            or any(type(n) is not int or n < 0 for n in (identity[1], identity[3]))
            or not valid_seat(identity[2]) or identity[2] != request.position.observer_seat):
        raise ValueError('Invalid source sample identity')
    source_labels = json.loads(debug_json(sample['labels']))
    source_action = Action(**source_labels['action'])
    held = {t.id for t in request.position.tiles if t.zone=='hand' and t.seat==identity[2]}
    if (source_action.kind not in ('discard','riichi') or source_action.actor != identity[2]
            or not set(source_action.tile_ids) <= held):
        raise ValueError('Label must select an observer hand tile')
    # Same draw sequence across configurations makes comparisons reproducible.
    sample_seed = stable_seed(seed, *identity)
    split_value = stable_seed('split', seed, sample['source_id']) % 100
    split = 'train' if split_value < 80 else 'validation' if split_value < 90 else 'test'
    for name, config in configs.items():
        observed, mapping = _observe(request, config, sample_seed)
        labels = deepcopy(source_labels)
        action = labels['action']
        action['tile_ids'] = [mapping[t] for t in action['tile_ids']]
        yield dict(schema_version='observed-discard-v1', source_id=identity[0], hand_index=identity[1],
                   observer=identity[2], event_count=identity[3], split=split, variant=name,
                   observation={'config':asdict(config), 'seed':sample_seed}, input=observed, labels=labels,
                   **({'rules_verification':deepcopy(sample['rules_verification'])} if 'rules_verification' in sample else {}))


def compare_methods(request):
    """Small audit result, not model accuracy or strength evaluation."""
    output = {}
    for method in ('minimal-shape-v1', 'fixed-v2'):
        result = analyze(replace(request, method=method, method_options={},
                                 tasks=('shanten', 'discard_shape', 'recommend')))
        ranking = result.situation['discard_ranking']
        output[method] = {
            'ranking_status': ranking.status,
            'top_tiles': [a.tile_ids[0] for a in ranking.value[0]] if ranking.value else [],
            'top_codes': list(dict.fromkeys(next(t.code for t in request.position.tiles if t.id==a.tile_ids[0])
                              for a in ranking.value[0])) if ranking.value else [],
            'executable': bool(result.recommendations),
            'candidate_statuses': {c.action.tile_ids[0]: {k:v.status for k,v in c.metrics.items()} for c in result.candidates if c.action.kind=='discard'},
            'action_candidates': [{'action':asdict(c.action), 'legality':c.legality,
                                   'metric_statuses':{k:v.status for k,v in c.metrics.items()}}
                                  for c in result.candidates],
        }
    return output


def export_dataset(sources, destination, *, configs=None, seed=0, shard_size=1000, audit_limit=20):
    """Read JSONL line by line; separate files by split and shard size.

    Source files must come from the confirmed Tenhou sample exporter. Memory
    does not grow with corpus size. No silent skip of malformed source rows.
    """
    if type(shard_size) is not int or shard_size < 1 or type(audit_limit) is not int or audit_limit < 0:
        raise ValueError('Invalid shard size or audit limit')
    configs = DEFAULT_VARIANTS if configs is None else configs
    if not isinstance(configs, dict) or not configs or any(not isinstance(k,str) or not k for k in configs):
        raise ValueError('Specify named observation variants')
    for config in configs.values(): config.__post_init__()
    sources = tuple(Path(p).resolve() for p in sources)
    if not sources or len(set(sources)) != len(sources) or any(not p.is_file() for p in sources):
        raise ValueError('Specify distinct existing sample JSONL files')
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    counts = {s:0 for s in ('train','validation','test')}
    streams = {}
    inputs = 0
    manifest = dict(schema_version='observation-dataset-v1', status='incomplete', seed=seed,
                    shard_size=shard_size, variants={k:asdict(v) for k,v in configs.items()},
                    input_rows=0, output_rows=counts, split_unit='source_id', audit_input_limit=audit_limit)
    manifest_path = destination/'manifest.json'
    manifest_path.write_text(debug_json(manifest),encoding='utf-8')
    try:
        with (destination/'audit.jsonl').open('w',encoding='utf-8') as audit:
            for source in sources:
                with source.open(encoding='utf-8-sig') as source_stream:
                    for line_number,line in enumerate(source_stream,1):
                        if not line.strip(): continue
                        try:
                            sample = json.loads(line)
                            if sample['schema_version'] not in ('tenhou-discard-v1', 'discard-sample-v1'):
                                raise ValueError('Expected confirmed discard sample')
                            for row in variants(sample,configs,seed=seed):
                                split = row['split']; n = counts[split]
                                if n % shard_size == 0:
                                    if split in streams: streams[split].close()
                                    streams[split] = (destination/f'{split}-{n//shard_size:05d}.jsonl').open('w',encoding='utf-8')
                                streams[split].write(debug_json(row,indent=None)+'\n')
                                counts[split] += 1
                                if inputs < audit_limit:
                                    audit.write(json.dumps(dict(source_id=row['source_id'],hand_index=row['hand_index'],
                                        event_count=row['event_count'],observer=row['observer'],variant=row['variant'],
                                        results=compare_methods(row['input'])),ensure_ascii=False)+'\n')
                        except (ValueError,KeyError,TypeError) as error:
                            raise ValueError(f'{source.name}:{line_number}: {error}') from error
                        inputs += 1
        manifest['status'] = 'complete'
    finally:
        for stream in streams.values(): stream.close()
        manifest['input_rows'] = inputs
        manifest_path.write_text(debug_json(manifest),encoding='utf-8')
    return manifest
