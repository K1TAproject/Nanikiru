import json
from pathlib import Path
import tempfile
import unittest
from dataclasses import asdict, replace

from nanikiru.arena.session import Session, inspect_turn, load_weights
from nanikiru.analysis.output import debug_json
from nanikiru.analysis.fixed_policy import Parameters
from nanikiru.contracts import Action
from nanikiru.rules.config import tenhou_hanchan
from nanikiru.simulation import Simulator
from nanikiru.simulation.persistence import save_simulation, load_simulation
from test_simulation import wall_for, choose, passes


class ArenaTests(unittest.TestCase):
    def test_observer_only_opaque_bot_boundary(self):
        session=Session(Simulator(seed=9,rules=tenhou_hanchan()))
        turn=session.turn(0,'v1 牌效')
        raw=debug_json(turn.request)
        self.assertNotRegex(raw,r'"t\d+"')
        self.assertNotIn('"wall"',raw);self.assertNotIn('"seed"',raw)
        self.assertTrue(all(t.seat==0 for t in turn.request.position.tiles if t.zone=='hand'))
        seen=[]
        def bot(request):
            seen.append(request.position.observer_seat)
            return inspect_turn(request).action
        session.step(bot=bot)
        self.assertEqual(seen,[0]);self.assertEqual(session.game.command_count,1)
        with self.assertRaisesRegex(ValueError,'旧建议'):session.submit(turn,inspect_turn(turn.request).action)

    def test_invalid_action_is_atomic(self):
        session=Session(Simulator(seed=3,rules=tenhou_hanchan()));turn=session.turn(0,'v1 牌效')
        before=session.game.debug_state()
        for action in (Action('discard',1,('v0',)),Action('discard',0,('not-visible',)),Action('ron',0)):
            with self.assertRaises(ValueError):session.submit(turn,action)
            self.assertEqual(before,session.game.debug_state())

    def test_response_local_id_translation_and_meld(self):
        game=Simulator(rules=tenhou_hanchan(),wall=wall_for({
            0:'3m 9m 9m 1p 2p 3p 7p 8p 9p 1s 2s 3s 1z',
            1:'1m 2m 6m 7m 8m 2p 3p 4p 4s 5s 6s 2z 3z',
            2:'3m 3m 4m 4m 5m 5m 6p 6p 7s 7s 8s 8s 4z'},['2z']))
        # A previous private observation makes global/local IDs different.
        game._emit('pass',3,private=True,missed=False)
        game.submit(choose(game,0,'discard','3m'))
        session=Session(game);turn=session.turn(1)
        result=inspect_turn(turn.request).result
        chi=next(c.action for c in result.candidates if c.action.kind=='chi' and c.legality=='allowed')
        actual=choose(game,1,'chi')
        self.assertNotEqual(chi.target_id,actual.target_id)
        session.submit(turn,chi);passes(game)
        self.assertTrue(any(g['kind']=='chi' for g in game.observe(1).groups.values()))

    def test_v1_and_v2_take_available_win(self):
        for mode in ('v1 牌效','默认 v2'):
            game=Simulator(rules=tenhou_hanchan(),wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['6s']))
            session=Session(game);action=session.step(mode)
            self.assertEqual(action.kind,'tsumo');self.assertEqual(game.phase,'ended')

    def test_complete_bot_hand_and_saved_replay(self):
        session=Session(Simulator(seed=18,rules=tenhou_hanchan()))
        for _ in range(300):
            if session.game.phase=='ended':break
            session.step('v1 牌效')
        self.assertEqual(session.game.phase,'ended')
        result=session.game.result
        self.assertEqual(sum(result['scores'])+result['riichi_sticks']*1000,100000)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'round.json';save_simulation(session.game,path)
            other=load_simulation(path)
            self.assertEqual(other.result,result)

    def test_fitted_weight_rule_guard(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'weights.json';weights=asdict(Parameters())
            path.write_text(json.dumps(weights),encoding='utf-8')
            from nanikiru.analysis.fixed_policy import FORMULA_VERSION
            report=dict(weights=weights,dataset=dict(status='complete',rules=asdict(tenhou_hanchan())))
            path.with_name('report.json').write_text(json.dumps(report),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'公式版本'):load_weights(path)
            report['formula_version']=FORMULA_VERSION
            path.with_name('report.json').write_text(json.dumps(report),encoding='utf-8')
            self.assertEqual(load_weights(path),weights)
            report['dataset']['rules']['kokushi_ankan_ron']=True
            path.with_name('report.json').write_text(json.dumps(report),encoding='utf-8')
            with self.assertRaises(ValueError):load_weights(path)
            session=Session(Simulator(rules=replace(tenhou_hanchan(),kokushi_ankan_ron=True)))
            with self.assertRaises(ValueError):session.turn(0,'拟合 v2',weights)
