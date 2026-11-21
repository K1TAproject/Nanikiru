"""Reject inconsistent source facts without turning the adapter into a scorer."""

import json
import gzip
import io
import tarfile
from pathlib import Path
import tempfile
import unittest

from test_mjai import events, pai, parse, raw, RULES
from test_tenhou import fixture
from nanikiru.adapters.mjai import export_mjai, parse_mjai, tile_code, iter_archive
from nanikiru.adapters.tenhou import parse_xml


class AdapterBoundaryTests(unittest.TestCase):
    def test_mjai_types(self):
        for value in ([],{},None,True,42):
            with self.subTest(value=value),self.assertRaises(ValueError):tile_code(value)
        for field,value in [('honba',-1),('kyotaku',True),('tehais',['x'*13]*4)]:
            rows=events();rows[1][field]=value
            with self.subTest(field=field),self.assertRaises(ValueError):parse(rows)
        with self.assertRaises(ValueError):parse_mjai(b'[]',rules=RULES)

    def test_missing_riichi_acceptance_before_draw(self):
        actions=[dict(type='tsumo',actor=0,pai=pai(100)),dict(type='reach',actor=0),
                 dict(type='dahai',actor=0,pai=pai(100),tsumogiri=True),
                 dict(type='tsumo',actor=1,pai=pai(101))]
        with self.assertRaisesRegex(ValueError,'Missing riichi acceptance'):parse(events(actions))
        with self.assertRaisesRegex(ValueError,'Missing riichi acceptance'):
            parse_xml(fixture('<T100/><REACH who="0" step="1"/><D100/><U101/>'))

    def test_ron_on_declaration_does_not_pay_deposit(self):
        actions=[dict(type='tsumo',actor=0,pai=pai(100)),dict(type='reach',actor=0),
                 dict(type='dahai',actor=0,pai=pai(100),tsumogiri=True)]
        hand=parse(events(actions,ending=[dict(type='hora',actor=1,target=0,deltas=[-1000,1000,0,0])]))
        self.assertEqual(hand.outcome['settlements'][0]['scores_before'],[25000]*4)

    def test_unearned_dora_and_events_after_end(self):
        rows=events();rows.insert(-3,dict(type='dora',dora_marker=pai(134)))
        with self.assertRaisesRegex(ValueError,'Dora reveal'):parse(rows)
        with self.assertRaisesRegex(ValueError,'Dora reveal'):parse_xml(fixture('<T100/><DORA hai="134"/><D100/>'))
        xml=fixture().replace('</mjloggm>','<DORA hai="134"/></mjloggm>')
        with self.assertRaisesRegex(ValueError,'after terminal'):parse_xml(xml)

    def test_delayed_kan_dora_remains_accepted(self):
        hand=parse_xml(fixture('<T100/><N who="0" m="0"/><T101/><D101/><DORA hai="134"/>',
                               hands={0:[0,1,2,3]}))[0]
        self.assertTrue(any(e.kind=='tenhou_dora' for e in hand.records[0].events))

    def test_double_riichi_fact_and_duplicate_acceptance(self):
        actions='<T100/><REACH who="0" step="1"/><D100/><REACH who="0" step="2"/>'
        hand=parse_xml(fixture(actions))[0]
        accepted=next(e for e in hand.records[0].events if e.kind=='tenhou_riichi_accepted')
        self.assertTrue(accepted.data['fact']['double'])
        with self.assertRaisesRegex(ValueError,'Invalid REACH sequence'):
            parse_xml(fixture(actions+'<REACH who="0" step="2"/>'))

    def test_kan_after_call_without_draw_is_rejected(self):
        actions='<T100/><D0/><N who="1" m="107"/><N who="1" m="115"/>'
        with self.assertRaisesRegex(ValueError,'Kan requires own draw'):
            parse_xml(fixture(actions,hands={0:[0],1:[1,2,3]},reserve=(0,1,2,3)))

    def test_terminal_conservation(self):
        rows=events();rows[-3]['deltas']=[1000,0,0,0]
        with self.assertRaisesRegex(ValueError,'not conserved'):parse(rows)
        with self.assertRaisesRegex(ValueError,'not conserved'):
            parse_xml(fixture(end='<RYUUKYOKU sc="250,10,250,0,250,0,250,0"/>'))
        with self.assertRaisesRegex(ValueError,'baseline'):
            parse_xml(fixture(end='<RYUUKYOKU sc="240,0,260,0,250,0,250,0"/>'))

    def test_cross_round_deposits(self):
        rows=events();following=events()[1:-1];following[0]['kyotaku']=1
        with self.assertRaisesRegex(ValueError,'Deposits inconsistent'):parse(rows[:-1]+following+[rows[-1]])

    def test_multiple_ron_conflicts(self):
        first=dict(type='hora',actor=1,target=0,deltas=[-1000,1000,0,0])
        for actor,target in ((1,0),(2,3),(2,2)):
            with self.subTest(actor=actor,target=target),self.assertRaisesRegex(ValueError,'multiple ron'):
                parse(events(ending=[first,dict(type='hora',actor=actor,target=target,deltas=[0]*4)]))
        with self.assertRaisesRegex(ValueError,'Ron differs'):
            parse_xml(fixture(end='<AGARI who="1" fromWho="0" machi="101" sc="250,-10,250,10,250,0,250,0"/>'))

    def test_xml_four_riichi_omitted_acceptance(self):
        actions=''
        for seat,(draw,discard) in enumerate(zip('TUVW','DEFG')):
            actions+=f'<{draw}{100+seat}/><REACH who="{seat}" step="1"/><{discard}{100+seat}/>'
            if seat<3:actions+=f'<REACH who="{seat}" step="2"/>'
        hand=parse_xml(fixture(actions,end='<RYUUKYOKU type="reach4" sc="240,0,240,0,240,0,240,0"/>'))[0]
        result=hand.outcome['settlements'][0]
        self.assertEqual(result['normalization'],'missing_fourth_reach_accepted')
        self.assertEqual(result['scores_after'],[24000]*4)

    def test_failed_export_has_source_and_error(self):
        with tempfile.TemporaryDirectory() as directory:
            source=Path(directory)/'broken.jsonl';source.write_bytes(raw(events()[:-1]))
            target=Path(directory)/'output'
            with self.assertRaises(ValueError):export_mjai(source,target,rules=RULES)
            report=json.loads((target/'import-report.json').read_text(encoding='utf-8'))
            self.assertEqual(report['status'],'incomplete')
            self.assertEqual(report['error']['member'],source.name)
            self.assertIn('ValueError',report['error']['message'])

    def test_compressed_jsonl_members_and_corruption(self):
        with tempfile.TemporaryDirectory() as directory:
            archive=Path(directory)/'games.tar'
            with tarfile.open(archive,'w') as output:
                for name,payload in [('valid.jsonl.gz',gzip.compress(raw(events()))),('broken.mjson.gz',b'\x1f\x8b')]:
                    info=tarfile.TarInfo(name);info.size=len(payload)
                    output.addfile(info,io.BytesIO(payload))
            members=iter_archive(archive)
            self.assertEqual(next(members)[1],raw(events()))
            with self.assertRaisesRegex(ValueError,'broken.mjson.gz'):next(members)
