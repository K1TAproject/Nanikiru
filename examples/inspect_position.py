"""Headless observation example: python -m examples.inspect_position."""

from pprint import pprint

from nanikiru import AnalysisRequest, PlayerInfo, RoundSnapshot, TileRecord, RULE_ID
from nanikiru.analysis.efficiency import analyze_discards


def sample_position():
    codes = "1m 2m 3m 1p 2p 3p 1s 2s 3s 7z 7z 4s 5s 9m".split()
    return RoundSnapshot(
        round_id="example", rule_id=RULE_ID, observer_seat=0,
        players=tuple(PlayerInfo(s, 25000, 0 if s == 0 else 13) for s in range(4)),
        tiles=tuple(TileRecord(f"t{i}", code, "hand", 0) for i, code in enumerate(codes)),
        phase="await_discard", actor=0,
        draw_context={"player": 0, "tile_id": "t13", "source": "live"},
        pending=None,
        completeness={"hand:0": True, "groups:0": True, "public_tiles": True},
    )


if __name__ == "__main__":
    request = AnalysisRequest("example:1", sample_position(), ("discard_shape",))
    print("Shape comparison only; no legal-action/recommendation engine yet.")
    pprint(analyze_discards(request.position, ("t13",)))
