"""Headless full-hand replay plus observer-only analysis."""

from nanikiru.simulation import Simulator
from nanikiru.processing.analysis_input import build_analysis_request
from nanikiru.analysis import analyze


def main():
    game = Simulator(seed=1)
    request = build_analysis_request(game.record(0), source_version=game.version)
    result = analyze(request)
    print('Opening shanten:', result.situation['shanten'].value)
    while game.phase != 'ended':
        options = [a for s in range(4) for a in game.legal_actions(s)]
        action = next((a for a in options if a.kind in ('ron', 'tsumo')), None)
        action = action or next((a for a in options if a.kind == 'pass'), None)
        action = action or next(a for a in options if a.kind == 'discard')
        game.submit(action)
    print('Commands:', game.command_count, 'Result:', game.result['kind'], 'Scores:', game.result['scores'])


if __name__ == '__main__':
    main()
