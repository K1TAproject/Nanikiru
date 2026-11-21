import json
from pathlib import Path
import tempfile
import unittest
from concurrent.futures import Future
from unittest.mock import patch

from test_mjai import events, raw, pai, RULES
from nanikiru.adapters.mjai import parse_mjai
from nanikiru.analysis.output import debug_json
from nanikiru.processing.records import replay
from nanikiru.training.ranking import fit
from examples.train_v2_round1 import collect_jobs
from training_ui.app import snapshot


class TrainingProgressTests(unittest.TestCase):
    def test_fourth_riichi_normalization_without_future_leak(self):
        actions=[]
        for seat in range(4):
            actions.extend([dict(type='tsumo',actor=seat,pai=pai(100+seat)),dict(type='reach',actor=seat),
                            dict(type='dahai',actor=seat,pai=pai(100+seat),tsumogiri=True)])
            if seat<3:actions.append(dict(type='reach_accepted',actor=seat))
        rows=events(actions)
        first=parse_mjai(raw(rows),rules=RULES)[0]
        self.assertEqual([p.score for p in replay(first.records[0]).players],[24000]*4)
        self.assertEqual(first.outcome['settlements'][0]['reason'],'four_riichi')
        self.assertEqual(first.outcome['settlements'][0]['normalization'],'missing_fourth_reach_accepted')
        self.assertNotIn('four_riichi_terminal',debug_json(list(first.samples())[-1]['input']))
        explicit=events([*actions,dict(type='reach_accepted',actor=3)])
        second=parse_mjai(raw(explicit),rules=RULES)[0]
        self.assertEqual(replay(first.records[0]).riichi_sticks,replay(second.records[0]).riichi_sticks)
        self.assertNotIn('normalization',second.outcome['settlements'][0])
        # Next-round evidence still independently checks scores; no blanket repair.
        next_start=dict(rows[1],honba=1,kyotaku=4,scores=[24000]*4)
        combined=rows[:-1]+[next_start,dict(type='ryukyoku',deltas=[0]*4),dict(type='end_kyoku'),dict(type='end_game')]
        self.assertEqual(len(parse_mjai(raw(combined),rules=RULES)),2)
        next_start['scores']=[24000,24000,24000,23000]
        with self.assertRaisesRegex(ValueError,'Scores inconsistent'):parse_mjai(raw(combined),rules=RULES)

    def test_failures_are_counted_while_other_jobs_finish(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);good=Future();bad=Future()
            good.set_result((1,{'train':30}));bad.set_exception(ValueError('bad source'))
            completed,failed=collect_jobs(root,{bad:0,good:1})
            self.assertEqual(completed,1);self.assertEqual(failed,[{'chunk':0,'error':'bad source'}])
            state=json.loads((root/'status.json').read_text())
            self.assertEqual(state['stage'],'preparing_with_errors')
            self.assertEqual(state['completed_chunks'],1)

    def test_monitor_uses_completed_manifests_not_stale_counter(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            (root/'experiment.json').write_text(json.dumps({'chunks':[{},{}]}))
            (root/'status.json').write_text(json.dumps({'stage':'failed','completed_chunks':0}))
            (root/'cache-000').mkdir();(root/'import-001').mkdir()
            (root/'cache-000/manifest.json').write_text(json.dumps({'status':'complete','rows':{'train':30}}))
            (root/'import-001/import-report.json').write_text(json.dumps({'status':'incomplete','games':2}))
            (root/'fit-progress.jsonl').write_text('{"event":"baseline"}\n{"event":')
            data=snapshot(root)
            self.assertEqual((data['completed'],data['failed'],data['rows']),(1,1,30))
            self.assertEqual(data['progress'],[{'event':'baseline'}])

    def test_fit_progress_reports_losses_without_test_selection(self):
        seen=[]
        def evaluate(db,weights,split):
            self.assertIn(split,('train','validation'))
            return {'all':{'evaluated':3,'loss':(weights['route']-1)**2+1}}
        with patch('nanikiru.training.ranking.evaluate',side_effect=evaluate):
            expected=fit(None,rounds=1,tune=('route',))
            actual=fit(None,rounds=1,tune=('route',),progress=seen.append)
        self.assertEqual(expected,actual)
        self.assertEqual(seen[0]['event'],'baseline')
        self.assertEqual(seen[-1]['event'],'round_complete')
        self.assertIn('validation_loss',seen[-1])
        self.assertTrue(any(e['event']=='trial_start' for e in seen))
        self.assertTrue(any(e['event']=='trial_complete' for e in seen))
