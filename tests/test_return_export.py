from copy import deepcopy
from hashlib import sha256
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest

from nanikiru.adapters.mjai import parse_mjai
from nanikiru.analysis.output import debug_json
from nanikiru.rules.config import tenhou_hanchan
from nanikiru.training.returns import final_labels, value_samples, export_returns, read_samples
from test_mjai import events, raw


def complete_game(delta=None):
    # Adapter validates source facts/payments, not the actual yaku. Bankruptcy
    # provides a complete short match fixture without constructing eight hands.
    return raw(events(ending=[dict(type='hora',actor=1,target=0,
                                  deltas=delta or [-26000,26000,0,0])]))


class ReturnExportTests(unittest.TestCase):
    def parse(self,data):return parse_mjai(data,rules=tenhou_hanchan())

    def test_final_rank_points_ties_and_no_mutation(self):
        hands=self.parse(complete_game());before=deepcopy(hands)
        labels=final_labels(hands)
        self.assertEqual(labels['ranking'],[1,2,3,0])
        self.assertEqual([p['final_rank'] for p in labels['players']],[4,1,2,3])
        self.assertEqual(sum(p['final_points'] for p in labels['players']),100000)
        self.assertAlmostEqual(sum(p['result_points'] for p in labels['players']),0)
        self.assertEqual(hands,before)

    def test_refuse_truncated_or_ambiguous_match_end(self):
        with self.assertRaisesRegex(ValueError,'termination'):final_labels(self.parse(raw(events())))
        with self.assertRaises(ValueError):self.parse(complete_game().rsplit(b'\n',1)[0])

    def test_future_return_changes_labels_not_earlier_inputs(self):
        first=self.parse(complete_game());second=self.parse(complete_game([-27000,27000,0,0]))
        a=list(value_samples(first,split='train',max_samples=100))
        b=list(value_samples(second,split='train',max_samples=100))
        self.assertEqual([(s['hand_index'],s['observer'],s['event_count']) for s in a],
                         [(s['hand_index'],s['observer'],s['event_count']) for s in b])
        for x,y in zip(a,b):
            self.assertEqual(x['input'],y['input'])
            self.assertEqual(x['features'],y['features'])
            self.assertNotIn('settlements',debug_json(x['input']))
            self.assertNotIn('scores_after',debug_json(x['input']))
        self.assertNotEqual(a[0]['labels'],b[0]['labels'])
        self.assertAlmostEqual(sum(x['sample_weight'] for x in a),1)

    def test_all_observers_same_partition_and_response_sites(self):
        samples=list(value_samples(self.parse(complete_game()),split='validation',max_samples=100))
        self.assertEqual({s['split'] for s in samples},{'validation'})
        self.assertEqual({s['observer'] for s in samples},{0,1,2,3})
        self.assertIn('response_observation',{s['sample_kind'] for s in samples})
        self.assertTrue(all('action' not in s['labels'] for s in samples))

    def test_export_manifest_and_existing_plan_required(self):
        data=complete_game();identity=sha256(data).hexdigest()
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);archive=root/'source.tar';plan=root/'plan.json'
            with tarfile.open(archive,'w') as tar:
                item=tarfile.TarInfo('2026011021gm-00a9-0000-1dddb8c0.mjai.json');item.size=len(data)
                tar.addfile(item,io.BytesIO(data))
            plan.write_text(json.dumps({'sources':[{'source_id':identity,'split':'test'}]}))
            result=export_returns(archive,root/'out',plan=plan,max_samples=6)
            self.assertEqual(result['status'],'complete')
            self.assertFalse(result['training_performed'])
            self.assertEqual(result['games'],1)
            rows=[json.loads(s) for s in (root/'out'/'samples.jsonl').read_text().splitlines()]
            self.assertTrue(all(s['split']=='test' for s in rows))
            self.assertEqual(rows,list(read_samples(root/'out',split='test')))
            self.assertEqual([],list(read_samples(root/'out',split='train')))
            with (root/'out'/'samples.jsonl').open('a') as f:f.write('\n')
            with self.assertRaisesRegex(ValueError,'checksum'):list(read_samples(root/'out',split='test'))
            with self.assertRaises(FileExistsError):export_returns(archive,root/'out',plan=plan)
            plan.write_text('{"sources":[]}')
            with self.assertRaisesRegex(ValueError,'split plan'):export_returns(archive,root/'bad',plan=plan)
            self.assertEqual(json.loads((root/'bad'/'manifest.json').read_text())['status'],'failed')

    def test_tied_score_uses_initial_dealer_order(self):
        rows=events(ending=[dict(type='hora',actor=2,target=1,deltas=[13000,-26000,13000,0])])
        rows[1]['oya']=1
        for e in rows:
            if e['type'] in ('tsumo','dahai'):e['actor']=1
        labels=final_labels(self.parse(raw(rows)))
        self.assertEqual(labels['ranking'],[2,0,3,1])


if __name__=='__main__':unittest.main()
