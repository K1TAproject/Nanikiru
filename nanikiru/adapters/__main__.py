"""Export local Tenhou logs to observer records and separated sample labels."""

import argparse
import json
from pathlib import Path

from .tenhou import load_tenhou
from ..analysis.output import debug_json
from ..processing.records import save_record


def export(source, destination):
    hands = load_tenhou(source)
    destination = Path(destination)
    # A fresh destination prevents silently overwriting user records.
    destination.mkdir(parents=True, exist_ok=False)
    count = 0
    with (destination / 'discard-samples.jsonl').open('w', encoding='utf-8') as stream:
        for hand in hands:
            for seat, record in enumerate(hand.records):
                save_record(record, destination / f'hand-{hand.hand_index:03d}-seat-{seat}.json')
            for sample in hand.samples():
                stream.write(json.dumps(json.loads(debug_json(sample)), ensure_ascii=False, allow_nan=False)+'\n')
                count += 1
    return len(hands), count


def main():
    parser = argparse.ArgumentParser(description='Import local four-player red-tile Tenhou XML or gzip mjlog')
    parser.add_argument('source', type=Path)
    parser.add_argument('destination', type=Path, help='new output directory')
    args = parser.parse_args()
    try:
        hands, samples = export(args.source, args.destination)
    except (ValueError, OSError) as error:
        parser.exit(1, f'{error}\n')
    print(f'Imported {hands} hands / {samples} discard samples into {args.destination}')


if __name__ == '__main__':
    main()
