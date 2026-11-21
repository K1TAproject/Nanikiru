"""Disk-backed, deterministic discard/riichi imitation experiments.

Labels never enter analyze(). This does not estimate counterfactual rewards.
"""

from collections import Counter
from contextlib import closing
from dataclasses import asdict, replace
from hashlib import sha256
import importlib.metadata
import json
import math
from pathlib import Path
import platform
import sqlite3

from ..analysis import analyze
from ..analysis.fixed_policy import Parameters, FORMULA_VERSION
from ..analysis.output import debug_json
from ..analysis.routes import route_potential
from ..contracts import Action, request_from_dict
from ..models import UNKNOWN
from ..rules.config import TENHOU_HANCHAN, tenhou_hanchan
from ..observation.dataset import stable_seed

SCHEMA='v2-ranking-features-2'
LINEAR=('shanten','ukeire','lookahead','value','bonus','reserve')
DEFAULTS=asdict(Parameters())
TUNABLE=tuple(k for k in DEFAULTS if k!='shanten')


def digest(path):
    with Path(path).open('rb') as stream:return sha256_stream(stream)


def sha256_stream(stream):
    value=sha256()
    for chunk in iter(lambda:stream.read(1024*1024),b''):value.update(chunk)
    return value.hexdigest()


def code_version():
    root=Path(__file__).resolve().parents[1]
    value=sha256()
    for path in sorted(root.rglob('*.py')):
        value.update(path.relative_to(root).as_posix().encode())
        value.update(path.read_bytes())
    return value.hexdigest()


def partition(source, seed):
    n=stable_seed('split',seed,source)%100
    return 'train' if n<80 else 'validation' if n<90 else 'test'


def action_key(action, position):
    codes={t.id:t.code for t in position.tiles}
    return (action.kind,*(codes[t] for t in action.tile_ids))


def extract(request, label):
    """Cache sufficient features for exact rescoring of discard/riichi branches."""
    if label.kind not in ('discard','riichi') or label.actor!=request.position.observer_seat:
        raise ValueError('Expected observer discard/riichi label')
    held={t.id for t in request.position.tiles if t.zone=='hand' and t.seat==label.actor}
    if not set(label.tile_ids)<=held:raise ValueError('Label tile is not in observer hand')
    request=replace(request,method='fixed-v2',method_options={},tasks=('recommend',))
    result=analyze(request)
    target=action_key(label,request.position)
    if UNKNOWN in target:return dict(skip_reason='unknown_label_code',candidates=[])
    # Declining a possible win is outside this training objective.
    if any(c.action.kind in ('ron','tsumo') and c.legality!='forbidden' for c in result.candidates):
        return dict(skip_reason='possible_win_outside_scope',candidates=[])
    candidates={}
    late=0 if request.position.remaining_draws is UNKNOWN else max(0,min(1,(70-request.position.remaining_draws)/70))
    for c in result.candidates:
        if c.action.kind not in ('discard','riichi') or c.legality=='forbidden':continue
        m=c.metrics
        if 'policy_score' not in m or m['policy_score'].value is None:continue
        key=action_key(c.action,request.position)
        terms=m['policy_score'].details['contributions']
        linear={k:terms.get(k,0)/DEFAULTS[k] for k in LINEAR}
        lock=0.0
        if c.action.kind=='riichi':
            linear['value']-=1/8
            lock=m['action_evaluation'].value['lock_threat']
        row=dict(action=list(key),legality=c.legality,linear=linear,risk=m['danger'].value,
                 late=late,lock=lock,routes=m['routes'].value['routes'] if m['shanten'].value>0 else [],
                 shanten=m['shanten'].value,ukeire=m['ukeire'].value,
                 missing_fields=m['policy_score'].details['missing_fields'])
        # Same-code physical copies are one decision. Red and ordinary fives,
        # discard and riichi remain distinct. Prefer the permitted copy.
        if key not in candidates or c.legality=='allowed':candidates[key]=row
    rows=[candidates[k] for k in sorted(candidates)]
    if target not in candidates:return dict(skip_reason='target_unavailable_or_forbidden',candidates=rows)
    return dict(skip_reason=None,label=list(target),candidates=rows,
                conditional=any(c['legality']!='allowed' for c in rows),
                other_actions=[c.action.kind for c in result.candidates if
                               c.action.kind not in ('discard','riichi') and c.legality!='forbidden'])


def score(candidate, weights):
    linear=sum(weights[k]*v for k,v in candidate['linear'].items())
    route=max((route_potential(r,weights['route_speed'],weights['route_ukeire'])
               for r in candidate['routes']),default=0)
    return (linear-weights['risk']*(1+weights['late_defense']*candidate['late'])*candidate['risk']
            -weights['commitment']*candidate['lock']+weights['route']*route)


def prepare(sources, destination, *, seed='0', rules_profile=None, progress=None):
    if rules_profile not in (None,TENHOU_HANCHAN):raise ValueError('Unsupported training rules profile')
    paths=sorted({Path(p).resolve() for p in sources})
    if not paths or any(not p.is_file() for p in paths):raise ValueError('Expected existing sample JSONL files')
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    manifest=dict(schema_version=SCHEMA,status='incomplete',seed=seed,code_sha256=code_version(),
                  python=platform.python_version(),mahjong=importlib.metadata.version('mahjong'),
                  split_unit='source_id',split_rule='sha256 80/10/10',
                  scope='discard_riichi_imitation_not_all_action_strength',rules_profile=rules_profile,
                  rules_check='verified_source_profile' if rules_profile else 'experimental_unverified_source',
                  inputs=[dict(path=str(p),sha256=digest(p)) for p in paths])
    counts=Counter();duplicates=0
    (destination/'manifest.json').write_text(debug_json(manifest),encoding='utf-8')
    database=destination/'features.sqlite'
    try:
        with closing(sqlite3.connect(database)) as db, db:
            db.execute('CREATE TABLE samples (identity TEXT PRIMARY KEY, source TEXT, split TEXT, variant TEXT, fingerprint TEXT, payload TEXT)')
            db.execute('CREATE INDEX samples_split ON samples(split)')
            for path in paths:
                with path.open(encoding='utf-8-sig') as stream:
                    for line_number,line in enumerate(stream,1):
                        if not line.strip():continue
                        try:
                            sample=json.loads(line)
                            if sample['schema_version'] not in ('tenhou-discard-v1','discard-sample-v1','observed-discard-v1'):
                                raise ValueError('Unsupported sample schema')
                            source=sample['source_id'];variant=sample.get('variant','complete')
                            if not isinstance(source,str) or not source or not isinstance(variant,str) or not variant:
                                raise ValueError('Invalid source or variant')
                            split=partition(source,seed)
                            if 'split' in sample and sample['split']!=split:
                                raise ValueError('Observation split disagrees with seed; use the observation export seed')
                            identity=[source,sample['hand_index'],sample['observer'],sample['event_count'],variant]
                            if any(type(n) is not int or n<0 for n in identity[1:4]):raise ValueError('Invalid sample identity')
                            request=request_from_dict(sample['input']);label=Action(**sample['labels']['action'])
                            rules=asdict(request.position.rules)
                            if 'rules' in manifest and manifest['rules']!=rules:raise ValueError('Mixed rule configurations in one training cache')
                            manifest['rules']=rules
                            if rules_profile:
                                verification=sample.get('rules_verification',{})
                                if (request.position.rules!=tenhou_hanchan() or verification.get('profile')!=rules_profile
                                        or verification.get('source_id')!=source
                                        or verification.get('evidence')!='tenhou-ranked-00a9-2023plus-and-standard-opening'):
                                    raise ValueError('Unverified or incompatible source rules; reimport with target profile')
                            if sample['observer']!=request.position.observer_seat:raise ValueError('Observer mismatch')
                            key=json.dumps(identity,separators=(',',':'))
                            fingerprint=sha256(debug_json([request,label],indent=None).encode()).hexdigest()
                            existing=db.execute('SELECT fingerprint FROM samples WHERE identity=?',(key,)).fetchone()
                            if existing:
                                if existing[0]!=fingerprint:raise ValueError('Conflicting duplicate sample identity')
                                duplicates+=1;continue
                            payload=extract(request,label)
                            db.execute('INSERT INTO samples VALUES (?,?,?,?,?,?)',
                                       (key,source,split,variant,fingerprint,debug_json(payload,indent=None)))
                            counts[split]+=1
                            if sum(counts.values())%100==0:
                                db.commit()
                                if progress:progress(dict(stage='extracting',rows=sum(counts.values())))
                        except (ValueError,KeyError,TypeError) as error:
                            raise ValueError(f'{path.name}:{line_number}: {error}') from error
            manifest['source_counts']={split:db.execute('SELECT count(DISTINCT source) FROM samples WHERE split=?',(split,)).fetchone()[0]
                                       for split in ('train','validation','test')}
        if any(digest(item['path'])!=item['sha256'] for item in manifest['inputs']):
            raise ValueError('Source changed during feature extraction; rebuild from completed exports')
        manifest.update(status='complete',rows=dict(counts),duplicates=duplicates,database_sha256=digest(database))
    finally:
        (destination/'manifest.json').write_text(debug_json(manifest),encoding='utf-8')
    return manifest


def open_cache(directory):
    directory=Path(directory)
    manifest=json.loads((directory/'manifest.json').read_text(encoding='utf-8'))
    if manifest['schema_version']!=SCHEMA or manifest['status']!='complete':raise ValueError('Incomplete or incompatible feature cache')
    if manifest['code_sha256']!=code_version():raise ValueError('Code changed; rebuild feature cache')
    if manifest['database_sha256']!=digest(directory/'features.sqlite'):raise ValueError('Feature cache checksum mismatch')
    db=sqlite3.connect((directory/'features.sqlite').resolve().as_uri()+'?mode=ro',uri=True)
    return db,manifest


def samples(db,split):
    # Cursor and JSON memory stay bounded by one sample, independent of corpus.
    for variant,payload in db.execute('SELECT variant,payload FROM samples WHERE split=? ORDER BY identity',(split,)):
        yield variant,json.loads(payload)


def observation_metrics(sample, weights):
    rows=sample['candidates'];target=sample['label']
    values=[score(c,weights) for c in rows]
    label_index=next(i for i,c in enumerate(rows) if c['action']==target)
    top=max(values);correct=values[label_index]
    ties=sum(abs(v-top)<=1e-9 for v in values)
    hit=abs(correct-top)<=1e-9
    rank=1+sum(v>correct+1e-9 for v in values)
    # Stable softmax ranking loss, not an estimate of game outcome probability.
    loss=top+math.log(sum(math.exp(v-top) for v in values))-correct
    discards=[c for c in rows if c['action'][0]=='discard']
    baseline_hit=None
    if discards and target[0]=='discard':
        use_ukeire=all(c['ukeire'] is not None for c in discards)
        key=lambda c:(c['shanten'],-c['ukeire'] if use_ukeire else 0)
        best=min(key(c) for c in discards)
        baseline_hit=any(c['action']==target and key(c)==best for c in discards)
    return dict(loss=loss,top1=float(hit),tie_adjusted_top1=(1/ties if hit else 0),
                reciprocal_rank=1/rank,v1_top1=baseline_hit)


def evaluate(db,weights,split):
    weights=asdict(Parameters(**weights));groups={}
    for variant,sample in samples(db,split):
        for name in ('all','variant:'+variant):
            group=groups.setdefault(name,dict(total=0,evaluated=0,conditional=0,forced=0,skips=Counter(),
                                              loss=0.,top1=0.,tie_adjusted_top1=0.,reciprocal_rank=0.,v1_top1=0.,v1_count=0))
            group['total']+=1
            if sample['skip_reason']:
                group['skips'][sample['skip_reason']]+=1;continue
            if len(sample['candidates'])<2:group['forced']+=1;continue
            group['evaluated']+=1;group['conditional']+=sample['conditional']
            metrics=observation_metrics(sample,weights)
            for key in ('loss','top1','tie_adjusted_top1','reciprocal_rank'):group[key]+=metrics[key]
            if metrics['v1_top1'] is not None:
                group['v1_count']+=1;group['v1_top1']+=metrics['v1_top1']
    for group in groups.values():
        n=group['evaluated']
        for key in ('loss','top1','tie_adjusted_top1','reciprocal_rank'):group[key]=group[key]/n if n else None
        group['v1_top1']=group['v1_top1']/group['v1_count'] if group['v1_count'] else None
        group['skips']=dict(group['skips'])
    return groups


def fit(db, *, rounds=2, tune=('route','route_speed','route_ukeire','value','risk'), initial=None, progress=None):
    """Coordinate search on training loss; validation selects an entire pass.

    Test rows are never queried here. Fixed shanten weight anchors score scale.
    """
    if type(rounds) is not int or rounds<1:raise ValueError('rounds must be positive')
    if not tune or len(set(tune))!=len(tune) or set(tune)-set(TUNABLE):raise ValueError('Invalid tunable parameters')
    weights=asdict(Parameters(**(initial or {})))
    train=evaluate(db,weights,'train');validation=evaluate(db,weights,'validation')
    if not train.get('all',{}).get('evaluated') or not validation.get('all',{}).get('evaluated'):
        raise ValueError('Need non-forced evaluable decisions in both train and validation splits')
    best=dict(weights);best_loss=validation['all']['loss'];trace=[]
    if progress:progress(dict(stage='fitting',event='baseline',round=0,rounds=rounds,train_loss=train['all']['loss'],validation_loss=best_loss,weights=dict(weights)))
    for iteration in range(rounds):
        for key in tune:
            current=evaluate(db,weights,'train')['all']['loss']
            chosen=weights[key]
            grid=sorted({0.,weights[key]*.5,weights[key],weights[key]*2,
                         DEFAULTS[key]*.5,DEFAULTS[key],DEFAULTS[key]*2})
            for value in grid:
                if progress:progress(dict(stage='fitting',event='trial_start',round=iteration+1,rounds=rounds,parameter=key,value=value))
                trial={**weights,key:value}
                loss=evaluate(db,trial,'train')['all']['loss']
                if progress:progress(dict(stage='fitting',event='trial_complete',round=iteration+1,rounds=rounds,parameter=key,value=value,train_loss=loss))
                if loss<current-1e-12:current,chosen=loss,value
            weights[key]=chosen
        validation=evaluate(db,weights,'validation')['all']['loss']
        trace.append(dict(round=iteration+1,weights=dict(weights),train_loss=evaluate(db,weights,'train')['all']['loss'],validation_loss=validation))
        if progress:progress(dict(stage='fitting',event='round_complete',rounds=rounds,**trace[-1]))
        if validation<best_loss-1e-12:best,best_loss=dict(weights),validation
    return best,trace


def parameter_coverage(db, weights):
    """Training-only finite perturbations; count changed candidate score gaps.

    Sensitivity is necessary, not proof of identifiability or adequate coverage.
    A constant shift of every action provides no ranking learning signal.
    """
    report={key:dict(informative_decisions=0,action_kinds=Counter()) for key in TUNABLE}
    for _,sample in samples(db,'train'):
        if sample['skip_reason'] or len(sample['candidates'])<2:continue
        rows=sample['candidates'];base=[score(c,weights) for c in rows]
        for key,entry in report.items():
            informative=False
            for value in (0.,DEFAULTS[key],max(DEFAULTS[key],weights[key])*2):
                trial={**weights,key:value}
                delta=[score(c,trial)-v for c,v in zip(rows,base)]
                if max(delta)-min(delta)>1e-9:
                    informative=True;break
            if informative:
                entry['informative_decisions']+=1
                entry['action_kinds'].update(set(c['action'][0] for c in rows))
    for entry in report.values():entry['action_kinds']=dict(entry['action_kinds'])
    return report


def run(directory,destination,*,rounds=2,tune=('route','route_speed','route_ukeire','value','risk'),initial=None,progress=None):
    db,manifest=open_cache(directory)
    try:
        if not tune or len(set(tune))!=len(tune) or set(tune)-set(TUNABLE):raise ValueError('Invalid tunable parameters')
        coverage=parameter_coverage(db,asdict(Parameters(**(initial or {}))))
        active=tuple(k for k in tune if coverage[k]['informative_decisions'])
        if not active:raise ValueError('No requested parameter has training candidate-gap sensitivity')
        weights,trace=fit(db,rounds=rounds,tune=active,initial=initial,progress=progress)
        if progress:progress(dict(stage='evaluating',event='final_evaluation',weights=weights))
        report=dict(schema_version='v2-ranking-report-1',dataset=manifest,scope='discard_riichi_imitation',
                    not_strength_evidence=True,formula_version=FORMULA_VERSION,
                    deployment_objective='mean_final_rank_then_settlement_points',
                    optimization_objective='replay_action_ranking_loss',
                    parameter_coverage=coverage,requested_tune=list(tune),
                    frozen_no_signal=[k for k in tune if k not in active],
                    rounds=rounds,tune=list(active),initial=asdict(Parameters(**(initial or {}))),
                    weights=weights,trace=trace,
                    baseline={s:evaluate(db,asdict(Parameters(**(initial or {}))),s) for s in ('train','validation','test')},
                    fitted={s:evaluate(db,weights,s) for s in ('train','validation','test')})
    finally:db.close()
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    (destination/'weights.json').write_text(debug_json(weights),encoding='utf-8')
    (destination/'report.json').write_text(debug_json(report),encoding='utf-8')
    return report


def evaluate_cache(directory,destination,weights=None):
    db,manifest=open_cache(directory)
    try:
        weights=asdict(Parameters(**(weights or {})))
        report=dict(schema_version='v2-ranking-report-1',dataset=manifest,weights=weights,
                    not_strength_evidence=True,
                    results={s:evaluate(db,weights,s) for s in ('train','validation','test')})
    finally:db.close()
    with Path(destination).open('x',encoding='utf-8') as stream:stream.write(debug_json(report))
    return report
