import argparse
import json
from pathlib import Path

from .dataset import export_dataset
from .observer import ObservationConfig


def main():
    parser=argparse.ArgumentParser(description='Generate complete/incomplete observation shards from sample JSONL')
    parser.add_argument('sources', nargs='+', type=Path)
    parser.add_argument('--output', required=True, type=Path)
    parser.add_argument('--seed', default='0')
    parser.add_argument('--shard-size', type=int, default=1000)
    parser.add_argument('--audit-limit', type=int, default=20)
    parser.add_argument('--config', type=Path, help='JSON object mapping variant names to ObservationConfig fields')
    args=parser.parse_args()
    try:
        configs = None if args.config is None else {name:ObservationConfig(**config) for name,config in
            json.loads(args.config.read_text(encoding='utf-8-sig')).items()}
        report=export_dataset(args.sources,args.output,configs=configs,seed=args.seed,
                              shard_size=args.shard_size,audit_limit=args.audit_limit)
    except (ValueError,OSError,TypeError) as error:
        parser.exit(1,f'{error}\n')
    print(json.dumps(report,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
