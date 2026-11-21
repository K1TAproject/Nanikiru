from copy import deepcopy
from dataclasses import replace
import unittest

from nanikiru.analysis import analyze
from nanikiru.analysis.fixed_policy import Parameters, danger
from nanikiru.analysis.output import summarize_result
from nanikiru.models import Event, UNKNOWN
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.simulation import Simulator


class FixedV2Tests(unittest.TestCase):
    def request(self, **options):
        game = Simulator(seed=17)
        return build_analysis_request(game.record(0), source_version='test', method='fixed-v2', method_options=options)

    def test_parameters_and_other_method_rejection(self):
        for bad in (-1, True, float('nan'), float('inf'), '2'):
            with self.assertRaises(ValueError): Parameters(risk=bad)
        with self.assertRaises(ValueError): analyze(self.request(typo=1))
        with self.assertRaises(ValueError): analyze(replace(self.request(risk=2), method='minimal-shape-v1'))

    def test_rankings_are_allowed_explainable_and_nonmutating(self):
        request = self.request()
        before = deepcopy(request)
        result = analyze(request)
        self.assertEqual(request, before)
        self.assertEqual(set(result.recommendations), {'attack', 'defense', 'overall'})
        for candidate in result.candidates:
            policy = candidate.metrics['policy_score']
            self.assertAlmostEqual(policy.value, sum(policy.details['contributions'].values()))
            self.assertEqual(policy.basis, 'heuristic')
            self.assertTrue(candidate.metrics['danger'].details['not_probability'])
        self.assertIn('非概率', summarize_result(request,result,result.candidates[0].action.tile_ids[0]))

    def test_risk_weight_changes_tradeoff_and_missing_visibility_is_not_zero(self):
        request = self.request()
        own = [t for t in request.position.tiles if t.zone == 'hand']
        safe = own[0].code
        request.history += (Event(2,'sim_riichi_accepted',1,data={'fact':{}}),
                            Event(3,'sim_discard',1,data={'fact':{'code':safe}}))
        defensive = analyze(replace(request, method_options={'risk':100, 'reserve':0}))
        aggressive = analyze(replace(request, method_options={'risk':0, 'reserve':0}))
        self.assertNotEqual(defensive.situation['discard_ranking'].value, aggressive.situation['discard_ranking'].value)
        request.position.completeness['public_tiles'] = False
        incomplete = analyze(request)
        for candidate in incomplete.candidates:
            self.assertIsNone(candidate.metrics['ukeire'].value)
            self.assertIsNone(candidate.metrics['lookahead_gain'].value)
            self.assertIn('visible_inventory', candidate.metrics['policy_score'].details['missing_fields'])

    def test_genbutsu_and_suji_have_different_guarantees(self):
        p = self.request().position
        history=(Event(1,'sim_riichi_accepted',1,data={'fact':{}}),
                 Event(2,'sim_discard',2,data={'fact':{'code':'4m'}}))
        rows = danger(p,history,'4m')[1]
        self.assertEqual(next(r for r in rows if r['seat']==1)['risk'],0)
        rows = danger(p,history,'1m')[1]
        r = next(r for r in rows if r['seat']==1)
        self.assertGreater(r['risk'],0)
        self.assertIn('suji_not_guaranteed_safe',r['evidence'])

    def test_tenpai_value_uses_scoring_and_unknown_context_stays_unavailable(self):
        from test_simulation import wall_for
        game = Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},draws=['9m']))
        request = build_analysis_request(game.record(0), source_version='v', method='fixed-v2')
        result = analyze(request)
        candidate = next(c for c in result.candidates if c.action.tile_ids==(request.position.draw_context['tile_id'],))
        metric = candidate.metrics['conditional_value']
        self.assertGreater(metric.value,0)
        self.assertTrue(metric.details['not_expected_net_gain'])
        from nanikiru.analysis.efficiency import visible_counts
        unseen=sum(4-n for n in visible_counts(request.position))
        self.assertAlmostEqual(candidate.metrics['lookahead_gain'].value,candidate.metrics['ukeire'].value/unseen)
        self.assertEqual(candidate.metrics['lookahead_gain'].details['conditional_quality'],1)
        request.position.dealer_seat = UNKNOWN
        result = analyze(request)
        self.assertTrue(all(c.metrics['conditional_value'].value is None for c in result.candidates))

    def test_incomplete_hand_and_riichi_constraints(self):
        request = self.request()
        request.history += (Event(2,'sim_riichi_accepted',0,data={'fact':{}}),)
        result=analyze(request)
        self.assertEqual(result.recommendations['overall'][0].tile_ids,(request.position.draw_context['tile_id'],))
        request.position.completeness['hand:0']=False
        result=analyze(request)
        self.assertIsNone(result.situation['discard_ranking'].value)
        self.assertFalse(result.recommendations)

