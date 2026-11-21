from copy import deepcopy
import unittest

from nanikiru.analysis import analyze
from nanikiru.contracts import Action
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.rules.config import tenhou_hanchan
from nanikiru.rules.visible_actions import additional_actions
from nanikiru.simulation import Simulator
from nanikiru.arena.branches import successors
from nanikiru.simulation.engine import inventory
from test_simulation import wall_for, choose, passes
from test_tenhou_rules import HANDS


class SuccessorTests(unittest.TestCase):
    def request(self, game, seat=0):
        return build_analysis_request(game.record(seat),source_version=game.version,method='fixed-v2')

    def test_nine_candidate_and_unknown_history(self):
        game=Simulator(wall=wall_for({0:'1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z'},['2m']))
        req=self.request(game)
        result=analyze(req)
        row=next(c for c in result.candidates if c.action.kind=='abort_nine')
        self.assertEqual(row.legality,'allowed')
        self.assertEqual(row.metrics['policy_score'].status,'unavailable')
        actual={(a.kind,frozenset(a.tile_ids)) for a in game.legal_actions(0)}
        self.assertEqual(actual,{(c.action.kind,frozenset(c.action.tile_ids))
                                for c in result.candidates if c.legality=='allowed'})
        branch,=successors(game,row.action)
        self.assertEqual(branch.game.result['kind'],'nine_terminals')
        self.assertEqual(branch.game.result['next_round']['honba'],1)
        self.assertEqual(branch.terminal_value(0).status,'unavailable')
        req.history=();req.coverage={'from_start':False,'gaps':[]}
        self.assertEqual(next(c.legality for c in additional_actions(req)
                              if c.action.kind=='abort_nine'),'unknown')

    def test_visible_extra_candidates_equal_engine_during_play(self):
        game=Simulator(seed=18,rules=tenhou_hanchan())
        for _ in range(80):
            if game.result:break
            for seat in range(4):
                req=self.request(game,seat)
                expected={(a.kind,frozenset(a.tile_ids)) for a in game.legal_actions(seat) if a.kind!='discard'}
                actual={(c.action.kind,frozenset(c.action.tile_ids)) for c in additional_actions(req)
                        if c.legality=='allowed'}
                self.assertEqual(expected,actual)
            actions=next(game.legal_actions(s) for s in range(4) if game.legal_actions(s))
            action=next((a for a in actions if a.kind in ('ron','tsumo','riichi','pon','chi')),actions[0])
            game.submit(action)

    def test_all_response_combinations_and_no_mutation(self):
        game=Simulator(wall=wall_for(HANDS,['9s']),rules=tenhou_hanchan())
        before=deepcopy(game.debug_state())
        children=list(successors(game,choose(game,0,'discard','3s')))
        self.assertEqual(before,game.debug_state())
        self.assertTrue(any(b.game.result and b.game.result['kind']=='three_ron' for b in children))
        self.assertTrue(any(b.game.result and len(b.game.result['winners'])==2 for b in children))
        self.assertTrue(any(not b.game.result for b in children))
        paths={b.actions for b in children}
        self.assertEqual(len(paths),len(children))
        for b in children:
            b.game._check_inventory()
            self.assertNotIn(b.game.phase,('await_response','await_kan_response'))

    def test_pass_records_furiten_and_keeps_future_choices(self):
        game=Simulator(wall=wall_for({0:'6s 9m 8m 7m 6m 5m 4m 3m 2m 1m 9p 8p 7p',
            2:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9s']))
        game.submit(choose(game,0,'discard','6s'))
        children=list(successors(game,choose(game,2,'pass')))
        child=next(b for b in children if not b.game.result and b.game.actor==1)
        self.assertTrue(child.game._furiten(2))
        self.assertTrue(child.decisions()[1])
        self.assertFalse(game._furiten(2))

    def test_closed_kan_rob_or_replacement_are_both_retained(self):
        game=Simulator(wall=wall_for({0:'1m 1m 1m 1m 2m 3m 4p 5p 6p 7s 8s 9s 2z',
            1:'9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z 7z'},['4m']))
        children=list(successors(game,choose(game,0,'closed_kan')))
        robbed=next(b.game for b in children if b.game.result)
        completed=next(b.game for b in children if not b.game.result)
        self.assertEqual(robbed._kan_count,0)
        self.assertEqual(len(robbed._live),len(game._live))
        self.assertEqual(completed._kan_count,1)
        self.assertEqual(completed._draw['source'],'replacement')
        self.assertEqual(completed._indicator_count,2)
        self.assertEqual(len(completed._live),len(game._live)-1)
        self.assertTrue(completed.legal_actions(0))

    def test_four_winds_is_discard_successor_not_new_action(self):
        remaining=inventory()
        for _ in range(4):remaining.remove('1z')
        hands={s:['1z']+[remaining.pop() for _ in range(12)] for s in range(4)}
        game=Simulator(wall=wall_for(hands,['1m','2m','3m','4m']))
        for seat in range(3):
            game.submit(choose(game,seat,'discard','1z'));passes(game)
        children=list(successors(game,choose(game,3,'discard','1z')))
        self.assertTrue(any(b.game.result and b.game.result['kind']=='four_winds' for b in children))
        self.assertEqual(game.phase,'await_discard')

    def test_four_riichi_deposit_and_terminal_boundary(self):
        hands={s:'1m 2m 3m 1p 2p 3p 1s 2s 3s 4s 5s '+f'{s+1}z {s+1}z' for s in range(4)}
        hands[3]=hands[3].replace('5s','0s')
        game=Simulator(wall=wall_for(hands,['9m','8m','7m','6m']),rules=tenhou_hanchan())
        for seat,code in enumerate(('9m','8m','7m')):
            game.submit(choose(game,seat,'riichi',code));passes(game)
        branch=next(b for b in successors(game,choose(game,3,'riichi','6m'))
                    if b.game.result and b.game.result['kind']=='four_riichi')
        self.assertEqual(branch.game.result['scores'],[24000]*4)
        self.assertEqual(branch.game.result['riichi_sticks'],4)
        self.assertEqual(branch.terminal_value(0).status,'unavailable')

    def test_final_rank_and_secondary_only_after_match_end(self):
        game=Simulator(wall=wall_for({3:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['6s']),
                       rules=tenhou_hanchan(),dealer=3,round_wind='south',hand_number=4)
        branch,=successors(game,choose(game,3,'tsumo'))
        value=branch.terminal_value(3)
        self.assertEqual(value.value['rank_value'],-1)
        self.assertEqual(value.value['secondary_unit'],'result_points')
        self.assertEqual(branch.decisions(),{})
        self.assertIsNone(game.result)

    def test_invalid_action_is_atomic(self):
        game=Simulator(seed=1);before=game.debug_state()
        with self.assertRaises(ValueError):list(successors(game,Action('abort_nine',1)))
        self.assertEqual(before,game.debug_state())

    def test_meld_followups_and_safe_observer_projection(self):
        game=Simulator(wall=wall_for({0:'3m 9m 9m 1p 2p 3p 7p 8p 9p 1s 2s 3s 1z',
            1:'1m 2m 6m 7m 8m 2p 3p 4p 4s 5s 6s 2z 3z',
            2:'3m 3m 4m 4m 5m 5m 6p 6p 7s 7s 8s 8s 4z'},['2z']))
        game.submit(choose(game,0,'discard','3m'))
        result=analyze(self.request(game,1))
        call=next(c for c in result.candidates if c.action.kind=='chi')
        children=list(successors(game,call.action))
        won=next(b for b in children if b.game.actor==1)
        lost=next(b for b in children if b.game.actor==2)
        expected={a for a in won.decisions()[1] if a.kind=='discard'}
        kept={c.action for c in call.metrics['action_evaluation'].value['follow_up_candidates']}
        self.assertEqual(expected,kept)
        turn=won.observer_turn(1)
        self.assertTrue(all(t.seat==1 for t in turn.request.position.tiles if t.zone=='hand'))
        self.assertTrue(all(not t.id.startswith('t') for t in turn.request.position.tiles))
        self.assertEqual(lost.game.actor,2)

    def test_added_kan_retains_robbed_and_completed_paths(self):
        game=Simulator(wall=wall_for({
            0:'1m 4m 5m 6m 1p 2p 3p 4p 5p 6p 1z 2z 3z',
            1:'1m 1m 2p 3p 4p 4s 5s 6s 5z 6z 7z 8p 9p',
            2:'2m 3m 1p 2p 3p 1s 2s 3s 5z 5z 7s 8s 9s'},
            ['9m','8m','7m','6m','1m']))
        game.submit(choose(game,0,'discard','1m'))
        game.submit(choose(game,1,'pon'));passes(game)
        game.submit(choose(game,1,'discard','7z'));passes(game)
        for seat in (2,3,0):
            game.submit(Action('discard',seat,(game._draw['tile'],)));passes(game)
        children=list(successors(game,choose(game,1,'added_kan')))
        robbed=next(b.game for b in children if b.game.result)
        completed=next(b for b in children if not b.game.result)
        self.assertEqual(robbed.result['details']['rob'],'added_kan')
        self.assertEqual(robbed._kan_count,0)
        self.assertEqual(completed.game._kan_count,1)
        self.assertEqual(completed.game._delayed_dora,1)
        discard=next(a for a in completed.decisions()[1] if a.kind=='discard')
        later=list(successors(completed.game,discard))
        self.assertTrue(all(b.game._indicator_count==2 for b in later))

    def test_open_kan_and_declined_tsumo_keep_discard_choices(self):
        game=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 1z 2z 3z 4z',
            1:'1m 1m 1m 4p 5p 6p 4s 5s 6s 5z 5z 6z 6z'},['9m']))
        game.submit(choose(game,0,'discard','1m'))
        branches=list(successors(game,choose(game,1,'open_kan')))
        branch=next(b for b in branches if not b.game.result and b.game.actor==1)
        self.assertEqual(branch.game._kan_count,1)
        self.assertEqual(branch.game._draw['source'],'replacement')
        self.assertTrue(branch.decisions()[1])
        game=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['6s']))
        self.assertIn('tsumo',{a.kind for a in game.legal_actions(0)})
        children=list(successors(game,choose(game,0,'discard','6s')))
        self.assertTrue(children)
        self.assertTrue(all(not b.game.result or b.game.result['details'].get('source') is not None
                            for b in children))


if __name__=='__main__':unittest.main()
