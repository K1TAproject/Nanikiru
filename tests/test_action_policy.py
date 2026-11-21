from copy import deepcopy
import json
import unittest

from nanikiru.analysis import analyze
from nanikiru.analysis.output import summarize_result
from nanikiru.analysis.routes import evaluate_routes
from nanikiru.observation import observe_request
from nanikiru.observation.dataset import compare_methods
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.rules.call_rules import call_banned
from nanikiru.rules.config import RuleConfig
from nanikiru.simulation import Simulator
from nanikiru.tiles import index
from test_simulation import wall_for, choose, passes


class ActionPolicyTests(unittest.TestCase):
    def request(self, game, seat=0):
        return build_analysis_request(game.record(seat),source_version='actions',method='fixed-v2')

    def assert_allowed(self, game, request, result):
        actual={(a.kind,tuple(sorted(a.tile_ids))) for a in game.legal_actions(request.position.observer_seat)}
        for c in result.candidates:
            if c.legality=='allowed':self.assertIn((c.action.kind,tuple(sorted(c.action.tile_ids))),actual)
        allowed={c.action for c in result.candidates if c.legality=='allowed'}
        for actions in result.recommendations.values():self.assertTrue(set(actions)<=allowed)

    def test_riichi_and_dama_are_separate_nonmutating_branches(self):
        g=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9m']))
        request=self.request(g);before=deepcopy(request);result=analyze(request)
        self.assertEqual(request,before)
        self.assert_allowed(g,request,result)
        drawn=request.position.draw_context['tile_id']
        rows=[c for c in result.candidates if c.action.tile_ids==(drawn,)]
        self.assertEqual({c.action.kind for c in rows},{'discard','riichi'})
        self.assertTrue(all('routes' in c.metrics for c in rows))
        for c in result.candidates:
            s=c.metrics['policy_score'];self.assertAlmostEqual(s.value,sum(s.details['contributions'].values()))
        request.position.remaining_draws=3
        self.assertFalse(any(c.action.kind=='riichi' for c in analyze(request).candidates))

    def test_chi_pon_and_followup_kuikae(self):
        g=Simulator(wall=wall_for({0:'3m 9m 9m 1p 2p 3p 7p 8p 9p 1s 2s 3s 1z',
            1:'1m 2m 6m 7m 8m 2p 3p 4p 4s 5s 6s 2z 3z',
            2:'3m 3m 4m 4m 5m 5m 6p 6p 7s 7s 8s 8s 4z'},['2z']))
        g.submit(choose(g,0,'discard','3m'))
        for seat,kind in ((1,'chi'),(2,'pon')):
            request=self.request(g,seat);before=deepcopy(request);result=analyze(request)
            self.assertEqual(request,before);self.assert_allowed(g,request,result)
            rows=[c for c in result.candidates if c.action.kind==kind]
            self.assertTrue(rows)
            codes={t.id:t.code for t in request.position.tiles}
            for c in rows:
                follow=c.metrics['action_evaluation'].value['follow_up_discard']
                banned=call_banned(kind,[codes[t] for t in c.action.tile_ids],'3m')
                self.assertNotIn(index(codes[follow.tile_ids[0]]),banned)
            self.assertIn('动作',summarize_result(request,result))
            json.dumps(compare_methods(request))

    def test_closed_and_open_kan_branches(self):
        g=Simulator(wall=wall_for({0:'1m 1m 1m 2m 3m 4p 5p 6p 7s 8s 9s 1z 1z'},['1m']))
        request=self.request(g);result=analyze(request);self.assert_allowed(g,request,result)
        self.assertTrue(any(c.action.kind=='closed_kan' for c in result.candidates))
        g=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 1z 2z 3z 4z',
            1:'1m 1m 1m 4p 5p 6p 4s 5s 6s 5z 5z 6z 6z'},['9m']))
        g.submit(choose(g,0,'discard','1m'))
        request=self.request(g,1);result=analyze(request);self.assert_allowed(g,request,result)
        self.assertTrue(any(c.action.kind=='open_kan' for c in result.candidates))

    def test_win_priority_and_missing_private_pass_history(self):
        g=Simulator(wall=wall_for({0:'6s 9m 8m 7m 6m 5m 4m 3m 2m 1m 9p 8p 7p',
            1:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9s']))
        g.submit(choose(g,0,'discard','6s'))
        request=self.request(g,1);result=analyze(request);self.assert_allowed(g,request,result)
        self.assertEqual(result.recommendations['overall'][0].kind,'ron')
        self.assertIn('荣和',summarize_result(request,result))
        observed=analyze(observe_request(request))
        self.assertEqual(next(c.legality for c in observed.candidates if c.action.kind=='ron'),'unknown')
        self.assertFalse(observed.recommendations)
        g=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['6s']))
        request=self.request(g);result=analyze(request);self.assert_allowed(g,request,result)
        self.assertEqual(result.recommendations['overall'][0].kind,'tsumo')
        json.dumps(compare_methods(request))

    def test_routes_and_open_tanyao_switch(self):
        p=self.request(Simulator(seed=1)).position
        routes=evaluate_routes('1m 1m 2p 2p 3s 3s 4m 4m 5p 5p 6s 6s 7z'.split(),[],p)
        self.assertEqual(next(r['distance'] for r in routes['routes'] if r['name']=='seven_pairs'),1)
        hand='2m 3m 4m 2p 3p 4p 6s 7s 8s 5z'.split();groups=[('pon',('2s',)*3)]
        p.rules=RuleConfig(open_tanyao=False)
        names={r['name'] for r in evaluate_routes(hand,groups,p)['routes']}
        self.assertFalse({'tanyao','seven_pairs','menzen_riichi'} & names)
        p.rules=RuleConfig(open_tanyao=True)
        self.assertIn('tanyao',{r['name'] for r in evaluate_routes(hand,groups,p)['routes']})

    def test_added_kan_and_robbery(self):
        g=Simulator(wall=wall_for({0:'1m 4m 5m 6m 1p 2p 3p 4p 5p 6p 1z 2z 3z',
            1:'1m 1m 2p 3p 4p 4s 5s 6s 5z 6z 7z 8p 9p',
            2:'2m 3m 1p 2p 3p 1s 2s 3s 5z 5z 7s 8s 9s'},['9m','8m','7m','6m','1m']))
        g.submit(choose(g,0,'discard','1m'));g.submit(choose(g,1,'pon'));passes(g)
        g.submit(choose(g,1,'discard','7z'));passes(g)
        for seat in (2,3,0):
            g.submit(next(a for a in g.legal_actions(seat) if a.kind=='discard' and a.tile_ids==(g.observe(seat).draw_context['tile_id'],)))
            passes(g)
        request=self.request(g,1);result=analyze(request);self.assert_allowed(g,request,result)
        self.assertTrue(any(c.action.kind=='added_kan' for c in result.candidates))
        g.submit(choose(g,1,'added_kan'))
        request=self.request(g,2);result=analyze(request);self.assert_allowed(g,request,result)
        self.assertEqual(result.recommendations['overall'][0].kind,'ron')
