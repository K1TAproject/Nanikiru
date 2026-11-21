"""Run with python -m examples.analyze_basics; no UI or recommendation engine."""

from dataclasses import asdict, replace
from pprint import pprint

from examples.inspect_position import sample_position
from nanikiru.analysis import analyze_shanten, analyze_waits, analyze_discard_shapes, score_hand
from nanikiru.analysis.efficiency import hand_codes


if __name__ == "__main__":
    position = sample_position()
    ready = replace(position, tiles=position.tiles[:-1], draw_context=None, phase="await_draw")
    pprint({
        "shanten": asdict(analyze_shanten(ready)),
        "waits": asdict(analyze_waits(ready)),
        "discard": asdict(analyze_discard_shapes(position, ("t13",))[0]),
        "conditional_score": asdict(score_hand(
            hand_codes(ready), (), "6s", seat=0, dealer=0, round_wind="east",
            tsumo=True, riichi=False, indicators=(),
        )),
    })
