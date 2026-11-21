import argparse
import json
from pathlib import Path

from .ranking import prepare, run, evaluate_cache
from ..rules.config import TENHOU_HANCHAN


def main():
    parser=argparse.ArgumentParser(description='Reproducible local v2 ranking experiments')
    commands=parser.add_subparsers(dest='command',required=True)
    cache=commands.add_parser('prepare')
    cache.add_argument('sources',nargs='+',type=Path)
    cache.add_argument('--output',required=True,type=Path)
    cache.add_argument('--seed',default='0')
    cache.add_argument('--rules-profile',choices=('tenhou','experimental'),default='tenhou')
    train=commands.add_parser('fit')
    train.add_argument('dataset',type=Path)
    train.add_argument('--output',required=True,type=Path)
    train.add_argument('--rounds',type=int,default=2)
    train.add_argument('--tune',nargs='+',default=['route','route_speed','route_ukeire','value','risk'])
    train.add_argument('--initial',type=Path)
    evaluation=commands.add_parser('evaluate')
    evaluation.add_argument('dataset',type=Path)
    evaluation.add_argument('--output',required=True,type=Path)
    evaluation.add_argument('--weights',type=Path)
    args=parser.parse_args()
    try:
        if args.command=='prepare':report=prepare(args.sources,args.output,seed=args.seed,
            rules_profile=TENHOU_HANCHAN if args.rules_profile=='tenhou' else None)
        elif args.command=='evaluate':
            weights=None if args.weights is None else json.loads(args.weights.read_text(encoding='utf-8-sig'))
            report=evaluate_cache(args.dataset,args.output,weights)
        else:
            initial=None if args.initial is None else json.loads(args.initial.read_text(encoding='utf-8-sig'))
            report=run(args.dataset,args.output,rounds=args.rounds,tune=tuple(args.tune),initial=initial)
        print(json.dumps({'output':str(args.output),'status':'complete',
                          'weights':report.get('weights'),'rows':report.get('rows')},ensure_ascii=False,indent=2))
    except (OSError,ValueError,KeyError,TypeError) as error:parser.exit(1,f'{error}\n')


if __name__=='__main__':main()
