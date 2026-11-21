"""Resume the saved failing hand as a regression probe, not a batch result."""
from contextlib import ExitStack
from pathlib import Path
import argparse

from nanikiru.arena.akochan import AkochanBot
from nanikiru.arena.evaluation import configuration, write
from nanikiru.arena.session import Session, inspect_turn
from nanikiru.simulation.persistence import load_simulation, save_simulation


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input',default='data/evaluations/akochan-pilot-02/match-00000-1/failed-hand.json')
    parser.add_argument('--output',required=True)
    args=parser.parse_args();out=Path(args.output);out.mkdir(parents=True,exist_ok=False)
    config=configuration('examples/evaluation-akochan.json')
    write(out/'manifest.json',config)
    game=load_simulation(args.input);session=Session(game);start=game.command_count
    with ExitStack() as stack:
        processes={}
        bot=config['bots']['akochan']
        for seat in (0,2,3):
            err=stack.enter_context((out/f'bot-{seat}.stderr.log').open('w'))
            process=AkochanBot(bot['command'],bot['cwd'],bot['timeout'],err)
            stack.callback(process.close);processes[seat]=process
        while game.phase!='ended':
            if game.command_count-start>=500:raise RuntimeError('Probe action limit exceeded')
            seat=session.acting_seat();b=config['bots']['fitted-v2']
            turn=session.turn(seat,b['mode'],b['parameters']) if seat==1 else session.turn(seat)
            action=inspect_turn(turn.request).action if seat==1 else processes[seat].decide(turn,game.record(seat))
            session.submit(turn,action)
            print(f'action={game.command_count} seat={seat} kind={action.kind}',flush=True)
        save_simulation(game,out/'replay.json')
        write(out/'result.json',dict(status='complete',continued_actions=game.command_count-start,result=game.result,
                                    scope='saved_failure_regression_not_a_new_hanchan_sample'))
        print('Failure hand reached settlement.',flush=True)


if __name__=='__main__':main()
