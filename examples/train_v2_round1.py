"""Reproducible first v2 experiment; existing importer, features and fitter only."""
import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, wait, FIRST_COMPLETED
from contextlib import closing, ExitStack
from hashlib import sha256
import io
import json
import os
from pathlib import Path
import sqlite3
import tarfile
import traceback
from datetime import datetime, timezone

from nanikiru.adapters.mjai import decode_payload, export_mjai
from nanikiru.analysis.output import debug_json
from nanikiru.rules.config import TENHOU_HANCHAN, tenhou_hanchan
from nanikiru.training.ranking import code_version, digest, open_cache, partition, prepare, run


def write_status(root, **values):
    values['pid']=os.getpid()
    values['updated_at']=datetime.now(timezone.utc).isoformat()
    path=root/'status.json';temporary=root/'status.tmp'
    temporary.write_text(debug_json(values),encoding='utf-8');temporary.replace(path)
    print(json.dumps(values,ensure_ascii=False),flush=True)


def select(archive, root):
    root.mkdir(parents=True,exist_ok=False)
    (root/'chunks').mkdir();(root/'splits').mkdir()
    with tarfile.open(archive) as source, ExitStack() as stack:
        members=[m for m in source if m.isfile() and m.name.endswith('.mjai.json.zst')]
        excluded=[m.name for m in members[:40]]
        chosen=sorted(members[40:],key=lambda m:sha256(('round1-20261113:'+m.name).encode()).hexdigest())[:1000]
        if len(chosen)!=1000:raise ValueError('Need 1000 independent games after exclusions')
        archives=[stack.enter_context(tarfile.open(root/'chunks'/f'{i:03d}.tar','w')) for i in range(40)]
        rows=[];seen=set()
        for i,member in enumerate(chosen):
            with source.extractfile(member) as stream:compressed=stream.read()
            identity=sha256(decode_payload(compressed)).hexdigest()
            if identity in seen:raise ValueError('Duplicate source game in selection')
            seen.add(identity);split=partition(identity,'0')
            archives[i//25].addfile(member,io.BytesIO(compressed))
            rows.append(dict(member=member.name,source_id=identity,split=split,chunk=i//25))
    plan=dict(schema_version='v2-round1-experiment-1',source=str(archive.resolve()),source_sha256=digest(archive),
              code_sha256=code_version(),selection_seed='round1-20261113',split_seed='0',
              excluded_previously_inspected_members=excluded,games=1000,samples_per_game=30,
              workers=4,rounds=2,tune=['route','route_speed','route_ukeire','value','risk'],
              rules_profile=TENHOU_HANCHAN,source_counts=dict(Counter(r['split'] for r in rows)),sources=rows,
              chunks=[dict(path=f'chunks/{i:03d}.tar',sha256=digest(root/'chunks'/f'{i:03d}.tar')) for i in range(40)])
    (root/'experiment.json').write_text(debug_json(plan),encoding='utf-8')
    for split in ('train','validation','test'):
        (root/'splits'/f'{split}.jsonl').write_text(''.join(debug_json(r,indent=None)+'\n' for r in rows if r['split']==split),encoding='utf-8')
    write_status(root,stage='selected',games=1000,source_counts=plan['source_counts'],completed_chunks=0,total_chunks=40)
    return plan


def build_chunk(arguments):
    root,index,chunk,samples=arguments;root=Path(root)
    def progress(value):
        path=root/f'chunk-{index:03d}.json';temp=path.with_suffix('.tmp')
        value=dict(value,chunk=index,pid=os.getpid(),updated_at=datetime.now(timezone.utc).isoformat())
        temp.write_text(debug_json(value),encoding='utf-8');temp.replace(path)
    try:
        progress(dict(stage='importing',games=0,samples=0))
        archive=root/chunk['path']
        if digest(archive)!=chunk['sha256']:raise ValueError('Selected archive changed')
        imported=root/f'import-{index:03d}';cache=root/f'cache-{index:03d}'
        export_mjai(archive,imported,rules=tenhou_hanchan(),verified_profile=TENHOU_HANCHAN,samples_per_game=samples,progress=progress)
        progress(dict(stage='extracting',rows=0))
        manifest=prepare([imported/'discard-samples.jsonl'],cache,rules_profile=TENHOU_HANCHAN,progress=progress)
        progress(dict(stage='complete',rows=sum(manifest['rows'].values())))
        return index,manifest['rows']
    except Exception as error:
        progress(dict(stage='failed',error=str(error)));raise


def collect_jobs(root, jobs):
    pending=set(jobs);completed=0;failed=[]
    while pending:
        done,pending=wait(pending,timeout=2,return_when=FIRST_COMPLETED)
        for future in done:
            try:future.result();completed+=1
            except Exception as error:failed.append(dict(chunk=jobs[future],error=str(error)))
        write_status(root,stage='preparing_with_errors' if failed else 'preparing',
                     completed_chunks=completed,total_chunks=len(jobs),failed_chunks=failed,running_or_queued=len(pending))
    return completed,failed


def merge_caches(paths, destination, expected_sources):
    destination.mkdir(exist_ok=False)
    manifest=None;inputs=[];seen=set()
    try:
        with closing(sqlite3.connect(destination/'features.sqlite')) as merged, merged:
            merged.execute('CREATE TABLE samples (identity TEXT PRIMARY KEY, source TEXT, split TEXT, variant TEXT, fingerprint TEXT, payload TEXT)')
            merged.execute('CREATE INDEX samples_split ON samples(split)')
            for path in paths:
                db,current=open_cache(path)
                try:
                    if manifest is None:manifest={**current,'status':'incomplete'}
                    if any(current.get(k)!=manifest.get(k) for k in ('schema_version','code_sha256','seed','rules_profile','rules','rules_check')):
                        raise ValueError('Incompatible chunk caches')
                    sources=dict(db.execute('SELECT DISTINCT source,split FROM samples'))
                    if seen.intersection(sources) or any(expected_sources.get(s)!=split for s,split in sources.items()):
                        raise ValueError('Source duplicate or split mismatch')
                    seen.update(sources);inputs.extend(current['inputs'])
                    merged.executemany('INSERT INTO samples VALUES (?,?,?,?,?,?)',db.execute('SELECT * FROM samples'))
                finally:db.close()
            if seen!=set(expected_sources):raise ValueError('Missing source games in prepared samples')
            manifest.update(inputs=inputs,duplicates=0,
                rows=dict(merged.execute('SELECT split,count(*) FROM samples GROUP BY split')),
                source_counts=dict(merged.execute('SELECT split,count(DISTINCT source) FROM samples GROUP BY split')))
        manifest.update(status='complete',database_sha256=digest(destination/'features.sqlite'))
    finally:
        if manifest is not None:(destination/'manifest.json').write_text(debug_json(manifest),encoding='utf-8')
    return manifest


def execute(root):
    plan=json.loads((root/'experiment.json').read_text(encoding='utf-8'))
    completed=0
    try:
        if code_version()!=plan['code_sha256']:raise ValueError('Analysis code changed since selection')
        write_status(root,stage='preparing',completed_chunks=0,total_chunks=len(plan['chunks']),workers=plan['workers'])
        with ProcessPoolExecutor(max_workers=plan['workers']) as pool:
            jobs={pool.submit(build_chunk,(str(root),i,c,plan['samples_per_game'])):i for i,c in enumerate(plan['chunks'])}
            completed,failed=collect_jobs(root,jobs)
        if failed:raise ValueError(f'Failed chunks: {failed}')
        write_status(root,stage='merging',completed_chunks=completed,total_chunks=completed)
        merge_caches([root/f'cache-{i:03d}' for i in range(len(plan['chunks']))],root/'features',
                     {r['source_id']:r['split'] for r in plan['sources']})
        write_status(root,stage='fitting',completed_chunks=completed,total_chunks=completed)
        # run() chooses using training/validation only; test is evaluated afterwards.
        def fit_progress(value):
            value=dict(value,updated_at=datetime.now(timezone.utc).isoformat())
            with (root/'fit-progress.jsonl').open('a',encoding='utf-8') as stream:stream.write(debug_json(value,indent=None)+'\n')
            write_status(root,completed_chunks=completed,total_chunks=completed,**value)
        report=run(root/'features',root/'fit',rounds=plan['rounds'],tune=tuple(plan['tune']),progress=fit_progress)
        write_status(root,stage='complete',completed_chunks=completed,total_chunks=completed,
                     weights=report['weights'],report=str(root/'fit/report.json'))
    except BaseException as error:
        (root/'error.txt').write_text(traceback.format_exc(),encoding='utf-8')
        write_status(root,stage='failed',completed_chunks=completed,error=str(error))
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('stage',choices=('select','run'));parser.add_argument('output',type=Path)
    parser.add_argument('--archive',type=Path)
    args=parser.parse_args()
    if args.stage=='select':
        if args.archive is None:parser.error('--archive required')
        select(args.archive,args.output.resolve())
    else:execute(args.output.resolve())


if __name__=='__main__':main()
