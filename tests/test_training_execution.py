from pathlib import Path
import tempfile
import unittest

import test_training
from examples.train_v2_round1 import merge_caches
from nanikiru.analysis.output import debug_json
from nanikiru.training.ranking import prepare, partition, open_cache


class ExperimentExecutionTests(unittest.TestCase):
    def test_merge_keeps_source_splits_and_rejects_reused_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);rows=test_training.TrainingTests().source_samples();paths=[]
            for i,row in enumerate(rows):
                source=root/f'{i}.jsonl';source.write_text(debug_json(row,indent=None),encoding='utf-8')
                path=root/f'cache-{i}';prepare([source],path);paths.append(path)
            expected={r['source_id']:partition(r['source_id'],'0') for r in rows}
            manifest=merge_caches(paths,root/'merged',expected)
            self.assertEqual(manifest['rows'],{'train':1,'validation':1,'test':1})
            db,_=open_cache(root/'merged')
            try:self.assertEqual(dict(db.execute('SELECT source,split FROM samples')),expected)
            finally:db.close()
            with self.assertRaisesRegex(ValueError,'duplicate'):
                merge_caches([paths[0],paths[0]],root/'duplicate',expected)
            wrong=dict(expected);wrong[rows[0]['source_id']]='test'
            with self.assertRaisesRegex(ValueError,'split mismatch'):
                merge_caches(paths,root/'wrong',wrong)
