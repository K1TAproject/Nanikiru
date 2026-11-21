from itertools import permutations
import unittest

from nanikiru.rules.scoring import evaluate
from nanikiru.rules.config import RuleConfig
from nanikiru.analysis import analyze
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.simulation import Simulator
from test_simulation import wall_for, choose, passes


class MeldOrderTests(unittest.TestCase):
    def test_screenshot_hand_all_chi_orders_ron_and_tsumo(self):
        hand='2m 3m 4m 6m 6m 4p 0p 6p 4s 5s'.split()
        for tsumo in (False,True):
            expected=evaluate(hand,[('chi',('3s','4s','5s'))],'3s',seat=0,
                              dealer=0,round_wind='east',tsumo=tsumo)
            self.assertIsNotNone(expected)
            self.assertIn('Tanyao',[y['name'] for y in expected['yaku']])
            for order in permutations(('3s','4s','5s')):
                with self.subTest(tsumo=tsumo,order=order):
                    self.assertEqual(evaluate(hand,[('chi',order)],'3s',seat=0,
                        dealer=0,round_wind='east',tsumo=tsumo),expected)
                    self.assertIsNone(evaluate(hand,[('chi',order)],'3s',seat=0,
                        dealer=0,round_wind='east',tsumo=tsumo,rules=RuleConfig(open_tanyao=False)))

    def test_progression_after_chi_can_win(self):
        for tsumo in (False,True):
            g=Simulator(wall=wall_for({
                0:'5s 1m 2m 3m 1p 2p 3p 1s 2s 6s 7s 8s 1z',
                1:'2m 3m 4m 6m 6m 4p 0p 6p 3s 4s 4s 5s 7z'},
                ['9m','8m','7m','6s' if tsumo else '3s','3s' if tsumo else '9p']))
            g.submit(choose(g,0,'discard','5s'))
            g.submit(next(a for a in g.legal_actions(1) if a.kind=='chi' and
                          sorted(g._codes[t] for t in a.tile_ids)==['3s','4s']))
            passes(g)
            g.submit(choose(g,1,'discard','7z'));passes(g)
            for seat in (2,3):
                g.submit(choose(g,seat,'discard',g._codes[g._draw['tile']]));passes(g)
            g.submit(choose(g,0,'discard',g._codes[g._draw['tile']]))
            if tsumo:passes(g)
            kind='tsumo' if tsumo else 'ron'
            action=choose(g,1,kind)
            request=build_analysis_request(g.record(1),source_version='regression',method='fixed-v2')
            result=analyze(request)
            self.assertEqual(result.recommendations['overall'][0].kind,kind)
            g.submit(action);passes(g)
            self.assertEqual(g.result['kind'],'win')
            self.assertIn(1,g.result['winners'])
