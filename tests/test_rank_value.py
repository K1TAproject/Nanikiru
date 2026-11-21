from copy import deepcopy
from dataclasses import replace
import json
import math
from pathlib import Path
import tempfile
import unittest

from nanikiru.analysis.rank_features import FEATURE_NAMES, extract_features
from nanikiru.analysis.rank_value import RankValueModel
from nanikiru.models import UNKNOWN
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.rules.config import tenhou_hanchan
from nanikiru.simulation import Simulator
from nanikiru.observation import observe_request
from test_simulation import wall_for


class RankValueTests(unittest.TestCase):
    def request(self):
        game=Simulator(seed=18,rules=tenhou_hanchan())
        return build_analysis_request(game.record(0),source_version='features')

    def test_features_finite_fixed_and_no_input_mutation(self):
        req=self.request();before=deepcopy(req);features=extract_features(req)
        self.assertEqual(req,before)
        self.assertEqual(len(features),len(FEATURE_NAMES))
        self.assertTrue(all(math.isfinite(x) for x in features))
        self.assertEqual(features,extract_features(req))

    def test_unknown_score_different_from_zero(self):
        req=self.request();req.position.players=(replace(req.position.players[0],score=0),*req.position.players[1:])
        zero=dict(zip(FEATURE_NAMES,extract_features(req)))
        req.position.players=(replace(req.position.players[0],score=UNKNOWN),*req.position.players[1:])
        unknown=dict(zip(FEATURE_NAMES,extract_features(req)))
        self.assertEqual(zero['score_0'],unknown['score_0'])
        self.assertEqual(zero['score_0_missing'],0)
        self.assertEqual(unknown['score_0_missing'],1)

    def test_metadata_and_hidden_payload_do_not_affect_features(self):
        req=self.request();before=extract_features(req)
        req.position.round_id='future-source';req.state_version='different'
        req.history[0].data['unused_future_result']={'rank':1,'wall':['1m']*136}
        self.assertEqual(before,extract_features(req))

    def test_untrained_model_is_not_a_prediction(self):
        model=RankValueModel()
        self.assertTrue(all(m.status=='unavailable' for m in model.evaluate(self.request()).values()))
        result=model.forward(extract_features(self.request()))
        self.assertEqual(result['probabilities'],[.25]*4)
        self.assertEqual(result['points'],0)

    def test_softmax_stability_and_separate_losses_no_updates(self):
        model=RankValueModel(rank_bias=[1000,-1000,0])
        features=extract_features(self.request());before=deepcopy(model)
        result=model.forward(features)
        self.assertAlmostEqual(sum(result['probabilities']),1)
        self.assertTrue(all(0<=x<=1 for x in result['probabilities']))
        a=model.loss(features,rank=2,result_points=10)
        b=model.loss(features,rank=2,result_points=-30)
        self.assertTrue(math.isfinite(a['rank_nll']))
        self.assertEqual(a['rank_nll'],b['rank_nll'])
        self.assertNotEqual(a['points_mse'],b['points_mse'])
        self.assertEqual(model,before)

    def test_model_roundtrip_and_version_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'untrained.json';model=RankValueModel();model.save(path)
            self.assertEqual(model,RankValueModel.load(path))
            with self.assertRaises(FileExistsError):model.save(path)
            data=json.loads(path.read_text());data['feature_names'].reverse()
            path.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError,'Incompatible'):RankValueModel.load(path)

    def test_reject_bad_coefficients_inputs_and_rules(self):
        model=RankValueModel();model.rank_bias[0]=float('nan')
        with self.assertRaises(ValueError):model.forward([0.0]*len(FEATURE_NAMES))
        with self.assertRaises(ValueError):RankValueModel().forward([0])
        with self.assertRaises(ValueError):RankValueModel(trained=True).validate()
        game=Simulator(seed=1)
        with self.assertRaisesRegex(ValueError,'A-rule'):extract_features(build_analysis_request(game.record(0),source_version='other'))

    def test_wait_value_and_private_furiten_coverage(self):
        game=Simulator(rules=tenhou_hanchan(),wall=wall_for({1:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9m']))
        req=build_analysis_request(game.record(1),source_version='wait')
        features=dict(zip(FEATURE_NAMES,extract_features(req)))
        self.assertGreater(features['base_ron_max'],0)
        self.assertEqual(features['base_ron_max_missing'],0)
        self.assertEqual(features['temporary_furiten_missing'],0)
        masked=dict(zip(FEATURE_NAMES,extract_features(observe_request(req))))
        self.assertEqual(masked['temporary_furiten_missing'],1)


if __name__=='__main__':unittest.main()
