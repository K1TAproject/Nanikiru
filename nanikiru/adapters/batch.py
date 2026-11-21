"""Import a local high-rank Tenhou pilot with explicit provenance and quality gates."""

import argparse
import gzip
from hashlib import sha256
import json
from pathlib import Path
import xml.etree.ElementTree as ET

from .tenhou import parse_xml
from ..analysis.output import debug_json
from ..processing.records import save_record


def inspect_log(raw, *, min_dan=7):
    if type(min_dan) is not int or not 1<=min_dan<=10:raise ValueError('min_dan must be 1..10')
    if raw.startswith(b'\x1f\x8b'):raw=gzip.decompress(raw)
    if b'<!DOCTYPE' in raw.upper() or b'<!ENTITY' in raw.upper():raise ValueError('XML declarations are not supported')
    root=ET.fromstring(raw)
    if root.tag!='mjloggm':raise ValueError('Expected Tenhou XML')
    go=root.find('GO');players=root.find('UN')
    if go is None or players is None:raise ValueError('Missing table/player metadata')
    mode=int(go.attrib['type'])
    ranks=[int(n) for n in players.attrib.get('dan','').split(',') if n]
    # Exact supported public Houou modes: 4-player, red, open tanyao, east/south.
    if mode not in (161,169) or go.get('lobby')!='0':raise ValueError('Not supported public four-player Houou mode')
    if len(ranks)!=4 or any(n<min_dan+9 or n>20 for n in ranks):raise ValueError('Player ranks missing or below threshold')
    if not any(n.tag in ('AGARI','RYUUKYOKU') and 'owari' in n.attrib for n in root):
        raise ValueError('Missing match-end result')
    return raw,dict(source_id=sha256(raw).hexdigest(),mode=mode,rank_codes=ranks,
                    ranks=[f'{n-9}dan' if n<20 else 'tenhou' for n in ranks],match_end=True)


def import_batch(sources,destination,*,min_dan=7,audit_per_game=24):
    if type(audit_per_game) is not int or audit_per_game<1:raise ValueError('audit_per_game must be positive')
    paths=sorted({Path(p).resolve() for p in sources})
    if not paths or any(not p.is_file() for p in paths):raise ValueError('Expected existing local replay files')
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=False)
    report=dict(schema_version='houou-pilot-import-1',status='incomplete',min_dan=min_dan,
                audit_per_game=audit_per_game,selection='evenly_spaced_decision_indices_per_match',
                license_status='not_inferred_from_public_access',sources=[],hands=0,samples=0,audit_samples=0)
    seen=set()
    try:
        with (destination/'discard-samples.jsonl').open('w',encoding='utf-8') as full, \
                (destination/'audit-samples.jsonl').open('w',encoding='utf-8') as audit:
            for path in paths:
                entry=dict(path=str(path));report['sources'].append(entry)
                try:
                    raw,metadata=inspect_log(path.read_bytes(),min_dan=min_dan)
                    entry.update(metadata)
                    if metadata['source_id'] in seen:entry['status']='duplicate';continue
                    hands=parse_xml(raw)
                except (ValueError,ET.ParseError,KeyError) as error:
                    entry.update(status='rejected',reason=str(error));continue
                seen.add(metadata['source_id'])
                records=destination/metadata['source_id'];records.mkdir()
                total=sum(len(h.decisions) for h in hands)
                size=min(audit_per_game,total)
                indices={i*(total-1)//max(1,size-1) for i in range(size)}
                cursor=0
                for hand in hands:
                    for seat,record in enumerate(hand.records):
                        save_record(record,records/f'hand-{hand.hand_index:03d}-seat-{seat}.json')
                    for sample in hand.samples():
                        text=debug_json(sample,indent=None)+'\n';full.write(text)
                        if cursor in indices:audit.write(text);report['audit_samples']+=1
                        cursor+=1
                entry.update(status='imported',hands=len(hands),samples=total)
                report['hands']+=len(hands);report['samples']+=total
        report['status']='complete'
    finally:(destination/'import-report.json').write_text(debug_json(report),encoding='utf-8')
    return report


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('sources',nargs='+',type=Path)
    parser.add_argument('--output',required=True,type=Path)
    parser.add_argument('--min-dan',type=int,default=7)
    parser.add_argument('--audit-per-game',type=int,default=24)
    args=parser.parse_args()
    try:report=import_batch(args.sources,args.output,min_dan=args.min_dan,audit_per_game=args.audit_per_game)
    except (OSError,ValueError) as error:parser.exit(1,f'{error}\n')
    print(json.dumps({k:report[k] for k in ('status','hands','samples','audit_samples')},indent=2))


if __name__=='__main__':main()
