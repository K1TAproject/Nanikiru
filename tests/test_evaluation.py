import copy
import json
from pathlib import Path
from collections import Counter
import sys
import tempfile
import unittest
from unittest.mock import patch

from nanikiru.arena.akochan import visible_log, translate
from nanikiru.arena.evaluation import configuration, play_match, run, summarize, compare, write
from nanikiru.arena.external import JsonProcess, RequestBot
from nanikiru.arena.session import Session
from nanikiru.contracts import Action
from nanikiru.rules.config import tenhou_hanchan
from nanikiru.simulation import Simulator
from nanikiru.simulation.persistence import load_simulation
from test_simulation import wall_for, choose, passes


class EvaluationTests(unittest.TestCase):
    @unittest.skipUnless(Path('data/external-bots/akochan/nanikiru-build/akochan.exe').exists(),'Local akochan build not installed')
    def test_akochan_native_capacity_regression(self):
        payload=json.loads(Path('tests/fixtures/akochan-capacity.json').read_text(encoding='utf-8'))
        config=json.loads(Path('examples/evaluation-akochan.json').read_text(encoding='utf-8'))['bots']['akochan']
        config['cwd']=str((Path('examples')/config['cwd']).resolve())
        config['command'][0]=str((Path('examples')/config['command'][0]).resolve())
        with tempfile.TemporaryFile() as err:
            bot=JsonProcess(config['command'],config['cwd'],config['timeout'],err)
            try:moves=bot.ask(payload)
            finally:bot.close()
        hand=Counter(payload['record'][1]['tehais'][3])
        for event in payload['record'][2:]:
            if event.get('actor')==3:
                hand[event['pai']]+=1 if event['type']=='tsumo' else -1
        self.assertEqual(moves[0]['type'],'dahai')
        self.assertEqual(moves[0]['actor'],3)
        self.assertGreater(hand[moves[0]['pai']],0)

    def test_full_hanchan_and_replay(self):
        config=configuration('examples/evaluation-smoke.json')
        with tempfile.TemporaryDirectory() as folder:
            result=play_match(config,18,2,folder)
            self.assertGreater(len(result['hands']),1)
            self.assertTrue(result['hands'][-1]['result']['match']['ended'])
            self.assertEqual(sum(result['scores']),100000)
            self.assertEqual(result['rank'],result['ranking'].index(2)+1)
            for before,after in zip(result['hands'],result['hands'][1:]):
                replay=load_simulation(Path(folder)/after['file'])
                self.assertEqual(replay._initial['scores'],before['result']['next_round']['scores'])
                self.assertEqual(replay.honba,before['result']['next_round']['honba'])

    def test_akochan_visible_record_and_action_translation(self):
        game=Simulator(seed=18,rules=tenhou_hanchan());session=Session(game)
        record=visible_log(game.record(0));turn=session.turn(0,'v1 牌效')
        self.assertEqual(record[1]['tehais'][1],['?']*13)
        self.assertNotIn('wall',json.dumps(record));self.assertNotIn('seed',json.dumps(record))
        code=record[-1]['pai']
        action,_=translate([dict(type='dahai',actor=0,pai=code,tsumogiri=True)],turn.request)
        self.assertEqual(action.tile_ids,(turn.request.position.draw_context['tile_id'],))
        session.submit(turn,action)
        record=visible_log(game.record(1))
        self.assertEqual(record[2]['pai'],'?')
        self.assertEqual(record[3]['pai'],code)

    def test_akochan_calls_and_riichi_sequences(self):
        game=Simulator(rules=tenhou_hanchan(),wall=wall_for({
            0:'3m 9m 9m 1p 2p 3p 7p 8p 9p 1s 2s 3s 1z',
            1:'1m 2m 6m 7m 8m 2p 3p 4p 4s 5s 6s 2z 3z'},['2z']))
        game.submit(choose(game,0,'discard','3m'));session=Session(game);turn=session.turn(1)
        move=dict(type='chi',actor=1,target=0,pai='3m',consumed=['1m','2m'])
        follow=dict(type='dahai',actor=1,pai='S',tsumogiri=False)
        action,queue=translate([move,follow],turn.request)
        session.submit(turn,action);passes(game)
        self.assertEqual(visible_log(game.record(1))[-1],move)
        session.submit(session.turn(1),translate(queue,session.turn(1).request)[0])
        self.assertEqual([e for e in visible_log(game.record(1)) if e['type']=='dahai'][-1],follow)
        bad=copy.deepcopy(move);bad['target']=2
        with self.assertRaises(ValueError):translate([bad],turn.request)

    def test_transport_timeout_bad_json_and_version(self):
        with tempfile.TemporaryFile() as err:
            bot=JsonProcess([sys.executable,'-u','-c','import sys; sys.stdin.readline(); sys.exit(7)'],None,3,err)
            try:
                with self.assertRaisesRegex(RuntimeError,r'exit=7 \(0x00000007\)'):bot.ask({})
            finally:bot.close()
            bot=JsonProcess([sys.executable,'-c','import time; time.sleep(5)'],None,.1,err)
            try:
                with self.assertRaises(TimeoutError):bot.ask({})
            finally:bot.close()
            bot=RequestBot([sys.executable,'-u','-c','import sys; sys.stdin.readline(); print("{}")'],None,3,err)
            try:
                turn=Session(Simulator(seed=1,rules=tenhou_hanchan())).turn(0)
                with self.assertRaisesRegex(ValueError,'stale'):bot.decide(turn,None)
            finally:bot.close()
            bot=RequestBot([sys.executable,'-u','-m','examples.stdio_bot'],None,10,err)
            try:
                session=Session(Simulator(seed=18,rules=tenhou_hanchan()))
                turn=session.turn(0,'v1 牌效');action=bot.decide(turn,None)
                session.submit(turn,action)
                self.assertEqual(session.game.command_count,1)
            finally:bot.close()

    def test_kan_dora_and_reach_event_order(self):
        g=Simulator(rules=tenhou_hanchan(),wall=wall_for({0:'1m 1m 1m 2m 3m 4p 5p 6p 7s 8s 9s 1z 1z'},['1m']))
        g.submit(choose(g,0,'closed_kan'));passes(g)
        events=visible_log(g.record(1))
        self.assertEqual([e['type'] for e in events][-3:],['ankan','dora','tsumo'])
        self.assertEqual(events[-3]['consumed'],['1m']*4)
        g=Simulator(rules=tenhou_hanchan(),wall=wall_for({0:'1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s'},['9m']))
        turn=Session(g).turn(0)
        action,_=translate([dict(type='reach',actor=0),dict(type='dahai',actor=0,pai='9m',tsumogiri=True)],turn.request)
        Session(g).submit(turn,action);passes(g)
        kinds=[e['type'] for e in visible_log(g.record(2))]
        self.assertLess(kinds.index('reach'),kinds.index('dahai'))
        self.assertLess(kinds.index('dahai'),kinds.index('reach_accepted'))
        g=Simulator(rules=tenhou_hanchan(),wall=wall_for({
            0:'1m 4m 5m 6m 1p 2p 3p 4p 5p 6p 1z 2z 3z',
            1:'1m 1m 2p 3p 4p 4s 5s 6s 5z 6z 7z 8p 9p',
            2:'2m 3m 1p 2p 3p 1s 2s 3s 5z 5z 7s 8s 9s'},['9m','8m','7m','6m','1m']))
        g.submit(choose(g,0,'discard','1m'));g.submit(choose(g,1,'pon'));passes(g)
        g.submit(choose(g,1,'discard','7z'));passes(g)
        for seat in (2,3,0):
            g.submit(Action('discard',seat,(g._draw['tile'],)));passes(g)
        g.submit(choose(g,1,'added_kan'))
        log=visible_log(g.record(2))
        self.assertEqual(log[-1],dict(type='kakan',actor=1,pai='1m',consumed=['1m']*3))
        passes(g)
        self.assertEqual(sum(e['type']=='kakan' for e in visible_log(g.record(2))),1)
        g.submit(choose(g,1,'discard'))
        kinds=[e['type'] for e in visible_log(g.record(2))]
        self.assertEqual(kinds[kinds.index('dora')+1],'dahai')

    def test_statistics_only_complete_rotation_blocks(self):
        rows=[dict(seed=1,rotation=s,rank=s+1,points=25000,result_points=0) for s in range(4)]
        rows.append(dict(seed=2,rotation=0,rank=1,points=50000,result_points=50))
        report=summarize(rows,8)
        self.assertEqual(report['metrics']['rank']['mean'],2.5)
        self.assertIsNone(report['metrics']['rank']['ci95_normal'])
        self.assertEqual(report['excluded_partial_block_matches'],1)

    def test_transient_windows_progress_file_lock(self):
        import os
        replace=os.replace
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'status.json'
            calls=[]
            def busy_once(source,target):
                calls.append(1)
                if len(calls)==1:raise PermissionError('reader temporarily holds file')
                return replace(source,target)
            with patch('nanikiru.arena.evaluation.os.replace',side_effect=busy_once):write(path,dict(stage='running'))
            self.assertEqual(json.loads(path.read_text()),dict(stage='running'))
            self.assertEqual(len(calls),2)

    def test_resume_failure_and_manifest_guard(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'run';cfg=root/'config.json'
            cfg.write_text(Path('examples/evaluation-smoke.json').read_text(encoding='utf-8'),encoding='utf-8')
            calls=[]
            def fake(config,seed,rotation,folder,progress):
                calls.append(rotation);Path(folder).mkdir(parents=True,exist_ok=True)
                if rotation==1 and len(calls)==2:raise RuntimeError('test failure')
                return dict(seed=seed,rotation=rotation,hands=[],rank=rotation+1,points=25000,result_points=0)
            with patch('nanikiru.arena.evaluation.play_match',side_effect=fake):
                with self.assertRaisesRegex(RuntimeError,'test failure'):run(cfg,out)
                self.assertEqual(json.loads((out/'status.json').read_text())['stage'],'failed')
                report=run(cfg,out)
                self.assertEqual(report['status'],'complete');self.assertEqual(calls,[0,1,1,2,3])
                self.assertEqual(compare(out,out)['new_minus_old']['rank']['mean'],0)
                changed=json.loads(cfg.read_text(encoding='utf-8'));changed['seeds']=[19];cfg.write_text(json.dumps(changed),encoding='utf-8')
                with self.assertRaisesRegex(ValueError,'changed'):run(cfg,out)


if __name__=='__main__':unittest.main()
