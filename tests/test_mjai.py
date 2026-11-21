import gzip
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
import xml.etree.ElementTree as ET

import zstandard
from test_tenhou import fixture
from nanikiru.adapters.tenhou import code
from nanikiru.adapters.mjai import HONORS, tile_code, parse_mjai, export_mjai, iter_archive
from nanikiru.analysis import analyze
from nanikiru.analysis.output import debug_json
from nanikiru.models import UNKNOWN
from nanikiru.observation import observe_request, ObservationConfig
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.processing.records import replay, load_record
from nanikiru.rules.config import RuleConfig
from nanikiru.training.ranking import prepare

RULES=RuleConfig(match_length='south')
def pai(t):
    c=code(t)
    return {v:k for k,v in HONORS.items()}.get(c, '5'+c[1]+'r' if c[0]=='0' else c)
def events(actions=None,hands=None,reserve=(),ending=None):
    init=ET.fromstring(fixture(hands=hands,reserve=reserve)).find('INIT').attrib
    start=dict(type='start_kyoku',bakaze='E',kyoku=1,honba=0,kyotaku=0,oya=0,
               scores=[25000]*4,dora_marker=pai(135),
               tehais=[[pai(int(t)) for t in init[f'hai{s}'].split(',')] for s in range(4)])
    return [dict(type='start_game',names=['private']*4,aka_flag=True),start,
            *(actions if actions is not None else [dict(type='tsumo',actor=0,pai=pai(100)),dict(type='dahai',actor=0,pai=pai(100),tsumogiri=True)]),
            *(ending if ending is not None else [dict(type='ryukyoku',deltas=[0]*4)]),dict(type='end_kyoku'),dict(type='end_game')]
def raw(rows):return '\n'.join(json.dumps(e) for e in rows).encode()
def parse(rows):return parse_mjai(raw(rows),rules=RULES)[0]

class MjaiTests(unittest.TestCase):
    def test_codes_and_reject_incomplete(self):
        self.assertEqual([tile_code(c) for c in ['E','P','C','5pr','9s']],['1z','5z','7z','0p','9s'])
        for c in ['?','0p','8z','10m']:
            with self.assertRaises(ValueError):tile_code(c)
        for mutation in ('unknown','three','no_red','truncated','deltas'):
            rows=events()
            if mutation=='unknown':rows[1]['tehais'][0][0]='?'
            elif mutation=='three':rows[1]['tehais'].pop()
            elif mutation=='no_red':rows[0]['aka_flag']=False
            elif mutation=='truncated':rows.pop()
            else:del rows[-3]['deltas']
            with self.subTest(mutation=mutation),self.assertRaises(ValueError):parse(rows)
        with self.assertRaises(ValueError):parse_mjai(raw(events()))

    def test_visibility_replay_and_observation(self):
        hand=parse(events())
        sample=next(hand.samples());req=sample['input']
        self.assertEqual(sample['schema_version'],'discard-sample-v1')
        self.assertNotIn('private',debug_json(req))
        self.assertNotIn('mjai_ended',debug_json(req))
        self.assertTrue(all(t.id.startswith('v') for t in req.position.tiles))
        self.assertTrue(all(t.seat==0 for t in req.position.tiles if t.zone=='hand'))
        self.assertIn(sample['labels']['action'].tile_ids[0],{t.id for t in req.position.tiles})
        self.assertIs(hand.records[1].events[0].data['fact']['code'],UNKNOWN)
        self.assertIs(hand.outcome['settlements'][0]['reason'],UNKNOWN)
        for record in hand.records:replay(record).validate()
        masked=observe_request(req,ObservationConfig(midgame=True,history_start=1))
        self.assertTrue(analyze(masked).recommendations)

    def test_opponent_allocation_and_future_do_not_change_input(self):
        rows=events();other=events()
        other[1]['tehais'][0],other[1]['tehais'][2]=other[1]['tehais'][2],other[1]['tehais'][0]
        a=parse(rows);b=parse(other)
        x=build_analysis_request(a.records[1],source_version='same',through=1)
        y=build_analysis_request(b.records[1],source_version='same',through=1)
        y.position.round_id=x.position.round_id
        self.assertEqual(x,y)

    def test_calls_and_kan(self):
        for kind,own,called,consumed,expected in [('chi',[4,8],0,[4,8],'chi'),('pon',[1,2],0,[1,2],'pon'),('daiminkan',[1,2,3],0,[1,2,3],'open_kan')]:
            actions=[dict(type='tsumo',actor=0,pai=pai(100)),dict(type='dahai',actor=0,pai=pai(called),tsumogiri=False),
                     dict(type=kind,actor=1,target=0,pai=pai(called),consumed=list(map(pai,consumed)))]
            if kind=='daiminkan':actions.append(dict(type='tsumo',actor=1,pai=pai(101)))
            actions.append(dict(type='dahai',actor=1,pai=pai(20),tsumogiri=False))
            hand=parse(events(actions,{0:[0],1:[*own,20]},reserve=(0,1,2,3,4,8,20)))
            self.assertEqual(next(iter(replay(hand.records[1]).groups.values()))['kind'],expected)
        actions=[dict(type='tsumo',actor=0,pai=pai(100)),dict(type='ankan',actor=0,consumed=['1m']*4),
                 dict(type='dora',dora_marker=pai(134)),dict(type='tsumo',actor=0,pai=pai(101)),
                 dict(type='dahai',actor=0,pai=pai(101),tsumogiri=True)]
        hand=parse(events(actions,{0:[0,1,2,3]}))
        self.assertEqual(next(iter(replay(hand.records[0]).groups.values()))['kind'],'closed_kan')
        self.assertTrue(any(e.data['fact'].get('source')=='replacement' for e in hand.records[0].events))

    def test_riichi_and_multiple_ron(self):
        actions=[dict(type='tsumo',actor=0,pai=pai(100)),dict(type='reach',actor=0),
                 dict(type='dahai',actor=0,pai=pai(100),tsumogiri=True),dict(type='reach_accepted',actor=0)]
        h=parse(events(actions))
        sample=next(h.samples())
        self.assertEqual(sample['labels']['action'].kind,'riichi')
        self.assertNotIn('mjai_riichi_declared',debug_json(sample['input']))
        self.assertEqual(replay(h.records[0]).players[0].score,24000)
        ends=[dict(type='hora',actor=1,target=0,deltas=[-1000,1000,0,0]),dict(type='hora',actor=2,target=0,deltas=[-2000,0,2000,0])]
        h=parse(events(ending=ends))
        self.assertEqual(replay(h.records[0]).players[0].score,22000)
        self.assertEqual(len(h.outcome['settlements']),2)

    def test_added_kan_and_robbery(self):
        actions=[dict(type='tsumo',actor=0,pai=pai(100)),dict(type='dahai',actor=0,pai='1m',tsumogiri=False),
                 dict(type='pon',actor=1,target=0,pai='1m',consumed=['1m']*2),
                 dict(type='dahai',actor=1,pai=pai(20),tsumogiri=False)]
        for seat,tile in [(2,102),(3,103),(0,104)]:
            actions.extend([dict(type='tsumo',actor=seat,pai=pai(tile)),dict(type='dahai',actor=seat,pai=pai(tile),tsumogiri=True)])
        actions.extend([dict(type='tsumo',actor=1,pai=pai(105)),dict(type='kakan',actor=1,pai='1m',consumed=['1m']*3)])
        fixed={0:[0],1:[1,2,3,20]}
        robbed=parse(events(actions,fixed,reserve=(0,1,2,3,20),ending=[dict(type='hora',actor=2,target=1,deltas=[0,-1000,1000,0])]))
        self.assertEqual(next(iter(replay(robbed.records[2]).groups.values()))['kind'],'pon')
        actions.extend([dict(type='dora',dora_marker=pai(134)),dict(type='tsumo',actor=1,pai=pai(106)),dict(type='dahai',actor=1,pai=pai(106),tsumogiri=True)])
        hand=parse(events(actions,fixed,reserve=(0,1,2,3,20)))
        self.assertEqual(next(iter(replay(hand.records[1]).groups.values()))['kind'],'added_kan')

    def test_compression_tar_export_and_training(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);payload=raw(events());archive=root/'games.tar'
            with tarfile.open(archive,'w') as tar:
                for name,data in [('../unsafe.mjai.json.zst',zstandard.ZstdCompressor().compress(payload)),('duplicate.json.gz',gzip.compress(payload))]:
                    info=tarfile.TarInfo(name);info.size=len(data);tar.addfile(info,io.BytesIO(data))
            self.assertEqual(len(list(iter_archive(archive))),2)
            report=export_mjai(archive,root/'out',rules=RULES,records=True)
            self.assertEqual((report['games'],report['duplicates'],report['samples']),(1,1,1))
            self.assertFalse((root.parent/'unsafe.mjai.json.zst').exists())
            record=next((root/'out').glob('*/hand-000-seat-0.json'))
            self.assertEqual(replay(load_record(record)),replay(parse(events()).records[0]))
            cache=prepare([root/'out/discard-samples.jsonl'],root/'cache')
            self.assertEqual(sum(cache['rows'].values()),1)
            with self.assertRaises(FileExistsError):export_mjai(archive,root/'out',rules=RULES)
