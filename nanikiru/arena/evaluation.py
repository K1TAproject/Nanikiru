"""A-rules hanchan evaluation. No fitting and no silent failed-game exclusion."""
from contextlib import contextmanager, ExitStack
from dataclasses import asdict
from hashlib import sha256
import json
import math
import os
from pathlib import Path
import statistics
import sys
import time

from .akochan import AkochanBot
from .external import RequestBot
from .session import Session, inspect_turn, load_weights
from ..rules.config import tenhou_hanchan, TENHOU_HANCHAN
from ..simulation import Simulator
from ..simulation.persistence import save_simulation


def read(path):return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def write(path, data):
    path=Path(path);temporary=path.with_suffix(path.suffix+'.tmp')
    with temporary.open('w',encoding='utf-8') as stream:
        json.dump(data,stream,ensure_ascii=False,indent=2,allow_nan=False)
        stream.flush();os.fsync(stream.fileno())
    # Windows readers/antivirus may briefly deny delete-sharing during polling.
    for attempt in range(20):
        try:
            os.replace(temporary,path)
            break
        except PermissionError:
            if attempt==19:raise
            time.sleep(.05)


def digest(path):return sha256(Path(path).read_bytes()).hexdigest()


def tree_digest(root, patterns):
    h=sha256()
    for path in sorted({p for pattern in patterns for p in Path(root).rglob(pattern) if p.is_file() and '.git' not in p.parts}):
        h.update(path.relative_to(root).as_posix().encode());h.update(path.read_bytes())
    return h.hexdigest()


def configuration(path):
    raw=read(path);base=Path(path).resolve().parent
    if set(raw)!={'rules_profile','bots','candidate','opponents','seeds','max_hands','max_actions'}:
        raise ValueError('Unexpected/missing evaluation configuration fields')
    if raw['rules_profile']!=TENHOU_HANCHAN:raise ValueError('Only A rules supported')
    if len(raw['opponents'])!=3 or any(b not in raw['bots'] for b in [raw['candidate'],*raw['opponents']]):
        raise ValueError('Specify a candidate and three configured opponents')
    if not raw['seeds'] or any(type(s) is not int for s in raw['seeds']) or len(set(raw['seeds']))!=len(raw['seeds']):
        raise ValueError('Seeds must be unique integers')
    for key in ('max_hands','max_actions'):
        if type(raw[key]) is not int or raw[key]<1:raise ValueError('Positive run limits required')
    for name,bot in raw['bots'].items():
        if bot['kind']=='builtin':
            if set(bot)-{'kind','mode','weights'} or bot['mode'] not in ('v1 牌效','默认 v2','拟合 v2'):raise ValueError('Invalid built-in bot')
            if bot['mode']=='拟合 v2':
                weights=(base/bot['weights']).resolve();bot['weights']=str(weights)
                bot['parameters']=load_weights(weights)
                bot['artifacts']={str(weights):digest(weights),str(weights.with_name('report.json')):digest(weights.with_name('report.json'))}
        elif bot['kind'] in ('akochan','request-jsonl'):
            if set(bot)!={'kind','command','cwd','timeout','artifacts','version','rules_profile'}:raise ValueError('External bot needs command/cwd/timeout/artifacts/version/rules_profile')
            if bot['rules_profile']!=TENHOU_HANCHAN:raise ValueError('External bot A-rule declaration required')
            if not bot['version'] or not bot['artifacts']:raise ValueError('External version and artifact paths required')
            if not isinstance(bot['command'],list) or not bot['command'] or any(not isinstance(v,str) for v in bot['command']):raise ValueError('Command must be an argv list')
            if type(bot['timeout']) not in (int,float) or not math.isfinite(bot['timeout']) or bot['timeout']<=0:raise ValueError('Positive finite timeout required')
            bot['cwd']=str((base/bot['cwd']).resolve())
            bot['command'][0]=str((base/bot['command'][0]).resolve())
            paths=[(base/v).resolve() for v in bot['artifacts']]
            paths.append(Path(bot['command'][0]))
            bot['artifacts']={str(p):tree_digest(p,('*',)) if p.is_dir() else digest(p) for p in paths}
        else:raise ValueError('Unsupported bot kind: '+bot['kind'])
    raw['rules']=asdict(tenhou_hanchan());raw['schema']='nanikiru-evaluation-v1'
    raw['code_sha256']=tree_digest(Path(__file__).resolve().parents[1],('*.py',))
    raw['python']=sys.version
    import importlib.metadata
    raw['mahjong']=importlib.metadata.version('mahjong')
    return raw


@contextmanager
def run_lock(folder):
    with (folder/'run.lock').open('a+b') as stream:
        stream.seek(0);stream.write(b'0');stream.flush();stream.seek(0)
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        try:yield
        finally:
            if os.name=='nt':
                stream.seek(0);msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)


def play_match(config, seed, rotation, folder, progress=lambda **kw:None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    lineup=[config['candidate'],*config['opponents']]
    lineup=lineup[-rotation:]+lineup[:-rotation] if rotation else lineup
    next_round={};rounds=[];game=None;action=None;seat=None
    with ExitStack() as stack:
        processes={}
        for seat,name in enumerate(lineup):
            bot=config['bots'][name]
            if bot['kind']!='builtin':
                err=stack.enter_context((folder/f'bot-{seat}.stderr.log').open('a',encoding='utf-8'))
                cls=AkochanBot if bot['kind']=='akochan' else RequestBot
                process=cls(bot['command'],bot['cwd'],bot['timeout'],err)
                stack.callback(process.close);processes[seat]=process
        try:
            for hand in range(config['max_hands']):
                # Same seed/hand stream across rotations and candidate versions.
                wall_seed=sha256(f'nanikiru-eval-v1:{seed}:{hand}'.encode()).hexdigest()
                game=Simulator(rules=tenhou_hanchan(),seed=wall_seed,**next_round)
                session=Session(game)
                while game.phase!='ended':
                    seat=session.acting_seat()
                    if seat is None:raise RuntimeError('No eligible actor before hand end')
                    if game.command_count>=config['max_actions']:raise RuntimeError('Action limit exceeded; not a draw')
                    bot=config['bots'][lineup[seat]]
                    action=None
                    progress(seed=seed,rotation=rotation,hand=hand+1,action=game.command_count,seat=seat,bot=lineup[seat])
                    turn=session.turn(seat,bot.get('mode','默认 v2'),bot.get('parameters'))
                    action=(processes[seat].decide(turn,game.record(seat)) if seat in processes else inspect_turn(turn.request).action)
                    if action is None:raise ValueError('No executable analyzer recommendation')
                    session.submit(turn,action)
                path=folder/f'hand-{hand:03d}.json';save_simulation(game,path)
                result=game.result
                if sum(result['scores'])+result['riichi_sticks']*1000!=100000:raise RuntimeError('Point conservation failed')
                rounds.append(dict(file=path.name,sha256=digest(path),result=result,actions=game.command_count))
                if result['match']['ended']:
                    ranking=result['match']['ranking'];scores=result['scores']
                    return dict(seed=seed,rotation=rotation,lineup=lineup,hands=rounds,
                        scores=scores,ranking=ranking,candidate_seat=rotation,
                        rank=ranking.index(rotation)+1,points=scores[rotation],
                        result_points=result['match']['result_points'][ranking.index(rotation)])
                next_round=result['next_round']
            raise RuntimeError('Hand limit exceeded; not a completed hanchan')
        except BaseException as error:
            if game is not None:save_simulation(game,folder/'failed-hand.json')
            write(folder/'failure.json',dict(seat=seat,bot=None if seat is None else lineup[seat],
                action=None if action is None else asdict(action),error=f'{type(error).__name__}: {error}'))
            raise


def summarize(rows, planned):
    groups={}
    for row in rows:groups.setdefault(row['seed'],{})[row['rotation']]=row
    complete=[list(group.values()) for group in groups.values() if set(group)==set(range(4))]
    data=[r for group in complete for r in group]
    metrics={}
    for key in ('rank','points','result_points'):
        means=[statistics.mean(r[key] for r in group) for group in complete]
        mean=statistics.mean(means) if means else None
        half=1.96*statistics.stdev(means)/math.sqrt(len(means)) if len(means)>=2 else None
        metrics[key]=dict(mean=mean,ci95_normal=None if half is None else [mean-half,mean+half])
    return dict(completed_matches=len(rows),planned_matches=planned,complete_seed_blocks=len(complete),
        excluded_partial_block_matches=len(rows)-len(data),metrics=metrics,
        rank_rates=[sum(r['rank']==k for r in data)/len(data) if data else None for k in range(1,5)],
        uncertainty='Approximate normal CI over independent seed-block means; small samples are not strength evidence.')


def compare(left, right):
    """Paired new-minus-old results; fail rather than compare different schedules."""
    folders=[Path(left),Path(right)];configs=[read(p/'manifest.json') for p in folders]
    for key in ('rules','seeds','max_hands','max_actions'):
        if configs[0][key]!=configs[1][key]:raise ValueError('Comparison schedule/rules differ: '+key)
    opponents=[[c['bots'][name] for name in c['opponents']] for c in configs]
    if opponents[0]!=opponents[1]:raise ValueError('Opponent versions or configurations differ')
    all_rows=[]
    for folder,config in zip(folders,configs):
        if read(folder/'summary.json')['status']!='complete':raise ValueError('Only complete evaluations may be compared')
        rows=[]
        for i,seed in enumerate(config['seeds']):
            for rotation in range(4):
                row=read(folder/f'match-{i:05d}-{rotation}'/'result.json')
                if (row['seed'],row['rotation'])!=(seed,rotation):raise ValueError('Mismatched paired result')
                rows.append(row)
        all_rows.append(rows)
    delta=[dict(seed=a['seed'],rotation=a['rotation'],**{k:b[k]-a[k] for k in ('rank','points','result_points')})
           for a,b in zip(*all_rows)]
    metrics=summarize(delta,len(delta))['metrics']
    return dict(schema='nanikiru-evaluation-comparison-v1',old=str(folders[0]),new=str(folders[1]),
        seed_blocks=len(configs[0]['seeds']),new_minus_old=metrics,
        interpretation='Negative rank difference is better; positive points difference is better. Approximate seed-block normal intervals; not a strength claim for small samples.',
        code_changed=configs[0]['code_sha256']!=configs[1]['code_sha256'])


def run(config_path, output):
    config=configuration(config_path);folder=Path(output);folder.mkdir(parents=True,exist_ok=True)
    with run_lock(folder):
        manifest=folder/'manifest.json'
        if manifest.exists():
            if read(manifest)!=config:raise ValueError('Configuration/code/artifacts changed; use a new output directory')
        else:
            if any(p.name!='run.lock' for p in folder.iterdir()):raise ValueError('Output is not an empty evaluation directory')
            write(manifest,config)
        rows=[];planned=len(config['seeds'])*4;started=time.monotonic();last_print=0;last_current={}
        def progress(**current):
            nonlocal last_print,last_current
            last_current=current
            state=dict(stage='running',pid=os.getpid(),completed_matches=len(rows),total_matches=planned,elapsed_seconds=round(time.monotonic()-started,1),**current)
            write(folder/'status.json',state)
            if time.monotonic()-last_print>=5:
                print(json.dumps(state,ensure_ascii=False),flush=True);last_print=time.monotonic()
        try:
            for i,seed in enumerate(config['seeds']):
                for rotation in range(4):
                    match_dir=folder/f'match-{i:05d}-{rotation}';path=match_dir/'result.json'
                    if path.exists():
                        row=read(path)
                        if row['seed']!=seed or row['rotation']!=rotation:raise ValueError('Mismatched completed match')
                        for hand in row['hands']:
                            if digest(match_dir/hand['file'])!=hand['sha256']:raise ValueError('Replay changed; cannot resume')
                    else:
                        row=play_match(config,seed,rotation,match_dir,progress)
                        write(path,row)
                    rows.append(row)
                    write(folder/'summary.json',dict(status='running',**summarize(rows,planned)))
            write(folder/'summary.json',dict(status='complete',**summarize(rows,planned)))
            write(folder/'status.json',dict(stage='complete',completed_matches=len(rows),total_matches=planned))
        except BaseException as error:
            failure=dict(stage='interrupted' if isinstance(error,KeyboardInterrupt) else 'failed',
                completed_matches=len(rows),total_matches=planned,error=f'{type(error).__name__}: {error}',**last_current)
            write(folder/'status.json',failure)
            with (folder/'failures.jsonl').open('a',encoding='utf-8') as stream:
                stream.write(json.dumps(dict(timestamp=time.time(),**failure),ensure_ascii=False)+'\n')
                stream.flush();os.fsync(stream.fileno())
            write(folder/'summary.json',dict(status='incomplete',**summarize(rows,planned)))
            raise
    return read(folder/'summary.json')
