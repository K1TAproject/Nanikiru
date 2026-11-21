"""Explicit entry assumptions for the manual UI, not a hidden game state."""

from uuid import uuid4

from nanikiru.models import UNKNOWN, RULE_ID, PlayerInfo, RoundRecord, RoundSnapshot, TileRecord


def parse_code(value):
    return UNKNOWN if value.strip() == "?" else value.strip()


def make_record(hand, drawn, indicator, *, actor=0, phase="await_discard",
                from_start=True, rivers=((), (), (), ()), public_complete=False, remaining=UNKNOWN):
    """No melds; observer 0, 13 tiles per player except the active discarder."""
    if len(hand) != 13 or len(rivers) != 4:
        raise ValueError("请输入 13 张自身手牌、四家牌河")
    if phase not in ("await_draw", "await_discard"):
        raise ValueError("初始化仅支持待摸牌或待弃牌")
    if from_start and (phase != "await_discard" or actor != 0 or any(rivers)):
        raise ValueError("开局入口限定自己庄家首次待弃牌，牌河为空；其他情况选中途")
    tiles = [TileRecord(f"h{i}", c, "hand", 0) for i, c in enumerate(hand)]
    if phase == "await_discard" and actor == 0:
        tiles.append(TileRecord("entry-draw", drawn, "hand", 0))
        draw = {"player": 0, "tile_id": "entry-draw", "source": "live"}
    elif phase == "await_discard":
        draw = {"player": actor, "tile_id": UNKNOWN, "source": UNKNOWN}
    else:
        draw = None
    tiles.append(TileRecord("indicator", indicator, "dora", None))
    for seat, river in enumerate(rivers):
        tiles.extend(TileRecord(f"r{seat}-{i}", c, "river", seat) for i, c in enumerate(river))
    completeness = {"hand:0": True, "public_tiles": True if from_start else public_complete,
                    **{f"groups:{s}": True for s in range(4)}}
    snapshot = RoundSnapshot(
        round_id=uuid4().hex, rule_id=RULE_ID, observer_seat=0,
        players=tuple(PlayerInfo(s, UNKNOWN, 0 if s == 0 else
                                14 if s == actor and phase == "await_discard" else 13) for s in range(4)),
        tiles=tuple(tiles), phase=phase, actor=actor, draw_context=draw, pending=None,
        dealer_seat=0 if from_start else UNKNOWN, remaining_draws=remaining,
        completeness=completeness,
    )
    return RoundRecord(snapshot, coverage={"from_start": from_start, "gaps": []})
