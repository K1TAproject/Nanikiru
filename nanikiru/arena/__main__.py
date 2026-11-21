"""python -m nanikiru.arena CONFIG --output DIRECTORY"""
import argparse
import json
from .evaluation import run, compare, write


def main():
    parser=argparse.ArgumentParser(description='A-rule hanchan batch evaluation; rerun identical command to resume')
    parser.add_argument('config');parser.add_argument('--output',required=True)
    parser.add_argument('--compare',metavar='NEW_DIRECTORY',help='Compare completed old/new directories instead of running games')
    args=parser.parse_args()
    if args.compare:
        result=compare(args.config,args.compare)
        from pathlib import Path
        if Path(args.output).exists():parser.error('Comparison output already exists')
        write(args.output,result)
    else:result=run(args.config,args.output)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
