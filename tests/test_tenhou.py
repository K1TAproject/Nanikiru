"""Synthetic format fixtures: these are not real player logs or strength tests."""

import gzip
import json
from pathlib import Path
import tempfile
import unittest
import xml.etree.ElementTree as ET

from nanikiru.adapters.tenhou import code, decode_meld, parse_xml, load_tenhou
from nanikiru.adapters.__main__ import export
from nanikiru.analysis import analyze
from nanikiru.analysis.output import debug_json
from nanikiru.models import UNKNOWN
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.processing.records import replay, load_record, save_record


def fixture(events='<T100/><D100/>', hands=None, reserve=(), end=None):
    hands = {} if hands is None else hands
    reserved = {100,101,102,103,104,105,106,107,135,134,*reserve}
    used = reserved | {t for h in hands.values() for t in h}
    available = [t for t in range(136) if t not in used]
    complete = []
    for s in range(4):
        fixed = list(hands.get(s,()))
        while len(fixed)<13: fixed.append(available.pop(0))
        complete.append(fixed)
    attrs = ' '.join(f'hai{s}="{",".join(map(str,h))}"' for s,h in enumerate(complete))
    if end is None:
        scores = [250]*4
        for event in ET.fromstring(f'<events>{events}</events>'):
            if event.tag=='REACH' and event.get('step')=='2':scores[int(event.get('who'))]-=10
        sc=','.join(str(n) for score in scores for n in (score,0))
        end=f'<RYUUKYOKU type="yao9" sc="{sc}"/>'
    return f'<mjloggm><GO type="1"/><INIT seed="0,0,0,2,3,135" ten="250,250,250,250" oya="0" {attrs}/>{events}{end}</mjloggm>'


class TenhouTests(unittest.TestCase):
    def test_tile_and_all_meld_encodings(self):
        self.assertEqual([code(t) for t in (16,52,88)],['0m','0p','0s'])
        vectors=((7,1,'chi',[0,4,8]), (107,1,'pon',[0,1,2]),
                 (115,1,'added_kan',[0,1,2,3]), (3,1,'open_kan',[0,1,2,3]),
                 (0,0,'closed_kan',[0,1,2,3]))
        for packed,seat,kind,tiles in vectors:
            result=decode_meld(packed,seat)
            self.assertEqual((result['kind'],result['tiles']),(kind,tiles))
        for bad in (-1,136,True):
            with self.assertRaises(ValueError):code(bad)

    def test_prefix_samples_visibility_and_future_labels(self):
        hand=parse_xml(fixture('<T100/><D100/><U101/><E101/>'))[0]
        samples=list(hand.samples())
        self.assertEqual(len(samples),2)
        for sample in samples:
            req=sample['input']; seat=req.position.observer_seat
            self.assertTrue(all(t.seat==seat for t in req.position.tiles if t.zone=='hand'))
            self.assertNotIn('tenhou_ended',debug_json(req))
            self.assertNotIn('SHUFFLE',debug_json(req))
            self.assertEqual(sample['labels']['action'].actor,seat)
            self.assertTrue(analyze(req).recommendations)
        self.assertIs(hand.records[1].events[0].data['fact']['code'],UNKNOWN)
        self.assertIsNone(hand.records[1].events[0].data['fact']['tile'])
        req=build_analysis_request(hand.records[0],source_version='x',through=1)
        alternate=parse_xml(fixture('<T100/><D100/><U102/><E102/>'))[0]
        req2=build_analysis_request(alternate.records[0],source_version='x',through=1)
        # Identity is metadata, not a policy feature.
        req2.position.round_id=req.position.round_id
        self.assertEqual(req,req2)

    def test_chi_pon_and_open_kan_replay(self):
        for packed,own,kind in ((7,[4,8],'chi'),(107,[1,2],'pon'),(3,[1,2,3],'open_kan')):
            events=f'<T100/><D0/><N who="1" m="{packed}"/>'
            events += '<U101/>' if kind=='open_kan' else ''
            events += '<E20/>'
            hand=parse_xml(fixture(events,{0:[0],1:[*own,20]},reserve=(0,1,2,3,4,8,20)))[0]
            p=replay(hand.records[1])
            self.assertEqual(next(iter(p.groups.values()))['kind'],kind)
            self.assertFalse(any(t.id=='t0' and t.zone=='river' for t in p.tiles))
            request=list(hand.samples())[-1]['input']
            result=analyze(request)
            self.assertTrue(result.recommendations)

    def test_closed_and_added_kan_dora_and_replacement(self):
        scenarios=[('<T100/><N who="0" m="0"/><DORA hai="134"/><T101/><D101/>',{0:[0,1,2,3]},'closed_kan'),
                   ('<T100/><D0/><N who="1" m="107"/><E20/><V102/><F102/><W103/><G103/><T104/><D104/><U105/><N who="1" m="115"/><DORA hai="134"/><U106/><E106/>',
                    {0:[0],1:[1,2,3,20]},'added_kan')]
        for events,hands,kind in scenarios:
            hand=parse_xml(fixture(events,hands,reserve=(0,1,2,3,20)))[0]
            p=replay(hand.records[0])
            self.assertEqual(next(iter(p.groups.values()))['kind'],kind)
            self.assertEqual(len([t for t in p.tiles if t.zone=='dora']),2)
            self.assertTrue(any(e.kind=='tenhou_draw' and e.data['fact']['source']=='replacement' for e in hand.records[0].events))
            for sample in hand.samples():sample['input'].position.validate()

    def test_riichi_acceptance_locks_subsequent_discard(self):
        events='<T100/><REACH who="0" step="1"/><D100/><REACH who="0" step="2"/><U101/><E101/><V102/><F102/><W103/><G103/><T104/><D104/>'
        hand=parse_xml(fixture(events))[0]
        self.assertNotIn('tenhou_riichi_declared',debug_json(list(hand.samples())[0]['input']))
        req=list(hand.samples())[-1]['input']
        result=analyze(req)
        self.assertEqual([c.action.tile_ids for c in result.candidates if c.legality=='allowed'],[('t104',)])
        self.assertEqual(req.position.riichi_sticks,1)
        self.assertEqual(req.position.players[0].score,24000)

    def test_multiple_wins_and_robbed_kan(self):
        end='<AGARI who="1" fromWho="0" machi="0" sc="250,-10,250,10,250,0,250,0"/><AGARI who="2" fromWho="0" machi="0" sc="240,-20,260,0,250,20,250,0"/>'
        hand=parse_xml(fixture('<T100/><N who="0" m="0"/>',{0:[0,1,2,3]},end=end))[0]
        self.assertEqual(len(hand.outcome['settlements']),2)
        self.assertFalse(replay(hand.records[1]).groups)
        self.assertEqual(replay(hand.records[1]).players[0].score,22000)

    def test_gzip_export_roundtrip_and_overwrite_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as temp:
            path=Path(temp)/'synthetic.mjlog';path.write_bytes(gzip.compress(fixture().encode()))
            hand=load_tenhou(path)[0]
            out=Path(temp)/'converted'
            self.assertEqual(export(path,out),(1,1))
            self.assertEqual(replay(load_record(out/'hand-000-seat-0.json')),replay(hand.records[0]))
            sample=json.loads((out/'discard-samples.jsonl').read_text(encoding='utf-8'))
            self.assertIn('labels',sample)
            self.assertNotIn('outcome',sample['input'])
            with self.assertRaises(FileExistsError):export(path,out)

    def test_unsupported_and_malformed_fail_explicitly(self):
        xml=fixture()
        bad=[xml.replace('type="1"','type="17"',1),xml.replace('type="1"','type="3"',1),
             fixture(end=''),fixture('<T100/><D101/>'),fixture('<REINIT/>'),
             xml.replace('<T100/>','<T0/>'), '<!DOCTYPE mjloggm>'+xml]
        for value in bad:
            with self.subTest(value=value[-80:]), self.assertRaises(ValueError):parse_xml(value)

