"""Run a visible simulator position with explicit human weights."""

import argparse
import json
from pathlib import Path

from nanikiru.analysis import analyze
from nanikiru.analysis.output import debug_json, summarize_result
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.simulation import Simulator


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--parameters', type=Path, help='JSON object of weight overrides')
    parser.add_argument('--seed', default='v2-demo')
    parser.add_argument('--json', action='store_true')
    args=parser.parse_args()
    options=json.loads(args.parameters.read_text(encoding='utf-8-sig')) if args.parameters else {}
    game=Simulator(seed=args.seed)
    request=build_analysis_request(game.record(game.actor), source_version=game.version,
                                   method='fixed-v2',method_options=options)
    result=analyze(request)
    print(summarize_result(request,result))
    for candidate in result.candidates:
        if candidate.action.kind != 'discard': continue
        tile=next(t.code for t in request.position.tiles if t.id==candidate.action.tile_ids[0])
        metrics=candidate.metrics
        print(tile, 'shanten=',metrics['shanten'].value, 'ukeire=',metrics['ukeire'].value,
              'value=',metrics['conditional_value'].value, 'risk=',metrics['danger'].value,
              'score=',round(metrics['policy_score'].value,4))
    if args.json: print(debug_json(result))


if __name__=='__main__': main()
