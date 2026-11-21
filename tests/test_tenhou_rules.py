import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from test_simulation import wall_for, choose, passes
from test_mjai import events, raw
from nanikiru.adapters.mjai import export_mjai
from nanikiru.analysis.output import debug_json
from nanikiru.observation.dataset import variants
from nanikiru.observation import ObservationConfig
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.rules.config import RuleConfig, tenhou_hanchan, TENHOU_HANCHAN
from nanikiru.rules.match import match_status
from nanikiru.rules.scoring import waits
from nanikiru.rules.visible_actions import additional_actions, win_candidate
from nanikiru.simulation import Simulator
from nanikiru.simulation.settlement import finish_win, finish_draw
from nanikiru.training.ranking import prepare

HANDS={0:'3s 9m 8m 7m 6m 5m 4m 3m 2m 1m 9p 8p 7p',
       1:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s',
       2:'1m 2m 3m 1p 2p 3p 1s 2s 3s 6z 6z 4s 5s',
       3:'1m 2m 3m 1p 2p 3p 1s 2s 4s 5s 6s 5z 5z'}

class TenhouRuleTests(unittest.TestCase):
    def test_triple_abort_and_double_honba(self):
        for count in (2,3):
            g=Simulator(wall=wall_for(HANDS,['9s']),rules=tenhou_hanchan(),honba=2,sticks=3)
            g.submit(choose(g,0,'discard','3s'))
            for seat in range(1,count+1):g.submit(choose(g,seat,'ron'))
            passes(g)
            if count==3:
                self.assertEqual(g.result['kind'],'three_ron')
                self.assertEqual(g.result['scores'],[25000]*4)
                self.assertEqual(g.result['riichi_sticks'],3)
                self.assertTrue(g.result['dealer_repeats'])
            else:
                self.assertEqual([(p['winner'],p['amount']) for p in g.result['payments'] if p['reason']=='honba'],[(1,600)])
                self.assertEqual(g.result['details']['deposit_winner'],1)
            g._check_inventory()

    def test_no_kokushi_ankan_rob_in_simulation_or_analysis(self):
        g=Simulator(wall=wall_for({0:'1m 1m 1m 1m 2m 3m 4p 5p 6p 7s 8s 9s 2z',
                                  1:'9m 1p 9p 1s 9s 1z 2z 3z 4z 5z 6z 7z 7z'},['4m']),rules=tenhou_hanchan())
        # Preserve the actual public declaration snapshot before auto-resolution.
        with patch.object(g,'_resolve'):
            g.submit(choose(g,0,'closed_kan'))
        self.assertFalse(any(a.kind=='ron' for a in g.legal_actions(1)))
        req=build_analysis_request(g.record(1),source_version='test',method='fixed-v2')
        self.assertFalse(any(c.action.kind=='ron' for c in additional_actions(req)))

    def test_added_kan_ippatsu_matches_analysis_and_commit_cancels(self):
        g=Simulator(wall=wall_for({0:'6s 9m 8m 7m 6m 5m 4m 3m 2m 1m 9p 8p 7p',
                                  1:HANDS[1]},['9s']),rules=tenhou_hanchan())
        g._emit('riichi_accepted',1,double=False)
        g._emit('kan_declared',2,kan_kind='added_kan')
        tile=next(t for t in g._hands[0] if g._codes[t]=='6s')
        score=g._score(1,tile,rob='added_kan')
        self.assertIn('Ippatsu',[y['name'] for y in score['yaku']])
        req=build_analysis_request(g.record(1),source_version='test')
        from nanikiru.models import TileRecord
        result=win_candidate(req,HANDS[1].split(),[],TileRecord('declared','6s','declared',0),tsumo=False,rob='added_kan')
        self.assertIn('Ippatsu',[y['name'] for y in result.metrics['win_score'].value['yaku']])
        g._emit('added_kan',2,tiles=[])
        self.assertNotIn('Ippatsu',[y['name'] for y in g._score(1,tile,rob='added_kan')['yaku']])

    def test_formal_dead_wait_is_not_a_physical_out(self):
        hand='1m 2p 3p 4p 2s 3s 4s 5z 5z 5z'.split()
        groups=[('pon',('1m',)*3)]
        self.assertEqual(waits(hand,groups),frozenset())
        self.assertEqual(waits(hand,groups,formal=True),{0})

    def test_pao_covers_compound_yakuman(self):
        g=Simulator(seed=1,honba=2,rules=tenhou_hanchan())
        g._groups={str(i):dict(kind='pon',seat=1,tiles=[next(t for t,c in g._codes.items() if c==code)],source=3)
                   for i,code in enumerate(('5z','6z','7z'))}
        score=dict(han=26,fu=40,cost=dict(main=64000,additional=0),
                   yaku=[dict(name='Daisangen',han=13,yakuman=True),dict(name='Tsuuiisou',han=13,yakuman=True)])
        with patch.object(g,'_score',return_value=score),patch.object(g,'_emit'):
            finish_win(g,[1],2,g._hands[2][0])
        paid={s:sum(p['amount'] for p in g.result['payments'] if p['payer']==s) for s in (2,3)}
        self.assertEqual(paid,{2:32000,3:32600})

    def test_match_end_boundaries_and_tie_order(self):
        def status(scores,**kwargs):
            return match_status(scores,**(dict(dealer=3,wind='south',number=4,repeat=False,kind='exhaustive')|kwargs))
        self.assertFalse(status([25000]*4)['ended'])
        self.assertTrue(status([30000,20000,25000,25000])['ended'])
        self.assertFalse(status([30000,20000,25000,25000],repeat=True)['ended'])
        self.assertTrue(status([20000,20000,20000,40000],repeat=True)['ended'])
        self.assertFalse(status([20000,20000,20000,40000],repeat=True,kind='three_ron')['ended'])
        self.assertTrue(status([25000]*4,wind='west')['ended'])
        self.assertFalse(status([0,30000,30000,40000],wind='east',number=1)['ended'])
        self.assertTrue(status([-100,30100,30000,40000],wind='east',number=1)['ended'])
        self.assertEqual(status([25000]*4,dealer=1)['ranking'],[2,3,0,1])

    def test_final_sticks_and_points_conserve(self):
        g=Simulator(seed=1,rules=tenhou_hanchan(),dealer=3,round_wind='south',hand_number=4,
                    scores=[30000,20000,25000,24000],sticks=1)
        with patch.object(g,'_waiting',return_value=set()):finish_draw(g,'exhaustive')
        self.assertTrue(g.result['match']['ended'])
        self.assertEqual(g.result['scores'],[31000,20000,25000,24000])
        self.assertEqual(sum(g.result['match']['result_points']),0)
        self.assertEqual(g.result['riichi_sticks'],0)
        g._check_inventory()

    def test_verified_import_observation_training_and_mismatch(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);source=root/'2026010100gm-00a9-0000-12345678.mjai.json'
            source.write_bytes(raw(events()))
            report=export_mjai(source,root/'import',rules=tenhou_hanchan(),verified_profile=TENHOU_HANCHAN)
            self.assertEqual(report['verified_profile'],TENHOU_HANCHAN)
            sample=json.loads((root/'import/discard-samples.jsonl').read_text(encoding='utf-8'))
            row=next(variants(sample,{'complete':ObservationConfig()},seed='0'))
            self.assertEqual(row['rules_verification'],sample['rules_verification'])
            data=root/'sample.jsonl';data.write_text(debug_json(row,indent=None),encoding='utf-8')
            result=prepare([data],root/'cache',rules_profile=TENHOU_HANCHAN)
            self.assertEqual(result['rules_check'],'verified_source_profile')
            del row['rules_verification'];data.write_text(debug_json(row,indent=None),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Unverified'):prepare([data],root/'bad',rules_profile=TENHOU_HANCHAN)
            sample['input']['position']['rules']['open_tanyao']=False
            data.write_text(debug_json(sample,indent=None),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Unverified'):prepare([data],root/'mismatch',rules_profile=TENHOU_HANCHAN)
            data.write_text(debug_json(sample,indent=None)+'\n'+debug_json(row,indent=None),encoding='utf-8')
            with self.assertRaisesRegex(ValueError,'Mixed rule'):prepare([data],root/'mixed')
            bad=root/'majsoul.jsonl';bad.write_bytes(source.read_bytes())
            with self.assertRaisesRegex(ValueError,'source identifier'):
                export_mjai(bad,root/'bad-import',rules=tenhou_hanchan(),verified_profile=TENHOU_HANCHAN)
