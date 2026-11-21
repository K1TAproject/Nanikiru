from copy import deepcopy
from dataclasses import asdict, replace
import json
from pathlib import Path
import tempfile
import unittest
import sqlite3

from nanikiru.analysis import analyze
from nanikiru.analysis.fixed_policy import Parameters
from nanikiru.analysis.output import debug_json
from nanikiru.analysis.routes import evaluate_routes
from nanikiru.models import UNKNOWN
from nanikiru.observation import observe_request, ObservationConfig
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.simulation import Simulator
from nanikiru.tiles import counts34
from nanikiru.training.ranking import extract, score, action_key, prepare, open_cache, fit, evaluate, partition, run, parameter_coverage
from test_simulation import wall_for


class TrainingTests(unittest.TestCase):
    def test_opportunity_weighted_lookahead_and_tenpai_no_duplicate_value(self):
        g=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9m']))
        request=build_analysis_request(g.record(0),source_version='x',method='fixed-v2')
        result=analyze(request)
        tenpai=[c for c in result.candidates if c.metrics.get('shanten') and c.metrics['shanten'].value==0]
        self.assertTrue(tenpai)
        for c in result.candidates:
            if 'lookahead_gain' not in c.metrics:continue
            metric=c.metrics['lookahead_gain'];detail=metric.details
            self.assertAlmostEqual(metric.value,detail['improving_fraction']*detail['conditional_quality'])
            self.assertLessEqual(metric.value,detail['improving_fraction'])
        for c in tenpai:
            terms=c.metrics['policy_score'].details['contributions']
            self.assertEqual(terms['route'],0)
            self.assertEqual(terms.get('bonus',0),0)

    def test_coverage_ignores_constant_shifts_and_test_rows(self):
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE samples(identity TEXT,split TEXT,variant TEXT,payload TEXT)')
            rows=[dict(linear={'value':v,'bonus':1},risk=0,late=0,lock=0,routes=[],action=['discard',c])
                  for c,v in [('1m',0),('2m',1)]]
            sample=dict(skip_reason=None,candidates=rows)
            db.execute('INSERT INTO samples VALUES (?,?,?,?)',('a','train','complete',json.dumps(sample)))
            db.execute('INSERT INTO samples VALUES (?,?,?,?)',('b','test','complete','invalid json must not read'))
            report=parameter_coverage(db,asdict(Parameters()))
            self.assertEqual(report['value']['informative_decisions'],1)
            self.assertEqual(report['bonus']['informative_decisions'],0)
            self.assertEqual(report['commitment']['informative_decisions'],0)
        finally:db.close()

    def request(self,seed=17):
        return build_analysis_request(Simulator(seed=seed).record(0),source_version='test',method='fixed-v2')

    def test_routes_distinguish_speed_and_route_specific_ukeire(self):
        p=self.request().position
        hand='1m 2m 3m 4m 5m 6m 1p 2p 3p 4s 5s 7z 7z'.split()
        seen=counts34(hand)
        rows=evaluate_routes(hand,[],p,seen)
        by_name={r['name']:r for r in rows['routes']}
        self.assertEqual(rows['preferred'],'menzen_riichi')
        self.assertEqual(by_name['regular']['ukeire'],8)
        self.assertEqual(set(by_name['regular']['effective_tiles']),{'3s','6s'})
        self.assertEqual(by_name['seven_pairs']['distance'],6)
        self.assertGreater(by_name['seven_pairs']['speed_loss'],0)
        self.assertEqual(by_name['seven_pairs']['potential'],0)
        missing=evaluate_routes(hand,[],p)
        self.assertTrue(all(r['ukeire'] is None for r in missing['routes']))
        self.assertEqual(next(r['effective_tiles'] for r in missing['routes'] if r['name']=='regular'),['3s','6s'])
        # A seven-pairs wait stays a candidate even if the remaining pair tile is dead.
        hand='1m 1m 3m 3m 5p 5p 7p 7p 2s 2s 6s 6s 7z'.split()
        seen=counts34(hand);seen[33]=4
        pair=next(r for r in evaluate_routes(hand,[],p,seen)['routes'] if r['name']=='seven_pairs')
        self.assertEqual(pair['effective_tiles'],['7z']);self.assertEqual(pair['ukeire'],0)

    def test_cached_score_matches_runtime_with_riichi_and_unknown(self):
        g=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9m']))
        original=build_analysis_request(g.record(0),source_version='x',method='fixed-v2')
        label=next(c.action for c in analyze(original).candidates if c.action.kind=='discard')
        for request in (original,observe_request(original,ObservationConfig(public_code_rate=1))):
            if request is not original:label=next(c.action for c in analyze(request).candidates if c.action.kind=='discard')
            before=deepcopy(request);cached=extract(request,label)
            self.assertEqual(request,before)
            for overrides in ({},{'route':0,'value':0,'risk':0},
                              {'route':7,'route_speed':.25,'route_ukeire':3,'late_defense':2,'commitment':4}):
                weights=asdict(Parameters(**overrides))
                result=analyze(replace(request,method_options=overrides))
                for row in cached['candidates']:
                    c=next(c for c in result.candidates if list(action_key(c.action,request.position))==row['action'] and c.legality!='forbidden')
                    self.assertAlmostEqual(score(row,weights),c.metrics['policy_score'].value)

    def source_samples(self):
        result=[]
        for number,split in enumerate(('train','validation','test')):
            source=next(f'test-{n}' for n in range(1000) if partition(f'test-{n}','0')==split)
            request=self.request(number+10)
            action=next(c.action for c in analyze(request).candidates if c.legality=='allowed' and c.action.kind=='discard')
            result.append(dict(schema_version='discard-sample-v1',source_id=source,hand_index=0,
                observer=0,event_count=1,input=request,labels={'action':action,'outcome':{'must_not_be_a_feature':number}}))
        return result

    def test_disk_pipeline_dedup_splits_repeatability_and_no_test_fit(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            root=Path(directory);source=root/'source.jsonl';rows=self.source_samples()
            source.write_text('\n'.join(debug_json(s,indent=None) for s in [*rows,rows[0]]),encoding='utf-8')
            manifest=prepare([source],root/'cache')
            self.assertEqual(manifest['duplicates'],1)
            self.assertEqual(manifest['rows'],{'train':1,'validation':1,'test':1})
            db,_=open_cache(root/'cache')
            try:
                before=list(db.execute('SELECT * FROM samples ORDER BY identity'))
                queries=[];db.set_trace_callback(queries.append)
                a=fit(db,rounds=1,tune=('route','risk'))
                self.assertFalse(any("split='test'" in q for q in queries))
                self.assertEqual(a,fit(db,rounds=1,tune=('route','risk')))
                self.assertEqual(before,list(db.execute('SELECT * FROM samples ORDER BY identity')))
                self.assertEqual(evaluate(db,a[0],'test')['all']['evaluated'],1)
                self.assertNotIn('must_not_be_a_feature',str(before))
            finally:db.close()
            first=run(root/'cache',root/'first',rounds=1,tune=('route','risk'))
            second=run(root/'cache',root/'second',rounds=1,tune=('route','risk'))
            self.assertEqual(first,second)
            self.assertEqual((root/'first/weights.json').read_bytes(),(root/'second/weights.json').read_bytes())
            Parameters(**json.loads((root/'first/weights.json').read_text()))
            with (root/'cache/features.sqlite').open('ab') as stream:stream.write(b'tamper')
            with self.assertRaisesRegex(ValueError,'checksum'):open_cache(root/'cache')

    def test_conflicting_identity_and_split_are_rejected(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            root=Path(directory);rows=self.source_samples();different=deepcopy(rows[0])
            different['input'].state_version='conflicting'
            source=root/'bad.jsonl';source.write_text('\n'.join(debug_json(s,indent=None) for s in (rows[0],different)),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Conflicting'):prepare([source],root/'bad')
            with self.assertRaisesRegex(ValueError,'Incomplete'):open_cache(root/'bad')
            rows[0]['split']='test'
            source.write_text(debug_json(rows[0],indent=None),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'split disagrees'):prepare([source],root/'split')

    def test_fit_reduces_training_loss_and_requires_validation(self):
        db=sqlite3.connect(':memory:')
        try:
            db.execute('CREATE TABLE samples(identity TEXT,split TEXT,variant TEXT,payload TEXT)')
            candidates=[]
            for code,value in (('1m',0),('2m',1)):
                candidates.append(dict(action=['discard',code],legality='allowed',linear={'value':value},
                    risk=0,late=0,lock=0,routes=[],shanten=2,ukeire=10))
            sample=dict(skip_reason=None,label=['discard','2m'],conditional=False,candidates=candidates)
            db.execute('INSERT INTO samples VALUES (?,?,?,?)',('a','train','complete',json.dumps(sample)))
            with self.assertRaisesRegex(ValueError,'both train and validation'):fit(db,tune=('value',))
            db.execute('INSERT INTO samples VALUES (?,?,?,?)',('b','validation','complete',json.dumps(sample)))
            before=evaluate(db,{},'train')['all']['loss']
            weights,_=fit(db,rounds=1,tune=('value',))
            self.assertGreater(weights['value'],Parameters().value)
            self.assertLess(evaluate(db,weights,'train')['all']['loss'],before)
        finally:db.close()
