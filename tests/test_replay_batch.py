from pathlib import Path
import json
import tempfile
import unittest
from unittest.mock import patch

from nanikiru.adapters.batch import inspect_log, import_batch
from nanikiru.contracts import request_from_dict
from nanikiru.training.ranking import prepare, open_cache
from test_tenhou import fixture


def high_rank_fixture():
    return fixture(end='<RYUUKYOKU type="yao9" sc="250,0,250,0,250,0,250,0" owari="250,0,250,0,250,0,250,0"/>').replace(
        '<GO type="1"/>','<GO type="169" lobby="0"/><UN dan="16,17,18,16"/>').encode()


class ReplayBatchTests(unittest.TestCase):
    def test_quality_filters(self):
        raw=high_rank_fixture()
        self.assertEqual(inspect_log(raw)[1]['ranks'],['7dan','8dan','9dan','7dan'])
        for old,new in ((b'169',b'9'),(b'16,17,18,16',b'15,17,18,16'),
                        (b'lobby="0"',b'lobby="7994"'),(b'owari=',b'other=')):
            with self.assertRaises(ValueError):inspect_log(raw.replace(old,new))

    def test_dedup_rejections_and_visible_export(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            root=Path(directory)
            for name in ('a.xml','b.xml'):(root/name).write_bytes(high_rank_fixture())
            (root/'c.xml').write_text(fixture(),encoding='utf-8')
            report=import_batch(list(root.glob('*.xml')),root/'out')
            self.assertEqual([s['status'] for s in report['sources']],['imported','duplicate','rejected'])
            self.assertEqual((report['hands'],report['samples'],report['audit_samples']),(1,1,1))
            rows=[json.loads(s) for s in (root/'out/discard-samples.jsonl').read_text().splitlines()]
            request=request_from_dict(rows[0]['input'])
            self.assertTrue(all(t.seat==0 for t in request.position.tiles if t.zone=='hand'))
            self.assertNotIn('rank_codes',rows[0]['input'])

    def test_changing_export_is_not_a_reproducible_dataset(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as directory:
            root=Path(directory);raw=root/'a.xml';raw.write_bytes(high_rank_fixture())
            import_batch([raw],root/'imported')
            source=root/'imported/discard-samples.jsonl'
            def change_source(*args):
                with source.open('a',encoding='utf-8') as stream:stream.write('\n')
                return dict(skip_reason='test',candidates=[])
            with patch('nanikiru.training.ranking.extract',side_effect=change_source):
                with self.assertRaisesRegex(ValueError,'Source changed'):prepare([source],root/'cache')
            with self.assertRaisesRegex(ValueError,'Incomplete'):open_cache(root/'cache')
