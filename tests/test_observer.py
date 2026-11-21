from copy import deepcopy
from dataclasses import replace
import json
from pathlib import Path
import tempfile
import unittest

from nanikiru.analysis import analyze
from nanikiru.analysis.output import debug_json
from nanikiru.contracts import request_from_dict
from nanikiru.models import UNKNOWN
from nanikiru.observation import ObservationConfig, observe_request
from nanikiru.observation.dataset import variants, export_dataset
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.simulation import Simulator
from test_simulation import choose, passes
from test_tenhou import fixture
from nanikiru.adapters.tenhou import parse_xml


def position():
    game=Simulator(seed=17)
    for _ in range(4):
        game.submit(choose(game,game.actor,'discard'))
        passes(game)
    return build_analysis_request(game.record(game.actor),source_version='test',method='fixed-v2')


class ObserverTests(unittest.TestCase):
    def test_reproducible_independent_and_opaque(self):
        request=position(); before=deepcopy(request)
        a=observe_request(request,ObservationConfig(public_code_rate=.5),seed=42)
        b=observe_request(request,ObservationConfig(public_code_rate=.5),seed=42)
        self.assertEqual(a,b)
        self.assertEqual(request,before)
        self.assertNotIn('changes',debug_json(a.history))
        self.assertTrue(all(t.id.startswith('o') for t in a.position.tiles))
        self.assertNotEqual(a.state_version,request.state_version)
        explicit_start=observe_request(request,ObservationConfig(midgame=True,history_start=0))
        self.assertEqual(explicit_start.coverage,request.coverage)

    def test_discard_kind_unknown_does_not_change_shape_or_ukeire(self):
        request=position()
        a=analyze(observe_request(request))
        brequest=observe_request(request,ObservationConfig(discard_kind_rate=1))
        b=analyze(brequest)
        for x,y in zip(a.candidates,b.candidates):
            self.assertEqual(x.metrics,y.metrics)
        self.assertEqual(a.recommendations,b.recommendations)
        self.assertTrue(all(e.data['fact']['tsumogiri'] is UNKNOWN for e in brequest.history if e.kind=='observed_discard'))
        draws={}
        for event in brequest.history:
            if event.kind=='observed_draw': draws[event.actor]=event.data['fact']
            elif event.kind=='observed_discard' and event.actor in draws:
                self.assertIs(draws[event.actor]['tile'],UNKNOWN)

    def test_entry_riichi_json_and_scoring_context(self):
        from test_simulation import wall_for
        from nanikiru.models import Event
        game=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},draws=['9m']))
        original=build_analysis_request(game.record(0),source_version='v',method='fixed-v2')
        original.history+=(Event(2,'sim_riichi_accepted',0,data={'fact':{}}),)
        request=observe_request(original,ObservationConfig(history_start=2))
        result=analyze(request)
        chosen=next(c for c in result.candidates if c.action.tile_ids==(request.position.draw_context['tile_id'],))
        self.assertGreater(chosen.metrics['conditional_value'].value,0)
        self.assertTrue(result.recommendations)
        no_qualification=observe_request(original,ObservationConfig(history_start=2,hide_riichi=True))
        result=analyze(no_qualification)
        self.assertFalse(result.recommendations)
        self.assertTrue(all(c.metrics['conditional_value'].value is None for c in result.candidates if c.action.kind=='discard'))
        self.assertTrue(all(c.legality=='unknown' for c in result.candidates if c.action.kind=='riichi'))

    def test_hidden_public_codes_not_restored_by_deltas_or_facts(self):
        request=observe_request(position(),ObservationConfig(public_code_rate=1))
        for t in request.position.tiles:
            if t.zone!='hand':self.assertIs(t.code,UNKNOWN)
        for event in request.history:
            if event.kind=='observed_discard':self.assertIs(event.data['fact']['code'],UNKNOWN)
        for method in ('minimal-shape-v1','fixed-v2'):
            result=analyze(replace(request,method=method))
            self.assertTrue(result.situation['discard_ranking'].value)
            self.assertTrue(all(c.metrics['ukeire'].value is None for c in result.candidates))
            self.assertTrue(all(c.metrics['shanten'].value is not None for c in result.candidates))

    def test_midgame_keeps_current_public_tiles_and_drops_old_history(self):
        original=position()
        request=observe_request(original,ObservationConfig(history_start=len(original.history)))
        self.assertFalse(request.history)
        self.assertFalse(request.coverage['from_start'])
        self.assertEqual(sorted(t.code for t in original.position.tiles),sorted(t.code for t in request.position.tiles))
        self.assertFalse(analyze(request).recommendations)  # no latest action evidence
        self.assertTrue(analyze(request).situation['discard_ranking'].value)

    def test_unknown_hand_disables_unique_ranking(self):
        request=observe_request(position(),ObservationConfig(hand_code_rate=1))
        for method in ('minimal-shape-v1','fixed-v2'):
            result=analyze(replace(request,method=method))
            self.assertEqual(result.situation['discard_ranking'].status,'unavailable')
            self.assertFalse(result.recommendations)

    def test_river_region_and_order_coverage(self):
        request=observe_request(position(),ObservationConfig(hidden_rivers=(1,),hide_order=True))
        self.assertFalse(request.position.completeness['public_tiles'])
        self.assertFalse(request.history)
        self.assertTrue(all(t.code is UNKNOWN for t in request.position.tiles if t.zone=='river' and t.seat==1))

    def test_entry_riichi_keeps_value_without_inventing_event_time(self):
        xml=fixture('<T100/><REACH who="0" step="1"/><D100/><REACH who="0" step="2"/><U101/><E101/><V102/><F102/><W103/><G103/><T104/><D104/>')
        sample=list(parse_xml(xml)[0].samples())[-1]
        request=observe_request(sample['input'],ObservationConfig(history_start=len(sample['input'].history)))
        self.assertIs(request.position.initial_context['riichi'][0],True)
        self.assertFalse(request.history)
        masked=observe_request(sample['input'],ObservationConfig(midgame=True,hide_riichi=True))
        self.assertIs(masked.position.initial_context['riichi'][0],UNKNOWN)
        self.assertFalse(any(e.kind.endswith('riichi_accepted') for e in masked.history))

    def test_request_json_roundtrip_and_contract_rejections(self):
        request=observe_request(position(),ObservationConfig(midgame=True,public_code_rate=.5))
        self.assertEqual(request,request_from_dict(json.loads(debug_json(request))))
        request.position.initial_context={'secret':'untrusted'}
        with self.assertRaises(ValueError):analyze(request)
        for config in ({'public_code_rate':-1},{'hand_code_rate':True},{'hidden_rivers':(4,)},{'midgame':1}):
            with self.assertRaises(ValueError):ObservationConfig(**config)
        with self.assertRaises(ValueError):observe_request(position(),ObservationConfig(history_start=99999))

    def test_variants_keep_labels_separate_and_remap_action(self):
        sample=list(parse_xml(fixture())[0].samples())[0]
        before=deepcopy(sample)
        rows=list(variants(sample,seed=77))
        self.assertEqual(sample,before)
        self.assertEqual(len({r['split'] for r in rows}),1)
        for row in rows:
            self.assertNotIn('outcome',debug_json(row['input']))
            self.assertIn(row['labels']['action']['tile_ids'][0],{t.id for t in row['input'].position.tiles})
        sample['observer']=1
        with self.assertRaises(ValueError):list(variants(sample))

    def test_streaming_shards_and_audit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as tmp:
            source=Path(tmp)/'source.jsonl'
            samples=list(parse_xml(fixture('<T100/><D100/><U101/><E101/>'))[0].samples())
            source.write_text('\n'.join(json.dumps(json.loads(debug_json(s))) for s in samples),encoding='utf-8')
            out=Path(tmp)/'output'
            report=export_dataset([source],out,shard_size=3,audit_limit=1)
            self.assertEqual(report['status'],'complete')
            self.assertEqual(report['input_rows'],2)
            self.assertEqual(sum(report['output_rows'].values()),10)
            shards=list(out.glob('train-*.jsonl'))+list(out.glob('validation-*.jsonl'))+list(out.glob('test-*.jsonl'))
            self.assertEqual(len(shards),4)
            for p in shards:self.assertLessEqual(len(p.read_text(encoding='utf-8').splitlines()),3)
            self.assertEqual(len((out/'audit.jsonl').read_text(encoding='utf-8').splitlines()),5)
            with self.assertRaises(FileExistsError):export_dataset([source],out)

    def test_failed_batch_is_marked_incomplete(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as tmp:
            source=Path(tmp)/'bad.jsonl';source.write_text('{}',encoding='utf-8')
            out=Path(tmp)/'out'
            with self.assertRaisesRegex(ValueError,'bad.jsonl:1'):export_dataset([source],out)
            self.assertEqual(json.loads((out/'manifest.json').read_text())['status'],'incomplete')

