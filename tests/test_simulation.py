import tempfile
import unittest
from pathlib import Path

from nanikiru.contracts import Action
from nanikiru.models import UNKNOWN, Event
from nanikiru.rules.config import RuleConfig
from nanikiru.rules.scoring import evaluate
from nanikiru.simulation import Simulator
from nanikiru.simulation.engine import inventory
from nanikiru.simulation.persistence import load_simulation, save_simulation, replay_simulation
from nanikiru.simulation.settlement import finish_draw, finish_win
from nanikiru.processing.records import replay, save_record, load_record
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.analysis import analyze
from nanikiru.analysis.output import debug_json


def wall_for(hands=None, draws=(), dead=None):
    fixed = {}
    for seat, codes in (hands or {}).items():
        codes = codes.split() if isinstance(codes, str) else codes
        assert len(codes) == 13
        fixed.update({seat*13+i: c for i, c in enumerate(codes)})
    fixed.update({52+i: c for i, c in enumerate(draws)})
    fixed.update({122+i: c for i, c in (dead or {}).items()})
    remaining = inventory()
    for c in fixed.values():
        remaining.remove(c)
    return [fixed[i] if i in fixed else remaining.pop() for i in range(136)]


def choose(game, seat, kind, code=None):
    return next(a for a in game.legal_actions(seat) if a.kind == kind
                and (code is None or any(game._codes[t] == code for t in a.tile_ids)))


def passes(game):
    while game.phase in ("await_response", "await_kan_response"):
        options = [a for s in range(4) for a in game.legal_actions(s) if a.kind == "pass"]
        assert options
        game.submit(options[0])


class SimulatorTests(unittest.TestCase):
    def test_seed_inventory_config_and_atomic_failure(self):
        a, b = Simulator(seed=7), Simulator(seed=7)
        self.assertEqual(a._codes, b._codes)
        self.assertEqual(len(a._live), 69)
        self.assertEqual(a.observe(0).rules, RuleConfig())
        before = a.debug_state()
        with self.assertRaises(ValueError):
            a.submit(Action("tsumo", 1))
        self.assertEqual(before, a.debug_state())
        with self.assertRaises(ValueError): RuleConfig(kan_dora=1)
        with self.assertRaises(ValueError): Simulator(wall=["1m"]*136)
        a._check_inventory()

    def test_observer_isolation_prefix_and_rule_roundtrip(self):
        g = Simulator(seed=9, rules=RuleConfig(open_tanyao=False))
        record = g.record(1)
        self.assertIs(record.events[0].data['fact']['code'], UNKNOWN)
        self.assertIsNone(record.events[0].data['fact']['tile'])
        text = debug_json(record)
        self.assertNotIn('"wall"', text)
        self.assertNotIn('"seed"', text)
        self.assertFalse(any(t.zone == 'hand' and t.seat != 1 for t in g.observe(1).tiles))
        req = build_analysis_request(record, source_version=g.version, through=0)
        self.assertFalse(req.history)
        self.assertFalse(req.position.rules.open_tanyao)
        self.assertEqual(analyze(req).situation['discard_ranking'].status, 'not_applicable')
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            p=Path(d)/'view.json'; save_record(record,p)
            self.assertEqual(replay(load_record(p)),g.observe(1))

    def test_full_exhaustive_replay_and_point_conservation(self):
        g = Simulator(seed=1, rules=RuleConfig(nagashi_mangan=False))
        for _ in range(300):
            if g.phase == 'ended': break
            options=[a for s in range(4) for a in g.legal_actions(s)]
            a=next((a for a in options if a.kind=='pass'),None)
            a=a or next(a for a in options if a.kind=='discard')
            g.submit(a)
        self.assertEqual(g.result['kind'],'exhaustive')
        self.assertEqual(len(g._live),0)
        g._check_inventory()
        for s in range(4): self.assertEqual(replay(g.record(s)),g.observe(s))
        with tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1]) as d:
            p=Path(d)/'full.json'; save_simulation(g,p); rebuilt=load_simulation(p)
        self.assertEqual(rebuilt.result,g.result)
        self.assertEqual(rebuilt.record(0),g.record(0))
        self.assertNotEqual(rebuilt.version,g.version)
        self.assertNotEqual(replay_simulation(g,len(g._commands)-1).phase,'ended')

    def test_chi_pon_competition_and_kuikae(self):
        g=Simulator(wall=wall_for({
            0:'3m 9m 9m 1p 2p 3p 7p 8p 9p 1s 2s 3s 1z',
            1:'1m 2m 6m 7m 8m 2p 3p 4p 4s 5s 6s 2z 3z',
            2:'3m 3m 4m 4m 5m 5m 6p 6p 7s 7s 8s 8s 4z'},['2z']))
        g.submit(choose(g,0,'discard','3m'))
        g.submit(choose(g,1,'chi'))
        self.assertEqual(g.phase,'await_response')
        g.submit(choose(g,2,'pon')); passes(g)
        self.assertEqual(g.actor,2)
        self.assertEqual(next(iter(g._groups.values()))['kind'],'pon')
        self.assertTrue(g._rivers[0][0]['called'])
        self.assertFalse(any(g._codes[a.tile_ids[0]]=='3m' for a in g.legal_actions(2) if a.kind=='discard'))
        self.assertEqual(replay(g.record(2)),g.observe(2))
        result=analyze(build_analysis_request(g.record(2),source_version=g.version))
        actual={a for a in g.legal_actions(2) if a.kind=='discard'}
        self.assertEqual({c.action for c in result.candidates if c.legality=='allowed'},actual)
        self.assertTrue(set(result.recommendations['overall']) <= actual)

    def test_closed_kan_replacement_and_dora_switch(self):
        wall=wall_for({0:'1m 1m 1m 2m 3m 4p 5p 6p 7s 8s 9s 1z 1z'},['1m'])
        for enabled in (True,False):
            g=Simulator(wall=wall,rules=RuleConfig(kan_dora=enabled))
            g.submit(choose(g,0,'closed_kan')); passes(g)
            self.assertEqual(g._kan_count,1)
            self.assertEqual(len(g._live),68)
            self.assertEqual(g._draw['source'],'replacement')
            self.assertEqual(g._indicator_count,2 if enabled else 1)
            self.assertEqual(len(g._hands[0]),11)
            self.assertEqual(len(g._dead),14)
            g._check_inventory()

    def test_open_kan_delays_dora_until_discard(self):
        g=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 1z 2z 3z 4z',
                                  1:'1m 1m 1m 4p 5p 6p 4s 5s 6s 5z 5z 6z 6z'},['9m']))
        g.submit(choose(g,0,'discard','1m')); g.submit(choose(g,1,'open_kan')); passes(g)
        self.assertEqual(g.actor,1); self.assertEqual(g._indicator_count,1)
        self.assertEqual(g._delayed_dora,1)
        g.submit(choose(g,1,'discard')); self.assertEqual(g._indicator_count,2)
        g._check_inventory()

    def test_riichi_acceptance_ippatsu_and_lock(self):
        g=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9m']))
        g.submit(choose(g,0,'riichi','9m')); passes(g)
        self.assertIsNotNone(g._riichi(0)); self.assertEqual(g._scores[0],24000)
        self.assertEqual(g._sticks,1); self.assertTrue(g._riichi(0).data['double'])
        while g.actor!=0 and g.phase!='ended':
            if g.phase=='await_discard': g.submit(choose(g,g.actor,'discard'))
            passes(g)
        discards=[a for a in g.legal_actions(0) if a.kind=='discard']
        self.assertEqual(len(discards),1); self.assertEqual(discards[0].tile_ids,(g._draw['tile'],))
        result=analyze(build_analysis_request(g.record(0),source_version=g.version))
        self.assertEqual(result.recommendations['overall'],tuple(discards))

    def test_three_ron_priority_deposits_and_each_honba(self):
        hands={0:'3s 9m 8m 7m 6m 5m 4m 3m 2m 1m 9p 8p 7p',
               1:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s',
               2:'1m 2m 3m 1p 2p 3p 1s 2s 3s 6z 6z 4s 5s',
               3:'1m 2m 3m 1p 2p 3p 1s 2s 4s 5s 6s 5z 5z'}
        g=Simulator(wall=wall_for(hands,['9s']),honba=2,sticks=3)
        g.submit(choose(g,0,'discard','3s'))
        for s in (3,2,1): g.submit(choose(g,s,'ron'))
        self.assertEqual(g.result['winners'],[1,2,3])
        self.assertEqual(g.result['details']['deposit_winner'],1)
        self.assertEqual(g.result['details']['deposits'],3000)
        self.assertEqual([p['amount'] for p in g.result['payments'] if p['reason']=='honba'],[600]*3)
        self.assertFalse(g.result['dealer_repeats']); g._check_inventory()

    def test_ron_on_riichi_declaration_does_not_take_deposit(self):
        # Both hands wait on 3s/6s, but declarer discards 6s to stay tenpai.
        g=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s',
                                  1:'1m 2m 3m 1p 2p 3p 1s 2s 3s 6z 6z 4s 5s'},['6s']))
        g.submit(choose(g,0,'riichi','6s')); g.submit(choose(g,1,'ron')); passes(g)
        self.assertIsNone(g._riichi(0)); self.assertEqual(g._sticks,0)
        self.assertEqual(g.result['kind'],'win')

    def test_temporary_and_discard_furiten(self):
        g=Simulator(wall=wall_for({0:'6s 9m 8m 7m 6m 5m 4m 3m 2m 1m 9p 8p 7p',
                                  1:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9s']))
        g.submit(choose(g,0,'discard','6s'))
        self.assertTrue(any(a.kind=='ron' for a in g.legal_actions(1)))
        # Check the private pass before resolution grants seat 1 its next draw.
        g._emit('pass',1,private=True,choice='pass',missed=True,target=g._pending['event'])
        self.assertTrue(g._furiten(1))
        self.assertFalse(any(e.kind=='sim_pass' for e in g.record(0).events))
        g._events.append(Event(len(g._events)+1,'draw',1))
        self.assertFalse(g._furiten(1))
        g._rivers[1].append(dict(tile=g._pending['tile'],called=True,riichi=False))
        self.assertTrue(g._furiten(1))

    def test_nine_terminals_and_bankruptcy_boundary(self):
        g=Simulator(wall=wall_for({0:'1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z'},['2m']))
        g.submit(choose(g,0,'abort_nine'))
        self.assertEqual(g.result['kind'],'nine_terminals')
        self.assertTrue(g.result['dealer_repeats']); self.assertEqual(g.result['next_round']['honba'],1)
        self.assertEqual(g.legal_actions(0),())
        with self.assertRaises(ValueError): Simulator(scores=[-1,25000,25000,25000])
        Simulator(scores=[0,25000,25000,25000])
        Simulator(rules=RuleConfig(bankruptcy=False),scores=[-1,25000,25000,25000])

    def test_four_winds_actual_sequence(self):
        hands={s:None for s in range(4)}
        remaining=inventory()
        for _ in range(4): remaining.remove('1z')
        for s in range(4): hands[s]=['1z']+[remaining.pop() for _ in range(12)]
        g=Simulator(wall=wall_for(hands,['1m','2m','3m','4m']))
        for s in range(4):
            g.submit(choose(g,s,'discard','1z')); passes(g)
        self.assertEqual(g.result['kind'],'four_winds')
        self.assertEqual(g.result['scores'],[25000]*4)

    def test_configured_scoring_changes(self):
        hand='2m 3m 4m 2p 3p 4p 3s 4s 5s 6s'.split()
        groups=(('chi',('6s','7s','8s')),)
        args=dict(seat=1,dealer=0,round_wind='east')
        self.assertIsNotNone(evaluate(hand,groups,'6s',rules=RuleConfig(),**args))
        self.assertIsNone(evaluate(hand,groups,'6s',rules=RuleConfig(open_tanyao=False),**args))
        kokushi='1m 9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z'.split()
        one=evaluate(kokushi,(),'1m',rules=RuleConfig(),**args)
        two=evaluate(kokushi,(),'1m',rules=RuleConfig(double_yakuman=True),**args)
        self.assertEqual(one['han'],13); self.assertEqual(two['han'],26)

    def test_added_kan_can_be_robbed_without_replacement(self):
        g=Simulator(wall=wall_for({
            0:'1m 4m 5m 6m 1p 2p 3p 4p 5p 6p 1z 2z 3z',
            1:'1m 1m 2p 3p 4p 4s 5s 6s 5z 6z 7z 8p 9p',
            2:'2m 3m 1p 2p 3p 1s 2s 3s 5z 5z 7s 8s 9s'},
            ['9m','8m','7m','6m','1m']))
        g.submit(choose(g,0,'discard','1m'))
        g.submit(choose(g,1,'pon')); passes(g)
        g.submit(choose(g,1,'discard','7z')); passes(g)
        for s in (2,3,0):
            self.assertEqual(g.actor,s)
            g.submit(Action('discard',s,(g._draw['tile'],))); passes(g)
        self.assertEqual(g.actor,1)
        before=len(g._live)
        g.submit(choose(g,1,'added_kan'))
        view=g.observe(2)
        self.assertEqual([t.code for t in view.tiles if t.zone=='declared'],['1m'])
        req=build_analysis_request(g.record(2),source_version=g.version)
        self.assertEqual(req.position,view)
        g.submit(choose(g,2,'ron')); passes(g)
        self.assertEqual(g.result['details']['rob'],'added_kan')
        self.assertEqual(g._kan_count,0); self.assertEqual(g._indicator_count,1)
        self.assertEqual(len(g._live),before)
        self.assertEqual(next(iter(g._groups.values()))['kind'],'pon')
        self.assertIn('Chankan',[y['name'] for y in g.result['details']['hands'][0]['score']['yaku']])

    def test_kokushi_robs_closed_kan(self):
        g=Simulator(wall=wall_for({0:'1m 1m 1m 1m 2m 3m 4p 5p 6p 7s 8s 9s 2z',
                                  1:'9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z 7z'},['4m']))
        g.submit(choose(g,0,'closed_kan'))
        self.assertEqual(len([t for t in g.observe(1).tiles if t.zone=='declared']),4)
        g.submit(choose(g,1,'ron')); passes(g)
        self.assertEqual(g._kan_count,0)
        self.assertEqual(g.result['details']['rob'],'closed_kan')

    def test_tsumo_tenhou_and_non_east_context(self):
        g=Simulator(wall=wall_for({2:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['6s']),
                    dealer=2,round_wind='south',honba=1)
        g.submit(choose(g,2,'tsumo'))
        self.assertTrue(g.result['dealer_repeats'])
        self.assertEqual(g.result['scores'],[8900,8900,73300,8900])
        self.assertEqual(g.result['next_round']['round_wind'],'south')
        self.assertIn('Tenhou',[y['name'] for y in g.result['details']['hands'][0]['score']['yaku']])

    def test_riichi_permanent_furiten_and_ippatsu_cancellation(self):
        g=Simulator(wall=wall_for({0:'6s 9m 8m 7m 6m 5m 4m 3m 2m 1m 9p 8p 7p',
                                  1:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9s']))
        g._emit('riichi_accepted',1,double=False)
        tile=next(t for t in g._hands[0] if g._codes[t]=='6s')
        score=g._score(1,tile)
        self.assertIn('Ippatsu',[y['name'] for y in score['yaku']])
        g._events.append(Event(len(g._events)+1,'kan_declared',2))
        self.assertNotIn('Ippatsu',[y['name'] for y in g._score(1,tile)['yaku']])
        g._events.append(Event(len(g._events)+1,'pass',1,data={'missed':True}))
        g._events.append(Event(len(g._events)+1,'draw',1))
        self.assertTrue(g._furiten(1))

    def test_four_riichi_and_four_kan_resolution(self):
        # Isolate response-resolution predicates; full physical inventory remains valid.
        for kind in ('four_riichi','four_kans'):
            g=Simulator(seed=8)
            if kind=='four_riichi':
                for s in range(4): g._emit('riichi_accepted',s,double=False)
            else:
                g._kan_count=4
                # Only ownership is read by this branch; no inventory mutation.
                g._groups={'a':{'kind':'closed_kan','seat':0},'b':{'kind':'closed_kan','seat':1}}
            from unittest.mock import patch
            with patch.object(g,'_emit'), patch.object(g,'_response_options',return_value=()), patch.object(g,'_missed_shape',return_value=False):
                g._apply(Action('discard',0,(g._hands[0][0],)))
            self.assertEqual(g.result['kind'],kind)

    def test_exhaustive_noten_and_nagashi_payments(self):
        from unittest.mock import patch
        g=Simulator(seed=8,rules=RuleConfig(nagashi_mangan=False),honba=2,sticks=2)
        with patch.object(g,'_waiting',side_effect=lambda s: {1} if s in (0,2) else set()):
            finish_draw(g,'exhaustive')
        self.assertEqual(g.result['scores'],[26500,23500,26500,23500])
        self.assertEqual(g.result['next_round']['honba'],3)
        self.assertEqual(g.result['riichi_sticks'],2)
        g._check_inventory()
        g=Simulator(seed=8,honba=2,sticks=1)
        # Settlement unit fixture: river metadata only; no progression follows.
        tile=next(t for t,c in g._codes.items() if c=='1z')
        g._rivers[1]=[dict(tile=tile,called=False)]
        with patch.object(g,'_waiting',return_value=set()), patch.object(g,'_emit'): finish_draw(g,'exhaustive')
        self.assertEqual(g.result['scores'],[21000,33000,23000,23000])
        self.assertEqual(g.result['details']['nagashi'],[1])
        self.assertEqual(g.result['riichi_sticks'],1)

    def test_pao_only_responsible_yakuman_component(self):
        from unittest.mock import patch
        g=Simulator(seed=1,honba=2)
        g._groups={str(i):dict(kind='pon',seat=1,tiles=[next(t for t,c in g._codes.items() if c==code)],
                               source=3) for i,code in enumerate(('5z','6z','7z'))}
        score=dict(han=26,fu=40,cost=dict(main=64000,additional=0),
                   yaku=[dict(name='Daisangen',han=13,yakuman=True),dict(name='Tsuuiisou',han=13,yakuman=True)])
        with patch.object(g,'_score',return_value=score),patch.object(g,'_emit'):
            finish_win(g,[1],2,g._hands[2][0])
        paid={s:sum(p['amount'] for p in g.result['payments'] if p['payer']==s) for s in (2,3)}
        self.assertEqual(paid,{2:48000,3:16600})
        self.assertEqual(g.result['scores'][1],89600)

    def test_one_player_four_kans_rinshan_and_no_fifth_kan(self):
        g=Simulator(wall=wall_for({0:'1m 1m 1m 1m 2m 2m 2m 2m 3m 3m 3m 3m 4m'},
                                  ['4m'],{0:'4m',1:'4m',2:'7z',3:'7z'}))
        for code in ('1m','2m','3m','4m'):
            g.submit(choose(g,0,'closed_kan',code)); passes(g)
        self.assertEqual(g._kan_count,4)
        self.assertEqual(g.phase,'await_discard')
        self.assertEqual(len(g._live),65)
        self.assertFalse(g._can_kan())
        g.submit(choose(g,0,'tsumo'))
        self.assertIn('Suu Kantsu',[y['name'] for y in g.result['details']['hands'][0]['score']['yaku']])
        g._check_inventory()

    def test_multiple_players_four_kans_complete_sequence(self):
        g=Simulator(wall=wall_for({
            0:'1m 1m 1m 1m 2m 2m 2m 2m 1p 2p 3p 4p 5p',
            1:'3m 3m 3m 3m 4m 4m 4m 4m 1s 2s 3s 4s 5s'},
            ['9m','8m'],{0:'1z',1:'2z',2:'3z',3:'4z'}))
        for code in ('1m','2m'): g.submit(choose(g,0,'closed_kan',code)); passes(g)
        g.submit(choose(g,0,'discard','9m')); passes(g)
        for code in ('3m','4m'): g.submit(choose(g,1,'closed_kan',code)); passes(g)
        self.assertEqual(g.phase,'await_discard')
        g.submit(choose(g,1,'discard','8m')); passes(g)
        self.assertEqual(g.result['kind'],'four_kans'); g._check_inventory()

    def test_four_riichi_complete_sequence(self):
        hands={s:('1m 2m 3m 1p 2p 3p 1s 2s 3s 4s 5s '+f'{s+1}z {s+1}z') for s in range(4)}
        hands[3]=hands[3].replace('5s','0s')
        g=Simulator(wall=wall_for(hands,['9m','8m','7m','6m']))
        for s,code in enumerate(('9m','8m','7m','6m')):
            g.submit(choose(g,s,'riichi',code)); passes(g)
        self.assertEqual(g.result['kind'],'four_riichi')
        self.assertEqual(g.result['scores'],[24000]*4)
        self.assertEqual(g.result['riichi_sticks'],4); g._check_inventory()

    def test_ura_and_ippatsu_rules_reach_analysis_scoring(self):
        from nanikiru.analysis import score_hand
        args=dict(concealed='1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'.split(),groups=(),win_tile='6s',
                  seat=1,dealer=0,round_wind='east',tsumo=False,riichi=True,indicators=('8m',),is_ippatsu=True)
        result=score_hand(**args,ura=UNKNOWN,rules=RuleConfig(ura_dora=False,ippatsu=False))
        self.assertEqual(result.status,'conditional')
        self.assertNotIn('Ippatsu',[y['name'] for y in result.value['score']['yaku']])
        self.assertEqual(score_hand(**args,ura=UNKNOWN,rules=RuleConfig()).status,'unavailable')

    def test_single_ron_option_and_riichi_minimum_live_tiles(self):
        g=Simulator(wall=wall_for({0:'6s 9m 8m 7m 6m 5m 4m 3m 2m 1m 9p 8p 7p',
                                  1:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s',
                                  2:'1m 2m 3m 1p 2p 3p 1s 2s 3s 6z 6z 4s 5s'},['9s']),
                    rules=RuleConfig(multiple_ron=False))
        g.submit(choose(g,0,'discard','6s')); g.submit(choose(g,2,'ron')); g.submit(choose(g,1,'ron')); passes(g)
        self.assertEqual(g.result['winners'],[1])
        g=Simulator(wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9m']))
        g._live=g._live[:3]
        self.assertFalse(any(a.kind=='riichi' for a in g.legal_actions(0)))

    def test_no_kuikae_and_aka_kan_ura_switches(self):
        from nanikiru.rules.discards import discard_ids
        hand=[('a','1m'),('b','0p'),('c','5p')]
        self.assertEqual(discard_ids(hand,riichi=False,drawn_id='c',banned={13}),('a',))
        self.assertEqual(discard_ids(hand,riichi=False,drawn_id='c'),('a','b','c'))
        args=dict(concealed='1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 0s'.split(),groups=(),win_tile='6s',
                  seat=1,dealer=0,round_wind='east',riichi=True,indicators=('8m','6z'),ura=('8p','6z'))
        base=evaluate(**args,rules=RuleConfig())
        noaka=evaluate(**args,rules=RuleConfig(aka_dora=False))
        noura=evaluate(**args,rules=RuleConfig(kan_ura=False))
        nokan=evaluate(**args,rules=RuleConfig(kan_dora=False))
        self.assertEqual(base['han']-noaka['han'],1)
        self.assertEqual(base['han']-noura['han'],2)
        self.assertEqual(base['han']-nokan['han'],4)


if __name__=='__main__': unittest.main()
